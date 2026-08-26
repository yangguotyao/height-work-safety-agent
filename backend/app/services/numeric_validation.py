from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from ..enums import AuditResult
from .deterministic_rules import numeric_object_at, numeric_object_key

MEASURE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>毫米|厘米|mm|cm|米|m|kg|千克|人|层|步|跨|个|处|次|小时|h|分钟|min)",
    re.I,
)
UNIT_ALIASES = {
    "毫米": "mm",
    "mm": "mm",
    "厘米": "cm",
    "cm": "cm",
    "米": "m",
    "m": "m",
    "千克": "kg",
    "kg": "kg",
    "小时": "h",
    "h": "h",
    "分钟": "min",
    "min": "min",
}
LENGTH_FACTORS = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


@dataclass(frozen=True)
class NumericValidation:
    valid: bool
    satisfied: bool | None
    reason: str


def _unit(value: str) -> str:
    normalized = value.strip().lower()
    return UNIT_ALIASES.get(normalized, normalized)


def _measure_present(text: str, expected_value: float, expected_unit: str) -> bool:
    normalized_unit = _unit(expected_unit)
    for match in MEASURE_RE.finditer(text):
        if not math.isclose(float(match.group("value")), expected_value, rel_tol=1e-9):
            continue
        if _unit(match.group("unit")) == normalized_unit:
            return True
    return False


def _measure_object_keys(text: str, expected_value: float, expected_unit: str) -> set[str]:
    normalized_unit = _unit(expected_unit)
    keys: set[str] = set()
    for match in MEASURE_RE.finditer(text):
        if not math.isclose(float(match.group("value")), expected_value, rel_tol=1e-9):
            continue
        if _unit(match.group("unit")) != normalized_unit:
            continue
        object_match = numeric_object_at(text, match.start(), match.end())
        if object_match:
            keys.add(object_match[0])
    return keys


def _comparable_values(
    plan_value: float, plan_unit: str, standard_value: float, standard_unit: str
) -> tuple[float, float] | None:
    plan_normalized = _unit(plan_unit)
    standard_normalized = _unit(standard_unit)
    if plan_normalized in LENGTH_FACTORS and standard_normalized in LENGTH_FACTORS:
        return (
            plan_value * LENGTH_FACTORS[plan_normalized],
            standard_value * LENGTH_FACTORS[standard_normalized],
        )
    if plan_normalized == standard_normalized:
        return plan_value, standard_value
    return None


def validate_numeric_comparison(
    comparison: dict[str, Any] | None,
    plan_sources: dict[str, str],
    standard_sources: dict[str, str],
    result: str | None = None,
) -> NumericValidation:
    if not comparison:
        return NumericValidation(False, None, "模型未返回完整的结构化数值比较。")
    try:
        plan_value = float(comparison["plan_value"])
        standard_value = float(comparison["standard_value"])
        plan_unit = str(comparison["plan_unit"])
        standard_unit = str(comparison["standard_unit"])
        operator = str(comparison["operator"])
        plan_segment_id = str(comparison["plan_segment_id"])
        standard_source_id = str(comparison["standard_source_id"])
        object_name = str(comparison["object"]).strip()
    except (KeyError, TypeError, ValueError):
        return NumericValidation(False, None, "模型数值比较字段不完整或类型无效。")

    plan_text = plan_sources.get(plan_segment_id)
    standard_text = standard_sources.get(standard_source_id)
    if not plan_text:
        return NumericValidation(False, None, "数值判断引用了输入之外的方案片段。")
    if not standard_text:
        return NumericValidation(False, None, "数值判断引用了输入之外的规范证据。")
    if not _measure_present(plan_text, plan_value, plan_unit):
        return NumericValidation(False, None, "方案数值或单位未在引用原文中出现。")
    if not _measure_present(standard_text, standard_value, standard_unit):
        return NumericValidation(False, None, "规范数值或单位未在引用条款中出现。")

    claimed_object = numeric_object_key(object_name)
    plan_objects = _measure_object_keys(plan_text, plan_value, plan_unit)
    standard_objects = _measure_object_keys(standard_text, standard_value, standard_unit)
    common_objects = plan_objects & standard_objects
    if not claimed_object or claimed_object not in common_objects:
        return NumericValidation(
            False,
            None,
            "方案数值与规范数值未绑定到同一可识别对象，禁止跨对象比较。",
        )

    values = _comparable_values(plan_value, plan_unit, standard_value, standard_unit)
    if values is None:
        return NumericValidation(False, None, "方案单位与规范单位无法可靠换算。")
    plan_normalized, standard_normalized = values
    operations = {
        ">=": plan_normalized >= standard_normalized,
        "<=": plan_normalized <= standard_normalized,
        ">": plan_normalized > standard_normalized,
        "<": plan_normalized < standard_normalized,
        "==": math.isclose(plan_normalized, standard_normalized, rel_tol=1e-9),
    }
    if operator not in operations:
        return NumericValidation(False, None, "模型返回了不支持的比较运算符。")
    satisfied = operations[operator]
    expected_result = (
        AuditResult.COMPLIANT.value if satisfied else AuditResult.NONCOMPLIANT.value
    )
    if result is not None and result != expected_result:
        return NumericValidation(False, satisfied, "模型文字结论与结构化数值计算不一致。")
    return NumericValidation(True, satisfied, "方案与规范数值均可追溯，单位及比较结果校验通过。")
