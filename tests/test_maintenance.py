import sqlite3
from contextlib import closing
from pathlib import Path

from backend.app import maintenance
from backend.app.platform.workspaces import ProjectWorkspaceManager


def _write_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        connection.commit()


def _read_value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT value FROM sample").fetchone()[0]


def test_full_backup_verify_and_restore_all_project_data(test_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(maintenance, "get_settings", lambda: test_settings)
    manager = ProjectWorkspaceManager(test_settings)
    default_project = manager.get("default-project")
    second_project = manager.create("武汉市住宅楼施工项目")

    default_database = Path(default_project["database_path"])
    second_database = Path(second_project["database_path"])
    _write_database(default_database, "default-before")
    _write_database(second_database, "second-before")
    default_upload = Path(default_project["upload_dir"])
    default_upload.mkdir(parents=True)
    (default_upload / "plan.txt").write_text("original", encoding="utf-8")
    test_settings.resolved_chroma_dir.mkdir(parents=True)
    (test_settings.resolved_chroma_dir / "index.bin").write_bytes(b"index-before")

    archive = maintenance.backup_all(tmp_path / "complete.tar.gz")
    manifest = maintenance.verify_backup(archive)
    assert {item["id"] for item in manifest["projects"]} == {
        "default-project",
        second_project["id"],
    }

    with closing(sqlite3.connect(default_database)) as connection:
        connection.execute("UPDATE sample SET value = 'changed'")
        connection.commit()
    (default_upload / "plan.txt").write_text("changed", encoding="utf-8")
    (test_settings.resolved_chroma_dir / "index.bin").write_bytes(b"changed")

    result = maintenance.restore_all(archive, confirmed=True)
    assert result["status"] == "restored"
    assert Path(result["rollback_backup"]).is_file()
    assert _read_value(default_database) == "default-before"
    assert _read_value(second_database) == "second-before"
    assert (default_upload / "plan.txt").read_text(encoding="utf-8") == "original"
    assert (test_settings.resolved_chroma_dir / "index.bin").read_bytes() == b"index-before"


def test_workspace_catalog_paths_follow_current_runtime(test_settings):
    manager = ProjectWorkspaceManager(test_settings)
    project = manager.create("武汉市住宅楼施工项目")
    payload = manager._catalog
    payload["projects"][0]["database_path"] = "C:\\old-machine\\default.sqlite"
    payload["projects"][1]["upload_dir"] = "C:\\old-machine\\uploads"
    manager._write(payload)

    migrated = ProjectWorkspaceManager(test_settings)
    assert Path(migrated.get("default-project")["database_path"]) == (
        test_settings.resolved_database_path
    )
    assert Path(migrated.get(project["id"])["upload_dir"]) == (
        test_settings.resolved_database_path.parent / "projects" / project["id"] / "uploads"
    )
