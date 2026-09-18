from __future__ import annotations

import hashlib
import re
from typing import Any

SCAFFOLD_SCENE = "施工脚手架"
VALUE_RE = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|米)"
PARAMETER_PATTERNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "work_scaffold_width": (
        "作业脚手架宽度",
        (
            rf"(?:立杆横距|(?<!件)横距|排距|内外立杆间距)\s*(?:为|按|取|=|：|:)??\s*{VALUE_RE}",
            r"(?:立杆横距|(?<!件)横距|排距|内外立杆间距)[^｜\n]{0,12}\((?P<unit>mm|cm|m|米)\)\s*｜\s*(?P<value>\d+(?:\.\d+)?)",
        ),
    ),
    "work_layer_height": (
        "作业层高度",
        (
            rf"(?:大横杆|立杆)?步距\s*(?:为|按|取|=|：|:)??\s*{VALUE_RE}",
            r"(?:大横杆|立杆)?步距[^｜\n]{0,12}[（(](?P<unit>mm|cm|m|米)[）)]\s*｜\s*(?P<value>\d+(?:\.\d+)?)",
        ),
    ),
    "longitudinal_spacing": (
        "立杆纵距",
        (
            rf"(?:立杆纵距|(?<!件)纵距)\s*(?:为|按|取|=|：|:)??\s*{VALUE_RE}",
            r"(?:立杆纵距|(?<!件)纵距)[^｜\n]{0,12}[（(](?P<unit>mm|cm|m|米)[）)]\s*｜\s*(?P<value>\d+(?:\.\d+)?)",
        ),
    ),
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _stable_id(key: str, anchor_id: str) -> str:
    return hashlib.sha256(f"scaffold-object|{key}|{anchor_id}".encode()).hexdigest()[:24]


def _nearby_layer_location(compact: str, scaffold_term: str) -> str | None:
    range_pattern = r"\d+\s*[～~—-]\s*\d+层"
    match = re.search(
        rf"{range_pattern}.{{0,24}}{scaffold_term}|"
        rf"{scaffold_term}.{{0,24}}{range_pattern}",
        compact,
    )
    if not match:
        return None
    # ``re`` does not expose repeated named captures. Read the matched fragment
    # once more to keep this helper compatible with the standard library.
    range_match = re.search(
        r"(?P<start>\d+)\s*[～~—-]\s*(?P<end>\d+)层", match.group(0)
    )
    if not range_match:
        return None
    return f"{range_match.group('start')}—{range_match.group('end')}层"


def _object_kind(text: str) -> tuple[str, str, str] | None:
    compact = _compact(text)
    if any(term in compact for term in ("内满堂脚手架", "内脚手架", "满堂脚手架")):
        return "interior_full", "内脚手架", "内脚手架"
    if any(term in compact for term in ("砖砌架", "砌筑脚手架", "砖架")):
        return "masonry", "砌筑脚手架", "砌筑脚手架"
    if re.search(r"(?:悬挑式.{0,24}脚手架|悬挑脚手架)", compact):
        location = _nearby_layer_location(compact, "悬挑式")
        if location:
            return "cantilever", f"{location}悬挑式脚手架", location
        return "cantilever", "悬挑式脚手架", "悬挑式脚手架"
    if "屋面" in compact and "落地" in compact:
        return "roof_ground", "屋面落地式脚手架", "屋面"
    if re.search(r"(?:落地式.{0,24}脚手架|落地脚手架)", compact):
        location = _nearby_layer_location(compact, "落地式")
        if location:
            return "ground", f"{location}落地式脚手架", location
        location = "首层" if "首层" in compact else "落地式脚手架"
        return "ground", f"{location}落地式脚手架" if location == "首层" else location, location
    return None


def _parameter_values(segment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    text = str(segment.get("text") or "")
    for key, (label, patterns) in PARAMETER_PATTERNS.items():
        matches = []
        for pattern in patterns:
            matches.extend(re.finditer(pattern, text, re.I))
        distinct = {
            (match.group("value"), match.group("unit"))
            for match in matches
        }
        if len(distinct) != 1:
            continue
        value, unit = next(iter(distinct))
        values[key] = {
            "label": label,
            "value": float(value),
            "unit": unit,
            "source_segment_id": str(segment["id"]),
            "quote": text,
        }
    return values


def extract_scaffold_object_cards(
    segments: list[dict[str, Any]], available_scenes: list[str]
) -> list[dict[str, Any]]:
    """Extract a few traceable scaffold objects without creating a new data model.

    The cards only separate objects that the scheme explicitly names.  They are
    routing context for existing rules, not additional audit conclusions.
    """
    if SCAFFOLD_SCENE not in available_scenes:
        return []

    cards: dict[str, dict[str, Any]] = {}
    for segment in segments:
        context = f"{segment.get('heading_path', '')} {segment.get('text', '')}"
        kind = _object_kind(context)
        if not kind:
            continue
        subtype, title, location_label = kind
        key = subtype if subtype in {"ground", "cantilever"} else f"{subtype}|{location_label}"
        card = cards.get(key)
        if card is None:
            card = {
                "id": _stable_id(key, str(segment["id"])),
                "scene": SCAFFOLD_SCENE,
                "title": title,
                "location": str(segment.get("location") or location_label),
                "segment_ids": [],
                "anchor_segment_ids": [],
                "object_type": "work_scaffold",
                "object_instance_key": key,
                "scaffold_subtype": subtype,
                "location_label": location_label,
                "parameters": {},
                "sources": ["deterministic_object_card"],
                "confidence": 0.98,
            }
            cards[key] = card
        card["segment_ids"].append(str(segment["id"]))
        card["anchor_segment_ids"].append(str(segment["id"]))

    # A single legacy paragraph may describe the cantilever body and end with a
    # separate roof-ground scaffold. Preserve that second named object without
    # inheriting the cantilever dimensions from the same paragraph.
    for segment in segments:
        text = _compact(str(segment.get("text") or ""))
        if "屋面" not in text or "落地" not in text:
            continue
        key = "roof_ground|屋面"
        card = cards.setdefault(
            key,
            {
                "id": _stable_id(key, str(segment["id"])),
                "scene": SCAFFOLD_SCENE,
                "title": "屋面落地式脚手架",
                "location": str(segment.get("location") or "屋面"),
                "segment_ids": [],
                "anchor_segment_ids": [],
                "object_type": "work_scaffold",
                "object_instance_key": key,
                "scaffold_subtype": "roof_ground",
                "location_label": "屋面",
                "parameters": {},
                "sources": ["deterministic_object_card"],
                "confidence": 0.98,
            },
        )
        for field in ("segment_ids", "anchor_segment_ids"):
            if str(segment["id"]) not in card[field]:
                card[field].append(str(segment["id"]))

    segment_by_id = {str(segment["id"]): segment for segment in segments}
    for card in cards.values():
        if card["scaffold_subtype"] == "roof_ground":
            continue
        anchors = [
            segment_by_id[segment_id]
            for segment_id in card["anchor_segment_ids"]
            if segment_id in segment_by_id
        ]
        anchor_headings = {str(segment.get("heading_path") or "") for segment in anchors}
        anchor_sequences = {int(segment.get("sequence_no") or 0) for segment in anchors}
        subtype_terms = {
            "ground": ("落地式脚手架", "落地脚手架"),
            "cantilever": ("悬挑式脚手架", "悬挑脚手架"),
            "interior_full": ("内脚手架", "满堂脚手架"),
            "masonry": ("砌筑脚手架", "砖架"),
        }.get(card["scaffold_subtype"], ())
        candidates: dict[str, list[dict[str, Any]]] = {}
        for segment in segments:
            context = _compact(
                f"{segment.get('heading_path', '')} {segment.get('text', '')}"
            )
            same_section = str(segment.get("heading_path") or "") in anchor_headings
            nearby = any(
                abs(int(segment.get("sequence_no") or 0) - sequence) <= 3
                for sequence in anchor_sequences
            )
            named_object = any(term in context for term in subtype_terms)
            if not (same_section or nearby or named_object):
                continue
            for key, parameter in _parameter_values(segment).items():
                candidates.setdefault(key, []).append(parameter)
                if str(segment["id"]) not in card["segment_ids"]:
                    card["segment_ids"].append(str(segment["id"]))
        for key, values in candidates.items():
            normalized = {
                round(
                    float(item["value"])
                    * ({"mm": 1, "cm": 10, "m": 1000, "米": 1000}[item["unit"]]),
                    6,
                )
                for item in values
            }
            if len(normalized) == 1:
                card["parameters"][key] = values[0]

    return sorted(cards.values(), key=lambda item: (item["location"], item["title"]))
