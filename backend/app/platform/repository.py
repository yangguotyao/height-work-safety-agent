from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..db import Database, json_load
from ..repositories import utc_now
from .security import hash_password, new_session_token, token_digest, verify_password

DEFAULT_PROJECT_ID = "default-project"


class PlatformRepository:
    def __init__(self, database: Database, *, project_name: str, session_hours: int = 12):
        self.db = database
        self.project_name = project_name
        self.session_hours = session_hours

    def bootstrap(
        self,
        *,
        username: str,
        password: str | None,
        display_name: str,
    ) -> None:
        now = utc_now()
        self.db.execute(
            """INSERT OR IGNORE INTO platform_projects
               (id, name, code, active, created_at, updated_at)
               VALUES (?, ?, 'default', 1, ?, ?)""",
            (DEFAULT_PROJECT_ID, self.project_name, now, now),
        )
        self.db.execute(
            "UPDATE platform_projects SET name = ?, updated_at = ? WHERE id = ?",
            (self.project_name, now, DEFAULT_PROJECT_ID),
        )
        normalized = username.strip().lower()
        user = self.db.fetch_one(
            "SELECT id FROM platform_users WHERE username=?", (normalized,)
        )
        if user is None:
            self.create_user(
                username=normalized,
                password=password or uuid4().hex,
                display_name=display_name,
                system_role="admin",
                project_id=DEFAULT_PROJECT_ID,
                project_role="admin",
            )
            return
        if password:
            self.db.execute(
                "UPDATE platform_users SET password_hash=?, display_name=?, active=1, updated_at=? WHERE id=?",
                (hash_password(password), display_name, now, user["id"]),
            )
        self.db.execute(
            """INSERT OR IGNORE INTO platform_project_members
               (project_id, user_id, project_role, worker_ref, team_ref, created_at)
               VALUES (?, ?, 'admin', '', '', ?)""",
            (DEFAULT_PROJECT_ID, user["id"], now),
        )

    def workspace_identity(self, workspace_id: str, workspace_name: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT u.*, m.project_role, m.worker_ref, m.team_ref
               FROM platform_users u
               JOIN platform_project_members m ON m.user_id = u.id
               WHERE u.active = 1 ORDER BY u.created_at LIMIT 1"""
        )
        if row is None:
            raise KeyError("项目工作空间尚未初始化")
        return {
            "id": row["id"],
            "username": "workspace",
            "display_name": "项目工作空间",
            "system_role": "admin",
            "project_id": DEFAULT_PROJECT_ID,
            "workspace_id": workspace_id,
            "project_name": workspace_name,
            "project_role": "admin",
            "worker_ref": "项目作业人员",
            "team_ref": "",
        }

    @staticmethod
    def _public_identity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "system_role": row["system_role"],
            "project_id": row.get("project_id"),
            "project_name": row.get("project_name", ""),
            "project_role": row.get("project_role") or "admin",
            "worker_ref": row.get("worker_ref", ""),
            "team_ref": row.get("team_ref", ""),
        }

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        system_role: str = "user",
        project_role: str = "worker",
        project_id: str | None = None,
        worker_ref: str = "",
        team_ref: str = "",
    ) -> dict[str, Any]:
        username = username.strip().lower()
        if not username or len(username) > 64:
            raise ValueError("用户名不能为空且最多64个字符")
        if project_role not in {"admin", "safety_officer", "team_leader", "worker"}:
            raise ValueError("项目角色无效")
        user_id = uuid4().hex
        now = utc_now()
        try:
            with self.db.connect() as connection:
                connection.execute(
                    """INSERT INTO platform_users
                       (id, username, display_name, password_hash, system_role,
                        active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        user_id,
                        username,
                        display_name.strip() or username,
                        hash_password(password),
                        system_role,
                        now,
                        now,
                    ),
                )
                if project_id:
                    connection.execute(
                        """INSERT INTO platform_project_members
                           (project_id, user_id, project_role, worker_ref, team_ref, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            project_id,
                            user_id,
                            project_role,
                            worker_ref.strip(),
                            team_ref.strip(),
                            now,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
        return self.get_user(user_id)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        row = self.db.fetch_one(
            "SELECT password_hash FROM platform_users WHERE id = ? AND active = 1", (user_id,)
        )
        if row is None or not verify_password(current_password, str(row["password_hash"])):
            raise ValueError("当前密码错误")
        self.db.execute(
            "UPDATE platform_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), utc_now(), user_id),
        )
        self.db.execute("DELETE FROM platform_auth_sessions WHERE user_id = ?", (user_id,))

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT u.*, NULL project_id, '' project_name, NULL project_role,
                      '' worker_ref, '' team_ref
               FROM platform_users u WHERE u.id = ? AND u.active = 1""",
            (user_id,),
        )
        if row is None:
            raise KeyError("用户不存在或已停用")
        return self._public_identity(row)

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT u.*, NULL project_id, '' project_name, NULL project_role,
                      '' worker_ref, '' team_ref,
                      (SELECT COUNT(*) FROM platform_project_members m
                       WHERE m.user_id=u.id) project_count
               FROM platform_users u ORDER BY u.created_at"""
        )
        return [
            self._public_identity(row)
            | {"active": bool(row["active"]), "project_count": row["project_count"]}
            for row in rows
        ]

    def register_project(
        self, *, project_id: str, name: str, code: str, owner_user_id: str
    ) -> None:
        now = utc_now()
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO platform_projects
                   (id, name, code, active, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, code=excluded.code, active=1,
                     updated_at=excluded.updated_at""",
                (project_id, name, code, now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO platform_project_members
                   (project_id, user_id, project_role, worker_ref, team_ref, created_at)
                   VALUES (?, ?, 'admin', '', '', ?)""",
                (project_id, owner_user_id, now),
            )

    def update_project(self, project_id: str, name: str) -> None:
        self.db.execute(
            "UPDATE platform_projects SET name=?, updated_at=? WHERE id=?",
            (name, utc_now(), project_id),
        )

    def delete_project(self, project_id: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE platform_auth_sessions SET active_project_id=NULL WHERE active_project_id=?",
                (project_id,),
            )
            connection.execute("DELETE FROM platform_projects WHERE id=?", (project_id,))

    def projects_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT p.id, p.name, p.code, m.project_role
               FROM platform_project_members m
               JOIN platform_projects p ON p.id=m.project_id
               WHERE m.user_id=? AND p.active=1 ORDER BY m.created_at""",
            (user_id,),
        )

    def user_has_project(self, user_id: str, project_id: str) -> bool:
        return self.db.fetch_one(
            "SELECT 1 ok FROM platform_project_members WHERE user_id=? AND project_id=?",
            (user_id, project_id),
        ) is not None

    def login(
        self, username: str, password: str, *, user_agent: str = "", remote_addr: str = ""
    ) -> tuple[str, dict[str, Any]]:
        row = self.db.fetch_one(
            "SELECT * FROM platform_users WHERE username = ? AND active = 1",
            (username.strip().lower(),),
        )
        if row is None or not verify_password(password, str(row["password_hash"])):
            raise ValueError("用户名或密码错误")
        token = new_session_token()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.session_hours)
        self.db.execute(
            """INSERT INTO platform_auth_sessions
               (token_hash, user_id, active_project_id, expires_at, created_at, last_seen_at,
                user_agent, remote_addr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_digest(token),
                row["id"],
                (
                    (self.projects_for_user(str(row["id"])) or [{}])[0].get("id")
                ),
                expires.isoformat(),
                now.isoformat(),
                now.isoformat(),
                user_agent[:500],
                remote_addr[:100],
            ),
        )
        return token, self.identity_for_token(token) or self.get_user(str(row["id"]))

    def identity_for_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        row = self.db.fetch_one(
            """SELECT user_id, active_project_id, expires_at FROM platform_auth_sessions
               WHERE token_hash = ?""",
            (token_digest(token),),
        )
        if row is None:
            return None
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]))
        except ValueError:
            return None
        if expires <= datetime.now(timezone.utc):
            self.db.execute(
                "DELETE FROM platform_auth_sessions WHERE token_hash = ?", (token_digest(token),)
            )
            return None
        self.db.execute(
            "UPDATE platform_auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (utc_now(), token_digest(token)),
        )
        try:
            user = self.get_user(str(row["user_id"]))
            project_id = row.get("active_project_id")
            if not project_id:
                return user
            membership = self.db.fetch_one(
                """SELECT m.project_role, m.worker_ref, m.team_ref, p.name project_name
                   FROM platform_project_members m
                   JOIN platform_projects p ON p.id=m.project_id
                   WHERE m.user_id=? AND m.project_id=? AND p.active=1""",
                (row["user_id"], project_id),
            )
            if membership is None:
                return user
            return user | {
                "project_id": project_id,
                "project_name": membership["project_name"],
                "project_role": membership["project_role"],
                "worker_ref": membership["worker_ref"],
                "team_ref": membership["team_ref"],
            }
        except KeyError:
            return None

    def set_session_project(self, token: str, user_id: str, project_id: str) -> None:
        if not self.user_has_project(user_id, project_id):
            raise PermissionError("当前账号无权访问该项目")
        self.db.execute(
            "UPDATE platform_auth_sessions SET active_project_id=?, last_seen_at=? WHERE token_hash=? AND user_id=?",
            (project_id, utc_now(), token_digest(token), user_id),
        )

    def deactivate_user(self, user_id: str, active: bool) -> None:
        self.db.execute(
            "UPDATE platform_users SET active=?, updated_at=? WHERE id=? AND system_role!='admin'",
            (int(active), utc_now(), user_id),
        )
        if not active:
            self.db.execute("DELETE FROM platform_auth_sessions WHERE user_id=?", (user_id,))

    def reset_password(self, user_id: str, password: str) -> None:
        self.db.execute(
            "UPDATE platform_users SET password_hash=?, updated_at=? WHERE id=?",
            (hash_password(password), utc_now(), user_id),
        )
        self.db.execute("DELETE FROM platform_auth_sessions WHERE user_id=?", (user_id,))

    def logout(self, token: str) -> None:
        if token:
            self.db.execute(
                "DELETE FROM platform_auth_sessions WHERE token_hash = ?", (token_digest(token),)
            )

    def create_conversation(self, identity: dict[str, Any], title: str) -> dict[str, Any]:
        conversation_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO agent_conversations
               (id, project_id, user_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                identity["project_id"],
                identity["id"],
                title.strip()[:80],
                now,
                now,
            ),
        )
        return self.get_conversation(conversation_id, identity)

    def get_conversation(
        self, conversation_id: str, identity: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT * FROM agent_conversations
               WHERE id = ? AND project_id = ? AND user_id = ?""",
            (conversation_id, identity["project_id"], identity["id"]),
        )
        if row is None:
            raise KeyError("会话不存在")
        return row

    def list_conversations(
        self, identity: dict[str, Any], limit: int = 30
    ) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT * FROM agent_conversations
               WHERE project_id = ? AND user_id = ?
               ORDER BY updated_at DESC LIMIT ?""",
            (identity["project_id"], identity["id"], limit),
        )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        agent_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO agent_messages
               (id, conversation_id, role, agent_name, content, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                conversation_id,
                role,
                agent_name,
                content,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                now,
            ),
        )
        self.db.execute(
            "UPDATE agent_conversations SET active_agent = ?, updated_at = ? WHERE id = ?",
            (agent_name or "coordinator", now, conversation_id),
        )
        return {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "agent_name": agent_name,
            "content": content,
            "metadata": metadata or {},
            "created_at": now,
        }

    def messages(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM (
                   SELECT * FROM agent_messages WHERE conversation_id = ?
                   ORDER BY created_at DESC LIMIT ?
               ) ORDER BY created_at""",
            (conversation_id, limit),
        )
        for row in rows:
            row["metadata"] = json_load(row.pop("metadata_json"), {})
        return rows

    def update_summary(self, conversation_id: str, summary: str) -> None:
        self.db.execute(
            "UPDATE agent_conversations SET summary = ?, updated_at = ? WHERE id = ?",
            (summary[:2000], utc_now(), conversation_id),
        )

    def save_memory(
        self,
        identity: dict[str, Any],
        *,
        memory_type: str,
        subject_key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        source_type: str = "conversation",
        source_id: str = "",
    ) -> dict[str, Any]:
        existing = self.db.fetch_one(
            """SELECT id FROM agent_memories
               WHERE project_id = ? AND user_id = ? AND memory_type = ?
                 AND subject_key = ? AND active = 1
               ORDER BY updated_at DESC LIMIT 1""",
            (identity["project_id"], identity["id"], memory_type, subject_key),
        )
        memory_id = uuid4().hex
        now = utc_now()
        with self.db.connect() as connection:
            if existing:
                connection.execute(
                    "UPDATE agent_memories SET active = 0, updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
            connection.execute(
                """INSERT INTO agent_memories
                   (id, project_id, user_id, memory_type, subject_key, content,
                    metadata_json, importance, source_type, source_id,
                    supersedes_id, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    memory_id,
                    identity["project_id"],
                    identity["id"],
                    memory_type,
                    subject_key,
                    content[:4000],
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    max(0.0, min(float(importance), 1.0)),
                    source_type,
                    source_id,
                    existing["id"] if existing else None,
                    now,
                    now,
                ),
            )
        return {"id": memory_id, "memory_type": memory_type, "content": content}

    def search_memories(
        self, identity: dict[str, Any], query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM agent_memories
               WHERE project_id = ? AND active = 1
                 AND (user_id = ? OR user_id IS NULL)
               ORDER BY importance DESC, updated_at DESC LIMIT 100""",
            (identity["project_id"], identity["id"]),
        )
        terms = {item for item in query.lower().replace("，", " ").split() if len(item) > 1}
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            haystack = f"{row['subject_key']} {row['content']}".lower()
            score = sum(term in haystack for term in terms)
            if terms and score == 0 and row["memory_type"] != "profile":
                continue
            row["metadata"] = json_load(row.pop("metadata_json"), {})
            ranked.append((score, row))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -float(item[1]["importance"]),
                item[1]["updated_at"],
            )
        )
        return [row for _, row in ranked[:limit]]

    def log_tool_run(
        self,
        *,
        conversation_id: str,
        message_id: str | None,
        agent_name: str,
        tool_name: str,
        status: str,
        input_data: dict[str, Any],
        output_data: Any,
        error: str,
        duration_ms: int,
    ) -> None:
        self.db.execute(
            """INSERT INTO agent_tool_runs
               (id, conversation_id, message_id, agent_name, tool_name, status,
                input_json, output_json, error, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid4().hex,
                conversation_id,
                message_id,
                agent_name,
                tool_name,
                status,
                json.dumps(input_data, ensure_ascii=False, default=str),
                json.dumps(output_data, ensure_ascii=False, default=str)[:100000],
                error[:2000],
                duration_ms,
                utc_now(),
            ),
        )
