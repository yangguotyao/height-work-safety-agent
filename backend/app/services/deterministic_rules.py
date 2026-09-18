from __future__ import annotations

import re
from dataclasses import dataclass

from ..enums import AuditResult

BOUND_RE = re.compile(
    r"(?P<operator>不应大于|不宜大于|不得大于|不大于|不得超过|不应超过|不超过|≤|"
    r"不应小于|不宜小于|不得小于|不小于|不得低于|不应低于|不低于|≥)"
    r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|米)",
    re.I,
)
GENERIC_BOUND_RE = re.compile(
    r"(?P<operator>不应大于|不宜大于|不得大于|不大于|不得超过|不应超过|不超过|≤|"
    r"不应小于|不宜小于|不得小于|不小于|不得低于|不应低于|不低于|≥)"
    r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|米|跨|步|层)",
    re.I,
)


@dataclass(frozen=True)
class Bound:
    direction: str
    value_mm: float
    display_value: str
    context: str
    object_key: str | None
    object_label: str | None


@dataclass(frozen=True)
class DeterministicDecision:
    result: AuditResult
    issue: str
    suggestion: str
    confidence: float
    segment_id: str


# Numeric comparison is safe only when both values describe the same physical
# object.  Order matters: specific components must win over generic containers.
NUMERIC_OBJECT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wall_tie_spacing", ("连墙件横距", "连墙件纵距", "连墙件间距", "连墙杆间距")),
    (
        "work_scaffold_width",
        (
            "作业脚手架宽度",
            "架体宽度",
            "内外立杆间距",
            "立杆横距",
            "立杆排距",
            "横距",
            "排距",
        ),
    ),
    ("work_layer_height", ("作业层高度", "立杆步距", "步距")),
    ("toe_board", ("挡脚板", "踢脚板")),
    ("guardrail_post_spacing", ("防护栏杆立杆间距", "栏杆立杆间距", "立杆间距")),
    ("guardrail_rail_spacing", ("防护栏杆横杆间距", "栏杆横杆间距", "横杆间距")),
    ("opening_short_side", ("洞口短边", "短边边长")),
    ("window_sill_height", ("窗台高度",)),
    ("column_formwork_platform", ("柱模板", "柱、墙模板", "柱墙模板")),
    ("safety_rope_sag", ("安全绳下垂", "钢丝绳下垂", "自然下垂度")),
    ("platform_load", ("平台施工荷载", "施工荷载", "平台荷载")),
    ("platform_height", ("操作平台高度", "平台高度")),
    ("ladder_rung_spacing", ("横档间距", "梯档间距", "踏步间距")),
    ("ladder_width", ("梯梁净宽", "梯道宽度", "梯宽")),
    ("guardrail_height", ("防护栏杆", "护身栏杆", "栏杆上杆", "上杆")),
)


def numeric_object_at(text: str, measure_start: int, measure_end: int) -> tuple[str, str] | None:
    """Return the closest known physical object to one numeric measure."""
    candidates: list[tuple[int, int, str, str]] = []
    for priority, (key, aliases) in enumerate(NUMERIC_OBJECT_PATTERNS):
        for alias in aliases:
            for match in re.finditer(re.escape(alias), text):
                if match.end() <= measure_start:
                    distance = measure_start - match.end()
                elif match.start() >= measure_end:
                    distance = match.start() - measure_end
                else:
                    distance = 0
                if distance <= 32:
                    candidates.append((distance, priority, key, alias))
    if not candidates:
        return None
    _, _, key, alias = min(candidates)
    return key, alias


def numeric_object_key(text: str) -> str | None:
    """Classify an explicit object label supplied by the model."""
    match = numeric_object_at(text, len(text), len(text))
    if match:
        return match[0]
    for key, aliases in NUMERIC_OBJECT_PATTERNS:
        if any(alias in text for alias in aliases):
            return key
    return None


def _to_mm(value: float, unit: str) -> float:
    normalized = unit.lower()
    if normalized in {"m", "米"}:
        return value * 1000
    if normalized == "cm":
        return value * 10
    return value


def extract_bounds(text: str) -> list[Bound]:
    bounds = []
    for match in BOUND_RE.finditer(text):
        operator = match.group("operator")
        direction = "max" if any(word in operator for word in ("大于", "超过", "≤")) else "min"
        raw_value = float(match.group("value"))
        unit = match.group("unit")
        object_match = numeric_object_at(text, match.start(), match.end())
        bounds.append(
            Bound(
                direction=direction,
                value_mm=_to_mm(raw_value, unit),
                display_value=f"{match.group('value')}{unit}",
                context=text[max(0, match.start() - 32) : min(len(text), match.end() + 32)],
                object_key=object_match[0] if object_match else None,
                object_label=object_match[1] if object_match else None,
            )
        )
    return bounds


def _generic_bound_keys(text: str) -> set[tuple[str, str, str]]:
    keys = set()
    for match in GENERIC_BOUND_RE.finditer(text):
        operator = match.group("operator")
        direction = "max" if any(word in operator for word in ("大于", "超过", "≤")) else "min"
        unit = match.group("unit").lower().replace("米", "m")
        keys.add((direction, match.group("value"), unit))
    return keys


def _has_orientation_conflict(rule_context: str, plan_context: str) -> bool:
    horizontal_terms = ("水平", "横向")
    vertical_terms = ("竖向", "垂直", "高度")
    rule_horizontal = any(term in rule_context for term in horizontal_terms)
    plan_horizontal = any(term in plan_context for term in horizontal_terms)
    rule_vertical = any(term in rule_context for term in vertical_terms)
    plan_vertical = any(term in plan_context for term in vertical_terms)
    return (rule_horizontal and plan_vertical) or (rule_vertical and plan_horizontal)


def compare_numeric_rule(rule: dict, evidence: list[dict]) -> DeterministicDecision | None:
    # The curated requirement is the atomic rule. ``original_text`` may contain
    # several neighbouring requirements from one standard clause and would mix
    # unrelated bounds (for example scaffold width and work-layer height).
    rule_text = f"{rule['threshold']} {rule['requirement']}"
    if not extract_bounds(rule_text):
        rule_text = f"{rule_text} {rule['original_text']}"
    if not 1 <= len(_generic_bound_keys(rule_text)) <= 2:
        return None
    rule_bounds = extract_bounds(rule_text)
    unique_rule_bounds = {(bound.direction, bound.value_mm): bound for bound in rule_bounds}
    if not 1 <= len(unique_rule_bounds) <= 2:
        return None
    bounds = list(unique_rule_bounds.values())
    object_keys = {bound.object_key for bound in bounds}
    if len(object_keys) != 1 or None in object_keys:
        return None

    object_key = next(iter(object_keys))
    minimum = next((bound for bound in bounds if bound.direction == "min"), None)
    maximum = next((bound for bound in bounds if bound.direction == "max"), None)
    if len(bounds) == 2 and (minimum is None or maximum is None):
        return None

    instance_parameters = rule.get("_scene_instance", {}).get("parameters", {})
    parameter = instance_parameters.get(object_key)
    candidates: list[tuple[float, str, str, str]] = []
    if parameter:
        candidates.append(
            (
                _to_mm(float(parameter["value"]), str(parameter["unit"])),
                f"{parameter['value']:g}{parameter['unit']}",
                str(parameter["source_segment_id"]),
                str(parameter.get("label") or bounds[0].object_label or "相关参数"),
            )
        )

    if not candidates and object_key in {"work_scaffold_width", "work_layer_height"}:
        measure_re = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|米)", re.I)
        for segment in evidence:
            for match in measure_re.finditer(segment["text"]):
                context = segment["text"][max(0, match.start() - 14) : match.end()]
                if re.search(r"不应|不宜|不得|不小于|不大于|不超过", context):
                    continue
                plan_object = numeric_object_at(segment["text"], match.start(), match.end())
                if not plan_object or plan_object[0] != object_key:
                    continue
                candidates.append(
                    (
                        _to_mm(float(match.group("value")), match.group("unit")),
                        f"{match.group('value')}{match.group('unit')}",
                        str(segment["id"]),
                        plan_object[1],
                    )
                )
        unique_candidates = {
            (round(value, 6), display): (value, display, segment_id, label)
            for value, display, segment_id, label in candidates
        }
        candidates = list(unique_candidates.values())

    if len(candidates) != 1:
        if len(bounds) != 1:
            return None
        rule_bound = bounds[0]
        for segment in evidence:
            plan_bounds = [
                bound
                for bound in extract_bounds(segment["text"])
                if bound.direction == rule_bound.direction
                and bound.object_key == rule_bound.object_key
            ]
            if len(plan_bounds) != 1:
                continue
            plan_bound = plan_bounds[0]
            if _has_orientation_conflict(rule_bound.context, plan_bound.context):
                continue
            violates = (
                rule_bound.direction == "max"
                and plan_bound.value_mm > rule_bound.value_mm
            ) or (
                rule_bound.direction == "min"
                and plan_bound.value_mm < rule_bound.value_mm
            )
            relation = "上限" if rule_bound.direction == "max" else "下限"
            object_label = plan_bound.object_label or rule_bound.object_label or "相关对象"
            if violates:
                return DeterministicDecision(
                    result=AuditResult.NONCOMPLIANT,
                    issue=(
                        f"方案中{object_label}的{relation}{plan_bound.display_value}与规则要求的"
                        f"{relation}{rule_bound.display_value}冲突。"
                    ),
                    suggestion=f"将相关参数调整为满足规则要求的{rule_bound.display_value}，并同步修订交底。",
                    confidence=0.98,
                    segment_id=segment["id"],
                )
            return DeterministicDecision(
                result=AuditResult.COMPLIANT,
                issue=f"方案参数{plan_bound.display_value}满足规则边界{rule_bound.display_value}。",
                suggestion="保留该参数，并确保相关章节与现场交底一致。",
                confidence=0.88,
                segment_id=segment["id"],
            )
        return None
    plan_value, plan_display, segment_id, object_label = candidates[0]
    violates_min = minimum is not None and plan_value < minimum.value_mm
    violates_max = maximum is not None and plan_value > maximum.value_mm
    if minimum and maximum:
        rule_display = f"{minimum.display_value}—{maximum.display_value}"
    else:
        boundary = minimum or maximum
        assert boundary is not None
        rule_display = boundary.display_value
    if violates_min or violates_max:
        return DeterministicDecision(
            result=AuditResult.NONCOMPLIANT,
            issue=f"方案中{object_label}为{plan_display}，不满足规则要求的{rule_display}范围。",
            suggestion=f"将{object_label}调整到{rule_display}范围内，并同步方案描述、计算书和交底。",
            confidence=0.98,
            segment_id=segment_id,
        )
    return DeterministicDecision(
        result=AuditResult.COMPLIANT,
        issue=f"方案中{object_label}{plan_display}满足规则要求的{rule_display}范围。",
        suggestion="保持方案描述、计算书和现场交底中的参数一致。",
        confidence=0.9,
        segment_id=segment_id,
    )
