from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .retrieval import retrieve_plan_evidence

GENERAL_MANAGEMENT_TOPICS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("专项方案", "施工方案", "安全技术措施"), ("施工方案", "安全技术措施")),
    (("交底",), ("交底",)),
    (("体检", "资格", "持证"), ("体检", "考试合格", "上岗证", "持证")),
    (("防护用品", "安全帽", "安全带", "防滑鞋"), ("安全帽", "安全带", "防滑鞋")),
    (("初次", "培训"), ("初次", "培训")),
    (("警示标志", "红灯", "警告标志"), ("警示", "警戒", "红灯", "标志牌")),
    (("工具", "仪表", "电气设施", "设备"), ("工具", "设备", "检查")),
    (("坠落的物料", "物料", "余料", "废料"), ("物料", "材料", "工具", "零配件")),
    (("走道", "通道板", "登高用具"), ("走道", "通道", "梯道", "脚手板")),
    (("防火", "消防"), ("防火", "消防", "电焊", "气割")),
    (("雨", "雪", "霜", "雾", "大风", "防滑"), ("雨", "雪", "雾", "大风", "防滑")),
    (("临时拆除", "变动", "恢复"), ("拆改", "拆除", "恢复", "变更")),
    (("验收", "合格证明", "隐蔽验收", "变更记录"), ("验收", "合格牌", "检查")),
    (("定型化", "工具化", "黑黄", "红白"), ("定型", "工具", "红白", "标识")),
)


def _general_management_topic_supported(
    rule: dict[str, Any], segments: list[dict], instance: dict[str, Any] | None = None
) -> bool:
    """Do not expand every generic high-work rule from a synthetic full-document scene."""
    rule_text = " ".join(
        str(rule.get(field, ""))
        for field in ("trigger_condition", "requirement")
    )
    scoped_ids = {
        str(value) for value in (instance or {}).get("segment_ids", [])
    }
    relevant_segments = (
        [item for item in segments if str(item.get("id")) in scoped_ids]
        if scoped_ids
        else segments
    )
    document_text = " ".join(str(item.get("text", "")) for item in relevant_segments)
    for rule_terms, evidence_terms in GENERAL_MANAGEMENT_TOPICS:
        if any(term in rule_text for term in rule_terms):
            return any(term in document_text for term in evidence_terms)
    return False


WORK_SCAFFOLD_ONLY_RE = re.compile(
    r"作业脚手架|外脚手架|外架|悬挑脚手架|落地(?:式|作业)?脚手架|"
    r"连墙件|连墙点|作业层|脚手板|挡脚板|安全防护网.*架体|"
    r"附着处|附着结构"
)
SUPPORT_SCAFFOLD_RE = re.compile(r"支撑脚手架|模板支架|模板支撑架|支模架")


def _rule_matches_scaffold_object(
    rule: dict[str, Any], instance: dict[str, Any]
) -> bool:
    scene = str(instance.get("scene") or "")
    if scene not in {"施工脚手架", "脚手架搭设与拆除"}:
        return True
    object_type = str(instance.get("object_type") or "unspecified_scaffold")
    text = " ".join(
        str(rule.get(field, ""))
        for field in ("process", "trigger_condition", "requirement", "original_text")
    )
    if object_type == "template_support_scaffold":
        # A clause that mentions both support scaffolds and work scaffolds (for
        # example, "do not attach a support scaffold to a work scaffold") still
        # presupposes that a work scaffold exists.  A formwork support system by
        # itself must not make that clause applicable.
        if WORK_SCAFFOLD_ONLY_RE.search(text):
            return False
        if scene == "施工脚手架":
            # The current rule set is mainly for work scaffolds. Do not borrow those
            # rules for a formwork support system; retain only rules that explicitly
            # name support scaffolds.
            return bool(SUPPORT_SCAFFOLD_RE.search(text))
        return True
    if object_type == "unspecified_scaffold" and scene == "施工脚手架":
        return False
    return True


def _rule_matches_lifecycle_phase(
    rule: dict[str, Any], instance: dict[str, Any]
) -> bool:
    if instance.get("scene") != "脚手架搭设与拆除":
        return True
    title = str(instance.get("title") or "")
    phase = ""
    if "（拆除阶段）" in title:
        phase = "dismantling"
    elif "（搭设阶段）" in title:
        phase = "erection"
    if not phase:
        return True
    process = str(rule.get("process") or "")
    trigger = str(rule.get("trigger_condition") or "")
    requirement = str(rule.get("requirement") or "")
    process_removal = bool(re.search(r"拆除|拆架|拆卸", process))
    process_erection = bool(re.search(r"搭设|安装|场地准备|地基|基础", process))
    # The curated business process is more specific than a reused generic
    # trigger such as "搭设、使用或拆除施工脚手架时".
    if phase == "dismantling" and process_erection and not process_removal:
        return False
    if phase == "erection" and process_removal and not process_erection:
        return False
    text = f"{process} {trigger} {requirement}"
    removal_marked = bool(re.search(r"拆除|拆架|拆卸", text))
    erection_marked = bool(re.search(r"搭设|安装|场地准备|地基|基础", text))
    if phase == "dismantling" and erection_marked and not removal_marked:
        return False
    if phase == "erection" and removal_marked and not erection_marked:
        return False
    return True


VERTICAL_OPENING_RE = re.compile(r"竖向洞口|垂直洞口|墙面|窗台|门洞|电梯井|井道")
ELEVATOR_OPENING_RE = re.compile(r"电梯井|电梯井道|井口防护门")
HORIZONTAL_OPENING_RE = re.compile(
    r"楼板.{0,6}(?:开洞|洞口|孔洞)|楼面.{0,6}(?:开洞|洞口|孔洞)|"
    r"板面.{0,6}(?:开洞|洞口|孔洞)|非竖向洞口|水平洞口|预留孔洞"
)


def _rule_matches_opening_object(
    rule: dict[str, Any],
    instance: dict[str, Any],
    segment_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Keep a broad 洞口防护 control from borrowing rules for another opening type."""
    if instance.get("scene") != "洞口作业":
        return True
    anchor_ids = instance.get("anchor_segment_ids") or instance.get("segment_ids", [])
    scene_text = " ".join(
        f"{segment_by_id.get(str(segment_id), {}).get('heading_path', '')} "
        f"{segment_by_id.get(str(segment_id), {}).get('text', '')}"
        for segment_id in anchor_ids
    )
    rule_text = " ".join(
        str(rule.get(field, ""))
        for field in ("trigger_condition", "requirement", "original_text")
    )
    has_horizontal = bool(HORIZONTAL_OPENING_RE.search(scene_text))
    has_vertical = bool(VERTICAL_OPENING_RE.search(scene_text))
    has_elevator = bool(ELEVATOR_OPENING_RE.search(scene_text))

    if has_horizontal and not has_vertical:
        if ELEVATOR_OPENING_RE.search(rule_text) or VERTICAL_OPENING_RE.search(rule_text):
            return False
        # The load requirement applies when a cover plate is the selected
        # protective measure.  Do not report the missing specification before
        # the scheme has actually selected a cover plate.
        if "洞口盖板" in rule_text and not re.search(r"盖板|覆盖", scene_text):
            return False
    if has_vertical and not has_horizontal and "非竖向洞口" in rule_text:
        return False
    if not has_elevator and ELEVATOR_OPENING_RE.search(rule_text):
        return False
    return True


def _non_safety_guardrail_instance(
    instance: dict[str, Any], segment_by_id: dict[str, dict[str, Any]]
) -> bool:
    if instance.get("scene") != "防护栏杆":
        return False
    text = " ".join(
        f"{segment_by_id.get(str(segment_id), {}).get('heading_path', '')} "
        f"{segment_by_id.get(str(segment_id), {}).get('text', '')}"
        for segment_id in instance.get("segment_ids", [])
    )
    product_protection = any(
        marker in text for marker in ("成品保护", "防止人员穿行", "避免踩踏", "划伤")
    )
    fall_protection = any(
        marker in text for marker in ("临边", "临空", "洞口", "坠落", "挡脚板", "安全网")
    )
    return product_protection and not fall_protection


def build_control_candidates(
    rules: list[dict[str, Any]],
    scene_instances: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group rules by business control before creating atomic audit units.

    Evidence is retrieved from the complete document. Instance segments receive a
    ranking preference, but evidence in later or earlier chapters is never excluded.
    """
    rules_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in rules:
        rules_by_scene[str(rule["scene"])].append(rule)
    segment_by_id = {str(item["id"]): item for item in segments}
    controls: list[dict[str, Any]] = []
    for instance in scene_instances:
        if _non_safety_guardrail_instance(instance, segment_by_id):
            continue
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rule in rules_by_scene.get(str(instance["scene"]), []):
            if not _rule_matches_scaffold_object(rule, instance):
                continue
            if not _rule_matches_lifecycle_phase(rule, instance):
                continue
            if not _rule_matches_opening_object(rule, instance, segment_by_id):
                continue
            if (
                instance["scene"] == "高处作业综合管理"
                and not _general_management_topic_supported(rule, segments, instance)
            ):
                continue
            grouped[str(rule.get("process") or rule["scene"])].append(rule)
        preferred_ids = set(str(value) for value in instance.get("segment_ids", []))
        instance_evidence = [
            segment_by_id[value] for value in preferred_ids if value in segment_by_id
        ]
        for title, control_rules in grouped.items():
            evidence_by_id: dict[str, dict[str, Any]] = {
                str(item["id"]): {**item, "score": 1.0} for item in instance_evidence[:4]
            }
            required_evidence_ids: list[str] = []
            rule_evidence_ids: dict[str, list[str]] = {}
            for rule in control_rules:
                trigger_rule = {
                    **rule,
                    # At this stage search for work objects and triggering conditions,
                    # not for the safety measure whose absence will later be audited.
                    "scene": "",
                    "process": "",
                    "requirement": "",
                    "threshold": "",
                }
                rule_evidence = retrieve_plan_evidence(
                    trigger_rule,
                    segments,
                    limit=2,
                    preferred_segment_ids=preferred_ids,
                )
                rule_evidence_ids[str(rule["rule_id"])] = [
                    str(item["id"]) for item in rule_evidence
                ]
                if rule_evidence:
                    required_evidence_ids.append(str(rule_evidence[0]["id"]))
                for item in rule_evidence:
                    current = evidence_by_id.get(str(item["id"]))
                    if current is None or float(item.get("score", 0)) > float(
                        current.get("score", 0)
                    ):
                        evidence_by_id[str(item["id"])] = item
            ranked_evidence = sorted(
                evidence_by_id.values(),
                key=lambda item: (-float(item.get("score", 0)), item.get("sequence_no", 0)),
            )
            required_evidence_ids = list(dict.fromkeys(required_evidence_ids))
            evidence = [
                evidence_by_id[item_id]
                for item_id in required_evidence_ids
                if item_id in evidence_by_id
            ]
            included = {str(item["id"]) for item in evidence}
            for item in ranked_evidence:
                if str(item["id"]) in included:
                    continue
                evidence.append(item)
                included.add(str(item["id"]))
                if len(evidence) >= max(24, len(required_evidence_ids)):
                    break
            controls.append(
                {
                    "key": f"{instance['id']}|{instance['scene']}|{title}",
                    "scene": instance["scene"],
                    "title": title,
                    "instance": instance,
                    "rules": sorted(control_rules, key=lambda item: item["rule_id"]),
                    "evidence": evidence,
                    "required_evidence_ids": required_evidence_ids,
                    "rule_evidence_ids": rule_evidence_ids,
                }
            )
    return controls


def batch_controls(
    controls: list[dict[str, Any]],
    max_controls: int = 8,
    max_rules: int = 36,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    rule_count = 0
    for control in controls:
        size = len(control["rules"])
        if current and (len(current) >= max_controls or rule_count + size > max_rules):
            batches.append(current)
            current = []
            rule_count = 0
        current.append(control)
        rule_count += size
    if current:
        batches.append(current)
    return batches


def merge_control_evidence(controls: list[dict[str, Any]], limit: int = 24) -> list[dict]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for control in controls:
        for item in control["evidence"]:
            current = evidence_by_id.get(str(item["id"]))
            if current is None or float(item.get("score", 0)) > float(current.get("score", 0)):
                evidence_by_id[str(item["id"])] = item
    ranked = sorted(
        evidence_by_id.values(),
        key=lambda item: (-float(item.get("score", 0)), item.get("sequence_no", 0)),
    )
    required_ids = list(
        dict.fromkeys(
            str(item_id)
            for control in controls
            for item_id in control.get("required_evidence_ids", [])
        )
    )
    selected = [evidence_by_id[item_id] for item_id in required_ids if item_id in evidence_by_id]
    included = {str(item["id"]) for item in selected}
    for item in ranked:
        if str(item["id"]) in included:
            continue
        selected.append(item)
        included.add(str(item["id"]))
        if len(selected) >= max(limit, len(required_ids)):
            break
    return selected
