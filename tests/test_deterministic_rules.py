from backend.app.enums import AuditResult
from backend.app.services.deterministic_rules import compare_numeric_rule


def _rule(requirement: str, original_text: str, threshold: str = "") -> dict:
    return {
        "threshold": threshold,
        "requirement": requirement,
        "original_text": original_text,
    }


def test_detects_clear_single_threshold_violation():
    rule = _rule(
        "防护栏杆立杆间距不应大于2m",
        "防护栏杆立杆间距不应大于2m。",
        "不应大于2m",
    )
    evidence = [
        {
            "id": "segment-1",
            "text": "结构临边防护栏杆立杆间距不大于2.5m。",
        }
    ]

    decision = compare_numeric_rule(rule, evidence)

    assert decision is not None
    assert decision.result is AuditResult.NONCOMPLIANT
    assert decision.segment_id == "segment-1"


def test_does_not_compare_different_objects_in_multi_threshold_rule():
    rule = _rule(
        "水平间距不得超过3跨，竖向间距不得超过3步",
        "水平间距不得超过3跨，竖向间距不得超过3步，悬臂高度不应超过2步，垂直间距不应大于4.0m。",
    )
    evidence = [
        {
            "id": "segment-2",
            "text": "连墙件水平间距不大于4.5m，竖向每层设置。",
        }
    ]

    assert compare_numeric_rule(rule, evidence) is None


def test_does_not_compare_toe_board_height_with_guardrail_height():
    rule = _rule(
        "垂直洞口应设置高度不小于1.2m的防护栏杆",
        "垂直洞口应设置高度不小于1.2m的防护栏杆，并设置挡脚板。",
        "不小于1.2m",
    )
    evidence = [
        {
            "id": "segment-3",
            "text": "设置1.2m高的两道护身栏杆，并设置不低于18cm的挡脚板。",
        }
    ]

    assert compare_numeric_rule(rule, evidence) is None
