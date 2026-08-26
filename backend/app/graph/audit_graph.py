from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..enums import ApplicabilityStatus, AuditResult, AuditStatus, PlanObligation
from ..repositories import Repository, utc_now
from ..schemas import LLMDecision
from ..services.control_applicability import (
    batch_controls,
    build_control_candidates,
    merge_control_evidence,
)
from ..services.deterministic_rules import compare_numeric_rule
from ..services.document_scope import assess_construction_scheme_document
from ..services.evidence_dedup import deduplicate_standard_evidence
from ..services.model_provider import AuditModel, requirement_items
from ..services.numeric_validation import validate_numeric_comparison
from ..services.retrieval import (
    explicit_semantic_compliance_evidence,
    retrieve_global_counter_evidence,
    retrieve_plan_evidence,
    share_semantic_control_evidence,
)
from ..services.rule_applicability import assess_rule_applicability
from ..services.rule_bundles import build_rule_bundles
from ..services.rule_scope import classify_plan_obligation, filter_rules_for_document_scope
from ..services.scene_router import (
    build_scene_keyword_hints,
    build_scene_context_batches,
    filter_model_scene_instances,
    merge_scene_instances,
)
from ..services.standard_rag import StandardRAGService


class AuditState(TypedDict, total=False):
    run_id: str
    document_id: str
    document_filename: str
    segments: list[dict[str, Any]]
    scenes: list[str]
    scene_instances: list[dict[str, Any]]
    scene_standard_evidence: dict[str, list[dict[str, Any]]]
    controls: list[dict[str, Any]]
    selected_controls: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    bundles: list[dict[str, Any]]
    generated_items: list[dict[str, Any]]


class AuditGraph:
    def __init__(
        self,
        repository: Repository,
        model: AuditModel,
        standard_rag: StandardRAGService | None = None,
        max_workers: int = 4,
    ):
        self.repository = repository
        self.model = model
        self.standard_rag = standard_rag
        self.max_workers = max(1, max_workers)
        builder = StateGraph(AuditState)
        builder.add_node("load_document", self._load_document)
        builder.add_node("route_scene_instances", self._route_scene_instances)
        builder.add_node(
            "discover_scene_standard_evidence", self._discover_scene_standard_evidence
        )
        builder.add_node("select_candidate_controls", self._select_candidate_controls)
        builder.add_node("assess_control_applicability", self._assess_control_applicability)
        builder.add_node("expand_candidate_rules", self._expand_candidate_rules)
        builder.add_node("assess_rule_applicability", self._assess_rule_applicability)
        builder.add_node("build_rule_bundles", self._build_rule_bundles)
        builder.add_node("audit_rule_bundles", self._audit_rule_bundles)
        builder.add_node("validate_persistence", self._validate_persistence)
        builder.add_edge(START, "load_document")
        builder.add_edge("load_document", "route_scene_instances")
        builder.add_edge("route_scene_instances", "discover_scene_standard_evidence")
        builder.add_edge("discover_scene_standard_evidence", "select_candidate_controls")
        builder.add_edge("select_candidate_controls", "assess_control_applicability")
        builder.add_edge("assess_control_applicability", "expand_candidate_rules")
        builder.add_edge("expand_candidate_rules", "assess_rule_applicability")
        builder.add_edge("assess_rule_applicability", "build_rule_bundles")
        builder.add_edge("build_rule_bundles", "audit_rule_bundles")
        builder.add_edge("audit_rule_bundles", "validate_persistence")
        builder.add_edge("validate_persistence", END)
        self.compiled = builder.compile()

    def invoke(self, run_id: str, document_id: str) -> AuditState:
        try:
            return self.compiled.invoke({"run_id": run_id, "document_id": document_id})
        except Exception as exc:
            self.repository.update_audit_run(
                run_id,
                status=AuditStatus.FAILED.value,
                current_node="failed",
                error=str(exc)[:2000],
                completed_at=utc_now(),
            )
            raise

    def _mark_node(self, state: AuditState, node: str, status: AuditStatus) -> None:
        self.repository.update_audit_run(state["run_id"], status=status.value, current_node=node)

    def _load_document(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "load_document", AuditStatus.ROUTING)
        document = self.repository.get_document(state["document_id"])
        if document["status"] != "parsed":
            raise ValueError("施工方案尚未解析，请先调用解析接口")
        segments = self.repository.list_segments(state["document_id"])
        if not segments:
            raise ValueError("施工方案没有可审计片段")
        return {"segments": segments, "document_filename": document["filename"]}

    def _route_scene_instances(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "route_scene_instances", AuditStatus.ROUTING)
        scope = assess_construction_scheme_document(
            state.get("document_filename", ""), state["segments"]
        )
        if not scope.auditable:
            self.repository.update_audit_run(
                state["run_id"],
                scenes_json="[]",
                scene_instances_json="[]",
                rule_limit=0,
                completed_rules=0,
            )
            return {"scenes": [], "scene_instances": []}
        available = self.repository.list_scenes()
        keyword_hints = build_scene_keyword_hints(state["segments"], available)
        batches = build_scene_context_batches(state["segments"])
        model_instances: list[dict[str, Any]] = []
        if self.model.provider_name != "mock" and batches:
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(batches)),
                thread_name_prefix="scene-instance-routing",
            ) as pool:
                futures = {}
                for batch in batches:
                    batch_ids = set(batch["segment_ids"])
                    local_hints = [
                        item
                        for item in keyword_hints
                        if batch_ids.intersection(item["segment_ids"])
                    ]
                    future = pool.submit(
                        self.model.identify_scene_instances,
                        batch["context"],
                        available,
                        local_hints,
                    )
                    futures[future] = batch
                for future in as_completed(futures):
                    try:
                        model_instances.extend(future.result())
                    except Exception:
                        continue
        model_instances = filter_model_scene_instances(model_instances, state["segments"])
        # In real audits keywords only recall passages for the model. The final
        # structured instances must all be confirmed by a model response. Mock mode
        # keeps the hints as a local-development fallback because it has no model.
        confirmed_instances = keyword_hints if self.model.provider_name == "mock" else []
        instances = merge_scene_instances(
            confirmed_instances, model_instances, state["segments"], available
        )
        scenes = sorted({item["scene"] for item in instances})
        self.repository.update_audit_run(
            state["run_id"],
            scenes_json=json.dumps(scenes, ensure_ascii=False),
            scene_instances_json=json.dumps(instances, ensure_ascii=False),
        )
        return {"scenes": scenes, "scene_instances": instances}

    def _discover_scene_standard_evidence(
        self, state: AuditState
    ) -> dict[str, Any]:
        """Retrieve standards from confirmed plan text without using atomic rules.

        This is the independent discovery lane. It may support routing and expose
        rule-library gaps, but it never creates an audit finding by itself.
        """
        self._mark_node(state, "discover_scene_standard_evidence", AuditStatus.ROUTING)
        if not self.standard_rag or not state.get("scene_instances"):
            return {"scene_standard_evidence": {}}
        segment_by_id = {str(item["id"]): item for item in state["segments"]}
        object_query_terms = {
            "template_support_scaffold": "模板支撑架 支撑脚手架 支模架",
            "work_scaffold": "作业脚手架 外脚手架",
            "mixed_scaffold": "模板支撑架 作业脚手架",
            "unspecified_scaffold": "脚手架",
        }

        def retrieve(instance: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
            anchor_ids = list(
                dict.fromkeys(
                    str(value)
                    for value in instance.get(
                        "anchor_segment_ids", instance.get("segment_ids", [])
                    )
                )
            )[:4]
            plan_quotes = [
                str(segment_by_id[segment_id].get("text", ""))[:1200]
                for segment_id in anchor_ids
                if segment_id in segment_by_id
            ]
            query = "\n".join(
                value
                for value in (
                    str(instance.get("scene") or ""),
                    str(instance.get("title") or ""),
                    object_query_terms.get(
                        str(instance.get("object_type") or ""),
                        str(instance.get("object_type") or ""),
                    ),
                    *plan_quotes,
                )
                if value.strip()
            )[:4800]
            if not query:
                return str(instance["id"]), []
            candidates = self.standard_rag.discover(
                query,
                limit=self.standard_rag.settings.scene_rag_limit,
            )
            return str(instance["id"]), candidates

        discovered: dict[str, list[dict[str, Any]]] = {}
        instances = state["scene_instances"]
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(instances)),
            thread_name_prefix="scene-standard-discovery",
        ) as pool:
            futures = [pool.submit(retrieve, instance) for instance in instances]
            for future in as_completed(futures):
                instance_id, candidates = future.result()
                discovered[instance_id] = candidates
        return {"scene_standard_evidence": discovered}

    def _select_candidate_controls(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "select_candidate_controls", AuditStatus.ROUTING)
        if not state["scenes"]:
            self.repository.update_audit_run(
                state["run_id"], rule_limit=0, completed_rules=0
            )
            return {"controls": []}
        base_rules = self.repository.list_enabled_rules(state["scenes"])
        base_rules = [
            rule
            for rule in base_rules
            if not any(
                excluded in rule["standard_status"]
                for excluded in ("待实施", "不完整", "废止", "待核验")
            )
        ]
        base_rules = filter_rules_for_document_scope(base_rules, state["segments"])
        controls = build_control_candidates(
            base_rules, state["scene_instances"], state["segments"]
        )
        return {"controls": controls}

    def _assess_control_applicability(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "assess_control_applicability", AuditStatus.ROUTING)
        controls_by_instance: dict[str, list[dict[str, Any]]] = {}
        for control in state.get("controls", []):
            controls_by_instance.setdefault(control["instance"]["id"], []).append(control)
        selected: list[dict[str, Any]] = []
        tasks: list[
            tuple[
                dict[str, Any],
                list[dict[str, Any]],
                list[dict[str, Any]],
                list[dict[str, Any]],
            ]
        ] = []
        for instance_controls in controls_by_instance.values():
            for control_batch in batch_controls(instance_controls):
                tasks.append(
                    (
                        control_batch[0]["instance"],
                        control_batch,
                        merge_control_evidence(control_batch),
                        state.get("scene_standard_evidence", {}).get(
                            str(control_batch[0]["instance"]["id"]), []
                        ),
                    )
                )

        def route_batch(task):
            instance, control_batch, evidence, standard_candidates = task
            rule_ids_by_clause: dict[tuple[str, str], list[str]] = {}
            for control in control_batch:
                for rule in control["rules"]:
                    key = (
                        str(rule.get("standard_code") or "").replace(" ", "").upper(),
                        str(rule.get("clause") or "").strip(),
                    )
                    rule_ids_by_clause.setdefault(key, []).append(str(rule["rule_id"]))
            standard_candidates = [
                {
                    **item,
                    "matched_rule_ids": rule_ids_by_clause.get(
                        (
                            str(item.get("standard_code") or "")
                            .replace(" ", "")
                            .upper(),
                            str(item.get("clause") or "").strip(),
                        ),
                        [],
                    ),
                }
                for item in standard_candidates
            ]
            # Only discoveries that map to at least one rule in this control
            # batch are useful to the applicability model. Unmatched vector
            # neighbours are retrieval diagnostics, not audit context.
            standard_candidates = [
                item for item in standard_candidates if item["matched_rule_ids"]
            ]
            try:
                decisions = self.model.select_applicable_controls(
                    instance, control_batch, evidence, standard_candidates
                )
            except Exception as exc:
                # The model adapter already retries malformed structured output once.
                # If both attempts fail, do not turn every rule into an applicable one;
                # that creates dozens of false omissions from a transport failure.
                decisions = {
                    control["key"]: {
                        "status": "uncertain",
                        "reason": f"业务控制项路由两次失败，未展开：{str(exc)[:300]}",
                        "applicable_rule_ids": [],
                        "evidence_ids": [],
                    }
                    for control in control_batch
                }
            return control_batch, decisions

        if tasks:
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(tasks)),
                thread_name_prefix="control-applicability",
            ) as pool:
                futures = [pool.submit(route_batch, task) for task in tasks]
                for future in as_completed(futures):
                    control_batch, decisions = future.result()
                    for control in control_batch:
                        decision = decisions[control["key"]]
                        if decision["status"] != "applicable":
                            continue
                        selected_rule_ids = set(decision["applicable_rule_ids"])
                        if not selected_rule_ids:
                            continue
                        selected.append(
                            {
                                **control,
                                "rules": [
                                    rule
                                    for rule in control["rules"]
                                    if rule["rule_id"] in selected_rule_ids
                                ],
                                "gate": decision,
                            }
                        )
        selected.sort(
            key=lambda item: (
                item["scene"],
                item["instance"]["location"],
                item["title"],
            )
        )
        return {"selected_controls": selected}

    def _expand_candidate_rules(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "expand_candidate_rules", AuditStatus.ROUTING)
        segment_by_id = {str(item["id"]): item for item in state["segments"]}
        rules: list[dict[str, Any]] = []
        for control in state.get("selected_controls", []):
            instance = control["instance"]
            for base_rule in control["rules"]:
                rule = dict(base_rule)
                rule["_scene_instances"] = [instance]
                rule["_scene_instance"] = instance
                rule["_preferred_segment_ids"] = list(instance["segment_ids"])
                rule["_scene_evidence"] = [
                    segment_by_id[str(segment_id)]
                    for segment_id in instance.get(
                        "anchor_segment_ids", instance["segment_ids"][:1]
                    )
                    if str(segment_id) in segment_by_id
                ][:4]
                rule["_audit_unit_id"] = f"{rule['rule_id']}@{instance['id']}"
                rule["_control_gate"] = control["gate"]
                rule["_scene_standard_evidence"] = state.get(
                    "scene_standard_evidence", {}
                ).get(str(instance["id"]), [])
                rules.append(rule)
        rules.sort(
            key=lambda rule: (
                rule["scene"],
                rule["_scene_instance"]["location"],
                rule["rule_id"],
            )
        )
        self.repository.update_audit_run(
            state["run_id"],
            rule_limit=len(rules),
            completed_rules=0,
            bundle_count=0,
            completed_bundles=0,
            model_call_count=0,
        )
        return {"rules": rules}

    def _select_candidate_rules(self, state: AuditState) -> dict[str, Any]:
        """Backward-compatible deterministic expansion used by focused unit tests."""
        self._mark_node(state, "select_candidate_rules", AuditStatus.ROUTING)
        base_rules = self.repository.list_enabled_rules(state["scenes"])
        rules_by_scene: dict[str, list[dict[str, Any]]] = {}
        for rule in base_rules:
            rules_by_scene.setdefault(rule["scene"], []).append(rule)
        rules: list[dict[str, Any]] = []
        for instance in state["scene_instances"]:
            for base_rule in rules_by_scene.get(instance["scene"], []):
                rule = dict(base_rule)
                rule["_scene_instances"] = [instance]
                rule["_scene_instance"] = instance
                rule["_preferred_segment_ids"] = list(instance["segment_ids"])
                rule["_audit_unit_id"] = f"{rule['rule_id']}@{instance['id']}"
                rules.append(rule)
        rules.sort(
            key=lambda rule: (
                rule["scene"],
                rule["_scene_instance"]["location"],
                rule["rule_id"],
            )
        )
        self.repository.update_audit_run(
            state["run_id"], rule_limit=len(rules), completed_rules=0
        )
        return {"rules": rules}

    def _assess_rule_applicability(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "assess_rule_applicability", AuditStatus.ROUTING)
        segment_by_id = {segment["id"]: segment for segment in state["segments"]}
        for rule in state["rules"]:
            scoped_evidence = [
                segment_by_id[segment_id]
                for segment_id in rule["_preferred_segment_ids"]
                if segment_id in segment_by_id
            ]
            evidence = retrieve_plan_evidence(
                rule,
                state["segments"],
                preferred_segment_ids=set(rule["_preferred_segment_ids"]),
            )
            counter_evidence = retrieve_global_counter_evidence(
                rule,
                state["segments"],
                limit=4,
                exclude_ids={str(item["id"]) for item in evidence},
            )
            evidence = [*evidence, *counter_evidence]
            applicability = assess_rule_applicability(
                rule, [*scoped_evidence, *evidence], rule["_scene_instances"]
            )
            evidence_ids = {item["id"] for item in evidence}
            for segment_id in applicability.supporting_segment_ids:
                if segment_id not in evidence_ids and segment_id in segment_by_id:
                    evidence.append({**segment_by_id[segment_id], "score": 0.95})
                    evidence_ids.add(segment_id)
            scope = classify_plan_obligation(rule)
            rule["_plan_evidence"] = evidence
            rule["_global_counter_evidence"] = counter_evidence
            rule["_applicability"] = {
                "status": applicability.status.value,
                "reason": applicability.reason,
                "supporting_segment_ids": list(applicability.supporting_segment_ids),
            }
            rule["_plan_scope"] = {
                "obligation": scope.obligation.value,
                "reason": scope.reason,
            }
        share_semantic_control_evidence(state["rules"])
        return {"rules": state["rules"]}

    def _build_rule_bundles(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "build_rule_bundles", AuditStatus.ROUTING)
        bundles = build_rule_bundles(state["rules"], max_size=6)
        self.repository.update_audit_run(
            state["run_id"], bundle_count=len(bundles), completed_bundles=0
        )
        return {"bundles": bundles}

    def _retrieve_standard_evidence(
        self, rule: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not self.standard_rag:
            return []
        # Exact clause binding is deterministic provenance, not semantic
        # discovery. If that binding is unavailable, the vector fallback uses
        # only scene metadata and plan text, never the rule's expected answer.
        query = " ".join(
            [
                rule["scene"],
                str(rule.get("_scene_instance", {}).get("title", "")),
                str(rule.get("_scene_instance", {}).get("object_type", "")),
                *(item["text"] for item in evidence[:2]),
            ]
        )
        bound = self.standard_rag.retrieve(
            query,
            standard_code=rule["standard_code"],
            clause=rule["clause"],
            expected_page=int(rule.get("pdf_page") or 0) or None,
            limit=1,
        )
        discovered = list(rule.get("_scene_standard_evidence", []))
        discovered_ids = {str(item.get("id")) for item in discovered}
        discovered_keys = {
            (
                str(item.get("standard_code") or "").replace(" ", "").upper(),
                str(item.get("clause") or "").strip(),
            )
            for item in discovered
        }
        rule_key = (
            str(rule.get("standard_code") or "").replace(" ", "").upper(),
            str(rule.get("clause") or "").strip(),
        )
        annotated_bound = []
        for item in bound:
            key = (
                str(item.get("standard_code") or "").replace(" ", "").upper(),
                str(item.get("clause") or "").strip(),
            )
            annotated_bound.append(
                {
                    **item,
                    "independent_scene_match": (
                        str(item.get("id")) in discovered_ids or key in discovered_keys
                    ),
                }
            )
        # Only a scene-vector clause that maps back to this exact curated rule
        # may enter the final rule audit. Unmatched discoveries remain visible in
        # the control-routing model log as rule-gap hints, but cannot create noise
        # or findings for a different rule.
        supplementary = [
            {**item, "independent_scene_match": True}
            for item in discovered
            if str(item.get("id")) not in {str(row.get("id")) for row in bound}
            and (
                str(item.get("standard_code") or "").replace(" ", "").upper(),
                str(item.get("clause") or "").strip(),
            )
            == rule_key
        ][:1]
        return deduplicate_standard_evidence(
            [*annotated_bound, *supplementary], limit=1
        )

    def _deterministic_decision(
        self, rule: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        applicability = rule["_applicability"]
        scope = rule["_plan_scope"]
        if scope["obligation"] == PlanObligation.EXTERNAL_ONLY.value:
            return (
                {
                    "result": AuditResult.OUT_OF_SCOPE,
                    "applicability_status": ApplicabilityStatus(applicability["status"]),
                    "applicability_reason": applicability["reason"],
                    "plan_obligation": PlanObligation.EXTERNAL_ONLY,
                    "plan_obligation_reason": scope["reason"],
                    "issue": "本条核验对象属于产品证明、检测结果或施工记录，不作为方案正文缺项。",
                    "risk_consequence": "需在相应材料验收、现场检查或施工记录环节另行核验。",
                    "suggestion": "不要求仅为本条在施工方案中补写检测数据或证明文件。",
                    "selected_plan_segment_ids": [],
                    "confidence": 0.98,
                },
                {"provider": "deterministic_scope", "reason": scope["reason"]},
            )
        if applicability["status"] == ApplicabilityStatus.NOT_APPLICABLE.value:
            return (
                {
                    "result": AuditResult.NOT_APPLICABLE,
                    "applicability_status": ApplicabilityStatus.NOT_APPLICABLE,
                    "applicability_reason": applicability["reason"],
                    "plan_obligation": PlanObligation(scope["obligation"]),
                    "plan_obligation_reason": scope["reason"],
                    "issue": "方案证据明确表明该规则的适用对象或触发条件不成立。",
                    "risk_consequence": "本条不属于当前方案已识别作业类型，不作为缺项。",
                    "suggestion": "若后续变更作业类型，应重新审查。",
                    "selected_plan_segment_ids": applicability["supporting_segment_ids"],
                    "confidence": 0.92,
                },
                {"provider": "deterministic_applicability", "reason": applicability["reason"]},
            )
        semantic_evidence = explicit_semantic_compliance_evidence(rule, evidence)
        if semantic_evidence is not None:
            return (
                {
                    "result": AuditResult.COMPLIANT,
                    "applicability_status": ApplicabilityStatus.APPLICABLE,
                    "applicability_reason": "方案已明确涉及该作业条件。",
                    "plan_obligation": PlanObligation(scope["obligation"]),
                    "plan_obligation_reason": scope["reason"],
                    "issue": "方案已明确规定6级及以上大风时停止高处或架上作业。",
                    "risk_consequence": rule["hazards"] or "可能增加高处作业安全风险。",
                    "suggestion": "保持现有恶劣天气停工要求并落实执行。",
                    "selected_plan_segment_ids": [semantic_evidence["id"]],
                    "decision_basis": "text",
                    "numeric_comparison": None,
                    "confidence": 0.98,
                },
                {
                    "provider": "deterministic_semantic_equivalence",
                    "reason": "同义规则共享的全文证据同时包含风级、停工动作和高处作业对象。",
                },
            )
        numeric = compare_numeric_rule(rule, evidence)
        if numeric is not None:
            return (
                {
                    "result": numeric.result,
                    "applicability_status": ApplicabilityStatus.APPLICABLE,
                    "applicability_reason": "方案与规则存在可直接追溯的同类数值边界。",
                    "plan_obligation": PlanObligation(scope["obligation"]),
                    "plan_obligation_reason": scope["reason"],
                    "issue": numeric.issue,
                    "risk_consequence": rule["hazards"]
                    or "不满足规范数值边界可能增加高处作业安全风险。",
                    "suggestion": numeric.suggestion,
                    "selected_plan_segment_ids": [numeric.segment_id],
                    "decision_basis": "deterministic_numeric",
                    "numeric_comparison": None,
                    "confidence": numeric.confidence,
                },
                {
                    "provider": "deterministic_numeric",
                    "reason": "规则与方案均包含唯一且同向的数值边界。",
                },
            )
        return None

    def _fallback_decision(self, rule: dict[str, Any], message: str) -> dict[str, Any]:
        return LLMDecision(
            result=AuditResult.NEEDS_HUMAN_REVIEW,
            applicability_status=ApplicabilityStatus.UNCERTAIN,
            applicability_reason="规则包模型调用失败，无法确认具体触发条件。",
            plan_obligation=PlanObligation(rule["_plan_scope"]["obligation"]),
            plan_obligation_reason=rule["_plan_scope"]["reason"],
            issue=message,
            risk_consequence=rule["hazards"] or "可能增加高处作业安全风险。",
            suggestion="请专业审查人员核对方案原文与规范条款。",
            confidence=0.0,
        ).model_dump()

    def _validate_decision(
        self,
        decision: dict[str, Any],
        rule: dict[str, Any],
        valid_evidence_ids: set[str],
        plan_sources: dict[str, str] | None = None,
        standard_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        selected_ids = [
            segment_id
            for segment_id in decision.get("selected_plan_segment_ids", [])
            if segment_id in valid_evidence_ids
        ]
        decision["selected_plan_segment_ids"] = selected_ids
        result = decision["result"]
        applicability = decision["applicability_status"]
        obligation = decision["plan_obligation"]
        result_value = result.value if isinstance(result, AuditResult) else result
        applicability_value = (
            applicability.value if isinstance(applicability, ApplicabilityStatus) else applicability
        )
        obligation_value = (
            obligation.value if isinstance(obligation, PlanObligation) else obligation
        )
        deterministic_scope = rule.get("_plan_scope", {}).get("obligation")
        if deterministic_scope in {
            PlanObligation.MUST_STATE.value,
            PlanObligation.REFERENCE_ONLY.value,
            PlanObligation.EXTERNAL_ONLY.value,
        }:
            obligation_value = deterministic_scope
            decision["plan_obligation"] = PlanObligation(deterministic_scope)
            decision["plan_obligation_reason"] = rule["_plan_scope"]["reason"]
        requirement_parts = requirement_items(rule)
        checks_by_index = {
            int(check["item_index"]): check
            for check in decision.get("requirement_checks", [])
            if isinstance(check, dict)
            and isinstance(check.get("item_index"), int)
            and 0 <= int(check["item_index"]) < len(requirement_parts)
        }
        check_evidence_ids = [
            str(segment_id)
            for check in checks_by_index.values()
            for segment_id in check.get("evidence_ids", [])
            if str(segment_id) in valid_evidence_ids
        ]
        if check_evidence_ids:
            selected_ids = list(dict.fromkeys([*selected_ids, *check_evidence_ids]))[:3]
            decision["selected_plan_segment_ids"] = selected_ids
        missing_indexes = sorted(
            index
            for index, check in checks_by_index.items()
            if check.get("status") == "missing"
        )
        if missing_indexes and result_value in {
            AuditResult.COMPLIANT.value,
            AuditResult.NOT_SPECIFIED.value,
        }:
            allowed = {
                PlanObligation.MUST_STATE.value,
                PlanObligation.CONDITIONAL_MUST_STATE.value,
            }
            if (
                applicability_value == ApplicabilityStatus.APPLICABLE.value
                and obligation_value in allowed
            ):
                decision.update(
                    result=AuditResult.NOT_SPECIFIED,
                    issue="方案未说明以下要求："
                    + "；".join(requirement_parts[index] for index in missing_indexes),
                )
                result_value = AuditResult.NOT_SPECIFIED.value
        elif (
            result_value == AuditResult.COMPLIANT.value
            and len(requirement_parts) > 1
            and checks_by_index
            and any(
                checks_by_index.get(index, {}).get("status") != "satisfied"
                for index in range(len(requirement_parts))
            )
        ):
            decision.update(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                issue="复合规则未逐项证明全部要求均已满足，程序未按符合处理。",
                confidence=min(float(decision["confidence"]), 0.3),
            )
            result_value = AuditResult.NEEDS_HUMAN_REVIEW.value

        # An explicit plan duty with no cited content is a genuine omission once the
        # model has confirmed applicability; it is not an unresolved human-review case.
        if (
            result_value == AuditResult.NEEDS_HUMAN_REVIEW.value
            and applicability_value == ApplicabilityStatus.APPLICABLE.value
            and obligation_value == PlanObligation.MUST_STATE.value
            and (
                not selected_ids
                or any(
                    marker in str(decision.get("issue", ""))
                    for marker in ("未明确", "未说明", "未提及", "缺少", "缺乏")
                )
            )
        ):
            decision.update(
                result=AuditResult.NOT_SPECIFIED,
                issue=decision.get("issue") or "施工方案未说明本条明确要求的内容。",
            )
            result_value = AuditResult.NOT_SPECIFIED.value

        # Guard a recurrent entity mismatch: prying a formwork panel is not evidence
        # that scaffold rods/components are forcibly pried. The model may reason about
        # entity association, but the final contradiction must still quote the same
        # action-object pair as the rule.
        if result_value == AuditResult.NONCOMPLIANT.value and selected_ids:
            rule_text = "".join(
                str(rule.get(field, ""))
                for field in ("trigger_condition", "requirement", "original_text")
            )
            selected_text = "\n".join(
                (plan_sources or {}).get(segment_id, "") for segment_id in selected_ids
            )
            scaffold_prying_rule = (
                "撬" in rule_text
                and any(term in rule_text for term in ("杆件", "构配件", "脚手架"))
            )
            template_only_prying = re.search(r"撬.{0,5}模板", selected_text) and not re.search(
                r"(?:撬动|撬别|强撬|击打|锤击|敲击)[^，。；]{0,4}"
                r"(?:脚手架|杆件|立杆|横杆|水平杆|剪刀撑)",
                selected_text,
            )
            if scaffold_prying_rule and template_only_prying:
                decision.update(
                    result=AuditResult.NEEDS_HUMAN_REVIEW,
                    issue="引用原文中的受力对象是模板，不足以证明方案要求强行撬动脚手架杆件。",
                    confidence=min(float(decision["confidence"]), 0.3),
                )
                result_value = AuditResult.NEEDS_HUMAN_REVIEW.value

        # Absence is an omission, not a contradiction. Compatible models can
        # occasionally label phrases such as "未明确" as noncompliant even when
        # they cite no conflicting plan statement. Normalize that semantic error
        # before user-facing business findings are built.
        omission_markers = ("未明确", "未说明", "未提及", "缺少", "缺乏")
        explicit_conflict_markers = (
            "低于",
            "高于",
            "超过",
            "小于",
            "大于",
            "冲突",
            "不一致",
            "不满足",
        )
        issue_text = str(decision.get("issue", ""))
        if (
            result_value == AuditResult.NONCOMPLIANT.value
            and decision.get("decision_basis") != "numeric"
            and any(marker in issue_text for marker in omission_markers)
            and not any(marker in issue_text for marker in explicit_conflict_markers)
        ):
            if (
                applicability_value == ApplicabilityStatus.APPLICABLE.value
                and obligation_value
                in {
                    PlanObligation.MUST_STATE.value,
                    PlanObligation.CONDITIONAL_MUST_STATE.value,
                }
            ):
                decision["result"] = AuditResult.NOT_SPECIFIED
                result_value = AuditResult.NOT_SPECIFIED.value
            else:
                decision.update(
                    result=AuditResult.NEEDS_HUMAN_REVIEW,
                    confidence=min(float(decision["confidence"]), 0.3),
                )
                result_value = AuditResult.NEEDS_HUMAN_REVIEW.value

        # Numeric requirements need a reproducible comparison. A fluent narrative
        # mentioning dimensions is not enough to issue a user-facing violation.
        numeric_rule = bool(re.search(r"\d", str(rule.get("threshold") or "")))
        if (
            result_value == AuditResult.NONCOMPLIANT.value
            and numeric_rule
            and decision.get("decision_basis") not in {"numeric", "deterministic_numeric"}
        ):
            decision.update(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                issue="数值要求未提供可追溯的结构化比较，程序未将其作为审计问题输出。",
                confidence=min(float(decision["confidence"]), 0.3),
            )
            result_value = AuditResult.NEEDS_HUMAN_REVIEW.value

        if decision.get("decision_basis") == "numeric":
            comparison = decision.get("numeric_comparison")
            comparison_segment_id = (
                str(comparison.get("plan_segment_id"))
                if isinstance(comparison, dict) and comparison.get("plan_segment_id")
                else ""
            )
            if comparison_segment_id in valid_evidence_ids:
                selected_ids = list(dict.fromkeys([*selected_ids, comparison_segment_id]))
                decision["selected_plan_segment_ids"] = selected_ids
            validation = validate_numeric_comparison(
                comparison,
                plan_sources or {},
                standard_sources or {},
                result_value
                if result_value
                in {AuditResult.COMPLIANT.value, AuditResult.NONCOMPLIANT.value}
                else None,
            )
            if not validation.valid:
                decision.update(
                    result=AuditResult.NEEDS_HUMAN_REVIEW,
                    issue=f"数值结论未通过程序校验：{validation.reason}",
                    confidence=min(float(decision["confidence"]), 0.3),
                )
                result_value = AuditResult.NEEDS_HUMAN_REVIEW.value
            else:
                computed_result = (
                    AuditResult.COMPLIANT
                    if validation.satisfied
                    else AuditResult.NONCOMPLIANT
                )
                decision["result"] = computed_result
                result_value = computed_result.value

        if (
            result_value in {AuditResult.COMPLIANT.value, AuditResult.NONCOMPLIANT.value}
            and not selected_ids
        ):
            decision.update(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                issue="结论缺少有效方案原文引用，程序已降级为待人工确认。",
                confidence=min(float(decision["confidence"]), 0.3),
            )
            result_value = AuditResult.NEEDS_HUMAN_REVIEW.value
        if result_value == AuditResult.NOT_APPLICABLE.value and not selected_ids:
            decision.update(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                applicability_status=ApplicabilityStatus.UNCERTAIN,
                applicability_reason="没有引用能够证明触发条件不成立的方案证据。",
                issue="规则适用性证据不足，需要人工确认。",
                confidence=min(float(decision["confidence"]), 0.3),
            )
            result_value = AuditResult.NEEDS_HUMAN_REVIEW.value
        if result_value == AuditResult.NOT_SPECIFIED.value and obligation_value in {
            PlanObligation.REFERENCE_ONLY.value,
            PlanObligation.EXTERNAL_ONLY.value,
        }:
            decision.update(
                result=AuditResult.OUT_OF_SCOPE,
                issue="该内容不以施工方案正文缺失作为问题判据。",
            )
            result_value = AuditResult.OUT_OF_SCOPE.value
        elif result_value == AuditResult.NOT_SPECIFIED.value:
            allowed = {
                PlanObligation.MUST_STATE.value,
                PlanObligation.CONDITIONAL_MUST_STATE.value,
            }
            if (
                applicability_value != ApplicabilityStatus.APPLICABLE.value
                or obligation_value not in allowed
            ):
                decision.update(
                    result=AuditResult.NEEDS_HUMAN_REVIEW,
                    issue="尚未同时确认规则适用且本内容必须在施工方案中说明，程序拒绝判为未说明。",
                    confidence=min(float(decision["confidence"]), 0.3),
                )
                result_value = AuditResult.NEEDS_HUMAN_REVIEW.value
        return decision

    @staticmethod
    def _humanize_segment_references(
        decision: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Keep internal segment IDs in traces, never in user-facing prose."""
        fields = (
            "issue",
            "risk_consequence",
            "suggestion",
            "applicability_reason",
            "plan_obligation_reason",
        )
        for field in fields:
            text = str(decision.get(field) or "")
            for segment_id, item in evidence_by_id.items():
                if segment_id not in text:
                    continue
                location = str(item.get("location") or item.get("heading_path") or "对应方案段落")
                text = text.replace(segment_id, f"“{location}”")
            decision[field] = text
        return decision

    @staticmethod
    def _sanitize_decision_text(
        decision: dict[str, Any], rule: dict[str, Any]
    ) -> dict[str, Any]:
        """Prevent model/processing placeholders from leaking into business output."""
        placeholder = re.compile(
            r"模型未提供|模型批量响应|输出不完整|需人工核对|需要人工确认|请人工核对"
        )
        result = decision.get("result")
        result_value = result.value if isinstance(result, AuditResult) else result
        issue = str(decision.get("issue") or "").strip()
        if not issue or placeholder.search(issue):
            if result_value == AuditResult.NOT_SPECIFIED.value:
                issue = f"方案未说明以下要求：{rule.get('requirement') or '本条安全控制要求'}"
            elif result_value == AuditResult.NONCOMPLIANT.value:
                issue = "方案现有表述与本条安全要求存在冲突。"
            elif result_value == AuditResult.COMPLIANT.value:
                issue = "方案已提供满足本条要求的原文证据。"
            else:
                issue = "现有方案证据不足以形成确定审计结论。"
        decision["issue"] = issue
        suggestion = str(decision.get("suggestion") or "").strip()
        if not suggestion or placeholder.search(suggestion):
            decision["suggestion"] = (
                "在对应安全技术措施章节中补充本条要求并明确执行责任。"
                if result_value
                in {AuditResult.NOT_SPECIFIED.value, AuditResult.NONCOMPLIANT.value}
                else "保持现有措施，并确保方案、交底与现场执行一致。"
            )
        return decision

    def _finalize_item(
        self,
        run_id: str,
        bundle_id: str,
        rule: dict[str, Any],
        decision: dict[str, Any],
        model_raw: dict[str, Any],
        bundle_evidence: list[dict[str, Any]],
        standard_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_by_id = {
            item["id"]: item
            for item in [
                *rule.get("_scene_evidence", []),
                *rule.get("_plan_evidence", []),
                *bundle_evidence,
            ]
        }
        standard_sources = {rule["rule_id"]: rule["original_text"]}
        standard_sources.update({item["id"]: item["text"] for item in standard_evidence})
        decision = self._validate_decision(
            decision,
            rule,
            set(evidence_by_id),
            plan_sources={item_id: item["text"] for item_id, item in evidence_by_id.items()},
            standard_sources=standard_sources,
        )
        result_value = (
            decision["result"].value
            if isinstance(decision["result"], AuditResult)
            else decision["result"]
        )
        actionable_results = {
            AuditResult.NONCOMPLIANT.value,
            AuditResult.NOT_SPECIFIED.value,
        }
        scene_evidence_ids = [
            str(item["id"])
            for item in rule.get("_scene_evidence", [])
            if str(item["id"]) in evidence_by_id
        ]
        insufficient_markers = (
            "未检索到足够证据",
            "未找到足够证据",
            "证据不足以",
            "没有足够证据",
        )
        if result_value in actionable_results and (
            not scene_evidence_ids
            or any(
                marker in str(decision.get("issue") or "")
                for marker in insufficient_markers
            )
        ):
            decision.update(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                applicability_status=ApplicabilityStatus.UNCERTAIN,
                issue="没有可验证的场景原文，程序未将本条作为业务问题输出。",
                confidence=min(float(decision.get("confidence", 0.0)), 0.2),
            )
            result_value = AuditResult.NEEDS_HUMAN_REVIEW.value
        elif result_value in actionable_results:
            # A missing requirement naturally has no satisfying rule evidence. Always
            # retain at least one separate scene anchor so the user can verify why the
            # control was considered applicable.
            decision["selected_plan_segment_ids"] = list(
                dict.fromkeys(
                    [
                        *scene_evidence_ids[:1],
                        *decision.get("selected_plan_segment_ids", []),
                    ]
                )
            )[:3]
        decision = self._sanitize_decision_text(decision, rule)
        decision = self._humanize_segment_references(decision, evidence_by_id)
        selected_ids = list(dict.fromkeys(decision["selected_plan_segment_ids"]))
        selected = [evidence_by_id[item] for item in selected_ids]
        scene_instance_basis = [
            {
                "instance_id": item["id"],
                "title": item["title"],
                "location": item["location"],
                "sources": item["sources"],
            }
            for item in rule["_scene_instances"]
        ]
        has_exact_rag = any(
            item.get("retrieval_type") == "exact_clause"
            and item.get("standard_code") == rule["standard_code"]
            and item.get("clause") == rule["clause"]
            for item in standard_evidence
        )
        basis = [] if has_exact_rag else [
            {
                "standard": rule["standard_name"],
                "standard_code": rule["standard_code"],
                "standard_status": rule["standard_status"],
                "clause": rule["clause"],
                "rule_id": rule["rule_id"],
                "pdf_page": rule["pdf_page"],
                "printed_page": rule["printed_page"],
                "quote": rule["original_text"],
                "scene_instances": scene_instance_basis,
            }
        ]
        basis.extend(
            {
                "standard": item["standard_name"],
                "standard_code": item["standard_code"],
                "standard_status": rule["standard_status"],
                "clause": item["clause"],
                "rule_id": rule["rule_id"],
                "chunk_id": item["id"],
                "page": item["page_start"],
                "quote": item["text"],
                "retrieval_type": item["retrieval_type"],
                "scene_instances": scene_instance_basis,
            }
            for item in standard_evidence
        )
        basis = deduplicate_standard_evidence(basis)
        evidences = [
            {
                "evidence_type": "plan",
                "source_id": item["id"],
                "quote": item["text"],
                "location": item["location"],
                "score": item.get("score"),
            }
            for item in selected
        ]
        standard_evidences = [] if has_exact_rag else [
            {
                "evidence_type": "standard",
                "source_id": rule["rule_id"],
                "quote": rule["original_text"],
                "location": f"{rule['standard_code']} {rule['clause']}",
                "score": None,
                "standard_code": rule["standard_code"],
                "clause": rule["clause"],
                "pdf_page": rule["pdf_page"],
            }
        ]
        standard_evidences.extend(
            {
                "evidence_type": "standard",
                "source_id": item["id"],
                "quote": item["text"],
                "location": f"{item['standard_code']} {item['clause']} PDF第{item['page_start']}页",
                "score": item["score"],
                "standard_code": item["standard_code"],
                "clause": item["clause"],
                "page_start": item["page_start"],
            }
            for item in standard_evidence
        )
        evidences.extend(deduplicate_standard_evidence(standard_evidences))
        result = decision["result"]
        applicability = decision["applicability_status"]
        obligation = decision["plan_obligation"]
        return {
            "run_id": run_id,
            "rule_id": rule["rule_id"],
            "scene": rule["scene"],
            "plan_quote": "\n".join(item["text"] for item in selected),
            "source_location": "；".join(item["location"] for item in selected),
            "issue": decision["issue"],
            "basis": basis,
            "risk_consequence": decision["risk_consequence"],
            "result": result.value if isinstance(result, AuditResult) else result,
            "applicability_status": (
                applicability.value
                if isinstance(applicability, ApplicabilityStatus)
                else applicability
            ),
            "applicability_reason": decision["applicability_reason"],
            "plan_obligation": (
                obligation.value
                if isinstance(obligation, PlanObligation)
                else obligation
            ),
            "plan_obligation_reason": decision["plan_obligation_reason"],
            "bundle_id": bundle_id,
            "business_group_key": rule["_business_group_key"],
            "control_title": rule["_control_title"],
            "suggestion": decision["suggestion"],
            "confidence": float(decision["confidence"]),
            "model_raw": model_raw,
            "evidences": evidences,
        }

    def _audit_bundle(
        self, run_id: str, bundle: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int]:
        rules = bundle["rules"]
        standard_by_rule = {
            rule["rule_id"]: self._retrieve_standard_evidence(rule, rule.get("_plan_evidence", []))
            for rule in rules
        }
        decisions: dict[str, dict[str, Any]] = {}
        raw_by_rule: dict[str, dict[str, Any]] = {}
        unresolved: list[dict[str, Any]] = []
        for rule in rules:
            deterministic = self._deterministic_decision(rule, rule.get("_plan_evidence", []))
            if deterministic is None:
                unresolved.append(rule)
            else:
                decisions[rule["rule_id"]], raw_by_rule[rule["rule_id"]] = deterministic

        model_calls = 0
        if unresolved:
            model_calls = 1
            try:
                model_decisions, raw = self.model.judge_rule_bundle(
                    unresolved, bundle["evidence"], standard_by_rule
                )
                for rule in unresolved:
                    decisions[rule["rule_id"]] = model_decisions[rule["rule_id"]].model_dump()
                    raw_by_rule[rule["rule_id"]] = raw
            except Exception as exc:
                for rule in unresolved:
                    decisions[rule["rule_id"]] = self._fallback_decision(
                        rule, "规则包模型调用失败，本条未进行语义强判。"
                    )
                    raw_by_rule[rule["rule_id"]] = {
                        "provider": self.model.provider_name,
                        "error": str(exc)[:500],
                    }

        pending_rules = []
        previous_pending: dict[str, LLMDecision] = {}
        for rule in unresolved:
            decision = decisions[rule["rule_id"]]
            result = decision.get("result")
            applicability = decision.get("applicability_status")
            obligation = decision.get("plan_obligation")
            result_value = result.value if isinstance(result, AuditResult) else result
            applicability_value = (
                applicability.value
                if isinstance(applicability, ApplicabilityStatus)
                else applicability
            )
            obligation_value = (
                obligation.value if isinstance(obligation, PlanObligation) else obligation
            )
            if (
                result_value == AuditResult.NEEDS_HUMAN_REVIEW.value
                and applicability_value == ApplicabilityStatus.APPLICABLE.value
                and obligation_value
                in {
                    PlanObligation.MUST_STATE.value,
                    PlanObligation.CONDITIONAL_MUST_STATE.value,
                }
            ):
                pending_rules.append(rule)
                previous_pending[rule["rule_id"]] = LLMDecision.model_validate(decision)
        if pending_rules and self.model.provider_name != "mock":
            try:
                resolved, resolution_raw = self.model.resolve_pending_bundle(
                    pending_rules, bundle["evidence"], previous_pending
                )
                model_calls += 1
                for rule in pending_rules:
                    rule_id = rule["rule_id"]
                    decisions[rule_id] = resolved[rule_id].model_dump()
                    raw_by_rule[rule_id] = resolution_raw
            except Exception:
                # Keep the first, traceable decision; hidden uncertain items must not
                # become user-facing problems merely because the recheck failed.
                pass

        items = [
            self._finalize_item(
                run_id,
                bundle["id"],
                rule,
                decisions[rule["rule_id"]],
                raw_by_rule[rule["rule_id"]],
                bundle["evidence"],
                standard_by_rule[rule["rule_id"]],
            )
            for rule in rules
        ]
        return items, model_calls

    def _audit_rule_bundles(self, state: AuditState) -> dict[str, Any]:
        bundles = state["bundles"]
        total_bundles = len(bundles)
        total_rules = len(state["rules"])
        self._mark_node(
            state, f"audit_rule_bundles 0/{total_bundles}", AuditStatus.AUDITING
        )
        if not bundles:
            return {"generated_items": []}
        ordered_results: list[list[dict[str, Any]] | None] = [None] * total_bundles
        completed_bundles = 0
        completed_rules = 0
        model_calls = 0
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, total_bundles),
            thread_name_prefix="rule-bundle-audit",
        ) as pool:
            futures = {
                pool.submit(self._audit_bundle, state["run_id"], bundle): index
                for index, bundle in enumerate(bundles)
            }
            for future in as_completed(futures):
                items, bundle_calls = future.result()
                call_ids = {
                    str(item.get("model_raw", {}).get("_monitor_call_id"))
                    for item in items
                    if item.get("model_raw", {}).get("_monitor_call_id")
                }
                monitor = getattr(self.model, "call_monitor", None)
                try:
                    for item in items:
                        if not item["basis"] or not item["basis"][0]["rule_id"]:
                            raise ValueError("审计项缺少可追溯的规范规则依据")
                        self.repository.create_audit_item(item, item["evidences"])
                except Exception as exc:
                    if monitor is not None:
                        for call_id in call_ids:
                            monitor.processing_failed(call_id, "persistence", exc)
                    raise
                if monitor is not None:
                    for call_id in call_ids:
                        monitor.mark_persisted(call_id)
                ordered_results[futures[future]] = items
                completed_bundles += 1
                completed_rules += len(items)
                model_calls += bundle_calls
                self.repository.update_audit_run(
                    state["run_id"],
                    current_node=f"audit_rule_bundles {completed_bundles}/{total_bundles}",
                    completed_rules=completed_rules,
                    completed_bundles=completed_bundles,
                    model_call_count=model_calls,
                )
        generated = [
            item
            for bundle_items in ordered_results
            if bundle_items is not None
            for item in bundle_items
        ]
        if len(generated) != total_rules:
            raise ValueError("规则包输出未覆盖全部候选原子规则")
        return {"generated_items": generated}

    def _validate_persistence(self, state: AuditState) -> dict[str, Any]:
        self._mark_node(state, "validate_persistence", AuditStatus.AUDITING)
        persisted_count = len(self.repository.list_audit_items(state["run_id"]))
        if persisted_count != len(state["generated_items"]):
            raise ValueError("逐包保存数量与已完成规则数量不一致")
        self.repository.update_audit_run(
            state["run_id"],
            status=AuditStatus.COMPLETED.value,
            current_node="completed" if state["generated_items"] else "no_applicable_scene",
            completed_rules=len(state["generated_items"]),
            completed_bundles=len(state["bundles"]),
            completed_at=utc_now(),
        )
        return {}
