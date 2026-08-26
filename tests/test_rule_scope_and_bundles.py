from backend.app.enums import ApplicabilityStatus, AuditResult, PlanObligation
from backend.app.graph.audit_graph import AuditGraph
from backend.app.services.business_findings import build_business_findings
from backend.app.services.model_provider import normalize_decision_payload
from backend.app.services.rule_bundles import build_rule_bundles
from backend.app.services.rule_scope import (
    classify_plan_obligation,
    filter_rules_for_document_scope,
)


def _rule(rule_id: str, requirement: str, process: str = "隔离与防护") -> dict:
    return {
        "rule_id": rule_id,
        "scene": "交叉作业",
        "process": process,
        "trigger_condition": "进行交叉作业时",
        "requirement": requirement,
        "original_text": requirement,
        "standard_code": "JGJ 80-2016",
        "clause": "7.1.1",
        "_scene_instances": [
            {
                "id": "instance-1",
                "location": "P055",
                "segment_ids": ["segment-1"],
            }
        ],
        "_plan_evidence": [
            {
                "id": "segment-1",
                "text": "交叉作业时设置安全网或防护棚。",
                "location": "P055",
                "sequence_no": 55,
                "score": 0.8,
            }
        ],
    }


def test_classifies_external_artifacts_outside_plan_omission_scope():
    decision = classify_plan_obligation(
        _rule("AQW-004", "使用前检查产品合格证、网目数及网体重量")
    )

    assert decision.obligation is PlanObligation.EXTERNAL_ONLY


def test_classifies_explicit_scheme_requirement_as_must_state():
    decision = classify_plan_obligation(
        _rule("GZ-001", "施工方案中应制定高处作业安全技术措施")
    )

    assert decision.obligation is PlanObligation.MUST_STATE


def test_acceptance_record_rule_requires_workflow_but_not_attached_record():
    decision = classify_plan_obligation(
        _rule("GZ-026", "验收资料应包括安全防护设施验收记录")
    )

    assert decision.obligation is PlanObligation.CONDITIONAL_MUST_STATE


def test_actual_acceptance_artifact_is_not_a_scheme_text_omission():
    decision = classify_plan_obligation(
        _rule("GZ-025", "安全防护设施验收资料应包括产品合格证明")
    )

    assert decision.obligation is PlanObligation.EXTERNAL_ONLY


def test_ppe_product_usage_details_are_reference_only_in_special_scheme():
    decision = classify_plan_obligation(
        {
            **_rule("PPE-004", "安全带应采用全身式系带，连接点位于前胸或后背"),
            "scene": "安全带使用",
        }
    )

    assert decision.obligation is PlanObligation.REFERENCE_ONLY


def test_railing_test_load_is_reference_detail_not_required_scheme_text():
    decision = classify_plan_obligation(
        {
            **_rule("HL-011", "防护栏杆应能承受任何方向1kN外力"),
            "scene": "防护栏杆",
        }
    )

    assert decision.obligation is PlanObligation.REFERENCE_ONLY


def test_access_route_is_an_explicit_scheme_control():
    decision = classify_plan_obligation(
        {
            **_rule("PD-001", "登高作业应借助施工通道、梯子或其他攀登设施"),
            "scene": "攀登作业",
        }
    )

    assert decision.obligation is PlanObligation.MUST_STATE


def test_bundles_rules_sharing_control_and_plan_evidence():
    rules = [_rule("JC-001", "设置隔离措施"), _rule("JC-002", "设置安全防护棚")]

    bundles = build_rule_bundles(rules)

    assert len(bundles) == 1
    assert [rule["rule_id"] for rule in bundles[0]["rules"]] == ["JC-001", "JC-002"]


def test_does_not_bundle_different_business_controls():
    rules = [
        _rule("JC-001", "设置隔离措施"),
        _rule("AQW-001", "安全网材质应合格", process="安全网选用与验收"),
    ]

    assert len(build_rule_bundles(rules)) == 2


def test_same_rule_in_different_scene_instances_stays_in_separate_bundles():
    first = _rule("JC-001", "设置隔离措施")
    second = _rule("JC-001", "设置隔离措施")
    second["_scene_instances"] = [
        {"id": "instance-2", "location": "P088", "segment_ids": ["segment-2"]}
    ]
    second["_plan_evidence"] = [
        {
            "id": "segment-2",
            "text": "另一处交叉作业。",
            "location": "P088",
            "sequence_no": 88,
            "score": 0.8,
        }
    ]

    bundles = build_rule_bundles([first, second])

    assert len(bundles) == 2
    assert first["_business_group_key"] != second["_business_group_key"]


def test_candidate_rules_expand_per_scene_instance():
    class FakeRepository:
        def list_enabled_rules(self, scenes):
            assert scenes == ["交叉作业"]
            return [
                {
                    "rule_id": "JC-001",
                    "scene": "交叉作业",
                    "standard_status": "现行",
                }
            ]

        def update_audit_run(self, run_id, **values):
            assert run_id == "run-1"

    graph = object.__new__(AuditGraph)
    graph.repository = FakeRepository()
    state = {
        "run_id": "run-1",
        "scenes": ["交叉作业"],
        "scene_instances": [
            {
                "id": "instance-1",
                "scene": "交叉作业",
                "location": "P010",
                "segment_ids": ["segment-1"],
            },
            {
                "id": "instance-2",
                "scene": "交叉作业",
                "location": "P080",
                "segment_ids": ["segment-2"],
            },
        ],
    }

    rules = graph._select_candidate_rules(state)["rules"]

    assert len(rules) == 2
    assert {rule["_audit_unit_id"] for rule in rules} == {
        "JC-001@instance-1",
        "JC-001@instance-2",
    }
    assert rules[0]["_preferred_segment_ids"] == ["segment-1"]
    assert rules[1]["_preferred_segment_ids"] == ["segment-2"]


def test_business_findings_merge_atomic_rules_and_hide_external_checks():
    base = {
        "scene": "交叉作业",
        "business_group_key": "交叉作业|隔离与防护",
        "control_title": "隔离与防护",
        "risk_consequence": "可能发生物体打击。",
        "suggestion": "补充隔离措施。",
        "plan_quote": "交叉作业时设置安全网。",
        "source_location": "P055",
        "confidence": 0.8,
        "basis": [],
    }
    items = [
        {
            **base,
            "rule_id": "JC-001",
            "result": AuditResult.NOT_SPECIFIED.value,
            "issue": "未说明隔离范围。",
        },
        {
            **base,
            "rule_id": "JC-002",
            "result": AuditResult.NONCOMPLIANT.value,
            "issue": "措施存在冲突。",
        },
        {
            **base,
            "rule_id": "AQW-004",
            "result": AuditResult.OUT_OF_SCOPE.value,
            "issue": "产品合格证另行核验。",
        },
        {
            **base,
            "rule_id": "JC-003",
            "result": AuditResult.NEEDS_HUMAN_REVIEW.value,
            "issue": "适用条件不确定。",
        },
    ]

    findings = build_business_findings(items)

    assert len(findings) == 1
    assert findings[0]["result"] == AuditResult.NONCOMPLIANT.value
    assert findings[0]["rule_ids"] == ["JC-001", "JC-002"]


def test_business_findings_hide_human_review_only_group():
    findings = build_business_findings(
        [
            {
                "scene": "施工脚手架",
                "rule_id": "JSJ-001",
                "result": AuditResult.NEEDS_HUMAN_REVIEW.value,
            }
        ]
    )

    assert findings == []


def test_document_scope_excludes_product_design_and_delegated_scaffold_details():
    segments = [
        {"text": "幕墙施工方案", "heading_path": "文档标题"},
        {
            "text": "本工程外脚手架另见《外立面幕墙装饰脚手架施工方案》。",
            "heading_path": "编制依据",
        },
    ]
    rules = [
        {"rule_id": "MQ-020", "scene": "建筑幕墙设计", "process": "防雷设计"},
        {
            "rule_id": "MQ-044",
            "scene": "玻璃幕墙",
            "process": "玻璃面板加工",
            "requirement": "玻璃应磨边倒角",
        },
        {
            "rule_id": "JSJ-037",
            "scene": "施工脚手架",
            "process": "扫地杆设置",
            "requirement": "设置纵横向扫地杆",
        },
        {
            "rule_id": "JSJ-045",
            "scene": "施工脚手架",
            "process": "作业层防护",
            "requirement": "作业层上应满铺脚手板",
        },
        {
            "rule_id": "JSJ-016",
            "scene": "施工脚手架",
            "process": "脚手架搭设、使用与拆除",
            "requirement": "动火申请批准后设置接火斗和灭火器并由专人监护",
        },
    ]

    selected = filter_rules_for_document_scope(rules, segments)

    assert [rule["rule_id"] for rule in selected] == ["JSJ-045", "JSJ-016"]


def test_scaffold_special_scheme_keeps_its_own_engineering_rules():
    segments = [
        {"text": "落地式脚手架专项施工方案", "heading_path": "文档标题"},
        {"text": "架体底部设置扫地杆。", "heading_path": "搭设要求"},
    ]
    rules = [
        {
            "rule_id": "JSJ-037",
            "scene": "施工脚手架",
            "process": "扫地杆设置",
            "requirement": "设置纵横向扫地杆",
        }
    ]

    assert filter_rules_for_document_scope(rules, segments) == rules


def test_business_findings_deduplicate_plan_segments_by_source_id():
    base = {
        "scene": "施工脚手架",
        "business_group_key": "施工脚手架|连墙件",
        "control_title": "连墙件",
        "result": AuditResult.NOT_SPECIFIED.value,
        "issue": "未说明连墙件布置。",
        "risk_consequence": "架体失稳。",
        "suggestion": "补充连墙件布置。",
        "plan_quote": "同一段方案原文",
        "source_location": "P022",
        "confidence": 0.8,
        "basis": [],
        "evidences": [
            {
                "evidence_type": "plan",
                "source_id": "segment-22",
                "quote": "同一段方案原文",
                "location": "P022",
            }
        ],
    }

    findings = build_business_findings(
        [{**base, "rule_id": "JSJ-001"}, {**base, "rule_id": "JSJ-002"}]
    )

    assert findings[0]["plan_quote"] == "同一段方案原文"
    assert findings[0]["source_location"] == "P022"


def test_business_findings_deduplicate_same_standard_passage_across_rules():
    base = {
        "scene": "防护栏杆",
        "business_group_key": "防护栏杆|构造要求",
        "control_title": "构造要求",
        "result": AuditResult.NOT_SPECIFIED.value,
        "issue": "未说明栏杆构造。",
        "risk_consequence": "可能发生高处坠落。",
        "suggestion": "补充栏杆构造。",
        "plan_quote": "",
        "source_location": "",
        "confidence": 0.8,
        "evidences": [],
    }
    quote = "防护栏杆立杆间距不应大于2m。"
    first = {
        **base,
        "rule_id": "HL-001",
        "basis": [
            {
                "rule_id": "HL-001",
                "standard_code": "JGJ 80-2016",
                "clause": "4.3.1",
                "pdf_page": 20,
                "quote": quote,
            }
        ],
    }
    second = {
        **base,
        "rule_id": "HL-002",
        "basis": [
            {
                "chunk_id": "different-internal-id",
                "standard_code": "JGJ 80-2016",
                "clause": "4.3.1",
                "page": 20,
                "quote": quote,
            }
        ],
    }

    findings = build_business_findings([first, second])

    assert len(findings[0]["basis"]) == 1


def test_user_facing_decision_replaces_internal_segment_id_with_location():
    segment_id = "efb55af15113499cac30478659094b1e"
    decision = {
        "issue": f"方案中{segment_id}未明确防护棚长度。",
        "risk_consequence": "可能发生物体打击。",
        "suggestion": f"请在{segment_id}对应章节补充。",
        "applicability_reason": f"{segment_id}明确设置防护棚。",
        "plan_obligation_reason": "属于方案应说明内容。",
    }

    readable = AuditGraph._humanize_segment_references(
        decision,
        {segment_id: {"location": "P061", "heading_path": "安全通道防护"}},
    )

    assert segment_id not in " ".join(str(value) for value in readable.values())
    assert "P061" in readable["issue"]


def test_user_facing_decision_replaces_internal_model_placeholder():
    decision = {
        "result": AuditResult.NOT_SPECIFIED,
        "issue": "模型未提供完整的问题描述，需人工核对。",
        "suggestion": "请人工核对本条规则。",
    }

    readable = AuditGraph._sanitize_decision_text(
        decision, {"requirement": "高处作业前应进行安全技术交底"}
    )

    assert "模型" not in readable["issue"]
    assert "人工" not in readable["issue"]
    assert "安全技术交底" in readable["issue"]


def test_compound_rule_reports_each_missing_requirement_item():
    graph = object.__new__(AuditGraph)
    rule = {
        "requirement": "标牌应注明允许负载和作业人数；物料不得超重、超高堆放",
        "_plan_scope": {
            "obligation": PlanObligation.MUST_STATE.value,
            "reason": "方案应说明",
        },
    }
    decision = {
        "result": AuditResult.NOT_SPECIFIED,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "方案设置卸料平台",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "方案应说明",
        "issue": "仅有限载牌",
        "selected_plan_segment_ids": ["platform"],
        "decision_basis": "missing",
        "numeric_comparison": None,
        "requirement_checks": [
            {"item_index": 0, "status": "missing", "evidence_ids": ["platform"]},
            {"item_index": 1, "status": "missing", "evidence_ids": ["platform"]},
        ],
        "confidence": 0.8,
    }

    validated = graph._validate_decision(decision, rule, {"platform"})

    assert "作业人数" in validated["issue"]
    assert "超高堆放" in validated["issue"]


def test_not_specified_requires_confirmed_applicability_and_plan_duty():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NOT_SPECIFIED,
        "applicability_status": ApplicabilityStatus.UNCERTAIN,
        "applicability_reason": "条件不清楚",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "方案应说明",
        "issue": "缺少内容",
        "selected_plan_segment_ids": [],
        "confidence": 0.8,
    }

    validated = graph._validate_decision(decision, {}, set())

    assert validated["result"] is AuditResult.NEEDS_HUMAN_REVIEW
    assert "拒绝判为未说明" in validated["issue"]


def test_reference_only_missing_content_is_not_a_plan_problem():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NOT_SPECIFIED,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "对象存在",
        "plan_obligation": PlanObligation.REFERENCE_ONLY,
        "plan_obligation_reason": "产品资料另行核验",
        "issue": "方案没写产品数据",
        "selected_plan_segment_ids": [],
        "confidence": 0.8,
    }

    validated = graph._validate_decision(decision, {}, set())

    assert validated["result"] is AuditResult.OUT_OF_SCOPE


def test_confirmed_explicit_plan_duty_without_evidence_becomes_not_specified():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NEEDS_HUMAN_REVIEW,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "高处作业场景已经确认",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "规则明确要求方案说明",
        "issue": "方案未提及初次作业人员培训。",
        "selected_plan_segment_ids": [],
        "confidence": 0.9,
    }

    validated = graph._validate_decision(decision, {}, set())

    assert validated["result"] is AuditResult.NOT_SPECIFIED


def test_generic_high_work_does_not_override_uncertain_climbing_applicability():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NEEDS_HUMAN_REVIEW,
        "applicability_status": ApplicabilityStatus.UNCERTAIN,
        "applicability_reason": "方案没写通道",
        "plan_obligation": PlanObligation.CONDITIONAL_MUST_STATE,
        "plan_obligation_reason": "模型不确定",
        "issue": "方案未明确上下通道。",
        "selected_plan_segment_ids": [],
        "confidence": 0.8,
    }
    rule = {
        "scene": "攀登作业",
        "_plan_scope": {
            "obligation": PlanObligation.MUST_STATE.value,
            "reason": "高处作业上下通行应在方案中明确。",
        },
        "_scene_instances": [
            {"sources": ["deterministic_inherent_access"]}
        ],
    }

    validated = graph._validate_decision(decision, rule, set())

    assert validated["result"] is AuditResult.NEEDS_HUMAN_REVIEW
    assert validated["applicability_status"] is ApplicabilityStatus.UNCERTAIN
    assert validated["plan_obligation"] is PlanObligation.MUST_STATE


def test_template_prying_cannot_prove_scaffold_rod_prying_violation():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NONCOMPLIANT,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "拆除场景已确认",
        "plan_obligation": PlanObligation.CONDITIONAL_MUST_STATE,
        "plan_obligation_reason": "拆除方法应在方案说明",
        "issue": "方案强行撬动杆件。",
        "selected_plan_segment_ids": ["segment-1"],
        "confidence": 0.9,
    }
    rule = {
        "trigger_condition": "脚手架拆除时",
        "requirement": "不得使用重锤击打或强行撬别杆件",
        "original_text": "拆除作业不得强行撬别脚手架杆件",
    }

    validated = graph._validate_decision(
        decision,
        rule,
        {"segment-1"},
        plan_sources={"segment-1": "用钢钎撬动模板，然后拆除水平杆和立杆。"},
    )

    assert validated["result"] is AuditResult.NEEDS_HUMAN_REVIEW
    assert "受力对象是模板" in validated["issue"]


def test_unknown_or_uncertain_model_result_falls_back_to_human_review():
    rule = {
        "hazards": "高处坠落",
        "_plan_scope": {
            "obligation": PlanObligation.CONDITIONAL_MUST_STATE.value,
            "reason": "需结合现场条件判断。",
        },
    }

    uncertain = normalize_decision_payload({"result": "uncertain"}, rule)
    unexpected = normalize_decision_payload({"result": "maybe"}, rule)

    assert uncertain["result"] == AuditResult.NEEDS_HUMAN_REVIEW.value
    assert unexpected["result"] == AuditResult.NEEDS_HUMAN_REVIEW.value


def test_common_chinese_result_aliases_are_normalized():
    rule = {
        "hazards": "高处坠落",
        "_plan_scope": {
            "obligation": PlanObligation.CONDITIONAL_MUST_STATE.value,
            "reason": "结合现场条件判断。",
        },
    }

    assert normalize_decision_payload({"result": "不满足"}, rule)[
        "result"
    ] == AuditResult.NONCOMPLIANT.value
    assert normalize_decision_payload({"result": "未提及"}, rule)[
        "result"
    ] == AuditResult.NOT_SPECIFIED.value
