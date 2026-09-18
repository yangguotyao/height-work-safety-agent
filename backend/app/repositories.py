from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .db import Database, json_load
from .enums import AuditStatus, DocumentStatus, ReviewStatus
from .parsers.docx_parser import ParsedDocument
from .services.business_findings import build_business_findings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, database: Database):
        self.db = database

    def create_document(self, filename: str, sha256: str, storage_path: str) -> dict[str, Any]:
        document_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO documents
               (id, filename, sha256, storage_path, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (document_id, filename, sha256, storage_path, DocumentStatus.UPLOADED.value, now, now),
        )
        return self.get_document(document_id)

    def get_document(self, document_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM documents WHERE id = ?", (document_id,))
        if row is None:
            raise KeyError("施工方案不存在")
        row["parse_warnings"] = json_load(row.pop("parse_warnings_json"), [])
        return row

    def save_parsed_document(self, document_id: str, parsed: ParsedDocument) -> None:
        now = utc_now()
        rows = [
            (
                uuid4().hex,
                document_id,
                segment.sequence_no,
                segment.segment_type,
                segment.heading_path,
                segment.location,
                segment.text,
                segment.markdown,
            )
            for segment in parsed.segments
        ]
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM document_segments WHERE document_id = ?", (document_id,)
            )
            connection.executemany(
                """INSERT INTO document_segments
                   (id, document_id, sequence_no, segment_type, heading_path, location, text,
                    markdown_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            connection.execute(
                """UPDATE documents SET status = ?, segment_count = ?, parse_warnings_json = ?,
                   markdown_text = ?,
                   updated_at = ? WHERE id = ?""",
                (
                    DocumentStatus.PARSED.value,
                    len(rows),
                    json.dumps(parsed.warnings, ensure_ascii=False),
                    parsed.markdown,
                    now,
                    document_id,
                ),
            )

    def get_document_markdown(self, document_id: str) -> str:
        row = self.db.fetch_one(
            "SELECT markdown_text FROM documents WHERE id = ?", (document_id,)
        )
        if row is None:
            raise KeyError("施工方案不存在")
        return str(row["markdown_text"] or "")

    def list_segments(self, document_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM document_segments WHERE document_id = ? ORDER BY sequence_no",
            (document_id,),
        )

    def count_rules(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) AS count FROM audit_rules")
        return int(row["count"]) if row else 0

    def count_standards(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) AS count FROM standards")
        return int(row["count"]) if row else 0

    def count_standard_chunks(self, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS count FROM standard_chunks"
        if active_only:
            sql += " WHERE active = 1"
        row = self.db.fetch_one(sql)
        return int(row["count"]) if row else 0

    def list_scenes(self) -> list[str]:
        rows = self.db.fetch_all(
            """SELECT DISTINCT scene FROM audit_rules
               WHERE enabled_status = '启用' AND scene <> '' ORDER BY scene"""
        )
        return [row["scene"] for row in rows]

    def list_enabled_rules(self, scenes: list[str] | None = None) -> list[dict[str, Any]]:
        if not scenes:
            return self.db.fetch_all(
                "SELECT * FROM audit_rules WHERE enabled_status = '启用' ORDER BY rule_id"
            )
        placeholders = ",".join("?" for _ in scenes)
        return self.db.fetch_all(
            f"""SELECT * FROM audit_rules WHERE enabled_status = '启用'
                AND scene IN ({placeholders}) ORDER BY rule_id""",
            scenes,
        )

    def create_audit_run(
        self,
        document_id: str,
        model_provider: str,
        model_name: str | None,
        rule_limit: int,
        browser_session_id: str = "default-session",
    ) -> dict[str, Any]:
        run_id = uuid4().hex
        self.db.execute(
            """INSERT INTO audit_runs
               (id, document_id, browser_session_id, status, model_provider, model_name,
                rule_limit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                document_id,
                browser_session_id,
                AuditStatus.CREATED.value,
                model_provider,
                model_name,
                rule_limit,
                utc_now(),
            ),
        )
        return self.get_audit_run(run_id)

    def update_audit_run(self, run_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "current_node",
            "scenes_json",
            "scene_instances_json",
            "rule_limit",
            "completed_rules",
            "bundle_count",
            "completed_bundles",
            "model_call_count",
            "error",
            "completed_at",
        }
        fields = [(name, value) for name, value in values.items() if name in allowed]
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name, _ in fields)
        self.db.execute(
            f"UPDATE audit_runs SET {assignments} WHERE id = ?",
            [value for _, value in fields] + [run_id],
        )

    def get_audit_run(self, run_id: str, include_items: bool = False) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT audit_runs.*, documents.created_at AS document_created_at
               FROM audit_runs JOIN documents ON documents.id = audit_runs.document_id
               WHERE audit_runs.id = ?""",
            (run_id,),
        )
        if row is None:
            raise KeyError("审计任务不存在")
        current_or_completed = row["completed_at"] or utc_now()
        audit_started = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        document_started = datetime.fromisoformat(
            row.pop("document_created_at").replace("Z", "+00:00")
        )
        ended = datetime.fromisoformat(current_or_completed.replace("Z", "+00:00"))
        row["elapsed_seconds"] = round(max(0.0, (ended - audit_started).total_seconds()), 3)
        row["total_elapsed_seconds"] = round(
            max(0.0, (ended - document_started).total_seconds()), 3
        )
        row["scenes"] = json_load(row.pop("scenes_json"), [])
        row["scene_instances"] = json_load(row.pop("scene_instances_json"), [])
        row["candidate_rule_count"] = row["rule_limit"]
        row["items"] = self.list_audit_items(run_id) if include_items else []
        row["findings"] = build_business_findings(row["items"]) if include_items else []
        row["revisions"] = self.list_plan_revisions(run_id)
        return row

    def list_plan_revisions(self, source_run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT p.*, d.filename revised_filename
               FROM plan_revisions p
               JOIN audit_runs a ON a.id=p.revised_run_id
               JOIN documents d ON d.id=a.document_id
               WHERE p.source_run_id=? ORDER BY p.attempt_no DESC""",
            (source_run_id,),
        )
        for row in rows:
            row["comparison"] = json_load(row.pop("comparison_json"), {})
        return rows

    def create_plan_revision(
        self, source_run_id: str, revised_run_id: str, submitted_by: str
    ) -> dict[str, Any]:
        source = self.db.fetch_one("SELECT id FROM audit_runs WHERE id=?", (source_run_id,))
        if source is None:
            raise KeyError("原方案审查不存在")
        count = self.db.fetch_one(
            "SELECT COALESCE(MAX(attempt_no), 0) value FROM plan_revisions WHERE source_run_id=?",
            (source_run_id,),
        )
        revision_id = uuid4().hex
        self.db.execute(
            """INSERT INTO plan_revisions
               (id, source_run_id, revised_run_id, attempt_no, status,
                comparison_json, submitted_by, created_at)
               VALUES (?, ?, ?, ?, 'analyzing', '{}', ?, ?)""",
            (
                revision_id,
                source_run_id,
                revised_run_id,
                int(count["value"] if count else 0) + 1,
                submitted_by,
                utc_now(),
            ),
        )
        return self.list_plan_revisions(source_run_id)[0]

    def complete_plan_revision(self, revision_id: str) -> dict[str, Any]:
        revision = self.db.fetch_one("SELECT * FROM plan_revisions WHERE id=?", (revision_id,))
        if revision is None:
            raise KeyError("方案修订记录不存在")
        original = self.get_audit_run(revision["source_run_id"], include_items=True)
        revised = self.get_audit_run(revision["revised_run_id"], include_items=True)
        revised_by_rule = {item["rule_id"]: item for item in revised["items"]}
        details = []
        counts = {"resolved": 0, "partial": 0, "unresolved": 0, "uncertain": 0}
        actionable = {"不符合", "未说明"}
        for finding in original["findings"]:
            matched = [
                revised_by_rule[rule_id]
                for rule_id in finding["rule_ids"]
                if rule_id in revised_by_rule
            ]
            results = [item.get("final_result") or item["result"] for item in matched]
            consistency_check = bool(finding["rule_ids"]) and all(
                rule_id.startswith("PLAN-CONSISTENCY-")
                for rule_id in finding["rule_ids"]
            )
            if not matched and consistency_check:
                outcome = "resolved"
                explanation = "修订方案中已未再检出该项方案描述与计算书矛盾。"
            elif not matched or any(result == "需人工复核" for result in results):
                outcome = "uncertain"
                explanation = "修订方案中的对应证据不足，暂时无法判断原问题是否消除。"
            elif all(result not in actionable for result in results):
                outcome = "resolved"
                explanation = "修订方案已通过原问题对应审查项。"
            elif any(result not in actionable for result in results):
                outcome = "partial"
                explanation = "原问题已有部分补充，但仍有对应审查项未通过。"
            else:
                outcome = "unresolved"
                explanation = "原问题对应审查项在修订方案中仍未通过。"
            counts[outcome] += 1
            remaining_items = [
                item
                for item in matched
                if (item.get("final_result") or item["result"])
                in {*actionable, "需人工复核"}
            ]
            remaining_issues = [
                {
                    "rule_id": item["rule_id"],
                    "result": item.get("final_result") or item["result"],
                    "issue": item.get("issue") or "对应审查项仍未通过。",
                    "suggestion": item.get("suggestion") or "请补充对应方案内容。",
                    "source_location": item.get("source_location") or "修订方案全文",
                }
                for item in remaining_items
            ]
            quotes = list(
                dict.fromkeys(
                    item["plan_quote"].strip()
                    for item in matched
                    if item.get("plan_quote", "").strip()
                )
            )[:2]
            details.append(
                {
                    "finding_id": finding["id"],
                    "title": finding["title"],
                    "original_issue": finding["issue"],
                    "outcome": outcome,
                    "explanation": (
                        f"原问题已有部分补充，但仍有{len(remaining_issues)}个对应审查项未通过。"
                        if outcome == "partial" and remaining_issues
                        else explanation
                    ),
                    "remaining_issues": remaining_issues,
                    "revised_evidence": quotes,
                }
            )
        status = (
            "closed"
            if details and counts["resolved"] == len(details)
            else "needs_revision"
        )
        comparison = {
            "result": status,
            "original_finding_count": len(details),
            "resolved_count": counts["resolved"],
            "partial_count": counts["partial"],
            "unresolved_count": counts["unresolved"],
            "uncertain_count": counts["uncertain"],
            "summary": (
                "原方案审查问题已全部消除，本轮方案整改自动完成。"
                if status == "closed"
                else "修订方案仍有原审查问题未完全消除，建议继续修订后再次提交。"
            ),
            "details": details,
        }
        now = utc_now()
        self.db.execute(
            """UPDATE plan_revisions SET status=?, comparison_json=?, completed_at=?
               WHERE id=?""",
            (status, json.dumps(comparison, ensure_ascii=False), now, revision_id),
        )
        return next(
            item
            for item in self.list_plan_revisions(revision["source_run_id"])
            if item["id"] == revision_id
        )

    def fail_plan_revision(self, revision_id: str, message: str) -> None:
        self.db.execute(
            """UPDATE plan_revisions SET status='failed', comparison_json=?, completed_at=?
               WHERE id=?""",
            (json.dumps({"error": message}, ensure_ascii=False), utc_now(), revision_id),
        )

    def create_audit_item(self, values: dict[str, Any], evidences: list[dict[str, Any]]) -> str:
        item_id = uuid4().hex
        now = utc_now()
        evidence_rows = [
            (
                item_id,
                evidence["evidence_type"],
                evidence["source_id"],
                evidence["quote"],
                evidence.get("location"),
                evidence.get("score"),
            )
            for evidence in evidences
        ]
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO audit_items
                   (id, run_id, rule_id, scene, plan_quote, source_location, issue, basis_json,
                    risk_consequence, result, applicability_status, applicability_reason,
                    plan_obligation, plan_obligation_reason, bundle_id, business_group_key,
                    control_title, suggestion, confidence, review_status, model_raw_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    values["run_id"],
                    values["rule_id"],
                    values["scene"],
                    values["plan_quote"],
                    values["source_location"],
                    values["issue"],
                    json.dumps(values["basis"], ensure_ascii=False),
                    values["risk_consequence"],
                    values["result"],
                    values["applicability_status"],
                    values["applicability_reason"],
                    values["plan_obligation"],
                    values["plan_obligation_reason"],
                    values["bundle_id"],
                    values["business_group_key"],
                    values["control_title"],
                    values["suggestion"],
                    values["confidence"],
                    ReviewStatus.NOT_REQUIRED.value,
                    json.dumps(values.get("model_raw"), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO audit_evidences
                   (audit_item_id, evidence_type, source_id, quote, location, score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                evidence_rows,
            )
        return item_id

    def list_audit_items(self, run_id: str) -> list[dict[str, Any]]:
        items = self.db.fetch_all(
            "SELECT * FROM audit_items WHERE run_id = ? ORDER BY scene, rule_id", (run_id,)
        )
        for item in items:
            item["basis"] = json_load(item.pop("basis_json"), [])
            item.pop("model_raw_json", None)
            item["evidences"] = self.db.fetch_all(
                """SELECT evidence_type, source_id, quote, location, score
                   FROM audit_evidences WHERE audit_item_id = ? ORDER BY id""",
                (item["id"],),
            )
        return items

    def review_item(
        self,
        item_id: str,
        action: str,
        reviewer: str,
        final_result: str | None,
        final_text: str | None,
        comment: str | None,
    ) -> None:
        item = self.db.fetch_one("SELECT * FROM audit_items WHERE id = ?", (item_id,))
        if item is None:
            raise KeyError("审计项不存在")
        status_by_action = {
            "confirm": ReviewStatus.CONFIRMED.value,
            "edit": ReviewStatus.EDITED.value,
            "reject": ReviewStatus.REJECTED.value,
        }
        review_status = status_by_action[action]
        effective_result = final_result if action == "edit" else item["result"]
        now = utc_now()
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO human_reviews
                   (id, audit_item_id, action, reviewer, original_result, final_result,
                    final_text, comment, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid4().hex,
                    item_id,
                    action,
                    reviewer,
                    item["result"],
                    effective_result,
                    final_text,
                    comment,
                    now,
                ),
            )
            connection.execute(
                """UPDATE audit_items SET review_status = ?, final_result = ?, final_text = ?,
                   updated_at = ? WHERE id = ?""",
                (review_status, effective_result, final_text, now, item_id),
            )
        self._complete_run_if_reviewed(item["run_id"])

    def _complete_run_if_reviewed(self, run_id: str) -> None:
        row = self.db.fetch_one(
            """SELECT COUNT(*) AS pending FROM audit_items
               WHERE run_id = ? AND review_status = ?""",
            (run_id, ReviewStatus.PENDING.value),
        )
        if row and row["pending"] == 0:
            self.update_audit_run(
                run_id,
                status=AuditStatus.COMPLETED.value,
                current_node="human_review_completed",
                completed_at=utc_now(),
            )
