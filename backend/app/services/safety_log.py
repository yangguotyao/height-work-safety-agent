from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import Settings
from ..db import Database, json_load
from ..repositories import Repository, utc_now
from .dynamic_risk import DynamicRiskService
from .project_knowledge import ProjectKnowledgeService
from .safety_log_docx import render_safety_log_docx
from .task_chain import TaskChainService


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _unique(items: list[Any], key) -> list[Any]:
    result = []
    seen = set()
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


class SafetyLogService:
    """Build a versioned daily artifact from existing deterministic project services."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        repository: Repository,
        dynamic_risk: DynamicRiskService,
        task_chain: TaskChainService,
        project_knowledge: ProjectKnowledgeService,
    ):
        self.settings = settings
        self.db = database
        self.repository = repository
        self.dynamic_risk = dynamic_risk
        self.task_chain = task_chain
        self.project_knowledge = project_knowledge
        self.output_dir = settings.resolved_upload_dir / "safety_logs"

    @staticmethod
    def _public(row: dict[str, Any], *, version_created: bool = False) -> dict[str, Any]:
        result = dict(row)
        result["content"] = json_load(result.pop("content_json", None), {})
        result["source_snapshot"] = json_load(result.pop("source_snapshot_json", None), {})
        result["change"] = json_load(result.pop("change_json", None), {})
        result["version_created"] = version_created
        return result

    def get(self, log_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM daily_safety_logs WHERE id = ?", (log_id,))
        if row is None:
            raise KeyError("安全日志不存在")
        return self._public(row)

    def latest(
        self,
        assessment_date: str | None = None,
        *,
        browser_session_id: str = "default-session",
    ) -> dict[str, Any] | None:
        parameters: tuple[Any, ...] = (browser_session_id,)
        where = "WHERE browser_session_id IN ('baseline', 'legacy', ?)"
        if assessment_date:
            where += " AND assessment_date = ?"
            parameters = (browser_session_id, assessment_date)
        row = self.db.fetch_one(
            f"""SELECT * FROM daily_safety_logs {where}
                ORDER BY assessment_date DESC, version DESC LIMIT 1""",
            parameters,
        )
        return self._public(row) if row else None

    def list(
        self, limit: int = 30, *, browser_session_id: str = "default-session"
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM daily_safety_logs
               WHERE browser_session_id IN ('baseline', 'legacy', ?)
               ORDER BY assessment_date DESC, version DESC LIMIT ?""",
            (browser_session_id, limit),
        )
        return [self._public(row) for row in rows]

    def _plan_activity(
        self, assessment_date: str, browser_session_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        audit_rows = self.db.fetch_all(
            """SELECT a.id, a.status, a.created_at, a.completed_at, d.filename
               FROM audit_runs a JOIN documents d ON d.id=a.document_id
               WHERE a.browser_session_id IN ('baseline', 'legacy', ?)
                 AND NOT EXISTS (
                   SELECT 1 FROM plan_revisions p WHERE p.revised_run_id=a.id
               ) AND (
                   date(datetime(a.created_at, '+8 hours'))=?
                   OR date(datetime(a.completed_at, '+8 hours'))=?
               )
               ORDER BY COALESCE(a.completed_at, a.created_at), a.id""",
            (browser_session_id, assessment_date, assessment_date),
        )
        audits: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        for row in audit_rows:
            detail = self.repository.get_audit_run(row["id"], include_items=True)
            finding_count = len(detail.get("findings") or [])
            item = {**row, "finding_count": finding_count, "link": "/audit"}
            audits.append(item)
            timeline.append(
                {
                    "type": "plan_audit",
                    "stage": "事前预控",
                    "at": row.get("completed_at") or row["created_at"],
                    "title": "完成施工方案审查",
                    "detail": f"{row['filename']} · 形成{finding_count}项方案问题",
                    "actor": "AI辅助审查",
                    "status": "审查完成" if row["status"] == "completed" else row["status"],
                    "link": "/audit",
                }
            )

        revision_rows = self.db.fetch_all(
            """SELECT p.*, source_doc.filename source_filename,
                      revised_doc.filename revised_filename
               FROM plan_revisions p
               JOIN audit_runs source_run ON source_run.id=p.source_run_id
               JOIN documents source_doc ON source_doc.id=source_run.document_id
               JOIN audit_runs revised_run ON revised_run.id=p.revised_run_id
               JOIN documents revised_doc ON revised_doc.id=revised_run.document_id
               WHERE source_run.browser_session_id IN ('baseline', 'legacy', ?)
                 AND (date(datetime(p.created_at, '+8 hours'))=?
                  OR date(datetime(p.completed_at, '+8 hours'))=?
                 )
               ORDER BY COALESCE(p.completed_at, p.created_at), p.attempt_no""",
            (browser_session_id, assessment_date, assessment_date),
        )
        revisions: list[dict[str, Any]] = []
        for row in revision_rows:
            comparison = json_load(row.get("comparison_json"), {})
            outstanding = [
                item.get("title") or "未命名问题"
                for item in comparison.get("details") or []
                if item.get("outcome") != "resolved"
            ]
            item = {
                **row,
                "comparison": comparison,
                "outstanding_titles": outstanding,
                "link": "/audit",
            }
            item.pop("comparison_json", None)
            revisions.append(item)
            resolved = int(comparison.get("resolved_count") or 0)
            total = int(comparison.get("original_finding_count") or 0)
            remaining = max(total - resolved, 0)
            timeline.append(
                {
                    "type": "plan_revision",
                    "stage": "事前预控",
                    "at": row.get("completed_at") or row["created_at"],
                    "title": f"完成第{row['attempt_no']}次方案整改对比",
                    "detail": (
                        f"{row['revised_filename']} · 原问题{total}项，"
                        f"已解决{resolved}项，仍需修改{remaining}项"
                    ),
                    "actor": row.get("submitted_by") or "方案提交人",
                    "status": "已关闭" if row["status"] == "closed" else "需继续修订",
                    "link": "/audit",
                }
            )
        return audits, revisions, timeline

    def _onsite_activity(
        self, assessment_date: str, browser_session_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        inspections = self.db.fetch_all(
            """SELECT i.id, i.task_id, i.description, i.original_filename,
                      i.storage_path,
                      i.analysis_status, i.submitted_by, i.created_at, i.completed_at,
                      COUNT(c.id) candidate_count,
                      SUM(CASE WHEN c.review_status='confirmed' THEN 1 ELSE 0 END) accepted_count,
                      SUM(CASE WHEN c.review_status='rejected' THEN 1 ELSE 0 END) rejected_count,
                      SUM(CASE WHEN c.review_status='pending' THEN 1 ELSE 0 END) pending_count
               FROM hazard_inspections i
               LEFT JOIN hazard_candidates c ON c.inspection_id=i.id
               WHERE i.browser_session_id IN ('baseline', 'legacy', ?)
                 AND (date(datetime(i.created_at, '+8 hours'))=?
                  OR date(datetime(i.completed_at, '+8 hours'))=?
                 )
               GROUP BY i.id ORDER BY COALESCE(i.completed_at, i.created_at)""",
            (browser_session_id, assessment_date, assessment_date),
        )
        for item in inspections:
            item["stored_image_name"] = Path(item.get("storage_path") or "").name
            item.pop("storage_path", None)
        timeline = [
            {
                "type": "onsite_inspection",
                "stage": "事中识别",
                "at": item.get("completed_at") or item["created_at"],
                "title": "完成现场多模态巡检",
                "detail": (
                    f"{item['original_filename']} · "
                    f"AI生成{int(item.get('candidate_count') or 0)}项疑似隐患，"
                    f"人工确认{int(item.get('accepted_count') or 0)}项、"
                    f"排除{int(item.get('rejected_count') or 0)}项"
                ),
                "actor": item.get("submitted_by") or "现场上报人",
                "status": (
                    "AI识别完成"
                    if item["analysis_status"] == "completed"
                    else item["analysis_status"]
                ),
                "link": "/hazards",
            }
            for item in inspections
        ]
        event_rows = self.db.fetch_all(
            """SELECT e.*, s.item_no, s.title safety_title
               FROM platform_data_events e
               LEFT JOIN safety_items s ON s.id=e.entity_id
               LEFT JOIN hazard_candidates c ON c.id=s.source_candidate_id
               LEFT JOIN hazard_inspections i ON i.id=c.inspection_id
               WHERE e.project_id='default-project'
                 AND i.browser_session_id IN ('baseline', 'legacy', ?)
                 AND date(datetime(e.created_at, '+8 hours'))=?
                 AND e.event_type NOT IN ('onsite_reported', 'ai_candidate_generated')
               ORDER BY e.created_at, e.id""",
            (browser_session_id, assessment_date),
        )
        rectification_types = {
            "rectification_order_created",
            "rectification_submitted",
            "review_passed",
            "review_returned",
            "safety_item_closed",
        }
        for row in event_rows:
            payload = json_load(row.get("payload_json"), {})
            timeline.append(
                {
                    "type": row["event_type"],
                    "stage": (
                        "事后整改" if row["event_type"] in rectification_types else "事中识别"
                    ),
                    "at": row["created_at"],
                    "title": payload.get("title") or row["event_type"],
                    "detail": payload.get("detail") or row.get("safety_title") or "",
                    "actor": payload.get("actor") or "项目人员",
                    "status": row.get("item_no") or "已记录",
                    "link": f"/rectification?item={row['entity_id']}",
                }
            )

        safety_items = self.db.fetch_all(
            """SELECT s.id, s.item_no, s.title, s.fact_description,
                      s.risk_level, s.location, s.status, s.basis_json,
                      s.rectification_requirement, s.responsible_ref,
                      s.created_at, s.updated_at,
                      o.id order_id, o.order_no, o.status order_status, o.due_at,
                      c.inspection_id, c.suspected_hazard,
                      i.original_filename before_original_filename,
                      i.storage_path before_storage_path,
                      (SELECT COUNT(*) FROM rectification_submissions sub
                       WHERE sub.order_id=o.id) submission_count,
                      (SELECT COUNT(*) FROM rectification_reviews rev
                       WHERE rev.order_id=o.id) review_count,
                      (SELECT sub.original_filename FROM rectification_submissions sub
                       WHERE sub.order_id=o.id ORDER BY sub.attempt_no DESC LIMIT 1)
                       after_original_filename,
                      (SELECT sub.storage_path FROM rectification_submissions sub
                       WHERE sub.order_id=o.id ORDER BY sub.attempt_no DESC LIMIT 1)
                       after_storage_path,
                      (SELECT sub.description FROM rectification_submissions sub
                       WHERE sub.order_id=o.id ORDER BY sub.attempt_no DESC LIMIT 1)
                       rectification_description,
                      (SELECT rev.result FROM rectification_reviews rev
                       WHERE rev.order_id=o.id ORDER BY rev.sequence_no DESC LIMIT 1)
                       latest_review_result,
                      (SELECT rev.reason FROM rectification_reviews rev
                       WHERE rev.order_id=o.id ORDER BY rev.sequence_no DESC LIMIT 1)
                       latest_review_reason
               FROM safety_items s
               JOIN hazard_candidates c ON c.id=s.source_candidate_id
               JOIN hazard_inspections i ON i.id=c.inspection_id
               LEFT JOIN rectification_orders o ON o.safety_item_id=s.id
               WHERE i.browser_session_id IN ('baseline', 'legacy', ?)
                 AND (date(datetime(s.created_at, '+8 hours'))=?
                  OR date(datetime(s.updated_at, '+8 hours'))=?
                 )
               ORDER BY s.updated_at DESC""",
            (browser_session_id, assessment_date, assessment_date),
        )
        for item in safety_items:
            item["basis"] = json_load(item.pop("basis_json", None), [])
            item["before_stored_image_name"] = Path(
                item.pop("before_storage_path", "") or ""
            ).name
            item["after_stored_image_name"] = Path(
                item.pop("after_storage_path", "") or ""
            ).name
        return inspections, safety_items, timeline

    def _open_items(self, browser_session_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        revision_rows = self.db.fetch_all(
            """SELECT p.*, d.filename
               FROM plan_revisions p
               JOIN audit_runs a ON a.id=p.source_run_id
               JOIN documents d ON d.id=a.document_id
               WHERE a.browser_session_id IN ('baseline', 'legacy', ?)
                 AND p.status!='closed' AND NOT EXISTS (
                   SELECT 1 FROM plan_revisions newer
                   WHERE newer.source_run_id=p.source_run_id
                     AND newer.attempt_no>p.attempt_no
               ) ORDER BY p.created_at DESC""",
            (browser_session_id,),
        )
        for row in revision_rows:
            comparison = json_load(row.get("comparison_json"), {})
            result.append(
                {
                    "type": "plan_revision",
                    "title": row["filename"],
                    "detail": comparison.get("summary") or "修订方案仍有问题需要处理。",
                    "status": "需继续修订",
                    "link": "/audit",
                }
            )
        safety_rows = self.db.fetch_all(
            """SELECT s.id, s.item_no, s.title, s.status, s.updated_at,
                      o.status order_status, o.due_at
               FROM safety_items s
               JOIN hazard_candidates c ON c.id=s.source_candidate_id
               JOIN hazard_inspections i ON i.id=c.inspection_id
               LEFT JOIN rectification_orders o ON o.safety_item_id=s.id
               WHERE i.browser_session_id IN ('baseline', 'legacy', ?)
                 AND s.status!='closed' ORDER BY s.updated_at DESC""",
            (browser_session_id,),
        )
        for row in safety_rows:
            result.append(
                {
                    "type": "safety_item",
                    "title": f"{row['item_no']} · {row['title']}",
                    "detail": "现场安全事项尚未完成整改复核。",
                    "status": row.get("order_status") or row["status"],
                    "due_at": row.get("due_at"),
                    "link": f"/rectification?item={row['id']}",
                }
            )
        return result

    def _weather(self, risk: dict[str, Any]) -> dict[str, Any]:
        snapshots = risk.get("weather") or {}
        first = next(iter(snapshots.values()), {}) if isinstance(snapshots, dict) else {}
        return {
            key: value
            for key, value in first.items()
            if key not in {"project_name", "raw"}
        }

    def _evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        item = {
            "evidence_type": str(evidence.get("evidence_type") or "项目数据"),
            "source_id": str(evidence.get("source_id") or ""),
            "title": str(evidence.get("title") or ""),
            "quote": str(evidence.get("quote") or ""),
            "location": str(evidence.get("location") or ""),
            "source_url": str(evidence.get("source_url") or ""),
        }
        if item["evidence_type"] == "weather":
            item["title"] = f"{self.settings.project_name}天气"
        return item

    def _collect(
        self, assessment_date: str, browser_session_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        risk = self.dynamic_risk.latest(
            assessment_date,
            include_test=False,
            browser_session_id=browser_session_id,
        )
        if risk is None:
            risk = self.dynamic_risk.evaluate(
                assessment_date,
                trigger_type="data_refresh",
                refresh_weather=True,
                include_test=False,
                browser_session_id=browser_session_id,
            )
        tasks = []
        findings: list[dict[str, Any]] = []
        evidences: list[dict[str, Any]] = []

        for risk_item in risk.get("items") or []:
            chain = self.task_chain.get(risk_item["task_id"])
            task = chain["task"]
            card = chain.get("risk_card") or {}
            task_findings = chain.get("audit_findings") or []
            task_evidences = [self._evidence(item) for item in card.get("evidences") or []]
            tasks.append(
                {
                    "task_id": task["id"],
                    "worker_ref": task.get("worker_ref") or "",
                    "team_ref": task.get("team_ref") or "",
                    "work_content": task["work_content"],
                    "work_location": task["work_location"],
                    "work_floor": task["work_floor"],
                    "work_time": task["work_time"],
                    "normalized_task": task["normalized_task"],
                    "created_at": task.get("created_at"),
                    "scenes": task.get("scenes") or [],
                    "risk_level": risk_item["risk_level"],
                    "priority_score": risk_item["priority_score"],
                    "risk_summary": risk_item["summary"],
                    "main_risks": card.get("main_risks") or [],
                    "pre_job_checks": card.get("pre_job_checks") or [],
                    "prohibited_behaviors": card.get("prohibited_behaviors") or [],
                    "interventions": risk_item.get("interventions") or [],
                    "weather_warnings": [
                        item.get("message") if isinstance(item, dict) else str(item)
                        for item in card.get("weather_warnings") or []
                        if (item.get("message") if isinstance(item, dict) else str(item))
                    ],
                    "similar_accidents": (card.get("similar_accidents") or [])[:3],
                    "audit_finding_ids": [item["id"] for item in task_findings],
                    "links": chain.get("links") or {},
                }
            )
            findings.extend(task_findings)
            evidences.extend(task_evidences)

        findings = _unique(findings, lambda item: item["id"])
        evidences = _unique(
            evidences,
            lambda item: (item["evidence_type"], item["source_id"], item["quote"]),
        )[:30]
        audits, plan_revisions, plan_timeline = self._plan_activity(
            assessment_date, browser_session_id
        )
        inspections, safety_items, onsite_timeline = self._onsite_activity(
            assessment_date, browser_session_id
        )
        open_items = self._open_items(browser_session_id)
        timeline = list(plan_timeline)
        for task in tasks:
            timeline.append(
                {
                    "type": "pre_job_risk",
                    "stage": "事前预控",
                    "at": task.get("created_at") or risk.get("created_at"),
                    "title": "生成班前风险提示卡",
                    "detail": f"{task['normalized_task']} · {task['risk_summary']}",
                    "actor": task.get("team_ref") or task.get("worker_ref") or "班组",
                    "status": {
                        "red": "高风险",
                        "yellow": "较高风险",
                        "green": "一般风险",
                    }.get(task["risk_level"], "风险已分析"),
                    "link": "/worker",
                }
            )
        timeline.extend(onsite_timeline)
        timeline.sort(key=lambda item: (item.get("at") or "", item["type"]))

        content = {
            "source_modules": [
                "施工方案审查",
                "班前风险分析",
                "现场隐患巡检",
                "隐患整改记录",
            ],
            "summary": {
                "plan_activity_count": len(audits) + len(plan_revisions),
                "task_count": len(tasks),
                "inspection_count": len(inspections),
                "red_count": int(risk.get("red_count") or 0),
                "yellow_count": int(risk.get("yellow_count") or 0),
                "green_count": int(risk.get("green_count") or 0),
                "audit_finding_count": len(findings),
                "safety_item_count": len(safety_items),
                "closed_safety_item_count": sum(
                    item["status"] == "closed" for item in safety_items
                ),
                "open_item_count": len(open_items),
            },
            "weather": self._weather(risk),
            "tasks": tasks,
            "plan_audits": audits,
            "plan_revisions": plan_revisions,
            "audit_findings": findings,
            "dynamic_risk": {
                "run_id": risk["id"],
                "input_fingerprint": risk.get("input_fingerprint") or "",
                "trigger_type": risk.get("trigger_type") or "",
                "created_at": risk.get("created_at"),
                "version_change": risk.get("version_change") or {},
            },
            "evidence_sources": evidences,
            "onsite_inspections": inspections,
            "onsite_rectification": safety_items,
            "timeline": timeline,
            "open_items": open_items,
        }
        snapshot = {
            "project_name": self.settings.project_name,
            "assessment_date": assessment_date,
            "browser_session_id": browser_session_id,
            "risk_fingerprint": risk.get("input_fingerprint") or risk["id"],
            "task_ids": sorted(item["task_id"] for item in tasks),
            "plan_activity": sorted(
                [item["id"], item.get("status") or ""]
                for item in [*audits, *plan_revisions]
            ),
            "evidence_ids": sorted(
                [item["evidence_type"], item["source_id"]] for item in evidences
            ),
            "safety_item_revisions": sorted(
                [item["id"], item["status"], item["updated_at"]]
                for item in safety_items
            ),
            "timeline_events": sorted(
                [item["type"], item.get("at") or "", item.get("status") or ""]
                for item in timeline
            ),
            "open_items": sorted(
                [item["type"], item["title"], item["status"]] for item in open_items
            ),
        }
        return content, snapshot

    @staticmethod
    def _change(previous: dict[str, Any] | None, snapshot: dict[str, Any]) -> dict[str, Any]:
        if previous is None:
            return {
                "previous_log_id": None,
                "changed_sources": [],
                "explanation": "这是该日期的首个安全日志版本。",
            }
        old = previous["source_snapshot"]
        labels = [
            ("project_name", "项目名称"),
            ("risk_fingerprint", "班前风险"),
            ("task_ids", "班前任务"),
            ("plan_activity", "方案审查与整改"),
            ("evidence_ids", "知识依据"),
            ("safety_item_revisions", "现场隐患整改"),
            ("timeline_events", "安全过程事件"),
            ("open_items", "未闭环事项"),
        ]
        changed = [label for key, label in labels if old.get(key) != snapshot.get(key)]
        return {
            "previous_log_id": previous["id"],
            "changed_sources": changed,
            "explanation": (
                f"较V{previous['version']}更新：{'、'.join(changed)}发生变化。"
                if changed
                else f"与V{previous['version']}使用相同的项目数据。"
            ),
        }

    def generate(
        self,
        assessment_date: str | None = None,
        *,
        browser_session_id: str = "default-session",
    ) -> dict[str, Any]:
        target = assessment_date or date.today().isoformat()
        try:
            date.fromisoformat(target)
        except ValueError as exc:
            raise ValueError("日志日期必须使用YYYY-MM-DD格式") from exc
        content, snapshot = self._collect(target, browser_session_id)
        fingerprint = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
        existing = self.db.fetch_one(
            """SELECT * FROM daily_safety_logs
               WHERE assessment_date = ? AND input_fingerprint = ?""",
            (target, fingerprint),
        )
        if existing:
            return self._public(existing, version_created=False)

        previous = self.latest(target, browser_session_id=browser_session_id)
        version = int(previous["version"] + 1) if previous else 1
        change = self._change(previous, snapshot)
        log_id = uuid4().hex
        created_at = utc_now()
        filename = f"{target}_V{version}_{log_id[:8]}.docx"
        path = self.output_dir / filename
        row = {
            "id": log_id,
            "browser_session_id": browser_session_id,
            "assessment_date": target,
            "project_name": self.settings.project_name,
            "version": version,
            "input_fingerprint": fingerprint,
            "content_json": _canonical(content),
            "source_snapshot_json": _canonical(snapshot),
            "change_json": _canonical(change),
            "docx_path": str(path),
            "created_at": created_at,
        }
        public = self._public(row, version_created=True)
        render_safety_log_docx(public, path)
        self.db.execute(
            """INSERT INTO daily_safety_logs
               (id, browser_session_id, assessment_date, project_name, version, input_fingerprint,
                content_json, source_snapshot_json, change_json, docx_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                browser_session_id,
                target,
                self.settings.project_name,
                version,
                fingerprint,
                row["content_json"],
                row["source_snapshot_json"],
                row["change_json"],
                str(path),
                created_at,
            ),
        )
        return public

    @staticmethod
    def download_name(log: dict[str, Any]) -> str:
        project = re.sub(r'[\\/:*?"<>|]+', "_", log["project_name"]).strip(" .")
        return f"{project}_{log['assessment_date']}_高处作业安全日志_V{log['version']}.docx"

    def document_path(self, log_id: str) -> tuple[Path, str]:
        log = self.get(log_id)
        path = Path(log["docx_path"])
        for inspection in log["content"].get("onsite_inspections") or []:
            if inspection.get("stored_image_name"):
                continue
            row = self.db.fetch_one(
                "SELECT storage_path FROM hazard_inspections WHERE id = ?",
                (inspection.get("id"),),
            )
            if row:
                inspection["stored_image_name"] = Path(row["storage_path"]).name
        for item in log["content"].get("onsite_rectification") or []:
            row = self.db.fetch_one(
                """SELECT s.fact_description, s.basis_json,
                          s.rectification_requirement,
                          c.inspection_id,
                          i.original_filename before_original_filename,
                          i.storage_path before_storage_path,
                          o.id order_id, o.status order_status
                   FROM safety_items s
                   JOIN hazard_candidates c ON c.id=s.source_candidate_id
                   JOIN hazard_inspections i ON i.id=c.inspection_id
                   LEFT JOIN rectification_orders o ON o.safety_item_id=s.id
                   WHERE s.id=?""",
                (item.get("id"),),
            )
            if row is None:
                continue
            item.update(
                {
                    **row,
                    "basis": json_load(row.get("basis_json"), []),
                    "before_stored_image_name": Path(
                        row.get("before_storage_path") or ""
                    ).name,
                }
            )
            item.pop("basis_json", None)
            item.pop("before_storage_path", None)
            if row.get("order_id"):
                submission = self.db.fetch_one(
                    """SELECT original_filename after_original_filename,
                              storage_path after_storage_path,
                              description rectification_description
                       FROM rectification_submissions WHERE order_id=?
                       ORDER BY attempt_no DESC LIMIT 1""",
                    (row["order_id"],),
                )
                if submission:
                    item.update(submission)
                    item["after_stored_image_name"] = Path(
                        item.pop("after_storage_path", "") or ""
                    ).name
                review = self.db.fetch_one(
                    """SELECT result latest_review_result, reason latest_review_reason
                       FROM rectification_reviews WHERE order_id=?
                       ORDER BY sequence_no DESC LIMIT 1""",
                    (row["order_id"],),
                )
                if review:
                    item.update(review)
        render_safety_log_docx(log, path)
        return path, self.download_name(log)
