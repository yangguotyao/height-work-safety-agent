from __future__ import annotations

import hashlib
import re
from typing import Any

from ..enums import ApplicabilityStatus, AuditResult, PlanObligation


def _first(segments: list[dict[str, Any]], pattern: str) -> dict[str, Any] | None:
    regex = re.compile(pattern, re.I)
    return next(
        (
            segment
            for segment in segments
            if regex.search(f"{segment.get('heading_path', '')} {segment.get('text', '')}")
        ),
        None,
    )


def _item(
    run_id: str,
    code: str,
    issue: str,
    suggestion: str,
    risk: str,
    evidences: list[dict[str, Any]],
    result: AuditResult = AuditResult.NONCOMPLIANT,
) -> dict[str, Any]:
    rule_id = f"PLAN-CONSISTENCY-{code}"
    bundle_id = hashlib.sha256(rule_id.encode("utf-8")).hexdigest()[:24]
    plan_evidence = [
        {
            "evidence_type": "plan",
            "source_id": str(segment["id"]),
            "quote": str(segment["text"]),
            "location": str(segment.get("location") or ""),
            "score": 1.0,
        }
        for segment in evidences
    ]
    return {
        "run_id": run_id,
        "rule_id": rule_id,
        "scene": "施工脚手架",
        "plan_quote": "\n".join(entry["quote"] for entry in plan_evidence),
        "source_location": "；".join(entry["location"] for entry in plan_evidence),
        "issue": issue,
        "basis": [
            {
                "standard": "方案内部一致性检查",
                "standard_code": "PLAN-CONSISTENCY",
                "standard_status": "internal_check",
                "clause": code,
                "rule_id": rule_id,
                "quote": "同一脚手架对象的方案描述、材料参数与计算书输入应相互一致。",
            }
        ],
        "risk_consequence": risk,
        "result": result.value,
        "applicability_status": ApplicabilityStatus.APPLICABLE.value,
        "applicability_reason": "方案正文与计算书均出现可直接对照的同类描述。",
        "plan_obligation": PlanObligation.MUST_STATE.value,
        "plan_obligation_reason": "属于方案内部参数和计算假定的一致性要求。",
        "bundle_id": bundle_id,
        "business_group_key": rule_id,
        "control_title": "方案描述与计算书一致性",
        "suggestion": suggestion,
        "confidence": 0.98,
        "model_raw": {"provider": "deterministic_plan_consistency", "code": code},
        "evidences": plan_evidence,
    }


def detect_plan_consistency_issues(
    run_id: str, segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find a small set of explicit narrative/calculation contradictions."""
    items: list[dict[str, Any]] = []

    channel_14 = _first(segments, r"(?:14B|14b)\s*[#＃]?(?:号)?.{0,6}?槽钢|槽钢.{0,10}(?:14B|14b)")
    channel_16 = _first(
        segments,
        r"\[?(?:16A|16a|16[#＃]|16号)\s*(?:号)?\s*槽钢|槽钢.{0,10}(?:16A|16a|16[#＃]|16号)",
    )
    if channel_14 and channel_16 and channel_14["id"] != channel_16["id"]:
        items.append(
            _item(
                run_id,
                "CHANNEL-STEEL",
                "悬挑脚手架型钢规格在方案描述与计算内容中分别出现14B和16号（16a）槽钢，取值不一致。",
                "统一悬挑型钢的设计规格，并同步修订材料表、搭设说明和计算书输入。",
                "型钢规格与计算模型不一致，可能导致现场选材和计算结论无法对应。",
                [channel_14, channel_16],
            )
        )

    pad = _first(segments, r"20\s*(?:cm|厘米|㎝)?\s*[×xX*]\s*20\s*(?:cm|厘米|㎝)")
    base_area = _first(
        segments,
        r"(?:基础底面面积|底座底面面积).{0,50}?A\s*=\s*0\.25(?:\s*m2|\s*m²)?",
    )
    if pad and base_area and pad["id"] != base_area["id"]:
        items.append(
            _item(
                run_id,
                "BASE-AREA",
                "落地脚手架垫块按20cm×20cm描述，其面积为0.04m²，但计算书采用基础底面面积0.25m²。",
                "按实际采用的垫板或底座尺寸统一基础底面积，并据此复核地基承载力计算。",
                "基础受力面积取值不一致，会使地基承载力验算与现场构造脱节。",
                [pad, base_area],
            )
        )

    rope_used = _first(
        segments,
        r"(?:采用|设置|使用|拉设).{0,18}(?:钢丝绳|钢丝拉绳)|(?:钢丝绳|钢丝拉绳).{0,18}(?:采用|设置|使用|拉设)",
    )
    rope_absent = _first(
        segments,
        r"(?:没有|未设|不设|未采用).{0,10}(?:钢丝绳|钢丝拉绳|支杆).{0,15}(?:拉结|连接)?",
    )
    if rope_used and rope_absent and rope_used["id"] != rope_absent["id"]:
        items.append(
            _item(
                run_id,
                "WIRE-ROPE-ASSUMPTION",
                "方案描述设置钢丝绳，而计算书按未设置钢丝绳或支杆拉结的条件计算，两者关系未说明。",
                "明确计算书是否采用不计钢丝绳有利作用的保守假定，并统一构造说明与计算假定。",
                "计算假定含义不清会造成审查人员无法判断计算模型是否覆盖实际搭设方式。",
                [rope_used, rope_absent],
                AuditResult.NOT_SPECIFIED,
            )
        )

    return items
