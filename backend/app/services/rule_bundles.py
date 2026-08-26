from __future__ import annotations

import hashlib
from typing import Any


def _evidence_ids(rule: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in rule.get("_plan_evidence", [])}


def _select_scene_instance(rule: dict[str, Any]) -> str:
    evidence_ids = _evidence_ids(rule)
    instances = rule.get("_scene_instances", [])
    if not instances:
        return f"scene:{rule['scene']}"
    ranked = sorted(
        instances,
        key=lambda item: (
            -len(evidence_ids.intersection(item.get("segment_ids", []))),
            item.get("location", ""),
            item.get("id", ""),
        ),
    )
    return str(ranked[0]["id"])


def enrich_rule_grouping(rule: dict[str, Any]) -> None:
    instance_id = _select_scene_instance(rule)
    control_title = str(rule.get("process") or rule["scene"]).strip()
    control_key = f"{rule['scene']}|{control_title}"
    rule["_scene_instance_id"] = instance_id
    rule["_control_key"] = control_key
    rule["_control_title"] = control_title
    rule["_bundle_group_key"] = f"{instance_id}|{control_key}"
    rule["_business_group_key"] = f"{instance_id}|{control_key}"


def _can_join(bundle: dict[str, Any], rule: dict[str, Any], max_size: int) -> bool:
    rules = bundle["rules"]
    if len(rules) >= max_size:
        return False
    first = rules[0]
    if first["_bundle_group_key"] != rule["_bundle_group_key"]:
        return False
    same_clause = any(
        item["standard_code"] == rule["standard_code"]
        and item["clause"] == rule["clause"]
        for item in rules
    )
    shared_evidence = any(_evidence_ids(item) & _evidence_ids(rule) for item in rules)
    return same_clause or shared_evidence


def build_rule_bundles(
    rules: list[dict[str, Any]], max_size: int = 6
) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for rule in sorted(rules, key=lambda item: (item["scene"], item["process"], item["rule_id"])):
        enrich_rule_grouping(rule)
        target = next(
            (bundle for bundle in bundles if _can_join(bundle, rule, max_size)), None
        )
        if target is None:
            target = {"rules": []}
            bundles.append(target)
        target["rules"].append(rule)

    for bundle in bundles:
        rule_ids = [rule["rule_id"] for rule in bundle["rules"]]
        raw = f"{bundle['rules'][0]['_bundle_group_key']}|{'|'.join(rule_ids)}"
        bundle["id"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for rule in bundle["rules"]:
            for evidence in rule.get("_plan_evidence", []):
                current = evidence_by_id.get(evidence["id"])
                if current is None or float(evidence.get("score", 0)) > float(
                    current.get("score", 0)
                ):
                    evidence_by_id[evidence["id"]] = evidence
        bundle["evidence"] = sorted(
            evidence_by_id.values(),
            key=lambda item: (-float(item.get("score", 0)), item.get("sequence_no", 0)),
        )[:8]
    return bundles
