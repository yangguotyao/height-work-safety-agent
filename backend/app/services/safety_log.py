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

    def latest(self, assessment_date: str | None = None) -> dict[str, Any] | None:
        parameters: tuple[Any, ...] = ()
        where = ""
        if assessment_date:
            where = "WHERE assessment_date = ?"
            parameters = (assessment_date,)
        row = self.db.fetch_one(
            f"""SELECT * FROM daily_safety_logs {where}
                ORDER BY assessment_date DESC, version DESC LIMIT 1""",
            parameters,
        )
        return self._public(row) if row else None

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM daily_safety_logs
               ORDER BY assessment_date DESC, version DESC LIMIT ?""",
            (limit,),
        )
        return [self._public(row) for row in rows]

    def _latest_audit(self) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """SELECT id FROM audit_runs WHERE status = 'completed'
               ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT 1"""
        )
        return self.repository.get_audit_run(row["id"], include_items=True) if row else None

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

    def _collect(self, assessment_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
        risk = self.dynamic_risk.latest(assessment_date, include_test=False)
        if risk is None:
            risk = self.dynamic_risk.evaluate(
                assessment_date,
                trigger_type="data_refresh",
                refresh_weather=True,
                include_test=False,
            )
        tasks = []
        findings: list[dict[str, Any]] = []
        evidences: list[dict[str, Any]] = []
        quiz_ids: set[str] = set()
        submitted_ids: set[str] = set()
        wrong_question_ids: set[str] = set()
        learning_revisions = []

        for risk_item in risk.get("items") or []:
            chain = self.task_chain.get(risk_item["task_id"])
            task = chain["task"]
            card = chain.get("risk_card") or {}
            task_findings = chain.get("audit_findings") or []
            task_evidences = [self._evidence(item) for item in card.get("evidences") or []]
            learning = chain.get("learning") or {}
            attempts = learning.get("quiz_attempts") or []
            wrong_questions = learning.get("wrong_questions") or []
            quiz_ids.update(str(item["id"]) for item in attempts)
            submitted_ids.update(
                str(item["id"]) for item in attempts if item.get("status") == "submitted"
            )
            wrong_question_ids.update(str(item["id"]) for item in wrong_questions)
            learning_revisions.extend(
                {
                    "id": item["id"],
                    "status": item.get("status"),
                    "submitted_at": item.get("submitted_at"),
                    "correct_count": item.get("correct_count"),
                    "total": item.get("total"),
                }
                for item in attempts
            )
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
                    "scenes": task.get("scenes") or [],
                    "risk_level": risk_item["risk_level"],
                    "priority_score": risk_item["priority_score"],
                    "risk_summary": risk_item["summary"],
                    "main_risks": card.get("main_risks") or [],
                    "pre_job_checks": card.get("pre_job_checks") or [],
                    "prohibited_behaviors": card.get("prohibited_behaviors") or [],
                    "interventions": risk_item.get("interventions") or [],
                    "weather_warnings": card.get("weather_warnings") or [],
                    "similar_accidents": (card.get("similar_accidents") or [])[:3],
                    "audit_finding_ids": [item["id"] for item in task_findings],
                    "active_wrong_count": len(wrong_questions),
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
        latest_audit = self._latest_audit()
        knowledge = self.project_knowledge.overview(include_test=False)
        recommendations = []
        if wrong_question_ids:
            recommendations.append(
                f"结合当前任务优先复习 {len(wrong_question_ids)} 道待复习错题。"
            )
        if tasks and not submitted_ids:
            recommendations.append("可按当日主要作业场景安排一次5题测验，形成学习记录。")
        if any(item["risk_level"] in {"red", "yellow"} for item in tasks):
            scenes = sorted({scene for item in tasks for scene in item.get("scenes") or []})
            if scenes:
                recommendations.append(f"重点学习与{'、'.join(scenes[:4])}有关的基础规则和禁止行为。")

        content = {
            "source_modules": ["方案审查", "工人助手", "安全知识图谱", "动态风险"],
            "summary": {
                "task_count": len(tasks),
                "red_count": int(risk.get("red_count") or 0),
                "yellow_count": int(risk.get("yellow_count") or 0),
                "green_count": int(risk.get("green_count") or 0),
                "audit_finding_count": len(findings),
            },
            "weather": self._weather(risk),
            "tasks": tasks,
            "audit": {
                "run_id": latest_audit.get("id") if latest_audit else None,
                "status": latest_audit.get("status") if latest_audit else "not_found",
                "completed_at": latest_audit.get("completed_at") if latest_audit else None,
                "finding_count": len(latest_audit.get("findings") or []) if latest_audit else 0,
                "relevant_finding_count": len(findings),
            },
            "audit_findings": findings,
            "learning": {
                "quiz_attempt_count": len(quiz_ids),
                "submitted_quiz_count": len(submitted_ids),
                "active_wrong_count": len(wrong_question_ids),
                "recommendations": recommendations,
            },
            "knowledge": {
                "entity_count": knowledge.get("entity_count", 0),
                "relation_count": knowledge.get("relation_count", 0),
                "evidence_count": len(evidences),
            },
            "dynamic_risk": {
                "run_id": risk["id"],
                "input_fingerprint": risk.get("input_fingerprint") or "",
                "trigger_type": risk.get("trigger_type") or "",
                "created_at": risk.get("created_at"),
                "version_change": risk.get("version_change") or {},
            },
            "evidence_sources": evidences,
        }
        snapshot = {
            "project_name": self.settings.project_name,
            "assessment_date": assessment_date,
            "risk_fingerprint": risk.get("input_fingerprint") or risk["id"],
            "task_ids": sorted(item["task_id"] for item in tasks),
            "audit_revisions": sorted(
                [
                    item["id"],
                    item.get("updated_at") or item.get("created_at") or "",
                    item.get("final_result") or item.get("result") or "",
                ]
                for item in findings
            ),
            "learning_revisions": sorted(
                learning_revisions, key=lambda item: str(item["id"])
            ),
            "wrong_question_ids": sorted(wrong_question_ids),
            "evidence_ids": sorted(
                [item["evidence_type"], item["source_id"]] for item in evidences
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
            ("risk_fingerprint", "动态风险"),
            ("task_ids", "作业任务"),
            ("audit_revisions", "方案审查"),
            ("learning_revisions", "学习记录"),
            ("wrong_question_ids", "错题记录"),
            ("evidence_ids", "知识依据"),
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

    def generate(self, assessment_date: str | None = None) -> dict[str, Any]:
        target = assessment_date or date.today().isoformat()
        try:
            date.fromisoformat(target)
        except ValueError as exc:
            raise ValueError("日志日期必须使用YYYY-MM-DD格式") from exc
        content, snapshot = self._collect(target)
        fingerprint = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
        existing = self.db.fetch_one(
            """SELECT * FROM daily_safety_logs
               WHERE assessment_date = ? AND input_fingerprint = ?""",
            (target, fingerprint),
        )
        if existing:
            return self._public(existing, version_created=False)

        previous = self.latest(target)
        version = int(previous["version"] + 1) if previous else 1
        change = self._change(previous, snapshot)
        log_id = uuid4().hex
        created_at = utc_now()
        filename = f"{target}_V{version}_{log_id[:8]}.docx"
        path = self.output_dir / filename
        row = {
            "id": log_id,
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
               (id, assessment_date, project_name, version, input_fingerprint,
                content_json, source_snapshot_json, change_json, docx_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
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
        if not path.exists():
            render_safety_log_docx(log, path)
        return path, self.download_name(log)
