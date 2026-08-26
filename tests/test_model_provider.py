from types import SimpleNamespace

from backend.app.enums import ApplicabilityStatus, AuditResult, PlanObligation
from backend.app.schemas import LLMDecision
from backend.app.services.model_provider import (
    OpenAICompatibleAuditModel,
    _recover_json_array_objects,
    normalize_decision_payload,
)


def test_normalizes_empty_optional_explanation_from_compatible_model():
    normalized = normalize_decision_payload(
        {
            "result": "符合",
            "issue": "方案已明确说明安全技术交底和记录要求。",
            "risk_consequence": "",
            "suggestion": "",
            "selected_plan_segment_ids": ["segment-1"],
            "confidence": 95,
            "unexpected_provider_field": "ignored",
        },
        {"hazards": "高处坠落；物体打击"},
    )

    assert normalized["result"] == AuditResult.COMPLIANT.value
    assert normalized["risk_consequence"] == "高处坠落；物体打击"
    assert normalized["suggestion"]
    assert normalized["selected_plan_segment_ids"] == ["segment-1"]
    assert normalized["confidence"] == 0.95
    assert "unexpected_provider_field" not in normalized


def test_control_gate_keeps_only_valid_applicable_rule_and_evidence_ids():
    model = object.__new__(OpenAICompatibleAuditModel)
    captured = {}

    def complete(*args, **kwargs):
        captured.update(args[1])
        return (
        {
            "controls": [
                {
                    "control_key": "instance|攀登作业|梯具与上下通道",
                    "status": "applicable",
                    "reason": "方案明确采用斜道。",
                    "applicable_rule_ids": ["PD-001", "PD-002", "NOT-A-RULE"],
                    "evidence_ids": ["segment-1", "NOT-A-SEGMENT"],
                }
            ]
        },
        {},
        )

    model._json_completion = complete
    control = {
        "key": "instance|攀登作业|梯具与上下通道",
        "title": "梯具与上下通道",
        "rules": [
            {"rule_id": "PD-001", "trigger_condition": "采用斜道时", "requirement": "设置斜道"},
            {"rule_id": "PD-002", "trigger_condition": "采用直梯时", "requirement": "设置护笼"},
        ],
    }
    decision = model.select_applicable_controls(
        {"scene": "攀登作业", "title": "斜道", "location": "P010"},
        [control],
        [{"id": "segment-1", "location": "P010", "text": "采用斜道上下"}],
        [
            {
                "id": "standard-1",
                "standard_code": "JGJ 80-2016",
                "clause": "5.1.1",
                "page_start": 20,
                "text": "攀登作业应设置可靠的上下通道。",
                "score": 0.82,
                "matched_rule_ids": ["PD-001"],
            }
        ],
    )[control["key"]]

    assert decision["applicable_rule_ids"] == ["PD-001"]
    assert decision["evidence_ids"] == ["segment-1"]
    assert captured["independent_standard_candidates"][0]["matched_rule_ids"] == [
        "PD-001"
    ]


def test_recovers_valid_bundle_decisions_when_one_json_object_is_malformed():
    content = (
        '{"decisions":['
        '{"rule_id":"A","result":"符合"},'
        '{"rule_id":"BROKEN","result":oops},'
        '{"rule_id":"B","result":"未说明"}'
        "]} trailing"
    )

    recovered = _recover_json_array_objects(content, "decisions")

    assert [item["rule_id"] for item in recovered] == ["A", "B"]


def test_json_completion_retries_an_empty_required_decision_array_once():
    model = object.__new__(OpenAICompatibleAuditModel)
    model.call_monitor = None
    model.run_id = None
    model.model_name = "test-model"
    model._dashscope_thinking = None
    model._dashscope_thinking_budgets = {}
    model._dashscope_default_thinking_budget = None
    responses = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"decisions":[{"rule_id":"R-001"}]}'
                        )
                    )
                ]
            ),
        ]
    )
    attempts = []

    def request(_request, **kwargs):
        attempts.append(kwargs["attempt"])
        return next(responses), {"content": "{}"}

    model._request_with_monitor = request
    parsed, _ = model._json_completion(
        "返回JSON对象。",
        {"rules": [{"rule_id": "R-001"}]},
        purpose="rule_bundle_audit",
        recover_array_key="decisions",
        retry_empty_array=True,
    )

    assert parsed["decisions"][0]["rule_id"] == "R-001"
    assert attempts == [1, 2]


def test_json_completion_retries_malformed_control_response_once():
    model = object.__new__(OpenAICompatibleAuditModel)
    model.call_monitor = None
    model.run_id = None
    model.model_name = "test-model"
    model._dashscope_thinking = None
    model._dashscope_thinking_budgets = {}
    model._dashscope_default_thinking_budget = None
    responses = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"controls":['))]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"controls":[{"control_key":"C-001"}]}'
                        )
                    )
                ]
            ),
        ]
    )
    attempts = []

    def request(_request, **kwargs):
        attempts.append(kwargs["attempt"])
        return next(responses), {"content": "{}"}

    model._request_with_monitor = request
    parsed, _ = model._json_completion(
        "返回JSON对象。",
        {"business_controls": [{"control_key": "C-001"}]},
        purpose="control_applicability",
        recover_array_key="controls",
        retry_empty_array=True,
    )

    assert parsed["controls"][0]["control_key"] == "C-001"
    assert attempts == [1, 2]


def test_json_completion_rejects_length_truncated_output():
    model = object.__new__(OpenAICompatibleAuditModel)
    model.call_monitor = None
    model.run_id = None
    model.model_name = "test-model"
    model._dashscope_thinking = None
    model._deepseek_options = None
    model._max_output_tokens = {}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"items":[]}'))]
    )
    model._request_with_monitor = lambda *_args, **_kwargs: (
        response,
        {"content": '{"items":[]}', "finish_reason": "length"},
    )

    try:
        model._json_completion("返回JSON。", {}, purpose="scene_identification")
    except ValueError as exc:
        assert "截断" in str(exc)
    else:
        raise AssertionError("长度截断的响应不应被接受")


def test_dashscope_thinking_budget_is_selected_by_call_purpose():
    model = object.__new__(OpenAICompatibleAuditModel)
    model._dashscope_thinking = {"enable_thinking": True}
    model._dashscope_thinking_budgets = {
        "scene_identification": 256,
        "rule_bundle_audit": 768,
    }
    model._dashscope_default_thinking_budget = 1024

    assert model._dashscope_options("scene_identification")["thinking_budget"] == 256
    assert model._dashscope_options("rule_bundle_audit")["thinking_budget"] == 768
    assert model._dashscope_options("unknown")["thinking_budget"] == 1024


def test_deepseek_non_thinking_options_and_output_budget_are_applied():
    model = object.__new__(OpenAICompatibleAuditModel)
    model._dashscope_thinking = None
    model._deepseek_options = {"thinking": {"type": "disabled"}}
    model._max_output_tokens = {
        "scene_identification": 5000,
        "rule_bundle_audit": 8000,
    }

    assert model._provider_extra_body("rule_bundle_audit") == {
        "thinking": {"type": "disabled"}
    }
    assert model._max_tokens_for("scene_identification") == 5000
    assert model._max_tokens_for("rule_bundle_audit") == 8000


def test_bundle_payload_contains_every_rule_referenced_plan_evidence():
    model = object.__new__(OpenAICompatibleAuditModel)
    model.call_monitor = None
    captured = {}

    def completion(_system, payload, **_kwargs):
        captured.update(payload)
        return (
            {
                "decisions": [
                    {
                        "rule_id": "R-001",
                        "result": "未说明",
                        "applicability_status": "applicable",
                        "plan_obligation": "must_state",
                        "issue": "方案未说明。",
                        "risk_consequence": "存在风险。",
                        "suggestion": "补充说明。",
                        "decision_basis": "missing",
                        "selected_plan_segment_ids": [],
                        "confidence": 0.8,
                    }
                ]
            },
            {},
        )

    model._json_completion = completion
    required = {
        "id": "later-counter",
        "location": "P158",
        "text": "雨雪天气后进行检查。",
    }
    rule = {
        "rule_id": "R-001",
        "scene": "施工脚手架",
        "process": "季节性检查",
        "trigger_condition": "雨雪天气后",
        "requirement": "应进行检查",
        "threshold": "",
        "rule_effect": "应执行",
        "hazards": "高处坠落",
        "standard_code": "JGJ 80-2016",
        "clause": "3.0.8",
        "original_text": "雨雪天气后应进行检查。",
        "_scene_instance": {"id": "instance", "title": "脚手架", "location": "P001"},
        "_plan_evidence": [required],
        "_global_counter_evidence": [required],
        "_applicability": {"status": "applicable"},
        "_plan_scope": {"obligation": "must_state", "reason": "方案应说明"},
    }
    leading = [
        {"id": f"leading-{index}", "location": f"P{index:03d}", "text": "其他原文"}
        for index in range(25)
    ]

    model.judge_rule_bundle([rule], leading, {"R-001": []})

    sent_ids = {item["segment_id"] for item in captured["plan_evidence"]}
    assert "later-counter" in sent_ids


def test_pending_resolution_uses_whole_document_evidence_and_returns_final_decision():
    model = object.__new__(OpenAICompatibleAuditModel)
    model.call_monitor = None
    captured = {}

    def completion(system, payload, **_kwargs):
        captured["system_prompt"] = system
        captured.update(payload)
        return (
            {
                "decisions": [
                    {
                        "rule_id": "GZ-004",
                        "result": "未说明",
                        "applicability_status": "applicable",
                        "plan_obligation": "must_state",
                        "issue": "方案仅有施工技术交底，未说明高处作业安全技术交底及记录。",
                        "risk_consequence": "作业人员可能不了解高处作业风险。",
                        "suggestion": "补充高处作业安全技术交底和记录要求。",
                        "selected_plan_segment_ids": ["briefing"],
                        "decision_basis": "missing",
                        "requirement_checks": [
                            {"item_index": 0, "status": "missing", "evidence_ids": ["briefing"]}
                        ],
                        "confidence": 0.92,
                    }
                ]
            },
            {},
        )

    model._json_completion = completion
    rule = {
        "rule_id": "GZ-004",
        "scene": "高处作业综合管理",
        "trigger_condition": "高处作业施工前",
        "requirement": "应进行安全技术交底并记录",
        "original_text": "高处作业施工前，应进行安全技术交底并记录。",
        "hazards": "高处坠落",
        "_applicability": {"status": "applicable"},
        "_plan_scope": {"obligation": "must_state", "reason": "方案必须说明"},
        "_plan_evidence": [
            {
                "id": "briefing",
                "location": "P007",
                "text": "技术人员向各专业施工队作好施工技术交底。",
            }
        ],
    }
    previous = LLMDecision(
        result=AuditResult.NEEDS_HUMAN_REVIEW,
        applicability_status=ApplicabilityStatus.APPLICABLE,
        applicability_reason="高处作业场景已确认",
        plan_obligation=PlanObligation.MUST_STATE,
        plan_obligation_reason="方案必须说明",
        issue="现有证据不足以形成确定结论。",
        risk_consequence="高处坠落",
        suggestion="补充说明。",
        confidence=0.3,
    )

    decisions, _ = model.resolve_pending_bundle(
        [rule], rule["_plan_evidence"], {"GZ-004": previous}
    )

    assert decisions["GZ-004"].result is AuditResult.NOT_SPECIFIED
    assert captured["whole_document_evidence"][0]["segment_id"] == "briefing"
    assert "json" in captured["system_prompt"].lower()
