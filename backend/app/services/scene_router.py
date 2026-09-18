from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from .document_scope import is_meta_discussion_segment

SCENE_ALIASES: dict[str, tuple[str, ...]] = {
    "高处作业综合管理": ("高处作业", "安全技术交底", "登高作业"),
    "施工脚手架": ("脚手架", "排栅", "架子工", "架体"),
    "脚手架搭设与拆除": ("脚手架搭设", "脚手架拆除", "拆架", "搭拆"),
    "附着式升降脚手架": ("附着式升降", "升降脚手架", "爬架"),
    "临边作业": (
        "临边",
        "楼层边",
        "屋面边",
        "楼梯侧边",
        "基坑周边",
        "坑周边",
        "周边防护",
    ),
    "洞口作业": ("洞口", "井口", "电梯井", "采光井", "预留孔"),
    "防护栏杆": ("防护栏杆", "护身栏杆", "临边栏杆", "护栏"),
    "悬空作业": ("悬空作业", "悬臂结构", "高处拆模", "钢结构安装", "吊装作业"),
    "攀登作业": ("攀登", "登高", "梯道", "爬梯", "梯子"),
    "交叉作业": (
        "交叉作业",
        "上下同时",
        "垂直交叉",
        "安全防护棚",
        "防护棚",
        "通道口防护",
        "出入口防护",
    ),
    "操作平台通用": ("操作平台", "作业平台", "卸料平台"),
    "悬挑式操作平台": ("悬挑式操作平台", "悬挑卸料平台"),
    "落地式操作平台": ("落地式操作平台", "落地平台"),
    "移动式操作平台": ("移动式操作平台", "移动平台"),
    "高处作业吊篮": ("吊篮", "悬吊平台"),
    "建筑施工安全网": ("安全网", "密目网", "平网", "立网"),
    "安全带使用": ("安全带", "安全绳", "生命绳", "安全锁扣"),
    "安全帽使用": ("安全帽",),
    "个体防护": ("个人防护", "个体防护", "防滑鞋"),
    "建筑幕墙设计": ("幕墙设计", "玻璃幕墙设计", "石材幕墙设计"),
    "建筑幕墙材料": ("幕墙材料", "幕墙玻璃", "结构胶", "密封胶"),
    "玻璃幕墙": ("玻璃幕墙", "幕墙玻璃"),
    "幕墙安装与验收": ("幕墙安装", "幕墙验收", "幕墙工程"),
}

# A tuple is an AND condition; alternative tuples are OR conditions. This prevents a
# generic word such as “脚手架” from alone proving the more specific dismantling scene.
SCENE_PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    scene: tuple((alias,) for alias in aliases) for scene, aliases in SCENE_ALIASES.items()
}
SCENE_PATTERNS["脚手架搭设与拆除"] = (
    ("脚手架", "搭设"),
    ("脚手架", "拆除"),
    ("脚手架", "搭拆"),
    ("拆架",),
)
SCENE_PATTERNS["交叉作业"] = (
    ("交叉作业",),
    ("上下同时",),
    ("垂直交叉",),
    ("防护棚",),
    ("通道口", "防护"),
    ("出入口", "防护"),
)
SCENE_PATTERNS["高处作业吊篮"] = (
    ("吊篮", "作业"),
    ("吊篮", "施工"),
    ("吊篮", "安装"),
    ("吊篮", "使用"),
    ("悬吊平台",),
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _matches(text: str, scene: str) -> bool:
    normalized = normalize_text(text)
    if scene == "附着式升降脚手架":
        if "附着式升降" in normalized or "升降脚手架" in normalized:
            return True
        return any(
            normalized[index - 1 : index] != "攀"
            and normalized[index + 2 : index + 3] != "子"
            for index in (
                match.start() for match in re.finditer("爬架", normalized)
            )
        )
    if scene == "高处作业吊篮":
        return "悬吊平台" in normalized or bool(
            re.search(
                r"吊篮.{0,8}(?:作业|施工|安装|使用)|"
                r"(?:作业|施工|安装|使用).{0,8}吊篮",
                normalized,
            )
        )
    patterns = SCENE_PATTERNS.get(scene, ((scene.replace("作业", ""),), (scene,)))
    return any(all(normalize_text(term) in normalized for term in pattern) for pattern in patterns)


def _heading_parts(heading_path: str) -> list[str]:
    return [part.strip() for part in heading_path.split(" > ") if part.strip()]


def _instance_id(scene: str, title: str, anchor_id: str) -> str:
    raw = f"{scene}|{normalize_text(title)}|{anchor_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _lifecycle_group(scene: str, title: str) -> str:
    """Keep meaningful scaffold erection/dismantling instances without section explosion."""
    if scene != "脚手架搭设与拆除":
        return "综合"
    normalized = normalize_text(title)
    has_dismantling = any(term in normalized for term in ("拆除", "拆架"))
    has_erection = any(term in normalized for term in ("搭设", "安装", "支设"))
    if has_dismantling and not has_erection:
        return "拆除"
    if has_erection and not has_dismantling:
        return "搭设"
    return "综合"


SCAFFOLD_SCENES = {"施工脚手架", "脚手架搭设与拆除"}
SUPPORT_SCAFFOLD_SIGNALS = (
    "模板支撑",
    "模板支架",
    "支模架",
    "支模",
    "梁底",
    "板底",
    "可调托撑",
    "可调头",
    "主龙骨",
    "次龙骨",
    "满堂脚手架",
    "满堂架",
    "支撑体系",
)
WORK_SCAFFOLD_SIGNALS = (
    "作业脚手架",
    "外脚手架",
    "外架",
    "悬挑脚手架",
    "落地式脚手架",
    "脚手架作业层",
    "连墙件",
    "脚手板",
    "挡脚板",
)


def _scaffold_object_type(texts: list[str], document_text: str = "") -> str:
    combined = " ".join(texts)
    has_support = any(signal in combined for signal in SUPPORT_SCAFFOLD_SIGNALS)
    has_work = any(signal in combined for signal in WORK_SCAFFOLD_SIGNALS)
    if has_support and not has_work:
        return "template_support_scaffold"
    if has_work and not has_support:
        return "work_scaffold"
    if has_support and has_work:
        return "mixed_scaffold"
    compact_document = normalize_text(document_text)
    if "模板" in compact_document[:500]:
        return "template_support_scaffold"
    if (
        "脚手架" in compact_document
        and "模板" not in compact_document[:500]
        and any(term in compact_document[:500] for term in ("专项方案", "施工方案", "搭设方案"))
    ):
        return "work_scaffold"
    return "unspecified_scaffold"


def _consolidate_scene_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge section hits into business-level instances rather than paragraph instances.

    Most controlled scenes represent one construction activity in a scheme. The combined
    erection/dismantling scene is the exception: erection and dismantling remain separate
    because their applicable controls differ. A generic hit is absorbed into a concrete
    lifecycle instance when one exists.
    """
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in instances:
        object_group = (
            str(item.get("object_type") or "unspecified_scaffold")
            if item["scene"] in SCAFFOLD_SCENES
            else "general"
        )
        key = (
            item["scene"],
            _lifecycle_group(item["scene"], item["title"]),
            object_group,
            str(item.get("object_instance_key") or "general"),
        )
        grouped.setdefault(key, []).append(item)

    lifecycle_keys = {
        phase
        for (scene, phase, _object_group, _object_key) in grouped
        if scene == "脚手架搭设与拆除" and phase != "综合"
    }
    if lifecycle_keys:
        for key in list(grouped):
            scene, phase, object_group, object_key = key
            if scene != "脚手架搭设与拆除" or phase != "综合":
                continue
            fallback_phase = "搭设" if "搭设" in lifecycle_keys else sorted(lifecycle_keys)[0]
            grouped.setdefault((scene, fallback_phase, object_group, object_key), []).extend(
                grouped.pop(key)
            )

    consolidated: list[dict[str, Any]] = []
    for (scene, phase, object_group, object_key), members in grouped.items():
        preferred = min(
            members,
            key=lambda item: (
                0 if "deterministic_object_card" in item["sources"] else 1,
                0 if "deterministic_heading" in item["sources"] else 1,
                len(item["title"]),
                item["location"],
            ),
        )
        segment_ids = list(
            dict.fromkeys(
                segment_id for item in members for segment_id in item["segment_ids"]
            )
        )
        title = preferred["title"]
        if scene == "脚手架搭设与拆除" and phase != "综合":
            title = f"{title}（{phase}阶段）"
        consolidated.append(
            {
                **preferred,
                "id": _instance_id(
                    scene, f"{scene}|{phase}|{object_group}|{object_key}", segment_ids[0]
                ),
                "title": title,
                "segment_ids": segment_ids,
                "anchor_segment_ids": list(
                    dict.fromkeys(
                        segment_id
                        for item in members
                        for segment_id in item.get("anchor_segment_ids", item["segment_ids"])
                    )
                )[:40],
                "object_type": object_group,
                "sources": sorted(
                    {source for item in members for source in item["sources"]}
                ),
                "confidence": max(float(item["confidence"]) for item in members),
            }
        )
    return sorted(
        consolidated, key=lambda item: (item["scene"], item["location"], item["title"])
    )


def route_scene_instances(
    segments: Iterable[dict[str, Any]], available_scenes: list[str]
) -> list[dict[str, Any]]:
    segment_list = list(segments)
    instances: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # Headings are the strongest deterministic evidence and define the section scope.
    for segment in segment_list:
        parts = _heading_parts(segment.get("heading_path", ""))
        for scene in available_scenes:
            for index, part in enumerate(parts):
                if not _matches(part, scene):
                    continue
                prefix = " > ".join(parts[: index + 1])
                key = (scene, normalize_text(prefix))
                if key in seen:
                    break
                seen.add(key)
                scoped_ids = [
                    candidate["id"]
                    for candidate in segment_list
                    if candidate.get("heading_path") == prefix
                    or candidate.get("heading_path", "").startswith(f"{prefix} > ")
                ][:80]
                instances.append(
                    {
                        "id": _instance_id(scene, prefix, segment["id"]),
                        "scene": scene,
                        "title": prefix,
                        "location": segment["location"],
                        "segment_ids": scoped_ids or [segment["id"]],
                        "sources": ["deterministic_heading"],
                        "confidence": 0.95,
                    }
                )
                break

    # Body text is an independent signal. A scene mentioned once can still be a real
    # construction activity, so group local matches instead of requiring two hits.
    for scene in available_scenes:
        if scene == "高处作业综合管理":
            continue
        covered_ids = {
            segment_id
            for item in instances
            if item["scene"] == scene
            for segment_id in item["segment_ids"]
        }
        matched_segments = [
            segment
            for segment in segment_list
            if segment["id"] not in covered_ids
            if _matches(f"{segment.get('heading_path', '')} {segment['text']}", scene)
        ]
        if not matched_segments:
            continue
        clusters: list[list[dict[str, Any]]] = []
        for segment in matched_segments:
            if (
                clusters
                and clusters[-1][-1].get("heading_path") == segment.get("heading_path")
                and segment["sequence_no"] - clusters[-1][-1]["sequence_no"] <= 3
            ):
                clusters[-1].append(segment)
            else:
                clusters.append([segment])
        for cluster in clusters[:12]:
            anchor = cluster[0]
            heading = anchor.get("heading_path") or "文档正文"
            title = f"{heading}｜{anchor['text'][:36]}"
            key = (scene, normalize_text(title))
            if key in seen:
                continue
            seen.add(key)
            instances.append(
                {
                    "id": _instance_id(scene, title, anchor["id"]),
                    "scene": scene,
                    "title": title,
                    "location": anchor["location"],
                    "segment_ids": [item["id"] for item in cluster[:40]],
                    "sources": ["deterministic_body"],
                    "confidence": 0.8,
                }
            )

    document_text = normalize_text("\n".join(segment["text"] for segment in segment_list))
    high_work_signal = any(
        signal in document_text
        for signal in (
            "高处",
            "高支模",
            "脚手架",
            "临边",
            "洞口",
            "吊篮",
            "幕墙",
            "安全带",
        )
    )
    if high_work_signal and "高处作业综合管理" in available_scenes:
        if not any(item["scene"] == "高处作业综合管理" for item in instances):
            anchor = segment_list[0]
            instances.append(
                {
                    "id": _instance_id("高处作业综合管理", "全文通用管理", anchor["id"]),
                    "scene": "高处作业综合管理",
                    "title": "全文通用管理",
                    "location": anchor["location"],
                    "segment_ids": [
                        item["id"]
                        for item in segment_list
                        if any(
                            keyword in normalize_text(
                                f"{item.get('heading_path', '')}{item.get('text', '')}"
                            )
                            for keyword in (
                                "高处作业",
                                "脚手架",
                                "临边",
                                "洞口",
                                "吊篮",
                                "幕墙",
                            )
                        )
                    ][:12]
                    or [anchor["id"]],
                    "sources": ["deterministic_document_signal"],
                    "confidence": 0.8,
                }
            )
    return sorted(instances, key=lambda item: (item["scene"], item["location"], item["title"]))


def build_scene_keyword_hints(
    segments: Iterable[dict[str, Any]], available_scenes: list[str]
) -> list[dict[str, Any]]:
    """Build non-authoritative recall hints for the model scene router.

    Keyword matches are deliberately kept separate from final scene instances. They
    help the model notice a relevant heading or an isolated body sentence, but they
    must never create an auditable construction scene on their own.
    """
    hints = route_scene_instances(segments, available_scenes)
    for hint in hints:
        hint["sources"] = [
            source.replace("deterministic", "keyword") for source in hint["sources"]
        ]
        hint["confidence"] = min(float(hint.get("confidence", 0.0)), 0.6)
    return hints


def filter_model_scene_instances(
    proposals: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reject model scenes whose cited text cannot prove the named work object.

    This is a validation boundary, not a second scene generator. In particular,
    words such as "钢模板拆除" and "脚手架边缘" in one paragraph must not be
    combined into a fictitious scaffold dismantling activity.
    """
    segment_by_id = {str(item["id"]): item for item in segments}
    accepted: list[dict[str, Any]] = []
    document_text = " ".join(str(item.get("text", "")) for item in segments[:12])
    for proposal in proposals:
        cited = [
            segment_by_id[str(segment_id)]
            for segment_id in proposal.get("segment_ids", [])
            if str(segment_id) in segment_by_id
        ]
        cited = [item for item in cited if not is_meta_discussion_segment(item)]
        if not cited:
            continue
        texts = [
            normalize_text(f"{item.get('heading_path', '')} {item.get('text', '')}")
            for item in cited
        ]
        scene = str(proposal.get("scene") or "")
        proposal = dict(proposal)
        proposal["anchor_segment_ids"] = [str(item["id"]) for item in cited]
        if scene in SCAFFOLD_SCENES:
            cited_sequences = {
                int(item.get("sequence_no") or 0) for item in cited
            }
            local_texts = [
                normalize_text(
                    f"{item.get('heading_path', '')} {item.get('text', '')}"
                )
                for item in segments
                if any(
                    abs(int(item.get("sequence_no") or 0) - sequence) <= 8
                    for sequence in cited_sequences
                )
            ]
            proposal["object_type"] = _scaffold_object_type(
                [*texts, *local_texts], document_text
            )
        if scene == "高处作业综合管理":
            proves_operational_high_work = any(
                any(
                    marker in text
                    for marker in (
                        "高处作业",
                        "高空作业",
                        "临边作业",
                        "洞口作业",
                        "攀登作业",
                        "悬空作业",
                        "操作平台",
                        "安全带",
                    )
                )
                and not (
                    "应急" in text
                    and any(marker in text for marker in ("事故", "救援", "抢救"))
                )
                for text in texts
            )
            if not proves_operational_high_work:
                continue
        if scene == "脚手架搭设与拆除":
            proves_lifecycle = any(
                re.search(r"(?:脚手架|架体).{0,10}(?:搭设|拆除|拆架|搭拆)", text)
                or re.search(r"(?:搭设|拆除|拆架|搭拆).{0,10}(?:脚手架|架体)", text)
                for text in texts
            )
            if not proves_lifecycle:
                continue
        if scene == "施工脚手架":
            proves_scaffold_work = any(
                re.search(
                    r"(?:脚手架|架体).{0,12}(?:搭设|拆除|使用|作业|施工|检查|验收|安装)",
                    text,
                )
                or re.search(
                    r"(?:搭设|拆除|使用|作业|施工|检查|验收|安装).{0,12}(?:脚手架|架体)",
                    text,
                )
                for text in texts
            )
            if not proves_scaffold_work:
                continue
        accepted.append(proposal)
    return accepted


def merge_scene_instances(
    deterministic: list[dict[str, Any]],
    model_instances: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    available_scenes: list[str],
) -> list[dict[str, Any]]:
    segment_by_id = {segment["id"]: segment for segment in segments}
    merged = [dict(item) for item in deterministic]
    keys = {
        (
            item["scene"],
            normalize_text(item["title"]),
            str(item.get("object_type") or ""),
        ): item
        for item in merged
    }
    for proposed in model_instances:
        scene = str(proposed.get("scene", "")).strip()
        if scene not in available_scenes:
            continue
        segment_ids = [
            str(segment_id)
            for segment_id in proposed.get("segment_ids", [])
            if str(segment_id) in segment_by_id
        ][:40]
        if not segment_ids:
            continue
        anchor = segment_by_id[segment_ids[0]]
        title = str(proposed.get("title") or anchor.get("heading_path") or anchor["location"])
        object_type = str(proposed.get("object_type") or "")
        key = (scene, normalize_text(title), object_type)
        proposed_ids = set(segment_ids)
        existing = keys.get(key) or next(
            (
                item
                for item in merged
                if item["scene"] == scene
                and str(item.get("object_type") or "") == object_type
                and proposed_ids.intersection(item["segment_ids"])
            ),
            None,
        )
        if existing:
            existing["sources"] = sorted(set(existing["sources"]) | {"model"})
            existing["segment_ids"] = list(
                dict.fromkeys(existing["segment_ids"] + segment_ids)
            )[:80]
            existing["anchor_segment_ids"] = list(
                dict.fromkeys(
                    [
                        *existing.get("anchor_segment_ids", []),
                        *proposed.get("anchor_segment_ids", segment_ids),
                    ]
                )
            )[:40]
            existing["confidence"] = max(float(existing["confidence"]), 0.85)
            continue
        item = {
            "id": _instance_id(scene, title, anchor["id"]),
            "scene": scene,
            "title": title,
            "location": anchor["location"],
            "segment_ids": segment_ids,
            "anchor_segment_ids": list(
                dict.fromkeys(proposed.get("anchor_segment_ids", segment_ids))
            )[:40],
            "object_type": object_type or (
                "unspecified_scaffold" if scene in SCAFFOLD_SCENES else "general"
            ),
            "sources": ["model"],
            "confidence": 0.85,
        }
        merged.append(item)
        keys[key] = item
    consolidated = _consolidate_scene_instances(merged)
    # A model usually cites only the anchor paragraph that proves a scene. Audit
    # evidence, however, may live elsewhere in the same section (for example the
    # 6 m trigger one paragraph before a dismantling prohibition). Expand anchors
    # to their real parsed section while keeping traceable segment ids.
    for item in consolidated:
        headings = {
            segment_by_id[segment_id].get("heading_path", "")
            for segment_id in item["segment_ids"]
            if segment_id in segment_by_id
            and segment_by_id[segment_id].get("heading_path", "")
        }
        related_ids = [
            segment["id"]
            for segment in segments
            if any(
                segment.get("heading_path") == heading
                or segment.get("heading_path", "").startswith(f"{heading} > ")
                for heading in headings
            )
        ]
        item["segment_ids"] = list(dict.fromkeys([*item["segment_ids"], *related_ids]))
    return consolidated


def route_scenes(segments: Iterable[dict], available_scenes: list[str]) -> list[str]:
    return sorted(
        {item["scene"] for item in route_scene_instances(segments, available_scenes)}
    )


def build_scene_context(segments: list[dict], max_chars: int = 8000) -> str:
    signals = tuple(alias for aliases in SCENE_ALIASES.values() for alias in aliases)
    selected: list[str] = []
    used = 0
    heading_counts: dict[str, int] = {}
    for segment in segments:
        heading_path = segment.get("heading_path", "")
        last_heading = _heading_parts(heading_path)[-1] if _heading_parts(heading_path) else ""
        text = segment["text"]
        if not any(signal in f"{last_heading} {text}" for signal in signals):
            continue
        if heading_counts.get(heading_path, 0) >= 2:
            continue
        heading_counts[heading_path] = heading_counts.get(heading_path, 0) + 1
        excerpt = text[:320]
        line = f"[{segment['id']}] {heading_path}｜{segment['location']}: {excerpt}\n"
        if used + len(line) > max_chars:
            break
        selected.append(line)
        used += len(line)
        if len(selected) >= 35:
            break
    if not selected:
        for segment in segments[:30]:
            line = f"[{segment['id']}] {segment['location']}: {segment['text']}\n"
            if used + len(line) > max_chars:
                break
            selected.append(line)
            used += len(line)
    return "".join(selected)


def build_scene_context_batches(
    segments: list[dict[str, Any]],
    max_chars: int | None = None,
    max_segments: int | None = None,
) -> list[dict[str, Any]]:
    """Cover every segment using a document-size-aware number of model calls."""
    total_chars = sum(
        len(str(segment.get("markdown_text") or segment.get("text") or ""))
        for segment in segments
    )
    if max_chars is None:
        if total_chars <= 18_000:
            max_chars = 24_000
        elif total_chars <= 60_000:
            max_chars = 24_000
        elif total_chars <= 160_000:
            max_chars = 20_000
        else:
            max_chars = 16_000
    if max_segments is None:
        if total_chars <= 18_000:
            max_segments = 100
        elif total_chars <= 60_000:
            max_segments = 80
        elif total_chars <= 160_000:
            max_segments = 70
        else:
            max_segments = 60
    batches: list[dict[str, Any]] = []
    lines: list[str] = []
    segment_ids: list[str] = []
    used = 0
    current_heading = ""
    for segment in segments:
        heading = segment.get("heading_path") or "文档正文"
        text = str(segment.get("markdown_text") or segment.get("text") or "")[:1200]
        heading_line = ""
        if heading != current_heading:
            depth = min(6, max(2, heading.count(" > ") + 2))
            heading_line = f"{'#' * depth} {heading}\n"
        line = (
            f"{heading_line}<!-- segment:{segment['id']} location:{segment['location']} -->\n"
            f"{text}\n"
        )
        if lines and (used + len(line) > max_chars or len(lines) >= max_segments):
            batches.append({"context": "".join(lines), "segment_ids": segment_ids})
            lines = []
            segment_ids = []
            used = 0
            current_heading = ""
            heading_line = f"## {heading}\n"
            line = (
                f"{heading_line}<!-- segment:{segment['id']} "
                f"location:{segment['location']} -->\n{text}\n"
            )
        lines.append(line)
        segment_ids.append(str(segment["id"]))
        used += len(line)
        current_heading = heading
    if lines:
        batches.append({"context": "".join(lines), "segment_ids": segment_ids})
    return batches
