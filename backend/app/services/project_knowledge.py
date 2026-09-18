from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from ..db import Database, json_load
from ..repositories import utc_now
from .accident_knowledge import AccidentKnowledgeRepository

TEST_WORKERS = {"工人01", "工人A", "工人B"}
TEST_WORKER_MARKERS = ("测试", "验收", "页面", "test")
DOMAIN_SEARCH_TERMS = (
    "附着式升降脚手架",
    "悬挑脚手架",
    "落地式脚手架",
    "操作平台",
    "脚手架",
    "电梯井",
    "安全带",
    "安全帽",
    "连墙件",
    "防护栏杆",
    "外墙",
    "幕墙",
    "模板",
    "吊篮",
    "洞口",
    "临边",
    "屋面",
    "拆除",
    "搭设",
    "安装",
    "清理",
    "清洗",
    "维修",
    "补漏",
    "挂点",
)
RELATION_LABELS = {
    "PROJECT_HAS_DOCUMENT": "项目包含方案",
    "PROJECT_HAS_AUDIT": "项目包含审计",
    "PROJECT_HAS_TASK": "项目包含任务",
    "PROJECT_HAS_WORKER": "项目包含工人标识",
    "PROJECT_HAS_ASSESSMENT": "项目包含动态评估",
    "DOCUMENT_HAS_AUDIT": "方案接受审计",
    "AUDIT_HAS_ISSUE": "审计发现问题",
    "ISSUE_IN_SCENE": "问题属于场景",
    "ISSUE_BASED_ON_RULE": "问题依据规则",
    "RULE_IN_SCENE": "规则适用于场景",
    "RULE_FROM_STANDARD": "规则来源于规范",
    "ACCIDENT_IN_SCENE": "事故涉及场景",
    "ACCIDENT_HAS_RISK": "事故包含风险因素",
    "ACCIDENT_HAS_BEHAVIOR": "事故包含不安全行为",
    "ACCIDENT_HAS_MEASURE": "事故对应预防措施",
    "TASK_ASSIGNED_TO": "任务关联工人",
    "TASK_IN_SCENE": "任务属于场景",
    "TASK_REFERENCES_AUDIT": "任务引用审计",
    "TASK_HAS_RISK": "任务包含风险",
    "TASK_SIMILAR_TO_ACCIDENT": "任务关联相似事故",
    "ASSESSMENT_HAS_RISK": "评估包含风险项",
    "RISK_EVALUATES_TASK": "风险项评估任务",
    "RISK_RECOMMENDS_INTERVENTION": "风险项建议干预",
    "QA_ASKED_BY": "问题由工人提出",
    "QA_ABOUT_TASK": "问题关联任务",
    "QA_SUPPORTED_BY": "回答依据",
}


def _identifier(entity_type: str, source_id: str) -> str:
    digest = hashlib.sha1(  # noqa: S324 - stable identifier, not cryptography
        f"{entity_type}|{source_id}".encode()
    ).hexdigest()[:20]
    return f"{entity_type}:{digest}"


def _relation_identifier(
    source_id: str, relation_type: str, target_id: str, source_type: str, record_id: str
) -> str:
    value = "|".join((source_id, relation_type, target_id, source_type, record_id))
    return hashlib.sha1(value.encode("utf-8")).hexdigest()  # noqa: S324


def _split_values(value: str | None) -> list[str]:
    if not value or value.strip() == "未说明":
        return []
    return list(
        dict.fromkeys(
            part.strip()
            for part in re.split(r"[；;、\n]", value)
            if part.strip() and part.strip() != "未说明"
        )
    )


def _short(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _scope_for_worker(worker_ref: str) -> str:
    normalized = worker_ref.strip()
    lowered = normalized.lower()
    if normalized in TEST_WORKERS or any(marker in lowered for marker in TEST_WORKER_MARKERS):
        return "test"
    if normalized.startswith("演示"):
        return "demo"
    return "official"


class ProjectKnowledgeService:
    def __init__(
        self,
        database: Database,
        accidents: AccidentKnowledgeRepository,
        project_name: str,
    ):
        self.db = database
        self.accidents = accidents
        self.project_name = project_name

    def sync(self) -> dict[str, int]:
        now = utc_now()
        entities: dict[str, tuple[Any, ...]] = {}
        relations: dict[str, tuple[Any, ...]] = {}

        def add_entity(
            entity_type: str,
            source_type: str,
            source_id: str,
            name: str,
            *,
            summary: str = "",
            scene: str = "",
            data_scope: str = "official",
            metadata: dict[str, Any] | None = None,
            updated_at: str | None = None,
        ) -> str:
            entity_id = _identifier(entity_type, source_id)
            entities[entity_id] = (
                entity_id,
                entity_type,
                _short(name, 240),
                _short(summary, 900),
                scene.strip(),
                data_scope,
                source_type,
                source_id,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                updated_at or now,
            )
            return entity_id

        def add_relation(
            source_entity_id: str,
            relation_type: str,
            target_entity_id: str,
            *,
            source_type: str,
            source_id: str,
            evidence: dict[str, Any] | None = None,
            data_scope: str = "official",
        ) -> None:
            if source_entity_id not in entities or target_entity_id not in entities:
                return
            relation_id = _relation_identifier(
                source_entity_id,
                relation_type,
                target_entity_id,
                source_type,
                source_id,
            )
            relations[relation_id] = (
                relation_id,
                source_entity_id,
                relation_type,
                target_entity_id,
                RELATION_LABELS[relation_type],
                json.dumps(evidence or {}, ensure_ascii=False, default=str),
                data_scope,
                source_type,
                source_id,
                now,
            )

        project_id = add_entity(
            "project",
            "settings",
            "default",
            self.project_name,
            summary="单项目高处作业安全知识空间",
        )

        scene_ids: dict[str, str] = {}

        def scene_entity(scene: str) -> str:
            normalized = scene.strip() or "未分类场景"
            if normalized not in scene_ids:
                scene_ids[normalized] = add_entity(
                    "scene", "scene", normalized, normalized, summary="高处作业场景"
                )
            return scene_ids[normalized]

        standard_ids: dict[str, str] = {}
        standards = self.db.fetch_all("SELECT * FROM standards ORDER BY standard_code")
        for standard in standards:
            code = str(standard["standard_code"])
            standard_ids[code] = add_entity(
                "standard",
                "standard",
                code,
                f"{code} {standard['standard_name']}",
                summary=f"状态：{standard['status']}；来源：{standard['source_file']}",
                metadata={
                    "standard_code": code,
                    "standard_name": standard["standard_name"],
                    "status": standard["status"],
                    "source_file": standard["source_file"],
                },
            )

        rule_ids: dict[str, str] = {}
        rules = self.db.fetch_all(
            "SELECT * FROM audit_rules WHERE enabled_status = '启用' ORDER BY rule_id"
        )
        for rule in rules:
            rule_id = str(rule["rule_id"])
            scene = str(rule["scene"])
            standard_code = str(rule["standard_code"])
            rule_entity = add_entity(
                "rule",
                "audit_rule",
                rule_id,
                f"{rule_id} · {_short(rule['requirement'], 90)}",
                summary=rule["requirement"],
                scene=scene,
                metadata={
                    "rule_id": rule_id,
                    "requirement": rule["requirement"],
                    "hazards": rule["hazards"],
                    "risk_level": rule["risk_level"],
                    "standard_code": standard_code,
                    "standard_name": rule["standard_name"],
                    "clause": rule["clause"],
                    "page": rule["pdf_page"],
                    "quote": rule["original_text"] or rule["requirement"],
                },
            )
            rule_ids[rule_id] = rule_entity
            add_relation(
                rule_entity,
                "RULE_IN_SCENE",
                scene_entity(scene),
                source_type="audit_rule",
                source_id=rule_id,
                evidence={"rule_id": rule_id, "requirement": rule["requirement"]},
            )
            standard_entity = standard_ids.get(standard_code)
            if standard_entity is None:
                standard_entity = add_entity(
                    "standard",
                    "audit_rule_standard",
                    standard_code,
                    f"{standard_code} {rule['standard_name']}",
                    metadata={"standard_code": standard_code},
                )
                standard_ids[standard_code] = standard_entity
            add_relation(
                rule_entity,
                "RULE_FROM_STANDARD",
                standard_entity,
                source_type="audit_rule",
                source_id=rule_id,
                evidence={
                    "standard_code": standard_code,
                    "clause": rule["clause"],
                    "page": rule["pdf_page"],
                    "quote": rule["original_text"] or rule["requirement"],
                },
            )

        accident_ids: dict[str, str] = {}
        for case in self.accidents.list_cases():
            case_id = str(case["case_id"])
            scene = str(case.get("scene") or "")
            accident_entity = add_entity(
                "accident",
                "accident_case",
                case_id,
                f"{case_id} · {case['title']}",
                summary=case.get("evidence") or case.get("direct_cause") or "",
                scene=scene,
                metadata={
                    "case_id": case_id,
                    "title": case["title"],
                    "task": case.get("task") or "",
                    "scene": scene,
                    "consequence": case.get("consequence") or "",
                    "source_agency": case.get("source_agency") or "",
                    "source_url": case.get("source_url") or "",
                },
            )
            accident_ids[case_id] = accident_entity
            add_relation(
                accident_entity,
                "ACCIDENT_IN_SCENE",
                scene_entity(scene),
                source_type="accident_case",
                source_id=case_id,
                evidence={
                    "case_id": case_id,
                    "source_url": case.get("source_url") or "",
                    "quote": _short(case.get("evidence") or case.get("direct_cause"), 500),
                },
            )
            for entity_type, field, relation_type in (
                ("risk", "risk_factors", "ACCIDENT_HAS_RISK"),
                ("unsafe_behavior", "unsafe_behaviors", "ACCIDENT_HAS_BEHAVIOR"),
                ("measure", "measures", "ACCIDENT_HAS_MEASURE"),
            ):
                for value in _split_values(case.get(field)):
                    target = add_entity(
                        entity_type,
                        entity_type,
                        value,
                        value,
                        scene=scene,
                        summary=f"来源于事故案例 {case_id}",
                    )
                    add_relation(
                        accident_entity,
                        relation_type,
                        target,
                        source_type="accident_case",
                        source_id=f"{case_id}:{field}:{value}",
                        evidence={"case_id": case_id, "source_url": case.get("source_url")},
                    )

        document_ids: dict[str, str] = {}
        for document in self.db.fetch_all("SELECT * FROM documents ORDER BY created_at"):
            document_id = str(document["id"])
            entity = add_entity(
                "document",
                "document",
                document_id,
                document["filename"],
                summary=f"施工方案文档；解析状态：{document['status']}",
                metadata={
                    "filename": document["filename"],
                    "status": document["status"],
                    "segment_count": document["segment_count"],
                    "created_at": document["created_at"],
                },
                updated_at=document["created_at"],
            )
            document_ids[document_id] = entity
            add_relation(
                project_id,
                "PROJECT_HAS_DOCUMENT",
                entity,
                source_type="document",
                source_id=document_id,
                evidence={"filename": document["filename"]},
            )

        audit_ids: dict[str, str] = {}
        audit_scopes: dict[str, str] = {}
        issue_ids: dict[str, str] = {}
        audits = self.db.fetch_all(
            """SELECT audit_runs.*, documents.filename
               FROM audit_runs JOIN documents ON documents.id = audit_runs.document_id
               WHERE audit_runs.status = 'completed'
                 AND (
                   audit_runs.id IN (
                     SELECT latest.id FROM audit_runs latest
                     JOIN documents latest_document
                       ON latest_document.id = latest.document_id
                     WHERE latest.status = 'completed'
                       AND NOT EXISTS (
                         SELECT 1 FROM audit_runs newer
                         JOIN documents newer_document
                           ON newer_document.id = newer.document_id
                         WHERE newer_document.sha256 = latest_document.sha256
                           AND newer.status = 'completed'
                           AND COALESCE(newer.completed_at, newer.created_at) >
                               COALESCE(latest.completed_at, latest.created_at)
                       )
                   )
                   OR audit_runs.id IN (
                     SELECT audit_run_id FROM work_tasks WHERE audit_run_id IS NOT NULL
                   )
                 )
               ORDER BY audit_runs.created_at"""
        )
        for audit in audits:
            audit_id = str(audit["id"])
            audit_scope = "test" if audit["model_provider"] == "mock" else "official"
            entity = add_entity(
                "audit",
                "audit_run",
                audit_id,
                f"{audit['filename']} · 高处作业专项审计",
                summary=f"识别场景：{'、'.join(json_load(audit['scenes_json'], []))}",
                data_scope=audit_scope,
                metadata={
                    "audit_run_id": audit_id,
                    "document_id": audit["document_id"],
                    "status": audit["status"],
                    "created_at": audit["created_at"],
                    "completed_at": audit["completed_at"],
                },
                updated_at=audit["completed_at"] or audit["created_at"],
            )
            audit_ids[audit_id] = entity
            audit_scopes[audit_id] = audit_scope
            add_relation(
                project_id,
                "PROJECT_HAS_AUDIT",
                entity,
                source_type="audit_run",
                source_id=audit_id,
                data_scope=audit_scope,
                evidence={"audit_run_id": audit_id, "filename": audit["filename"]},
            )
            if audit["document_id"] in document_ids:
                add_relation(
                    document_ids[audit["document_id"]],
                    "DOCUMENT_HAS_AUDIT",
                    entity,
                    source_type="audit_run",
                    source_id=audit_id,
                    data_scope=audit_scope,
                    evidence={"audit_run_id": audit_id},
                )

        audit_items = self.db.fetch_all(
            """SELECT * FROM audit_items
               WHERE run_id IN (SELECT id FROM audit_runs WHERE status = 'completed')
               ORDER BY created_at"""
        )
        for item in audit_items:
            item_id = str(item["id"])
            if item["run_id"] not in audit_ids:
                continue
            scene = str(item["scene"])
            result = item["final_result"] or item["result"]
            issue = item["final_text"] or item["issue"]
            if result not in {"不符合", "未说明"}:
                continue
            audit_scope = audit_scopes.get(str(item["run_id"]), "official")
            entity = add_entity(
                "audit_issue",
                "audit_item",
                item_id,
                f"{result} · {_short(item['control_title'] or issue, 100)}",
                summary=issue,
                scene=scene,
                data_scope=audit_scope,
                metadata={
                    "audit_item_id": item_id,
                    "audit_run_id": item["run_id"],
                    "result": result,
                    "rule_id": item["rule_id"],
                    "plan_quote": item["plan_quote"],
                    "source_location": item["source_location"],
                    "suggestion": item["suggestion"],
                    "review_status": item["review_status"],
                },
                updated_at=item["updated_at"] or item["created_at"],
            )
            issue_ids[item_id] = entity
            if item["run_id"] in audit_ids:
                add_relation(
                    audit_ids[item["run_id"]],
                    "AUDIT_HAS_ISSUE",
                    entity,
                    source_type="audit_item",
                    source_id=item_id,
                    data_scope=audit_scope,
                    evidence={"issue": issue, "location": item["source_location"]},
                )
            add_relation(
                entity,
                "ISSUE_IN_SCENE",
                scene_entity(scene),
                source_type="audit_item",
                source_id=item_id,
                data_scope=audit_scope,
                evidence={"issue": issue, "location": item["source_location"]},
            )
            if item["rule_id"] in rule_ids:
                basis_items = json_load(item["basis_json"], [])
                selected_basis = next(
                    (
                        basis
                        for basis in basis_items
                        if str(basis.get("rule_id") or "") == item["rule_id"]
                    ),
                    basis_items[0] if basis_items else {},
                )
                add_relation(
                    entity,
                    "ISSUE_BASED_ON_RULE",
                    rule_ids[item["rule_id"]],
                    source_type="audit_item",
                    source_id=item_id,
                    data_scope=audit_scope,
                    evidence={
                        "rule_id": item["rule_id"],
                        "standard_code": selected_basis.get("standard_code"),
                        "standard": selected_basis.get("standard"),
                        "clause": selected_basis.get("clause"),
                        "page": selected_basis.get("pdf_page") or selected_basis.get("page"),
                        "quote": _short(selected_basis.get("quote"), 500),
                        "plan_quote": _short(item["plan_quote"], 500),
                        "source_location": item["source_location"],
                    },
                )

        worker_ids: dict[str, str] = {}

        def worker_entity(worker_ref: str) -> tuple[str | None, str]:
            normalized = worker_ref.strip()
            scope = _scope_for_worker(normalized)
            if not normalized:
                return None, scope
            if normalized not in worker_ids:
                worker_ids[normalized] = add_entity(
                    "worker",
                    "worker_ref",
                    normalized,
                    normalized,
                    summary="工人标识对应的个人任务与学习记录",
                    data_scope=scope,
                    metadata={"worker_ref": normalized},
                )
                add_relation(
                    project_id,
                    "PROJECT_HAS_WORKER",
                    worker_ids[normalized],
                    source_type="worker_ref",
                    source_id=normalized,
                    data_scope=scope,
                    evidence={"worker_ref": normalized},
                )
            return worker_ids[normalized], scope

        task_ids: dict[str, str] = {}
        task_rows_by_id: dict[str, dict[str, Any]] = {}
        tasks = self.db.fetch_all("SELECT * FROM work_tasks ORDER BY created_at")
        for task in tasks:
            task_id = str(task["id"])
            task_rows_by_id[task_id] = task
            worker_id, scope = worker_entity(task["worker_ref"])
            scenes = json_load(task["scenes_json"], [])
            entity = add_entity(
                "task",
                "work_task",
                task_id,
                task["normalized_task"] or task["work_content"],
                summary=(
                    f"{task['work_time']}，{task['work_floor']}，{task['work_location']}；"
                    f"班组：{task['team_ref'] or '未填写'}"
                ),
                scene=str(scenes[0]) if scenes else "",
                data_scope=scope,
                metadata={
                    "task_id": task_id,
                    "worker_ref": task["worker_ref"],
                    "team_ref": task["team_ref"],
                    "work_content": task["work_content"],
                    "location": task["work_location"],
                    "floor": task["work_floor"],
                    "work_time": task["work_time"],
                    "scenes": scenes,
                    "created_at": task["created_at"],
                },
                updated_at=task["created_at"],
            )
            task_ids[task_id] = entity
            add_relation(
                project_id,
                "PROJECT_HAS_TASK",
                entity,
                source_type="work_task",
                source_id=task_id,
                data_scope=scope,
                evidence={"task_id": task_id},
            )
            if worker_id:
                add_relation(
                    entity,
                    "TASK_ASSIGNED_TO",
                    worker_id,
                    source_type="work_task",
                    source_id=task_id,
                    data_scope=scope,
                    evidence={"worker_ref": task["worker_ref"]},
                )
            for scene in scenes:
                add_relation(
                    entity,
                    "TASK_IN_SCENE",
                    scene_entity(str(scene)),
                    source_type="work_task",
                    source_id=f"{task_id}:{scene}",
                    data_scope=scope,
                    evidence={"task_id": task_id, "scene": scene},
                )
            if task["audit_run_id"] in audit_ids:
                add_relation(
                    entity,
                    "TASK_REFERENCES_AUDIT",
                    audit_ids[task["audit_run_id"]],
                    source_type="work_task",
                    source_id=task_id,
                    data_scope=scope,
                    evidence={"audit_run_id": task["audit_run_id"]},
                )

        cards = self.db.fetch_all("SELECT task_id, card_json FROM task_risk_cards")
        for row in cards:
            task_id = str(row["task_id"])
            if task_id not in task_ids:
                continue
            card = json_load(row["card_json"], {})
            scope = _scope_for_worker(str(card.get("worker_ref") or ""))
            for risk in card.get("main_risks") or []:
                risk_entity = add_entity(
                    "risk", "risk_theme", str(risk), str(risk), summary="任务风险卡风险主题"
                )
                add_relation(
                    task_ids[task_id],
                    "TASK_HAS_RISK",
                    risk_entity,
                    source_type="risk_card",
                    source_id=f"{task_id}:risk:{risk}",
                    data_scope=scope,
                    evidence={"task_id": task_id, "risk": risk},
                )
            task_row = task_rows_by_id[task_id]
            current_accidents = self.accidents.search(
                normalized_task=str(task_row["normalized_task"] or ""),
                work_content=str(task_row["work_content"] or ""),
                location=str(task_row["work_location"] or ""),
                scenes=json_load(task_row["scenes_json"], []),
            )
            for accident in current_accidents:
                case_id = str(accident.get("case_id") or "")
                if case_id in accident_ids:
                    add_relation(
                        task_ids[task_id],
                        "TASK_SIMILAR_TO_ACCIDENT",
                        accident_ids[case_id],
                        source_type="risk_card",
                        source_id=f"{task_id}:accident:{case_id}",
                        data_scope=scope,
                        evidence={
                            "task_id": task_id,
                            "case_id": case_id,
                            "score": accident.get("score"),
                            "source_url": accident.get("source_url"),
                        },
                    )

        dynamic_items = self.db.fetch_all(
            """SELECT i.*, r.assessment_date, r.trigger_type, r.created_at AS run_created_at,
                      t.worker_ref
               FROM dynamic_risk_items i
               JOIN dynamic_risk_runs r ON r.id = i.run_id
               JOIN work_tasks t ON t.id = i.task_id
               WHERE r.input_fingerprint <> ''
               ORDER BY r.created_at, i.created_at"""
        )
        run_entities: dict[str, str] = {}
        run_scopes: dict[str, str] = {}
        for item in dynamic_items:
            run_id = str(item["run_id"])
            item_scope = _scope_for_worker(str(item["worker_ref"] or ""))
            if run_id not in run_scopes or item_scope == "official":
                run_scopes[run_id] = item_scope
        for item in dynamic_items:
            task_id = str(item["task_id"])
            if task_id not in task_ids:
                continue
            scope = _scope_for_worker(str(item["worker_ref"] or ""))
            run_id = str(item["run_id"])
            run_scope = run_scopes[run_id]
            if run_id not in run_entities:
                run_entities[run_id] = add_entity(
                    "dynamic_assessment",
                    "dynamic_risk_run",
                    run_id,
                    f"{item['assessment_date']}动态风险评估",
                    summary=f"触发方式：{item['trigger_type']}",
                    data_scope=run_scope,
                    metadata={
                        "run_id": run_id,
                        "assessment_date": item["assessment_date"],
                        "trigger_type": item["trigger_type"],
                    },
                    updated_at=item["run_created_at"],
                )
                add_relation(
                    project_id,
                    "PROJECT_HAS_ASSESSMENT",
                    run_entities[run_id],
                    source_type="dynamic_risk_run",
                    source_id=run_id,
                    data_scope=run_scope,
                    evidence={"assessment_date": item["assessment_date"]},
                )
            item_id = str(item["id"])
            triggers = json_load(item["triggers_json"], [])
            interventions = json_load(item["interventions_json"], [])
            risk_entity = add_entity(
                "dynamic_risk",
                "dynamic_risk_item",
                item_id,
                f"{item['risk_level'].upper()} · {task_rows_by_id[task_id]['normalized_task']}",
                summary=item["summary"],
                data_scope=scope,
                metadata={
                    "item_id": item_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "risk_level": item["risk_level"],
                    "priority_score": item["priority_score"],
                    "triggers": triggers,
                    "interventions": interventions,
                },
                updated_at=item["created_at"],
            )
            add_relation(
                run_entities[run_id],
                "ASSESSMENT_HAS_RISK",
                risk_entity,
                source_type="dynamic_risk_item",
                source_id=item_id,
                data_scope=scope,
                evidence={"risk_level": item["risk_level"]},
            )
            add_relation(
                risk_entity,
                "RISK_EVALUATES_TASK",
                task_ids[task_id],
                source_type="dynamic_risk_item",
                source_id=item_id,
                data_scope=scope,
                evidence={"task_id": task_id, "triggers": triggers},
            )
            for index, intervention in enumerate(interventions):
                intervention_entity = add_entity(
                    "intervention",
                    "dynamic_intervention",
                    str(intervention),
                    str(intervention),
                    summary="动态评估生成的现场干预建议",
                    data_scope=scope,
                )
                add_relation(
                    risk_entity,
                    "RISK_RECOMMENDS_INTERVENTION",
                    intervention_entity,
                    source_type="dynamic_risk_item",
                    source_id=f"{item_id}:intervention:{index}",
                    data_scope=scope,
                    evidence={"intervention": intervention},
                )
        qa_ids: dict[str, str] = {}
        qa_rows = self.db.fetch_all("SELECT * FROM safety_qa_records ORDER BY created_at")
        for qa in qa_rows:
            qa_id = str(qa["id"])
            worker_id, scope = worker_entity(qa["worker_ref"])
            entity = add_entity(
                "qa",
                "safety_qa",
                qa_id,
                qa["question"],
                summary=qa["answer"],
                data_scope=scope,
                metadata={
                    "qa_id": qa_id,
                    "worker_ref": qa["worker_ref"],
                    "task_id": qa["task_id"],
                    "answer_status": qa["answer_status"],
                    "created_at": qa["created_at"],
                },
                updated_at=qa["created_at"],
            )
            qa_ids[qa_id] = entity
            if worker_id:
                add_relation(
                    entity,
                    "QA_ASKED_BY",
                    worker_id,
                    source_type="safety_qa",
                    source_id=qa_id,
                    data_scope=scope,
                    evidence={"question": qa["question"]},
                )
            if qa["task_id"] in task_ids:
                add_relation(
                    entity,
                    "QA_ABOUT_TASK",
                    task_ids[qa["task_id"]],
                    source_type="safety_qa",
                    source_id=qa_id,
                    data_scope=scope,
                    evidence={"question": qa["question"]},
                )
            for evidence in json_load(qa["evidence_json"], []):
                source_id = str(evidence.get("source_id") or "")
                target = rule_ids.get(source_id) or accident_ids.get(source_id)
                if target:
                    add_relation(
                        entity,
                        "QA_SUPPORTED_BY",
                        target,
                        source_type="safety_qa_evidence",
                        source_id=f"{qa_id}:{source_id}",
                        data_scope=scope,
                        evidence=evidence,
                    )

        with self.db.connect() as connection:
            connection.execute("DELETE FROM knowledge_relations")
            connection.execute("DELETE FROM knowledge_entities")
            connection.executemany(
                """INSERT INTO knowledge_entities
                   (id, entity_type, name, summary, scene, data_scope, source_type,
                    source_id, metadata_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entities.values(),
            )
            connection.executemany(
                """INSERT INTO knowledge_relations
                   (id, source_entity_id, relation_type, target_entity_id, label,
                    evidence_json, data_scope, source_type, source_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                relations.values(),
            )
        return {"entity_count": len(entities), "relation_count": len(relations)}

    @staticmethod
    def _public_entity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **{key: value for key, value in row.items() if key != "metadata_json"},
            "metadata": json_load(row.get("metadata_json"), {}),
        }

    @staticmethod
    def _public_relation(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **{key: value for key, value in row.items() if key != "evidence_json"},
            "evidence": json_load(row.get("evidence_json"), {}),
        }

    def search(
        self,
        *,
        query: str = "",
        entity_type: str = "",
        scene: str = "",
        include_test: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        clauses = ["entity_type <> 'worker'"]
        parameters: list[Any] = []
        if not include_test:
            clauses.append("data_scope <> 'test'")
        if entity_type:
            clauses.append("entity_type = ?")
            parameters.append(entity_type)
        if scene:
            clauses.append("scene LIKE ?")
            parameters.append(f"%{scene.strip()}%")
        rows = self.db.fetch_all(
            f"""SELECT * FROM knowledge_entities
                WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, name LIMIT 3000""",
            parameters,
        )
        relation_scope = "" if include_test else "WHERE data_scope <> 'test'"
        relation_counts = {
            row["entity_id"]: int(row["count"])
            for row in self.db.fetch_all(
                f"""SELECT entity_id, COUNT(*) count FROM (
                      SELECT source_entity_id entity_id, data_scope FROM knowledge_relations
                      UNION ALL
                      SELECT target_entity_id entity_id, data_scope FROM knowledge_relations
                    ) {relation_scope}
                    GROUP BY entity_id"""
            )
        }
        normalized = query.strip().lower()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            name = str(row["name"]).lower()
            summary = str(row["summary"]).lower()
            row_scene = str(row["scene"]).lower()
            if normalized:
                score = 0.0
                if normalized in name:
                    score += 0.7
                if normalized in summary:
                    score += 0.25
                if normalized in row_scene:
                    score += 0.2
                tokens = [token for token in re.split(r"\s+", normalized) if token]
                if len(tokens) == 1 and len(normalized) > 2:
                    tokens = [
                        normalized[index : index + 2]
                        for index in range(len(normalized) - 1)
                    ]
                haystack = f"{name} {summary} {row_scene}"
                domain_terms = [term for term in DOMAIN_SEARCH_TERMS if term in normalized]
                if "电梯井" in domain_terms and "洞口" in domain_terms:
                    domain_terms.remove("洞口")
                if any(
                    term in domain_terms
                    for term in ("附着式升降脚手架", "悬挑脚手架", "落地式脚手架")
                ) and "脚手架" in domain_terms:
                    domain_terms.remove("脚手架")
                if len(domain_terms) >= 2 and any(term not in haystack for term in domain_terms):
                    continue
                token_hits = sum(token in haystack for token in dict.fromkeys(tokens))
                if normalized not in haystack and len(normalized) >= 4 and token_hits < 2:
                    continue
                score += 0.08 * token_hits
                if score <= 0:
                    continue
            else:
                score = 0.1
            item = self._public_entity(row)
            item["relation_count"] = relation_counts.get(row["id"], 0)
            ranked.append((score, item))
        ranked.sort(key=lambda value: (-value[0], -value[1]["relation_count"], value[1]["name"]))
        return {
            "query": query,
            "total": len(ranked),
            "items": [item for _, item in ranked[:limit]],
        }

    def entity_graph(
        self, entity_id: str, *, include_test: bool = False, limit: int = 30
    ) -> dict[str, Any]:
        center = self.db.fetch_one("SELECT * FROM knowledge_entities WHERE id = ?", (entity_id,))
        if center is None or (center["data_scope"] == "test" and not include_test):
            raise KeyError("知识实体不存在")
        if center["entity_type"] == "worker":
            raise KeyError("工人个人知识请使用工人标识精确查询")
        scope_sql = "" if include_test else "AND data_scope <> 'test'"
        relation_rows = self.db.fetch_all(
            f"""SELECT * FROM knowledge_relations
                WHERE (source_entity_id = ? OR target_entity_id = ?) {scope_sql}
                  AND source_entity_id NOT IN (
                    SELECT id FROM knowledge_entities WHERE entity_type = 'worker'
                  )
                  AND target_entity_id NOT IN (
                    SELECT id FROM knowledge_entities WHERE entity_type = 'worker'
                  )
                ORDER BY relation_type, created_at DESC LIMIT ?""",
            (entity_id, entity_id, limit),
        )
        node_ids = {entity_id}
        for row in relation_rows:
            node_ids.update((row["source_entity_id"], row["target_entity_id"]))
        placeholders = ",".join("?" for _ in node_ids)
        nodes = self.db.fetch_all(
            f"SELECT * FROM knowledge_entities WHERE id IN ({placeholders})", list(node_ids)
        )
        return {
            "center": self._public_entity(center),
            "nodes": [self._public_entity(row) for row in nodes],
            "relations": [self._public_relation(row) for row in relation_rows],
        }

    def overview(self, *, include_test: bool = False) -> dict[str, Any]:
        entity_scope = "1 = 1" if include_test else "data_scope <> 'test'"
        relation_scope = "1 = 1" if include_test else "r.data_scope <> 'test'"
        counts = self.db.fetch_all(
            f"""SELECT entity_type, COUNT(*) count FROM knowledge_entities
                WHERE {entity_scope} GROUP BY entity_type ORDER BY count DESC"""
        )
        relation_count = self.db.fetch_one(
            f"SELECT COUNT(*) count FROM knowledge_relations r WHERE {relation_scope}"
        )

        def top_targets(relation_types: tuple[str, ...], limit: int = 6) -> list[dict[str, Any]]:
            placeholders = ",".join("?" for _ in relation_types)
            return self.db.fetch_all(
                f"""SELECT e.id, e.name, e.entity_type, COUNT(*) count
                    FROM knowledge_relations r
                    JOIN knowledge_entities e ON e.id = r.target_entity_id
                    WHERE r.relation_type IN ({placeholders}) AND {relation_scope}
                    GROUP BY e.id, e.name, e.entity_type
                    ORDER BY count DESC, e.name LIMIT ?""",
                (*relation_types, limit),
            )

        recent = self.db.fetch_all(
            f"""SELECT * FROM knowledge_entities
                WHERE {entity_scope} AND entity_type IN
                    ('task', 'audit_issue', 'qa')
                ORDER BY updated_at DESC LIMIT 10"""
        )
        scope_counts = self.db.fetch_all(
            """SELECT data_scope, COUNT(*) count FROM knowledge_entities
               GROUP BY data_scope ORDER BY data_scope"""
        )
        return {
            "project_name": self.project_name,
            "entity_count": sum(int(row["count"]) for row in counts),
            "relation_count": int((relation_count or {}).get("count", 0)),
            "entity_type_counts": counts,
            "data_scope_counts": scope_counts,
            "top_scenes": top_targets(("TASK_IN_SCENE", "ISSUE_IN_SCENE")),
            "top_risks": top_targets(("TASK_HAS_RISK",)),
            "recent_items": [self._public_entity(row) for row in recent],
        }

    def worker_knowledge(self, worker_ref: str) -> dict[str, Any]:
        normalized = worker_ref.strip()
        if not normalized:
            raise ValueError("请填写工人标识")
        worker = self.db.fetch_one(
            """SELECT * FROM knowledge_entities
               WHERE entity_type = 'worker' AND source_type = 'worker_ref' AND source_id = ?""",
            (normalized,),
        )
        if worker is None:
            raise KeyError("未找到该工人的任务或学习记录")
        rows = self.db.fetch_all(
            """SELECT r.*, source.entity_type source_type_name,
                      source.name source_name, target.entity_type target_type_name,
                      target.name target_name
               FROM knowledge_relations r
               JOIN knowledge_entities source ON source.id = r.source_entity_id
               JOIN knowledge_entities target ON target.id = r.target_entity_id
               WHERE r.source_entity_id = ? OR r.target_entity_id = ?
               ORDER BY r.created_at DESC""",
            (worker["id"], worker["id"]),
        )
        related: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            other_id = (
                row["target_entity_id"]
                if row["source_entity_id"] == worker["id"]
                else row["source_entity_id"]
            )
            other = self.db.fetch_one("SELECT * FROM knowledge_entities WHERE id = ?", (other_id,))
            if other:
                related[other["entity_type"]].append(self._public_entity(other))
        for values in related.values():
            seen: set[str] = set()
            unique_values = []
            for item in values:
                if item["id"] not in seen:
                    seen.add(item["id"])
                    unique_values.append(item)
            values[:] = unique_values
        return {
            "worker": self._public_entity(worker),
            "summary": {
                "task_count": len(related.get("task", [])),
                "qa_count": len(related.get("qa", [])),
            },
            "tasks": related.get("task", [])[:20],
            "qa_records": related.get("qa", [])[:20],
        }
