from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from ..db import Database, json_load


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelCallMonitor:
    """Optional, removable persistence for external model call observability."""

    def __init__(self, database: Database):
        self.database = database
        with database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_call_logs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_name TEXT,
                    request_json TEXT NOT NULL,
                    response_json TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_model_calls_run_created
                ON model_call_logs(run_id, created_at);
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(model_call_logs)").fetchall()
            }
            lifecycle_columns = {
                "http_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "parse_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "validation_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "persistence_status": "TEXT NOT NULL DEFAULT 'unknown'",
            }
            for name, definition in lifecycle_columns.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE model_call_logs ADD COLUMN {name} {definition}"
                    )

    def start(
        self,
        *,
        run_id: str,
        purpose: str,
        attempt: int,
        provider: str,
        model_name: str | None,
        request: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        call_id = uuid4().hex
        self.database.execute(
            """INSERT INTO model_call_logs
               (id, run_id, purpose, attempt, status, provider, model_name,
                request_json, metadata_json, created_at, http_status, parse_status,
                validation_status, persistence_status)
               VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, 'running', 'pending',
                       'pending', 'pending')""",
            (
                call_id,
                run_id,
                purpose,
                attempt,
                provider,
                model_name,
                json.dumps(request, ensure_ascii=False, default=str),
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                _utc_now(),
            ),
        )
        return call_id, perf_counter()

    def http_completed(
        self, call_id: str, started: float, response: dict[str, Any]
    ) -> None:
        """Record a provider response without claiming it is usable audit output."""
        usage = response.get("usage") or {}
        self.database.execute(
            """UPDATE model_call_logs
               SET status='http_completed', http_status='succeeded', response_json=?,
                   prompt_tokens=?, completion_tokens=?, total_tokens=?, latency_ms=?,
                   completed_at=? WHERE id=?""",
            (
                json.dumps(response, ensure_ascii=False, default=str),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                round((perf_counter() - started) * 1000, 3),
                _utc_now(),
                call_id,
            ),
        )

    def mark_parse(self, call_id: str, *, partial: bool = False) -> None:
        parse_status = "partial" if partial else "succeeded"
        status = "parse_partial" if partial else "parsed"
        self.database.execute(
            "UPDATE model_call_logs SET status=?, parse_status=? WHERE id=?",
            (status, parse_status, call_id),
        )

    def mark_validation(self, call_id: str, *, partial: bool = False) -> None:
        validation_status = "partial" if partial else "succeeded"
        status = "validation_partial" if partial else "validated"
        self.database.execute(
            "UPDATE model_call_logs SET status=?, validation_status=? WHERE id=?",
            (status, validation_status, call_id),
        )

    def mark_persisted(self, call_id: str) -> None:
        self.database.execute(
            """UPDATE model_call_logs
               SET status='persisted', persistence_status='succeeded' WHERE id=?""",
            (call_id,),
        )

    def processing_failed(self, call_id: str, stage: str, error: Exception | str) -> None:
        if stage not in {"parse", "validation", "persistence"}:
            raise ValueError("不支持的模型调用处理阶段")
        column = f"{stage}_status"
        self.database.execute(
            f"UPDATE model_call_logs SET status=?, {column}='failed', error=? WHERE id=?",
            (f"{stage}_failed", str(error)[:4000], call_id),
        )

    def succeed(self, call_id: str, started: float, response: dict[str, Any]) -> None:
        """Backward-compatible helper for callers that have no later processing stages."""
        usage = response.get("usage") or {}
        self.database.execute(
            """UPDATE model_call_logs
               SET status='completed', http_status='succeeded', parse_status='not_required',
                   validation_status='not_required', persistence_status='not_required',
                   response_json=?, prompt_tokens=?,
                   completion_tokens=?, total_tokens=?, latency_ms=?, completed_at=?
               WHERE id=?""",
            (
                json.dumps(response, ensure_ascii=False, default=str),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                round((perf_counter() - started) * 1000, 3),
                _utc_now(),
                call_id,
            ),
        )

    def fail(self, call_id: str, started: float, error: Exception) -> None:
        self.database.execute(
            """UPDATE model_call_logs
               SET status='failed', http_status='failed', latency_ms=?, error=?, completed_at=?
               WHERE id=?""",
            (
                round((perf_counter() - started) * 1000, 3),
                str(error)[:4000],
                _utc_now(),
                call_id,
            ),
        )

    def list_for_run(self, run_id: str) -> dict[str, Any]:
        exists = self.database.fetch_one("SELECT id FROM audit_runs WHERE id = ?", (run_id,))
        if exists is None:
            raise KeyError("审计任务不存在")
        rows = self.database.fetch_all(
            """SELECT id, purpose, attempt, status, provider, model_name,
                      metadata_json, prompt_tokens, completion_tokens, total_tokens,
                      http_status, parse_status, validation_status, persistence_status,
                      latency_ms, error, created_at, completed_at
               FROM model_call_logs WHERE run_id=? ORDER BY created_at, id""",
            (run_id,),
        )
        for row in rows:
            row["metadata"] = json_load(row.pop("metadata_json"), {})
        summary = self.database.fetch_one(
            """SELECT COUNT(*) call_count,
                      SUM(CASE WHEN status IN ('completed','validated','validation_partial',
                                               'persisted') THEN 1 ELSE 0 END)
                          completed_count,
                      SUM(CASE WHEN status LIKE '%failed' OR status='failed' THEN 1 ELSE 0 END)
                          failed_count,
                      SUM(CASE WHEN parse_status='failed' THEN 1 ELSE 0 END) parse_failed_count,
                      SUM(CASE WHEN validation_status='failed' THEN 1 ELSE 0 END)
                          validation_failed_count,
                      SUM(CASE WHEN validation_status='partial' THEN 1 ELSE 0 END)
                          validation_partial_count,
                      COALESCE(SUM(prompt_tokens),0) prompt_tokens,
                      COALESCE(SUM(completion_tokens),0) completion_tokens,
                      COALESCE(SUM(total_tokens),0) total_tokens,
                      COALESCE(SUM(latency_ms),0) latency_ms
               FROM model_call_logs WHERE run_id=?""",
            (run_id,),
        ) or {}
        return {"run_id": run_id, "summary": summary, "items": rows}

    def get_call(self, call_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM model_call_logs WHERE id = ?", (call_id,))
        if row is None:
            raise KeyError("模型调用记录不存在")
        row["request"] = json_load(row.pop("request_json"), {})
        row["response"] = json_load(row.pop("response_json"), None)
        row["metadata"] = json_load(row.pop("metadata_json"), {})
        return row
