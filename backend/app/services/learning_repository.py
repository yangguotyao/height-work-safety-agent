from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..db import Database, json_load
from ..repositories import utc_now


class LearningRepository:
    def __init__(self, database: Database):
        self.db = database

    def add_event(
        self, worker_ref: str, event_type: str, resource_id: str = "", metadata: dict | None = None
    ) -> None:
        self.db.execute(
            """INSERT INTO worker_learning_events
               (id, worker_ref, event_type, resource_id, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                uuid4().hex,
                worker_ref,
                event_type,
                resource_id,
                json.dumps(metadata or {}, ensure_ascii=False),
                utc_now(),
            ),
        )

    def save_qa(
        self,
        *,
        worker_ref: str,
        task_id: str | None,
        question: str,
        answer: str,
        answer_status: str,
        evidence: list[dict[str, Any]],
        model_provider: str,
    ) -> dict[str, Any]:
        record_id = uuid4().hex
        created_at = utc_now()
        self.db.execute(
            """INSERT INTO safety_qa_records
               (id, worker_ref, task_id, question, answer, answer_status, evidence_json,
                model_provider, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                worker_ref,
                task_id,
                question,
                answer,
                answer_status,
                json.dumps(evidence, ensure_ascii=False),
                model_provider,
                created_at,
            ),
        )
        self.add_event(worker_ref, "safety_qa", record_id, {"task_id": task_id})
        return {
            "id": record_id,
            "worker_ref": worker_ref,
            "task_id": task_id,
            "question": question,
            "answer": answer,
            "answer_status": answer_status,
            "evidences": evidence,
            "model_provider": model_provider,
            "created_at": created_at,
        }

    def create_quiz(
        self, worker_ref: str, task_id: str | None, scene: str, question_ids: list[str]
    ) -> dict[str, Any]:
        attempt_id = uuid4().hex
        started_at = utc_now()
        self.db.execute(
            """INSERT INTO quiz_attempts
               (id, worker_ref, task_id, scene, question_ids_json, status, started_at)
               VALUES (?, ?, ?, ?, ?, 'in_progress', ?)""",
            (
                attempt_id,
                worker_ref,
                task_id,
                scene,
                json.dumps(question_ids, ensure_ascii=False),
                started_at,
            ),
        )
        self.add_event(worker_ref, "quiz_started", attempt_id, {"scene": scene})
        return self.get_quiz(attempt_id)

    def get_quiz(self, attempt_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,))
        if row is None:
            raise KeyError("测验记录不存在")
        row["question_ids"] = json_load(row.pop("question_ids_json"), [])
        return row

    def submit_quiz(
        self, attempt_id: str, worker_ref: str, answers: list[tuple[str, str, bool]]
    ) -> None:
        now = utc_now()
        rows = [
            (uuid4().hex, attempt_id, worker_ref, question_id, answer, int(correct), now)
            for question_id, answer, correct in answers
        ]
        with self.db.connect() as connection:
            connection.executemany(
                """INSERT INTO quiz_answers
                   (id, attempt_id, worker_ref, question_id, submitted_answer,
                    is_correct, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            connection.execute(
                """UPDATE quiz_attempts SET status = 'submitted', submitted_at = ?
                   WHERE id = ?""",
                (now, attempt_id),
            )
        self.add_event(
            worker_ref,
            "quiz_submitted",
            attempt_id,
            {"correct_count": sum(1 for *_, correct in answers if correct)},
        )

    def answered_question_ids(self, worker_ref: str) -> set[str]:
        rows = self.db.fetch_all(
            "SELECT DISTINCT question_id FROM quiz_answers WHERE worker_ref = ?", (worker_ref,)
        )
        return {row["question_id"] for row in rows}

    def active_wrong_question_ids(self, worker_ref: str) -> list[str]:
        rows = self.db.fetch_all(
            """SELECT question_id, is_correct, created_at, id FROM quiz_answers
               WHERE worker_ref = ? ORDER BY question_id, created_at DESC, id DESC""",
            (worker_ref,),
        )
        by_question: dict[str, list[bool]] = {}
        for row in rows:
            by_question.setdefault(row["question_id"], []).append(bool(row["is_correct"]))
        return [
            question_id
            for question_id, history in by_question.items()
            if False in history and history[:2] != [True, True]
        ]

    def learning_record(self, worker_ref: str) -> dict[str, Any]:
        qa_rows = self.db.fetch_all(
            """SELECT * FROM safety_qa_records WHERE worker_ref = ?
               ORDER BY created_at DESC LIMIT 20""",
            (worker_ref,),
        )
        for row in qa_rows:
            row["evidences"] = json_load(row.pop("evidence_json"), [])
        attempts = self.db.fetch_all(
            """SELECT id, task_id, scene, status, started_at, submitted_at
               FROM quiz_attempts WHERE worker_ref = ? ORDER BY started_at DESC LIMIT 20""",
            (worker_ref,),
        )
        for attempt in attempts:
            counts = self.db.fetch_one(
                """SELECT COUNT(*) total, COALESCE(SUM(is_correct), 0) correct_count
                   FROM quiz_answers WHERE attempt_id = ?""",
                (attempt["id"],),
            )
            attempt.update(counts or {"total": 0, "correct_count": 0})
        events = self.db.fetch_all(
            """SELECT event_type, resource_id, metadata_json, created_at
               FROM worker_learning_events WHERE worker_ref = ?
               ORDER BY created_at DESC LIMIT 50""",
            (worker_ref,),
        )
        for event in events:
            event["metadata"] = json_load(event.pop("metadata_json"), {})
        return {"qa_records": qa_rows, "quiz_attempts": attempts, "events": events}
