from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _local_date(created_at: str | None, now: datetime | None = None) -> date:
    if now is not None:
        return (now if now.tzinfo else now.replace(tzinfo=SHANGHAI)).astimezone(SHANGHAI).date()
    if created_at:
        try:
            parsed = datetime.fromisoformat(created_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(SHANGHAI).date()
        except ValueError:
            pass
    return datetime.now(SHANGHAI).date()


def resolve_task_schedule(
    work_time: str, *, created_at: str | None = None, now: datetime | None = None
) -> tuple[str, str]:
    """Turn relative task time into an immutable local date and coarse time window."""
    text = str(work_time or "").strip()
    base = _local_date(created_at, now)
    year_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", text)
    month_match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", text)
    iso_match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    target = base
    try:
        if year_match:
            target = date(*(int(value) for value in year_match.groups()))
        elif iso_match:
            target = date(*(int(value) for value in iso_match.groups()))
        elif month_match:
            month, day = (int(value) for value in month_match.groups())
            target = date(base.year, month, day)
            if target < base - timedelta(days=30):
                target = date(base.year + 1, month, day)
        elif "后天" in text:
            target = base + timedelta(days=2)
        elif "明天" in text or "明早" in text or "明晚" in text:
            target = base + timedelta(days=1)
    except ValueError:
        target = base

    window = "all_day"
    for terms, value in (
        (("早上", "明早"), "morning"),
        (("上午",), "morning"),
        (("中午",), "midday"),
        (("下午",), "afternoon"),
        (("晚上", "今晚", "明晚", "夜间"), "evening"),
    ):
        if any(term in text for term in terms):
            window = value
            break
    return target.isoformat(), window


def backfill_task_schedules(database: Database) -> int:
    rows = database.fetch_all(
        """SELECT id, work_time, created_at FROM work_tasks
           WHERE scheduled_date = '' OR scheduled_date IS NULL"""
    )
    updates = []
    for row in rows:
        scheduled_date, time_window = resolve_task_schedule(
            row["work_time"], created_at=row["created_at"]
        )
        updates.append((scheduled_date, time_window, row["id"]))
    if updates:
        database.executemany(
            "UPDATE work_tasks SET scheduled_date = ?, time_window = ? WHERE id = ?",
            updates,
        )
    return len(updates)
