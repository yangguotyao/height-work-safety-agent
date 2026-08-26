from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import get_settings
from .platform.workspaces import DEFAULT_WORKSPACE_ID, ProjectWorkspaceManager

BACKUP_FORMAT_VERSION = 1


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sqlite_backup(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as source_connection, closing(
        sqlite3.connect(target)
    ) as target_connection:
        source_connection.backup(target_connection)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_database(destination: Path | None = None) -> Path:
    """Keep the original single-database command for backward compatibility."""
    settings = get_settings()
    source = settings.resolved_database_path
    target = destination or source.parent / "backups" / (
        f"height_work_agent_{_timestamp()}.sqlite"
    )
    _sqlite_backup(source, target)
    return target


def backup_all(destination: Path | None = None) -> Path:
    """Create a portable backup of every project, upload and local vector index."""
    settings = get_settings()
    manager = ProjectWorkspaceManager(settings)
    runtime_root = settings.resolved_database_path.parent.resolve()
    target = destination or runtime_root / "backups" / f"height_work_agent_{_timestamp()}.tar.gz"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="height-work-backup-") as temporary:
        stage = Path(temporary)
        projects: list[dict[str, Any]] = []
        for item in manager.list()["projects"]:
            project = manager.get(item["id"])
            database_path = Path(project["database_path"]).resolve()
            upload_dir = Path(project["upload_dir"]).resolve()
            if not database_path.is_relative_to(runtime_root):
                raise ValueError(f"项目数据库不在运行目录内：{database_path}")
            if not upload_dir.is_relative_to(runtime_root):
                raise ValueError(f"项目上传目录不在运行目录内：{upload_dir}")

            database_member: Path | None = None
            if database_path.exists():
                database_member = Path("databases") / f"{project['id']}.sqlite"
                _sqlite_backup(database_path, stage / database_member)
            uploads_member = Path("uploads") / project["id"]
            (stage / uploads_member).mkdir(parents=True, exist_ok=True)
            if upload_dir.exists():
                shutil.copytree(upload_dir, stage / uploads_member, dirs_exist_ok=True)
            projects.append(
                {
                    "id": project["id"],
                    "name": project["name"],
                    "database": database_member.as_posix() if database_member else None,
                    "uploads": uploads_member.as_posix(),
                }
            )

        shutil.copy2(manager.catalog_path, stage / "project_workspaces.json")
        chroma_member: str | None = None
        chroma_dir = settings.resolved_chroma_dir
        if chroma_dir.exists():
            shutil.copytree(chroma_dir, stage / "chroma")
            chroma_member = "chroma"

        files = {
            path.relative_to(stage).as_posix(): _sha256(path)
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active_project_id": manager.list()["active_project_id"],
            "projects": projects,
            "catalog": "project_workspaces.json",
            "chroma": chroma_member,
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with tarfile.open(target, "w:gz") as archive:
            for path in sorted(stage.rglob("*")):
                archive.add(
                    path,
                    arcname=path.relative_to(stage).as_posix(),
                    recursive=False,
                )
    return target


def _extract_verified(archive_path: Path, destination: Path) -> dict[str, Any]:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if (
                member.name.startswith(("/", "\\"))
                or not member_path.is_relative_to(destination.resolve())
                or member.issym()
                or member.islnk()
            ):
                raise ValueError(f"备份包包含不安全路径：{member.name}")
        archive.extractall(destination, members=members)

    manifest_path = destination / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("备份包缺少 manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError("备份格式版本不受支持")
    for relative, expected in manifest.get("files", {}).items():
        path = (destination / relative).resolve()
        if not path.is_relative_to(destination.resolve()) or not path.is_file():
            raise ValueError(f"备份文件缺失：{relative}")
        if _sha256(path) != expected:
            raise ValueError(f"备份文件校验失败：{relative}")
    return manifest


def verify_backup(archive_path: Path) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"备份包不存在：{archive_path}")
    with TemporaryDirectory(prefix="height-work-verify-") as temporary:
        return _extract_verified(archive_path, Path(temporary))


def _staged_member(stage: Path, relative: str) -> Path:
    path = (stage / relative).resolve()
    if not path.is_relative_to(stage.resolve()):
        raise ValueError(f"备份清单包含不安全路径：{relative}")
    return path


def _replace_directory(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.restore-new")
    previous = target.with_name(f".{target.name}.restore-old")
    for path in (staged, previous):
        if path.exists():
            shutil.rmtree(path)
    shutil.copytree(source, staged)
    if target.exists():
        target.replace(previous)
    staged.replace(target)
    if previous.exists():
        shutil.rmtree(previous)


def restore_all(archive_path: Path, *, confirmed: bool = False) -> dict[str, Any]:
    """Restore a verified archive. The web container must be stopped first."""
    if not confirmed:
        raise ValueError("恢复会替换当前数据，请停止应用后使用 --confirm")
    settings = get_settings()
    runtime_root = settings.resolved_database_path.parent.resolve()
    rollback = backup_all(runtime_root / "backups" / f"pre_restore_{_timestamp()}.tar.gz")
    with TemporaryDirectory(prefix="height-work-restore-") as temporary:
        stage = Path(temporary)
        manifest = _extract_verified(archive_path.resolve(), stage)
        for project in manifest["projects"]:
            project_id = project["id"]
            if project_id != DEFAULT_WORKSPACE_ID and not re.fullmatch(
                r"[a-f0-9]{32}", project_id
            ):
                raise ValueError(f"备份清单包含无效项目标识：{project_id}")
            if project_id == DEFAULT_WORKSPACE_ID:
                database_target = settings.resolved_database_path
                upload_target = settings.resolved_upload_dir
            else:
                workspace = runtime_root / "projects" / project_id
                database_target = workspace / "workspace.sqlite"
                upload_target = workspace / "uploads"
            database_target.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(f"{database_target}{suffix}").unlink(missing_ok=True)
            database_member = project.get("database")
            if database_member:
                temporary_database = database_target.with_suffix(".restore.sqlite")
                shutil.copy2(_staged_member(stage, database_member), temporary_database)
                database_target.unlink(missing_ok=True)
                temporary_database.replace(database_target)
            else:
                database_target.unlink(missing_ok=True)
            upload_source = _staged_member(stage, project["uploads"])
            _replace_directory(upload_source, upload_target)

        chroma_member = manifest.get("chroma")
        if chroma_member and _staged_member(stage, chroma_member).exists():
            _replace_directory(
                _staged_member(stage, chroma_member), settings.resolved_chroma_dir
            )
        shutil.copy2(
            _staged_member(stage, manifest["catalog"]),
            runtime_root / "project_workspaces.json",
        )
    return {"status": "restored", "rollback_backup": str(rollback), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="高处作业安全审查与预警智能体维护工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="仅备份默认 SQLite（兼容旧命令）")
    backup.add_argument("--output", type=Path)
    backup_full = subparsers.add_parser("backup-all", help="备份全部项目数据")
    backup_full.add_argument("--output", type=Path)
    verify = subparsers.add_parser("verify", help="校验完整备份包")
    verify.add_argument("archive", type=Path)
    restore = subparsers.add_parser("restore", help="恢复完整备份包")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--confirm", action="store_true")

    arguments = parser.parse_args()
    if arguments.command == "backup":
        print(backup_database(arguments.output))
    elif arguments.command == "backup-all":
        print(backup_all(arguments.output))
    elif arguments.command == "verify":
        print(json.dumps(verify_backup(arguments.archive), ensure_ascii=False, indent=2))
    elif arguments.command == "restore":
        print(
            json.dumps(
                restore_all(arguments.archive, confirmed=arguments.confirm),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
