from backend.app.enums import ApplicabilityStatus, AuditResult, PlanObligation
from backend.app.graph.audit_graph import AuditGraph
from backend.app.services.numeric_validation import validate_numeric_comparison


def _comparison() -> dict:
    return {
        "object": "防护栏杆上杆",
        "plan_value": 1.0,
        "plan_unit": "m",
        "operator": ">=",
        "standard_value": 1.2,
        "standard_unit": "m",
        "plan_segment_id": "plan-1",
        "standard_source_id": "HL-001",
    }


def test_validates_model_numeric_violation_from_cited_sources():
    validation = validate_numeric_comparison(
        _comparison(),
        {"plan-1": "临边防护栏杆上杆高度为1.0m。"},
        {"HL-001": "防护栏杆上杆距地面高度应为1.2m。"},
        AuditResult.NONCOMPLIANT.value,
    )

    assert validation.valid
    assert validation.satisfied is False


def test_rejects_model_text_result_that_disagrees_with_numeric_calculation():
    validation = validate_numeric_comparison(
        _comparison(),
        {"plan-1": "临边防护栏杆上杆高度为1.0m。"},
        {"HL-001": "防护栏杆上杆距地面高度应为1.2m。"},
        AuditResult.COMPLIANT.value,
    )

    assert not validation.valid
    assert "不一致" in validation.reason


def test_validates_convertible_length_units():
    comparison = {**_comparison(), "plan_value": 100, "plan_unit": "cm"}

    validation = validate_numeric_comparison(
        comparison,
        {"plan-1": "临边防护栏杆上杆高度为100cm。"},
        {"HL-001": "防护栏杆上杆距地面高度应为1.2m。"},
        AuditResult.NONCOMPLIANT.value,
    )

    assert validation.valid


def test_rejects_numeric_comparison_between_toe_board_and_guardrail():
    comparison = {
        **_comparison(),
        "plan_value": 18,
        "plan_unit": "cm",
    }

    validation = validate_numeric_comparison(
        comparison,
        {"plan-1": "设置不低于18cm的挡脚板。"},
        {"HL-001": "防护栏杆上杆距地面高度应为1.2m。"},
        AuditResult.NONCOMPLIANT.value,
    )

    assert not validation.valid
    assert "跨对象" in validation.reason


def test_numeric_calculation_can_be_verified_without_model_text_result():
    comparison = {
        **_comparison(),
        "object": "柱模板拆除设置操作平台的高度阈值",
        "plan_value": 6,
        "operator": "<=",
        "standard_value": 2,
        "standard_source_id": "XK-013",
    }

    validation = validate_numeric_comparison(
        comparison,
        {"plan-1": "拆除6m高度以上柱模板时设置操作平台。"},
        {"XK-013": "2m及以上高处拆除柱模板时应设置操作平台。"},
    )

    assert validation.valid
    assert validation.satisfied is False


def test_graph_uses_verified_numeric_result_when_model_label_is_uncertain():
    graph = object.__new__(AuditGraph)
    comparison = {
        "object": "柱模板拆除设置操作平台的高度阈值",
        "plan_value": 6,
        "plan_unit": "m",
        "operator": "<=",
        "standard_value": 2,
        "standard_unit": "m",
        "plan_segment_id": "plan-1",
        "standard_source_id": "XK-013",
    }
    decision = {
        "result": AuditResult.NEEDS_HUMAN_REVIEW,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "柱模板拆除对象已对应",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "操作平台要求应在方案明确",
        "issue": "方案从6m才开始设置操作平台。",
        "selected_plan_segment_ids": ["plan-1"],
        "decision_basis": "numeric",
        "numeric_comparison": comparison,
        "confidence": 0.9,
    }

    validated = graph._validate_decision(
        decision,
        {},
        {"plan-1"},
        plan_sources={"plan-1": "拆除6m高度以上柱模板时设置操作平台。"},
        standard_sources={"XK-013": "2m及以上高处拆除柱模板时应设置操作平台。"},
    )

    assert validated["result"] is AuditResult.NONCOMPLIANT


def test_graph_hides_numeric_violation_without_structured_comparison():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NONCOMPLIANT,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "方案涉及连墙件",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "方案应说明",
        "issue": "连墙件间距4.05m大于3跨。",
        "selected_plan_segment_ids": ["plan-1"],
        "decision_basis": "text",
        "numeric_comparison": None,
        "confidence": 0.8,
    }

    validated = graph._validate_decision(
        decision,
        {"threshold": "3跨"},
        {"plan-1"},
        plan_sources={"plan-1": "连墙件间距4.05m。"},
    )

    assert validated["result"] is AuditResult.NEEDS_HUMAN_REVIEW
    assert "结构化比较" in validated["issue"]


def test_graph_normalizes_missing_content_mislabeled_as_noncompliant():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NONCOMPLIANT,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "安全网场景已经确认",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "方案必须明确",
        "issue": "方案未明确安全网应绑扎牢固。",
        "selected_plan_segment_ids": ["plan-1"],
        "decision_basis": "text",
        "numeric_comparison": None,
        "confidence": 0.9,
    }

    validated = graph._validate_decision(
        decision,
        {"threshold": ""},
        {"plan-1"},
        plan_sources={"plan-1": "方案只写设置安全网。"},
    )

    assert validated["result"] is AuditResult.NOT_SPECIFIED


def test_graph_keeps_explicit_conflict_even_if_issue_also_mentions_an_omission():
    graph = object.__new__(AuditGraph)
    decision = {
        "result": AuditResult.NONCOMPLIANT,
        "applicability_status": ApplicabilityStatus.APPLICABLE,
        "applicability_reason": "洞口场景已经确认",
        "plan_obligation": PlanObligation.MUST_STATE,
        "plan_obligation_reason": "方案必须明确",
        "issue": "防护门1.2m低于1.5m，并且未明确挡脚板。",
        "selected_plan_segment_ids": ["plan-1"],
        "decision_basis": "text",
        "numeric_comparison": None,
        "confidence": 0.9,
    }

    validated = graph._validate_decision(
        decision,
        {"threshold": ""},
        {"plan-1"},
        plan_sources={"plan-1": "防护门高度为1.2m。"},
    )

    assert validated["result"] is AuditResult.NONCOMPLIANT


def test_graph_uses_unambiguous_deterministic_bound_before_model():
    graph = object.__new__(AuditGraph)
    rule = {
        "threshold": "2m",
        "requirement": "防护栏杆立杆间距不应大于2m",
        "original_text": "防护栏杆立杆间距不应大于2m",
        "hazards": "高处坠落",
        "_applicability": {
            "status": ApplicabilityStatus.UNCERTAIN.value,
            "reason": "已识别防护栏杆场景",
            "supporting_segment_ids": [],
        },
        "_plan_scope": {
            "obligation": PlanObligation.CONDITIONAL_MUST_STATE.value,
            "reason": "方案应说明栏杆构造",
        },
    }
    evidence = [
        {
            "id": "railing",
            "text": "结构临边防护栏杆立杆间距不大于2.5m。",
        }
    ]

    decision, raw = graph._deterministic_decision(rule, evidence)

    assert decision["result"] is AuditResult.NONCOMPLIANT
    assert decision["selected_plan_segment_ids"] == ["railing"]
    assert raw["provider"] == "deterministic_numeric"
