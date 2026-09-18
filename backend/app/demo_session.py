from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request

from .db import Database

BASELINE_SESSION_ID = "baseline"
DEFAULT_SESSION_ID = "default-session"
SESSION_HEADER = "X-Demo-Session"
_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,80}$")


def browser_session_id(request: Request) -> str:
    value = request.headers.get(SESSION_HEADER, "").strip()
    return value if _SESSION_PATTERN.fullmatch(value) else DEFAULT_SESSION_ID


def cleanup_browser_session(database: Database, session_id: str) -> dict[str, int]:
    if session_id in {"", BASELINE_SESSION_ID, DEFAULT_SESSION_ID, "legacy"}:
        return {"audits": 0, "tasks": 0, "inspections": 0, "logs": 0}

    files: set[Path] = set()
    counts = {"audits": 0, "tasks": 0, "inspections": 0, "logs": 0}
    with database.connect() as connection:
        audit_rows = connection.execute(
            "SELECT id, document_id FROM audit_runs WHERE browser_session_id=?",
            (session_id,),
        ).fetchall()
        audit_ids = [row["id"] for row in audit_rows]
        document_ids = [row["document_id"] for row in audit_rows]
        counts["audits"] = len(audit_ids)

        task_rows = connection.execute(
            "SELECT id FROM work_tasks WHERE browser_session_id=?", (session_id,)
        ).fetchall()
        counts["tasks"] = len(task_rows)

        inspection_rows = connection.execute(
            "SELECT id, storage_path FROM hazard_inspections WHERE browser_session_id=?",
            (session_id,),
        ).fetchall()
        inspection_ids = [row["id"] for row in inspection_rows]
        files.update(Path(row["storage_path"]) for row in inspection_rows)
        counts["inspections"] = len(inspection_ids)

        if inspection_ids:
            placeholders = ",".join("?" for _ in inspection_ids)
            submission_rows = connection.execute(
                f"""SELECT sub.storage_path
                    FROM rectification_submissions sub
                    JOIN rectification_orders o ON o.id=sub.order_id
                    JOIN safety_items s ON s.id=o.safety_item_id
                    JOIN hazard_candidates c ON c.id=s.source_candidate_id
                    WHERE c.inspection_id IN ({placeholders})""",
                inspection_ids,
            ).fetchall()
            files.update(Path(row["storage_path"]) for row in submission_rows)
            safety_rows = connection.execute(
                f"""SELECT s.id FROM safety_items s
                    JOIN hazard_candidates c ON c.id=s.source_candidate_id
                    WHERE c.inspection_id IN ({placeholders})""",
                inspection_ids,
            ).fetchall()
            safety_ids = [row["id"] for row in safety_rows]
            if safety_ids:
                safety_placeholders = ",".join("?" for _ in safety_ids)
                connection.execute(
                    f"DELETE FROM platform_data_events WHERE entity_id IN ({safety_placeholders})",
                    safety_ids,
                )
                connection.execute(
                    f"DELETE FROM safety_items WHERE id IN ({safety_placeholders})",
                    safety_ids,
                )
            connection.execute(
                f"DELETE FROM hazard_inspections WHERE id IN ({placeholders})", inspection_ids
            )

        log_rows = connection.execute(
            "SELECT id, docx_path FROM daily_safety_logs WHERE browser_session_id=?",
            (session_id,),
        ).fetchall()
        files.update(Path(row["docx_path"]) for row in log_rows if row["docx_path"])
        counts["logs"] = len(log_rows)
        connection.execute(
            "DELETE FROM daily_safety_logs WHERE browser_session_id=?", (session_id,)
        )

        connection.execute(
            "DELETE FROM worker_sessions WHERE browser_session_id=?", (session_id,)
        )
        connection.execute(
            "DELETE FROM dynamic_risk_runs WHERE browser_session_id=?", (session_id,)
        )
        connection.execute(
            """DELETE FROM dynamic_risk_runs
               WHERE NOT EXISTS (
                   SELECT 1 FROM dynamic_risk_items i WHERE i.run_id=dynamic_risk_runs.id
               )"""
        )

        if audit_ids:
            placeholders = ",".join("?" for _ in audit_ids)
            connection.execute(
                f"DELETE FROM audit_runs WHERE id IN ({placeholders})", audit_ids
            )
        for document_id in document_ids:
            if connection.execute(
                "SELECT 1 FROM audit_runs WHERE document_id=? LIMIT 1", (document_id,)
            ).fetchone():
                continue
            document = connection.execute(
                "SELECT storage_path FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if document:
                files.add(Path(document["storage_path"]))
            connection.execute("DELETE FROM document_segments WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))

    for path in files:
        path.unlink(missing_ok=True)
    return counts
