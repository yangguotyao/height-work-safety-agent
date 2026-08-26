from __future__ import annotations

import re
from typing import Any


def normalize_standard_quote(value: Any) -> str:
    """Normalize formatting noise while preserving the actual clause wording."""
    text = str(value or "").strip().lower()
    lines = text.splitlines()
    if lines and lines[0].count("｜") >= 2:
        lines = lines[1:]
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", "".join(lines))


def _standard_code(entry: dict[str, Any]) -> str:
    return str(entry.get("standard_code") or entry.get("standard") or "").strip()


def _page(entry: dict[str, Any]) -> str:
    return str(
        entry.get("page")
        or entry.get("page_start")
        or entry.get("pdf_page")
        or ""
    ).strip()


def same_standard_source(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether two records represent the same normative source passage."""
    left_id = str(left.get("id") or left.get("chunk_id") or "").strip()
    right_id = str(right.get("id") or right.get("chunk_id") or "").strip()
    if left_id and right_id and left_id == right_id:
        return True

    left_text = normalize_standard_quote(left.get("text") or left.get("quote"))
    right_text = normalize_standard_quote(right.get("text") or right.get("quote"))
    if not left_text or not right_text:
        return False
    same_clause = (
        _standard_code(left) == _standard_code(right)
        and str(left.get("clause") or "").strip()
        == str(right.get("clause") or "").strip()
    )
    if not same_clause:
        return False
    if left_text == right_text:
        return True

    shorter, longer = sorted((left_text, right_text), key=len)
    # A rule row and the PDF chunk can differ only by a title prefix or a short
    # continuation. Treat them as the same passage when most text overlaps.
    return len(shorter) >= 40 and shorter in longer and len(shorter) / len(longer) >= 0.75


def deduplicate_standard_evidence(
    entries: list[dict[str, Any]], limit: int | None = None
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in entries:
        if any(same_standard_source(entry, existing) for existing in result):
            continue
        result.append(entry)
        if limit is not None and len(result) >= limit:
            break
    return result
