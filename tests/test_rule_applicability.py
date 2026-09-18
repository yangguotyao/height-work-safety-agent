from backend.app.enums import ApplicabilityStatus
from backend.app.services.rule_applicability import assess_rule_applicability


def _rule(requirement: str, trigger: str = "搭设脚手架时") -> dict:
    return {
        "scene": "施工脚手架",
        "process": "搭设",
        "trigger_condition": trigger,
        "requirement": requirement,
        "original_text": requirement,
    }


def _evidence(text: str) -> list[dict]:
    return [{"id": "seg-1", "heading_path": "方案选择", "text": text}]


def test_excludes_only_when_plan_proves_mutually_exclusive_variant():
    decision = assess_rule_applicability(
        _rule("悬挑脚手架立杆底部应可靠连接"),
        _evidence("本工程全部采用落地式脚手架。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.NOT_APPLICABLE
    assert decision.supporting_segment_ids == ("seg-1",)


def test_missing_variant_evidence_is_uncertain_not_not_applicable():
    decision = assess_rule_applicability(
        _rule("悬挑脚手架立杆底部应可靠连接"),
        _evidence("方案设置脚手架安全技术措施。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.UNCERTAIN


def test_rule_covering_multiple_variants_is_not_automatically_excluded():
    decision = assess_rule_applicability(
        _rule("落地作业脚手架、悬挑脚手架均应同步搭设"),
        _evidence("本工程采用支撑脚手架。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.UNCERTAIN


def test_high_work_alone_does_not_make_every_climbing_rule_applicable():
    rule = {
        **_rule("登高作业应借助施工通道或梯子"),
        "scene": "攀登作业",
    }
    instances = [
        {
            "scene": "攀登作业",
            "title": "高处作业上下通行",
            "sources": ["deterministic_inherent_access"],
        }
    ]

    decision = assess_rule_applicability(rule, [], instances)

    assert decision.status is ApplicabilityStatus.UNCERTAIN


def test_deep_foundation_climbing_rule_does_not_expand_from_scaffold_ladder_scene():
    rule = {
        **_rule("作业人员严禁沿坑壁、支撑或乘运土工具上下", "深基坑施工时"),
        "scene": "攀登作业",
        "original_text": "深基坑施工应设置扶梯，作业人员严禁沿坑壁上下。",
    }

    decision = assess_rule_applicability(
        rule,
        _evidence("脚手架人员上下应走人行梯道，不准攀爬架体。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.NOT_APPLICABLE


def test_hot_work_rule_requires_positive_hot_work_evidence():
    decision = assess_rule_applicability(
        _rule("脚手架内动火应设置接火斗和灭火器", "脚手架内动火时"),
        _evidence("本工程搭设落地式脚手架。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.NOT_APPLICABLE


def test_weather_rule_requires_positive_weather_evidence():
    decision = assess_rule_applicability(
        _rule("雨雪天气后应检查安全设施", "雨雪天气后恢复作业时"),
        _evidence("本工程搭设落地式脚手架。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.NOT_APPLICABLE


def test_dismantling_rule_requires_dismantling_work_evidence():
    decision = assess_rule_applicability(
        _rule("拆除杆件严禁向下抛掷", "脚手架拆除时"),
        _evidence("本阶段仅进行脚手架搭设。"),
        [],
    )

    assert decision.status is ApplicabilityStatus.NOT_APPLICABLE
