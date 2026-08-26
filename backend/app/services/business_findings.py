from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from ..enums import AuditResult
from .evidence_dedup import same_standard_source

ACTIONABLE_RESULTS = {
    AuditResult.NONCOMPLIANT.value,
    AuditResult.NOT_SPECIFIED.value,
}
RESULT_PRIORITY = {
    AuditResult.NONCOMPLIANT.value: 3,
    AuditResult.NOT_SPECIFIED.value: 2,
}


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    result = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    return result if limit is None else result[:limit]


def _business_text(value: str) -> bool:
    return not any(
        marker in value
        for marker in ("模型未提供", "模型批量响应", "输出不完整", "需人工核对", "需要人工确认")
    )


def _plan_evidence(
    items: list[dict[str, Any]], limit: int = 4
) -> list[dict[str, str]]:
    """Return distinct source segments instead of concatenated per-rule quotes."""
    result: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seen_quotes: set[str] = set()
    for item in items:
        plan_entries = [
            entry
            for entry in item.get("evidences", [])
            if entry.get("evidence_type") == "plan" and entry.get("quote")
        ]
        if not plan_entries and item.get("plan_quote"):
            plan_entries = [
                {
                    "source_id": "",
                    "quote": item["plan_quote"],
                    "location": item.get("source_location", ""),
                }
            ]
        for entry in plan_entries:
            source_id = str(entry.get("source_id") or "").strip()
            quote = str(entry.get("quote") or "").strip()
            quote_key = "".join(quote.split())
            duplicate_source = bool(source_id and source_id in seen_sources)
            if not quote_key or duplicate_source or quote_key in seen_quotes:
                continue
            if source_id:
                seen_sources.add(source_id)
            seen_quotes.add(quote_key)
            result.append(
                {"quote": quote, "location": str(entry.get("location") or "").strip()}
            )
            if len(result) >= limit:
                return result
    return result


def build_business_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        # Uncertain machine judgments are retained in atomic audit traces but are
        # not user-facing problems. Only confirmed actionable findings appear here.
        if item["result"] not in ACTIONABLE_RESULTS:
            continue
        key = item.get("business_group_key") or f"{item['scene']}|{item['rule_id']}"
        groups.setdefault(key, []).append(item)

    findings: list[dict[str, Any]] = []
    for key, atomic_items in groups.items():
        atomic_items.sort(key=lambda item: item["rule_id"])
        result = max(atomic_items, key=lambda item: RESULT_PRIORITY[item["result"]])["result"]
        counts = Counter(item["result"] for item in atomic_items)
        issues = _unique(
            [item["issue"] for item in atomic_items if _business_text(item["issue"])], 6
        )
        if not issues:
            issues = ["方案未完整说明该业务控制项要求。"]
        if len(atomic_items) == 1:
            issue = issues[0]
        else:
            summary = "、".join(f"{name}{count}项" for name, count in counts.items())
            issue = f"该业务控制项关联{len(atomic_items)}条原子规则（{summary}）。"
            issue += "\n" + "\n".join(f"- {value}" for value in issues)
        suggestions = _unique([item["suggestion"] for item in atomic_items], 4)
        risks = _unique([item["risk_consequence"] for item in atomic_items], 3)
        plan_evidence = _plan_evidence(atomic_items)
        basis: list[dict[str, Any]] = []
        for item in atomic_items:
            for entry in item.get("basis", []):
                if any(same_standard_source(entry, existing) for existing in basis):
                    continue
                basis.append(entry)
        finding_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        findings.append(
            {
                "id": finding_id,
                "scene": atomic_items[0]["scene"],
                "title": atomic_items[0].get("control_title") or atomic_items[0]["scene"],
                "result": result,
                "issue": issue,
                "risk_consequence": "；".join(risks),
                "suggestion": "\n".join(f"- {value}" for value in suggestions),
                "plan_quote": "\n\n".join(entry["quote"] for entry in plan_evidence),
                "source_location": "；".join(
                    entry["location"] for entry in plan_evidence if entry["location"]
                ),
                "confidence": min(float(item["confidence"]) for item in atomic_items),
                "rule_ids": [item["rule_id"] for item in atomic_items],
                "basis": basis,
                "atomic_items": atomic_items,
            }
        )
    return sorted(findings, key=lambda item: (item["scene"], item["title"], item["id"]))
