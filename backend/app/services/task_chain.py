from __future__ import annotations

from typing import Any

from ..db import Database, json_load
from .dynamic_risk import DynamicRiskService, logical_task_key


class TaskChainService:
    """Aggregate one logical work task without rewriting its source records."""

    def __init__(
        self,
        database: Database,
        dynamic_risk: DynamicRiskService,
    ):
        self.db = database
        self.dynamic_risk = dynamic_risk

    @staticmethod
    def _task(row: dict[str, Any]) -> dict[str, Any]:
        task = dict(row)
        task["scenes"] = json_load(task.pop("scenes_json"), [])
        return task

    def _duplicates(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM work_tasks WHERE scheduled_date = ?
               ORDER BY created_at, id""",
            (task["scheduled_date"],),
        )
        key = logical_task_key(task)
        return [candidate for row in rows if logical_task_key(candidate := self._task(row)) == key]

    def _risk_versions(
        self, task: dict[str, Any], duplicate_ids: set[str]
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT id FROM dynamic_risk_runs
               WHERE assessment_date = ? AND status = 'completed'
                 AND include_test = 0 AND input_fingerprint <> ''
               ORDER BY created_at DESC, id DESC LIMIT 20""",
            (task["scheduled_date"],),
        )
        task_key = logical_task_key(task)
        versions = []
        for row in rows:
            run = self.dynamic_risk.get_run(row["id"])
            item = next(
                (
                    candidate
                    for candidate in run["items"]
                    if logical_task_key(candidate) == task_key
                    or bool(duplicate_ids & set(candidate.get("merged_task_ids") or []))
                ),
                None,
            )
            if item:
                versions.append(
                    {
                        "run_id": run["id"],
                        "assessment_date": run["assessment_date"],
                        "trigger_type": run["trigger_type"],
                        "created_at": run["created_at"],
                        "completed_at": run["completed_at"],
                        "risk_level": item["risk_level"],
                        "priority_score": item["priority_score"],
                        "summary": item["summary"],
                        "triggers": item["triggers"],
                        "interventions": item["interventions"],
                        "evidences": item["evidences"],
                        "change": item["change"],
                    }
                )
        return versions

    def _audit_links(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        run_ids = sorted({str(task.get("audit_run_id") or "") for task in tasks} - {""})
        if not run_ids:
            return []
        scenes = {scene for task in tasks for scene in task.get("scenes") or []}
        placeholders = ",".join("?" for _ in run_ids)
        rows = self.db.fetch_all(
            f"""SELECT i.id, i.run_id, i.scene, i.issue, i.suggestion, i.result,
                       i.final_result, i.final_text, i.review_status, i.created_at,
                       r.standard_code, r.clause, r.original_text,
                       r.risk_level, r.rule_effect,
                       a.status run_status, a.completed_at, d.filename
                FROM audit_items i
                JOIN audit_rules r ON r.rule_id = i.rule_id
                JOIN audit_runs a ON a.id = i.run_id
                JOIN documents d ON d.id = a.document_id
                WHERE i.run_id IN ({placeholders})
                ORDER BY i.created_at, i.id""",
            tuple(run_ids),
        )
        result = []
        for row in rows:
            effective = row.get("final_result") or row.get("result")
            if scenes and row.get("scene") not in scenes:
                continue
            if effective not in {"不符合", "未说明", "需人工复核"}:
                continue
            row["effective_result"] = effective
            result.append(row)
        return result[:12]

    def get(self, task_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM work_tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError("作业任务不存在")
        requested = self._task(row)
        duplicates = self._duplicates(requested)
        representative = max(
            duplicates,
            key=lambda task: (
                bool(task.get("audit_run_id")),
                str(task.get("created_at") or ""),
                task["id"],
            ),
        )
        duplicate_ids = {task["id"] for task in duplicates}
        card_row = self.db.fetch_one(
            "SELECT card_json FROM task_risk_cards WHERE task_id = ?",
            (representative["id"],),
        )
        if card_row is None:
            for candidate_id in duplicate_ids:
                card_row = self.db.fetch_one(
                    "SELECT card_json FROM task_risk_cards WHERE task_id = ?", (candidate_id,)
                )
                if card_row:
                    break
        risk_card = json_load(card_row["card_json"], {}) if card_row else None
        versions = self._risk_versions(representative, duplicate_ids)
        audits = self._audit_links(duplicates)
        timeline = [
            {
                "type": "task",
                "title": "作业任务已登记",
                "detail": representative.get("normalized_task") or representative["work_content"],
                "at": representative["created_at"],
            }
        ]
        if risk_card:
            timeline.append(
                {
                    "type": "risk_card",
                    "title": "任务风险提示卡已生成",
                    "detail": f"{len(risk_card.get('main_risks') or [])}项主要风险",
                    "at": risk_card.get("created_at") or representative["created_at"],
                }
            )
        timeline.extend(
            {
                "type": "dynamic_risk",
                "title": f"动态风险评估 · {version['risk_level']}",
                "detail": version["change"].get("explanation") or version["summary"],
                "at": version["completed_at"] or version["created_at"],
            }
            for version in reversed(versions)
        )
        timeline.sort(key=lambda item: str(item.get("at") or ""))
        return {
            "task": representative,
            "requested_task_id": task_id,
            "duplicate_count": len(duplicates),
            "merged_task_ids": sorted(duplicate_ids),
            "data_quality": {
                "status": "merged" if len(duplicates) > 1 else "clean",
                "message": (
                    f"已将{len(duplicates)}条语义相同的任务记录合并展示，原始记录全部保留。"
                    if len(duplicates) > 1
                    else "未发现相同工人、作业、位置和时段的重复记录。"
                ),
            },
            "risk_card": risk_card,
            "current_dynamic_risk": versions[0] if versions else None,
            "dynamic_risk_versions": versions,
            "audit_findings": audits,
            "timeline": timeline,
            "links": {
                "worker": f"/#/worker?task={representative['id']}",
                "dynamic_risk": f"/#/risk?task={representative['id']}",
                "audit": f"/#/audit?run={representative.get('audit_run_id') or ''}",
            },
        }
