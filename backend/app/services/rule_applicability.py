from __future__ import annotations

import re
from dataclasses import dataclass

from ..enums import ApplicabilityStatus


@dataclass(frozen=True)
class ApplicabilityDecision:
    status: ApplicabilityStatus
    reason: str
    supporting_segment_ids: tuple[str, ...] = ()


# These groups contain mutually exclusive construction variants. Automatic
# exclusion is permitted only when the rule names one variant and the plan gives
# positive evidence for another variant in the same group. Missing words alone
# never prove non-applicability.
EXCLUSIVE_VARIANT_GROUPS: tuple[tuple[tuple[str, ...], ...], ...] = (
    (
        ("附着式升降脚手架", "升降脚手架", "爬架"),
        ("悬挑式脚手架", "悬挑脚手架"),
        ("落地式脚手架", "落地脚手架", "落地作业脚手架"),
        ("支撑脚手架", "模板支架", "高支模"),
    ),
    (
        ("悬挑式操作平台", "悬挑卸料平台"),
        ("落地式操作平台",),
        ("移动式操作平台",),
    ),
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _matched_variants(text: str, group: tuple[tuple[str, ...], ...]) -> set[int]:
    compact = _compact(text)
    return {
        index
        for index, aliases in enumerate(group)
        if any(_compact(alias) in compact for alias in aliases)
    }


def assess_rule_applicability(
    rule: dict, evidence: list[dict], scene_instances: list[dict]
) -> ApplicabilityDecision:
    rule_text = " ".join(
        str(rule.get(field, ""))
        for field in ("scene", "process", "trigger_condition", "requirement", "original_text")
    )
    plan_parts = [
        f"{item.get('heading_path', '')} {item.get('text', '')}" for item in evidence
    ]
    plan_parts.extend(
        f"{item.get('scene', '')} {item.get('title', '')}" for item in scene_instances
    )
    plan_text = " ".join(plan_parts)
    compact_rule = _compact(rule_text)
    compact_plan = _compact(plan_text)
    scene_anchor_ids = tuple(str(item["id"]) for item in evidence[:2])

    # Conditional operations require positive plan evidence. Their absence does
    # not mean the scheme omitted a mandatory measure; it usually means that the
    # operation or environmental condition is not part of this scheme.
    positive_condition_triggers = (
        (
            ("动火", "电焊", "气焊", "气割", "接火斗"),
            ("动火", "电焊", "气焊", "气割", "焊接"),
            "脚手架内动火作业",
        ),
        (
            ("雨", "雪", "霜", "雾", "雷雨", "大风", "沙尘暴", "恶劣气候"),
            ("雨", "雪", "霜", "雾", "雷雨", "大风", "沙尘暴", "恶劣天气"),
            "恶劣天气施工或复工作业",
        ),
    )
    for rule_markers, plan_markers, label in positive_condition_triggers:
        if any(marker in compact_rule for marker in rule_markers) and not any(
            marker in compact_plan for marker in plan_markers
        ):
            return ApplicabilityDecision(
                status=ApplicabilityStatus.NOT_APPLICABLE,
                reason=f"规则以“{label}”为前提，当前方案证据未表明该条件成立。",
                supporting_segment_ids=scene_anchor_ids,
            )

    dismantling_rule = bool(
        re.search(r"拆除|拆卸|拆架|构配件下运|严禁抛掷", compact_rule)
    )
    dismantling_plan = bool(re.search(r"拆除|拆卸|拆架", compact_plan))
    if dismantling_rule and not dismantling_plan:
        return ApplicabilityDecision(
            status=ApplicabilityStatus.NOT_APPLICABLE,
            reason="规则仅适用于脚手架拆除工序，当前方案证据未表明包含拆除作业。",
            supporting_segment_ids=scene_anchor_ids,
        )

    # Some clauses are conditional on a very specific work object. A scaffold
    # scheme should not be reported as missing deep-foundation, external-stair or
    # hoist-platform measures merely because it contains a generic ladder/edge
    # scene. These qualifiers require positive object evidence.
    positive_object_triggers = (
        (("深基坑", "坑壁", "运土工具"), ("深基坑", "基坑", "坑壁", "土方开挖"), "深基坑作业"),
        (("外设楼梯",), ("外设楼梯", "室外楼梯"), "外设楼梯"),
        (
            ("施工升降机", "龙门架", "井架物料提升机", "停层平台"),
            ("施工升降机", "龙门架", "井架", "物料提升机", "停层平台"),
            "垂直运输停层平台",
        ),
    )
    for rule_markers, plan_markers, label in positive_object_triggers:
        if any(marker in compact_rule for marker in rule_markers) and not any(
            marker in compact_plan for marker in plan_markers
        ):
            return ApplicabilityDecision(
                status=ApplicabilityStatus.NOT_APPLICABLE,
                reason=f"规则限定为“{label}”，当前已识别方案对象未包含该专项对象。",
                supporting_segment_ids=scene_anchor_ids,
            )

    if "没有设置外脚手架" in compact_rule and any(
        marker in compact_plan
        for marker in ("外脚手架", "双排钢管脚手架", "全高全封闭")
    ):
        return ApplicabilityDecision(
            status=ApplicabilityStatus.NOT_APPLICABLE,
            reason="规则针对未设置外脚手架的工程，方案已明确设置外脚手架。",
            supporting_segment_ids=tuple(
                str(item["id"])
                for item in evidence
                if any(
                    marker in _compact(
                        f"{item.get('heading_path', '')}{item.get('text', '')}"
                    )
                    for marker in ("外脚手架", "双排钢管脚手架", "全高全封闭")
                )
            )[:4]
            or scene_anchor_ids,
        )

    for group in EXCLUSIVE_VARIANT_GROUPS:
        rule_variants = _matched_variants(rule_text, group)
        plan_variants = _matched_variants(plan_text, group)
        if len(rule_variants) != 1 or not plan_variants:
            continue
        rule_variant = next(iter(rule_variants))
        supporting_ids = tuple(
            str(item["id"])
            for item in evidence
            if _matched_variants(
                f"{item.get('heading_path', '')} {item.get('text', '')}", group
            )
            & plan_variants
        )[:4]
        if rule_variant not in plan_variants and len(plan_variants) == 1:
            plan_variant = next(iter(plan_variants))
            rule_name = group[rule_variant][0]
            plan_name = group[plan_variant][0]
            return ApplicabilityDecision(
                status=ApplicabilityStatus.NOT_APPLICABLE,
                reason=f"规则限定为“{rule_name}”，方案证据明确为“{plan_name}”，作业类型相斥。",
                supporting_segment_ids=supporting_ids,
            )
        if rule_variant in plan_variants:
            return ApplicabilityDecision(
                status=ApplicabilityStatus.APPLICABLE,
                reason=f"规则与方案均明确涉及“{group[rule_variant][0]}”。",
                supporting_segment_ids=supporting_ids,
            )

    if not rule.get("trigger_condition"):
        return ApplicabilityDecision(
            status=ApplicabilityStatus.APPLICABLE,
            reason="规则未设置额外触发条件，已识别场景即满足适用前提。",
        )
    return ApplicabilityDecision(
        status=ApplicabilityStatus.UNCERTAIN,
        reason="已识别对应场景，但规则仍含具体触发条件；需结合方案证据判断，不能因未提及而自动排除。",
    )
