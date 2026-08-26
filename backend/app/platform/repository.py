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
        if self.db.fetch_one("SELECT id FROM platform_users LIMIT 1"):
            return
        self.create_user(
            username=username,
            password=password or uuid4().hex,
            display_name=display_name,
            system_role="admin",
            project_role="admin",
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
            "project_id": row.get("project_id", DEFAULT_PROJECT_ID),
            "project_name": row.get("project_name", ""),
            "project_role": row.get("project_role", "worker"),
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
                connection.execute(
                    """INSERT INTO platform_project_members
                       (project_id, user_id, project_role, worker_ref, team_ref, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        DEFAULT_PROJECT_ID,
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
            """SELECT u.*, m.project_id, p.name project_name, m.project_role,
                      m.worker_ref, m.team_ref
               FROM platform_users u
               JOIN platform_project_members m ON m.user_id = u.id
               JOIN platform_projects p ON p.id = m.project_id
               WHERE u.id = ? AND u.active = 1 AND p.active = 1""",
            (user_id,),
        )
        if row is None:
            raise KeyError("用户不存在或已停用")
        return self._public_identity(row)

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT u.*, m.project_id, p.name project_name, m.project_role,
                      m.worker_ref, m.team_ref
               FROM platform_users u
               JOIN platform_project_members m ON m.user_id = u.id
               JOIN platform_projects p ON p.id = m.project_id
               ORDER BY u.created_at"""
        )
        return [self._public_identity(row) | {"active": bool(row["active"])} for row in rows]

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
               (token_hash, user_id, expires_at, created_at, last_seen_at,
                user_agent, remote_addr)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                token_digest(token),
                row["id"],
                expires.isoformat(),
                now.isoformat(),
                now.isoformat(),
                user_agent[:500],
                remote_addr[:100],
            ),
        )
        return token, self.get_user(str(row["id"]))

    def identity_for_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        row = self.db.fetch_one(
            """SELECT user_id, expires_at FROM platform_auth_sessions
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
            return self.get_user(str(row["user_id"]))
        except KeyError:
            return None

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
