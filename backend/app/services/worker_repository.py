from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..db import Database, json_load
from ..repositories import utc_now
from .task_schedule import resolve_task_schedule


class WorkerAssistantRepository:
    def __init__(self, database: Database):
        self.db = database

    def create_session(
        self,
        *,
        worker_ref: str = "",
        team_ref: str = "",
        audit_run_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO worker_sessions
               (id, worker_ref, team_ref, audit_run_id, status, draft_json, created_at,
                updated_at)
               VALUES (?, ?, ?, ?, 'collecting', '{}', ?, ?)""",
            (session_id, worker_ref.strip(), team_ref.strip(), audit_run_id, now, now),
        )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM worker_sessions WHERE id = ?", (session_id,))
        if row is None:
            raise KeyError("工人助手会话不存在")
        row["draft"] = json_load(row.pop("draft_json"), {})
        return row

    def update_session(
        self,
        session_id: str,
        *,
        draft: dict[str, Any],
        status: str,
        worker_ref: str | None = None,
        team_ref: str | None = None,
        audit_run_id: str | None = None,
    ) -> None:
        current = self.get_session(session_id)
        self.db.execute(
            """UPDATE worker_sessions
               SET draft_json = ?, status = ?, worker_ref = ?, team_ref = ?,
                   audit_run_id = ?, updated_at = ?
               WHERE id = ?""",
            (
                json.dumps(draft, ensure_ascii=False),
                status,
                (worker_ref if worker_ref is not None else current["worker_ref"]).strip(),
                (team_ref if team_ref is not None else current["team_ref"]).strip(),
                audit_run_id if audit_run_id is not None else current["audit_run_id"],
                utc_now(),
                session_id,
            ),
        )

    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.db.execute(
            """INSERT INTO worker_messages (id, session_id, role, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid4().hex, session_id, role, content.strip(), utc_now()),
        )

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT role, content, created_at FROM worker_messages
               WHERE session_id = ? ORDER BY created_at, id""",
            (session_id,),
        )

    def create_task(self, session: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
        existing = self.db.fetch_one(
            "SELECT id FROM work_tasks WHERE session_id = ?", (session["id"],)
        )
        if existing:
            return self.get_task(existing["id"])
        task_id = uuid4().hex
        created_at = utc_now()
        scheduled_date, time_window = resolve_task_schedule(
            draft["work_time"], created_at=created_at
        )
        self.db.execute(
            """INSERT INTO work_tasks
               (id, session_id, worker_ref, team_ref, audit_run_id, work_content,
                work_location, work_floor, work_time, normalized_task, scenes_json,
                task_action, equipment_type, scheduled_date, time_window, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                session["id"],
                session["worker_ref"],
                session["team_ref"],
                session["audit_run_id"],
                draft["work_content"],
                draft["location"],
                draft["floor"],
                draft["work_time"],
                draft["normalized_task"],
                json.dumps(draft.get("scenes", []), ensure_ascii=False),
                draft.get("task_action", ""),
                draft.get("equipment_type", ""),
                scheduled_date,
                time_window,
                created_at,
            ),
        )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM work_tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError("每日作业任务不存在")
        row["scenes"] = json_load(row.pop("scenes_json"), [])
        return row

    def get_task_for_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one("SELECT id FROM work_tasks WHERE session_id = ?", (session_id,))
        return self.get_task(row["id"]) if row else None

    def save_risk_card(self, task_id: str, card: dict[str, Any]) -> dict[str, Any]:
        existing = self.db.fetch_one(
            "SELECT card_json FROM task_risk_cards WHERE task_id = ?", (task_id,)
        )
        if existing:
            return json_load(existing["card_json"], {})
        card_id = uuid4().hex
        created_at = utc_now()
        stored = {**card, "id": card_id, "task_id": task_id, "created_at": created_at}
        self.db.execute(
            """INSERT INTO task_risk_cards (id, task_id, card_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (card_id, task_id, json.dumps(stored, ensure_ascii=False), created_at),
        )
        return stored

    def get_risk_card(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            "SELECT card_json FROM task_risk_cards WHERE task_id = ?", (task_id,)
        )
        return json_load(row["card_json"], {}) if row else None

    def latest_completed_audit_run_id(self) -> str | None:
        row = self.db.fetch_one(
            """SELECT id FROM audit_runs WHERE status = 'completed'
               ORDER BY COALESCE(completed_at, created_at) DESC LIMIT 1"""
        )
        return str(row["id"]) if row else None
