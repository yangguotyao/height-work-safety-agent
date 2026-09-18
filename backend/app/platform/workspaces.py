from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from ..config import Settings

DEFAULT_WORKSPACE_ID = "default-project"
DEFAULT_PROJECT_NAME = "XX综合医院扩建项目"
LEGACY_DEFAULT_PROJECT_NAMES = {"比赛演示项目", "武汉市XX区写字楼施工项目"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectWorkspaceManager:
    """Small persistent catalog for single-active-project deployments."""

    def __init__(self, settings: Settings):
        self.base_settings = settings
        self.catalog_path = settings.resolved_database_path.parent / "project_workspaces.json"
        self.projects_dir = settings.resolved_database_path.parent / "projects"
        self._lock = RLock()
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._catalog = self._load()

    def _load(self) -> dict[str, Any]:
        if self.catalog_path.exists():
            try:
                payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if payload.get("projects"):
                    original = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    self._normalize_paths(payload)
                    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    if normalized != original:
                        self._write(payload)
                    return payload
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        now = _now()
        payload = {
            "active_project_id": DEFAULT_WORKSPACE_ID,
            "projects": [
                {
                    "id": DEFAULT_WORKSPACE_ID,
                    "name": DEFAULT_PROJECT_NAME,
                    "code": "WH-XXQ-OFFICE",
                    "database_path": str(self.base_settings.resolved_database_path),
                    "upload_dir": str(self.base_settings.resolved_upload_dir),
                    "standard_collection": self.base_settings.standard_collection,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        }
        self._write(payload)
        return payload

    def _normalize_paths(self, payload: dict[str, Any]) -> None:
        """Keep the catalog portable when runtime data moves to Linux or a container."""
        for project in payload["projects"]:
            if project["id"] == DEFAULT_WORKSPACE_ID:
                if project.get("name") in LEGACY_DEFAULT_PROJECT_NAMES:
                    project["name"] = DEFAULT_PROJECT_NAME
                project["database_path"] = str(self.base_settings.resolved_database_path)
                project["upload_dir"] = str(self.base_settings.resolved_upload_dir)
                project.setdefault("standard_collection", self.base_settings.standard_collection)
                continue
            workspace_dir = self.projects_dir / project["id"]
            project["database_path"] = str(workspace_dir / "workspace.sqlite")
            project["upload_dir"] = str(workspace_dir / "uploads")
            project.setdefault(
                "standard_collection",
                f"{self.base_settings.standard_collection}_{project['id'][:8]}",
            )

    def _write(self, payload: dict[str, Any]) -> None:
        temporary = self.catalog_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.catalog_path)

    @staticmethod
    def public(project: dict[str, Any]) -> dict[str, Any]:
        return {
            key: project.get(key, "")
            for key in (
                "id",
                "name",
                "code",
                "city",
                "address",
                "created_at",
                "updated_at",
            )
        }

    def list(self) -> dict[str, Any]:
        with self._lock:
            active = self._catalog["active_project_id"]
            projects = [
                {**self.public(project), "active": project["id"] == active}
                for project in self._catalog["projects"]
            ]
            return {
                "active_project_id": active,
                "active_project": next(item for item in projects if item["active"]),
                "projects": projects,
            }

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            project = next(
                (item for item in self._catalog["projects"] if item["id"] == project_id),
                None,
            )
            if project is None:
                raise KeyError("项目不存在")
            return dict(project)

    def active(self) -> dict[str, Any]:
        return self.get(str(self._catalog["active_project_id"]))

    def create(self, name: str, *, city: str = "", address: str = "") -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", name.strip())
        if len(normalized) < 4 or len(normalized) > 80:
            raise ValueError("项目名称需要4至80个字符")
        with self._lock:
            if any(item["name"] == normalized for item in self._catalog["projects"]):
                raise ValueError("项目名称已存在")
            project_id = uuid4().hex
            workspace_dir = self.projects_dir / project_id
            now = _now()
            project = {
                "id": project_id,
                "name": normalized,
                "code": f"PROJECT-{project_id[:8].upper()}",
                "city": re.sub(r"\s+", " ", city.strip())[:60],
                "address": re.sub(r"\s+", " ", address.strip())[:160],
                "database_path": str(workspace_dir / "workspace.sqlite"),
                "upload_dir": str(workspace_dir / "uploads"),
                "standard_collection": f"{self.base_settings.standard_collection}_{project_id[:8]}",
                "created_at": now,
                "updated_at": now,
            }
            self._catalog["projects"].append(project)
            self._write(self._catalog)
            return dict(project)

    def activate(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            project = self.get(project_id)
            self._catalog["active_project_id"] = project_id
            project["updated_at"] = _now()
            for index, item in enumerate(self._catalog["projects"]):
                if item["id"] == project_id:
                    self._catalog["projects"][index] = project
                    break
            self._write(self._catalog)
            return dict(project)

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", name.strip())
        if len(normalized) < 4 or len(normalized) > 80:
            raise ValueError("项目名称需要4至80个字符")
        with self._lock:
            if any(
                item["id"] != project_id and item["name"] == normalized
                for item in self._catalog["projects"]
            ):
                raise ValueError("项目名称已存在")
            for index, item in enumerate(self._catalog["projects"]):
                if item["id"] != project_id:
                    continue
                project = {**item, "name": normalized, "updated_at": _now()}
                self._catalog["projects"][index] = project
                self._write(self._catalog)
                return dict(project)
            raise KeyError("项目不存在")

    def delete(self, project_id: str) -> dict[str, Any]:
        """Delete one inactive workspace and all of its project-local files."""
        with self._lock:
            if len(self._catalog["projects"]) <= 1:
                raise ValueError("至少需要保留一个项目，当前项目不能删除")
            if project_id == self._catalog["active_project_id"]:
                raise ValueError("当前项目不能直接删除，请先切换到其他项目")
            project = next(
                (item for item in self._catalog["projects"] if item["id"] == project_id),
                None,
            )
            if project is None:
                raise KeyError("项目不存在")

            if project_id == DEFAULT_WORKSPACE_ID:
                database_path = self.base_settings.resolved_database_path.resolve()
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{database_path}{suffix}").unlink(missing_ok=True)
                if self.base_settings.resolved_upload_dir.exists():
                    shutil.rmtree(self.base_settings.resolved_upload_dir)
                conversions = self.base_settings.resolved_upload_dir.parent / "conversions"
                if conversions.exists():
                    shutil.rmtree(conversions)
            else:
                workspace_dir = (self.projects_dir / project_id).resolve()
                if workspace_dir.parent != self.projects_dir.resolve():
                    raise ValueError("项目存储路径不安全，已拒绝删除")
                if workspace_dir.exists():
                    shutil.rmtree(workspace_dir)

            self._catalog["projects"] = [
                item for item in self._catalog["projects"] if item["id"] != project_id
            ]
            self._write(self._catalog)
            return dict(project)

    def settings_for(self, project: dict[str, Any]) -> Settings:
        return self.base_settings.model_copy(
            update={
                "project_name": project["name"],
                "database_path": Path(project["database_path"]),
                "upload_dir": Path(project["upload_dir"]),
                "standard_collection": project["standard_collection"],
            }
        )
