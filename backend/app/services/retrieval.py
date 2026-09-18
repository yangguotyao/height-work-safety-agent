from __future__ import annotations

import re
from collections import Counter

PUNCTUATION_RE = re.compile(r"[\s，。；：、（）()《》〈〉\[\]【】/\\]+")
ALNUM_RE = re.compile(r"[a-zA-Z]+\d+(?:\.\d+)*|\d+(?:\.\d+)?(?:mm|cm|m|kg|t)?", re.I)
DOMAIN_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("作业脚手架宽度", "架体宽度", "立杆横距", "立杆排距", "排距", "内外立杆间距"),
    ("作业层高度", "立杆步距", "步距"),
    ("浇筑混凝土", "混凝土浇筑", "施加荷载"),
    ("架体下", "支模底下", "支架下方", "脚手架下方"),
    ("严禁有人", "不得有人", "禁止人员", "禁止无关人员", "严禁人员"),
    ("上下通道", "施工通道", "梯道", "爬梯", "梯子", "登高设施"),
    ("安全技术交底", "方案交底", "三级安全技术交底"),
    ("警戒线", "警戒区", "警示标志", "警告标志"),
    ("雨雪后", "雨雪天气后", "雨雪过后", "雨、雪天气后"),
    ("个人防护用品", "安全防护用品", "安全帽", "安全带", "防滑鞋"),
    ("材料工具", "材料、工具", "工具材料", "随手工具"),
    ("防护棚", "安全防护棚", "通道防护", "出入口防护"),
    ("六级风", "六级以上", "6级以上", "6级及以上", "六级及以上"),
    ("停止高处作业", "停止露天或高空作业", "停止架上作业", "不得进行露天攀登"),
    ("防滑", "防止滑倒", "脚手板防滑", "小雨防滑"),
)


def semantic_control_signature(rule: dict) -> str:
    """Return a conservative signature for requirements expressed by multiple rules."""
    text = "".join(
        str(rule.get(field, ""))
        for field in ("process", "trigger_condition", "requirement", "original_text")
    )
    if (
        any(term in text for term in ("6级", "六级"))
        and any(term in text for term in ("大风", "强风"))
        and any(term in text for term in ("停止", "不得进行"))
        and any(term in text for term in ("高处作业", "高空作业", "架上作业", "攀登", "悬空"))
    ):
        return "weather_stop_six_level_wind"
    if "防滑" in text and any(term in text for term in ("雨", "雪", "霜", "雾")):
        return "wet_weather_slip_control"
    if "安全设施" in text and any(term in text for term in ("雨雪", "雨、雪", "天气后")):
        return "post_weather_safety_facility_check"
    if any(term in text for term in ("安全技术交底", "方案交底")):
        return "high_work_safety_briefing"
    if "验收" in text and "安全防护设施" in text:
        return "safety_facility_acceptance"
    if "工具" in text and any(term in text for term in ("防坠", "工具袋")):
        return "tool_fall_prevention"
    if any(term in text for term in ("不得抛掷", "不得抛", "不得向下丢弃")):
        return "material_no_throw"
    return ""


def share_semantic_control_evidence(rules: list[dict], limit: int = 8) -> None:
    """Share whole-document evidence across equivalent rules before model judgment."""
    by_signature: dict[str, list[dict]] = {}
    for rule in rules:
        signature = semantic_control_signature(rule)
        if signature:
            by_signature.setdefault(signature, []).append(rule)
    for related_rules in by_signature.values():
        if len(related_rules) < 2:
            continue
        pooled: dict[str, dict] = {}
        for rule in related_rules:
            for item in rule.get("_plan_evidence", []):
                current = pooled.get(str(item["id"]))
                if current is None or float(item.get("score", 0)) > float(
                    current.get("score", 0)
                ):
                    pooled[str(item["id"])] = item
        ranked = sorted(
            pooled.values(),
            key=lambda item: (-float(item.get("score", 0)), item.get("sequence_no", 0)),
        )[:limit]
        for rule in related_rules:
            existing = {str(item["id"]): item for item in rule.get("_plan_evidence", [])}
            for item in ranked:
                existing.setdefault(str(item["id"]), item)
            rule["_plan_evidence"] = list(existing.values())
            counter_ids = {
                str(item["id"]) for item in rule.get("_global_counter_evidence", [])
            }
            rule["_global_counter_evidence"] = [
                *rule.get("_global_counter_evidence", []),
                *(item for item in ranked if str(item["id"]) not in counter_ids),
            ][:limit]


def explicit_semantic_compliance_evidence(
    rule: dict, evidence: list[dict]
) -> dict | None:
    """Resolve only high-confidence equivalent wording, leaving other semantics to the model."""
    if semantic_control_signature(rule) != "weather_stop_six_level_wind":
        return None
    for item in evidence:
        text = "".join(
            str(item.get(field, "")) for field in ("heading_path", "text")
        ).replace(" ", "")
        has_level = any(term in text for term in ("6级", "六级"))
        has_stop = any(term in text for term in ("停止", "不得进行", "严禁"))
        has_work = any(
            term in text for term in ("高处作业", "高空作业", "架上作业", "露天作业")
        )
        has_wind = any(term in text for term in ("大风", "强风", "风以上", "风及以上"))
        if has_level and has_stop and has_work and has_wind:
            return item
    return None


def _cjk_bigrams(text: str) -> Counter[str]:
    compact = PUNCTUATION_RE.sub("", text.lower())
    grams = Counter(
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
        if not compact[index : index + 2].isdigit()
    )
    for token in ALNUM_RE.findall(text):
        grams[token.lower()] += 3
    return grams


def similarity(query: str, text: str) -> float:
    query_grams = _cjk_bigrams(query)
    if not query_grams:
        return 0.0
    text_grams = _cjk_bigrams(text)
    overlap = sum(min(weight, text_grams.get(gram, 0)) for gram, weight in query_grams.items())
    return overlap / max(sum(query_grams.values()), 1)


def rule_query(rule: dict) -> str:
    return " ".join(
        [
            rule["scene"],
            rule["process"],
            rule["trigger_condition"],
            rule["requirement"],
            rule["threshold"],
        ]
    )


def _counter_query_variants(rule: dict) -> list[str]:
    full = " ".join(
        str(rule.get(field, ""))
        for field in ("process", "requirement", "threshold", "original_text")
    )
    focused = " ".join(
        str(rule.get(field, "")) for field in ("requirement", "threshold")
    )
    clauses = [
        value.strip()
        for source in (str(rule.get("requirement", "")), str(rule.get("original_text", "")))
        for value in re.split(r"[；;。]", source)
        if len(value.strip()) >= 4
    ]
    return list(dict.fromkeys([full, focused, *clauses]))


def retrieve_plan_evidence(
    rule: dict,
    segments: list[dict],
    limit: int = 4,
    preferred_segment_ids: set[str] | None = None,
) -> list[dict]:
    query = rule_query(rule)
    preferred = preferred_segment_ids or set()
    scored = []
    for segment in segments:
        target = f"{segment['heading_path']} {segment['text']}"
        score = similarity(query, target)
        query_compact = PUNCTUATION_RE.sub("", query)
        target_compact = PUNCTUATION_RE.sub("", target)
        synonym_hits = sum(
            1
            for group in DOMAIN_SYNONYM_GROUPS
            if any(term in query_compact for term in group)
            and any(term in target_compact for term in group)
        )
        score += 0.12 * synonym_hits
        if score > 0:
            if segment["id"] in preferred:
                score += 0.12
            scored.append({**segment, "score": round(score, 4)})
    scored.sort(key=lambda item: (-item["score"], item["sequence_no"]))
    return scored[:limit]


def retrieve_global_counter_evidence(
    rule: dict,
    segments: list[dict],
    limit: int = 4,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Search the whole scheme for evidence that may disprove an omission.

    This query intentionally emphasizes the required measure and threshold instead
    of the scene title. Results are diversified by chapter so a later section is not
    hidden by several near-duplicate paragraphs in the current scene section.
    """
    queries = _counter_query_variants(rule)
    excluded = exclude_ids or set()
    scored: list[dict] = []
    for segment in segments:
        if str(segment["id"]) in excluded:
            continue
        target = f"{segment['heading_path']} {segment['text']}"
        score = max((similarity(query, target) for query in queries), default=0.0)
        query_compacts = [PUNCTUATION_RE.sub("", query) for query in queries]
        target_compact = PUNCTUATION_RE.sub("", target)
        synonym_hits = sum(
            1
            for group in DOMAIN_SYNONYM_GROUPS
            if any(any(term in query_compact for term in group) for query_compact in query_compacts)
            and any(term in target_compact for term in group)
        )
        score += 0.15 * synonym_hits
        if score > 0:
            scored.append({**segment, "score": round(score, 4)})
    scored.sort(key=lambda item: (-item["score"], item["sequence_no"]))
    selected: list[dict] = []
    per_heading: Counter[str] = Counter()
    for item in scored:
        heading = str(item.get("heading_path") or "文档正文")
        if per_heading[heading] >= 2:
            continue
        selected.append(item)
        per_heading[heading] += 1
        if len(selected) >= limit:
            break
    return selected


def rank_rules(rules: list[dict], segments: list[dict], limit: int) -> list[dict]:
    document_sample = "\n".join(
        f"{segment['heading_path']} {segment['text']}" for segment in segments
    )
    ranked = []
    for rule in rules:
        score = similarity(rule_query(rule), document_sample)
        risk_bonus = 0.08 if rule["risk_level"] == "重大" else 0.03
        ranked.append(({**rule, "routing_score": round(score + risk_bonus, 4)}, score + risk_bonus))
    ranked.sort(key=lambda pair: (-pair[1], pair[0]["rule_id"]))
    return [rule for rule, _ in ranked[:limit]]
