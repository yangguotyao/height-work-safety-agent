from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..db import Database, json_load
from ..repositories import utc_now
from .question_bank import QuestionBank
from .risk_card_service import RiskCardService, filter_card_for_task_action

RISK_ORDER = {"red": 3, "yellow": 2, "green": 1}
TEST_WORKERS = {"工人01", "工人A", "工人B"}
TEST_WORKER_MARKERS = ("测试", "验收", "页面", "test")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _display_text(text: str) -> str:
    return re.sub(r"[：:]\s*>$", "。", str(text or "").strip())


def _task_dict(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["scenes"] = json_load(value.pop("scenes_json"), [])
    return value


def _identity_text(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:、/\\_-]+", "", str(value or "").strip().lower())


def logical_task_key(task: dict[str, Any]) -> str:
    """Return a stable semantic identity without replacing the stored task id."""
    worker_scope = _identity_text(task.get("worker_ref")) or _identity_text(
        task.get("team_ref")
    )
    content = _identity_text(task.get("normalized_task") or task.get("work_content"))
    return "|".join(
        (
            worker_scope,
            content,
            _identity_text(task.get("work_location")),
            _identity_text(task.get("work_floor")),
            _identity_text(task.get("scheduled_date")),
            _identity_text(task.get("time_window") or task.get("work_time")),
        )
    )


def _is_non_official_task(task: dict[str, Any]) -> bool:
    worker_ref = str(task.get("worker_ref") or "").strip()
    lowered = worker_ref.lower()
    return (
        worker_ref in TEST_WORKERS
        or worker_ref.startswith("演示")
        or any(marker in lowered for marker in TEST_WORKER_MARKERS)
    )


def _weather_work_time(task: dict[str, Any]) -> str:
    target = date.fromisoformat(task["scheduled_date"])
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    offset = (target - today).days
    day = (
        "今天"
        if offset == 0
        else "明天"
        if offset == 1
        else "后天"
        if offset == 2
        else target.isoformat()
    )
    window = {
        "morning": "上午",
        "midday": "中午",
        "afternoon": "下午",
        "evening": "晚上",
        "all_day": "",
    }.get(task.get("time_window"), "")
    return f"{day}{window}"


class DynamicRiskService:
    """Deterministic project-level risk ranking with traceable evidence."""

    def __init__(
        self,
        database: Database,
        risk_cards: RiskCardService,
        question_bank: QuestionBank,
        project_name: str,
    ):
        self.db = database
        self.risk_cards = risk_cards
        self.question_bank = question_bank
        self.project_name = project_name

    def _tasks_for_date(
        self, assessment_date: str, *, include_test: bool = False
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM work_tasks WHERE scheduled_date = ?
               ORDER BY created_at, id""",
            (assessment_date,),
        )
        tasks = [_task_dict(row) for row in rows]
        if not include_test:
            tasks = [task for task in tasks if not _is_non_official_task(task)]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            grouped.setdefault(logical_task_key(task), []).append(task)
        result = []
        for key, candidates in grouped.items():
            representative = max(
                candidates,
                key=lambda task: (
                    bool(task.get("audit_run_id")),
                    sum(bool(task.get(field)) for field in ("worker_ref", "team_ref", "scenes")),
                    str(task.get("created_at") or ""),
                    str(task.get("id") or ""),
                ),
            )
            result.append(
                {
                    **representative,
                    "_logical_task_key": key,
                    "_duplicate_task_ids": [task["id"] for task in candidates],
                    "_duplicate_count": len(candidates),
                }
            )
        return sorted(result, key=lambda task: (task.get("created_at", ""), task["id"]))

    def _card(self, task: dict[str, Any], *, refresh_weather: bool) -> dict[str, Any]:
        stored = self.db.fetch_one(
            "SELECT card_json FROM task_risk_cards WHERE task_id = ?", (task["id"],)
        )
        card = json_load(stored["card_json"], {}) if stored else self.risk_cards.build(task)
        card = filter_card_for_task_action(task, card)
        if refresh_weather:
            weather = self.risk_cards.weather.get_forecast(_weather_work_time(task))
            evidences = [
                item
                for item in card.get("evidences") or []
                if item.get("evidence_type") != "weather"
            ]
            evidences.append(
                {
                    "evidence_type": "weather",
                    "source_id": weather.get("source") or "weather",
                    "title": f"{weather.get('project_name') or self.project_name}天气",
                    "quote": weather.get("summary") or "天气服务未返回摘要",
                    "location": weather.get("forecast_window"),
                    "source_url": "https://docs.caiyunapp.com/weather-api/v2/v2.6/6-weather.html",
                }
            )
            card = {
                **card,
                "weather": weather,
                "weather_warnings": self.risk_cards.weather_warnings(task, weather),
                "evidences": evidences,
            }
        return card

    def _audit_issues(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        if not task.get("audit_run_id"):
            return []
        scenes = set(task.get("scenes") or [])
        rows = self.db.fetch_all(
            """SELECT i.id, i.rule_id, i.scene, i.issue, i.suggestion, i.result,
                      i.final_result, i.final_text, i.review_status, r.risk_level, r.rule_effect,
                      r.standard_code, r.clause, r.original_text
               FROM audit_items i JOIN audit_rules r ON r.rule_id = i.rule_id
               WHERE i.run_id = ? ORDER BY i.created_at, i.id""",
            (task["audit_run_id"],),
        )
        results = []
        for row in rows:
            effective = row["final_result"] or row["result"]
            if row["scene"] not in scenes or effective not in {"不符合", "未说明"}:
                continue
            row["effective_result"] = effective
            results.append(row)
        return results[:8]

    def _active_wrong_questions(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        worker_refs = {str(task.get("worker_ref") or "").strip()}
        if task.get("team_ref"):
            rows = self.db.fetch_all(
                "SELECT DISTINCT worker_ref FROM work_tasks WHERE team_ref = ?",
                (task["team_ref"],),
            )
            worker_refs.update(str(row["worker_ref"] or "").strip() for row in rows)
        worker_refs.discard("")
        if not worker_refs:
            return []
        placeholders = ",".join("?" for _ in worker_refs)
        rows = self.db.fetch_all(
            f"""SELECT worker_ref, question_id, is_correct, created_at, id
                FROM quiz_answers WHERE worker_ref IN ({placeholders})
                ORDER BY worker_ref, question_id, created_at DESC, id DESC""",
            tuple(sorted(worker_refs)),
        )
        history: dict[tuple[str, str], list[bool]] = {}
        for row in rows:
            history.setdefault((row["worker_ref"], row["question_id"]), []).append(
                bool(row["is_correct"])
            )
        bank_scene = self.question_bank.infer_scene(task)
        result = []
        for (worker_ref, question_id), states in history.items():
            question = self.question_bank.questions.get(question_id)
            if not question or question["scene"] != bank_scene:
                continue
            if False in states and states[:2] != [True, True]:
                result.append({"worker_ref": worker_ref, "question": question})
        return result

    def _review_questions(
        self, task: dict[str, Any], wrong: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        scene = self.question_bank.infer_scene(task)
        preferred = [entry["question"] for entry in wrong]
        candidates = [
            item for item in self.question_bank.questions.values() if item["scene"] == scene
        ]
        candidates.sort(key=lambda item: item["id"])
        ordered = []
        for item in [*preferred, *candidates]:
            if item["id"] not in {entry["id"] for entry in ordered}:
                ordered.append(item)
        singles = [item for item in ordered if item["type"] == "single_choice"][:3]
        judgements = [item for item in ordered if item["type"] == "true_false"][:2]
        return [self.question_bank.public(item) for item in [*singles, *judgements]]

    def _evaluate_task(
        self, task: dict[str, Any], *, refresh_weather: bool
    ) -> dict[str, Any]:
        card = self._card(task, refresh_weather=refresh_weather)
        audit_issues = self._audit_issues(task)
        wrong = self._active_wrong_questions(task)
        triggers: list[dict[str, Any]] = []

        def trigger(
            code: str,
            level: str,
            title: str,
            detail: str,
            *,
            source_type: str,
            source_id: str = "",
            hard_block: bool = False,
        ) -> None:
            triggers.append(
                {
                    "code": code,
                    "level": level,
                    "title": title,
                    "detail": detail,
                    "source_type": source_type,
                    "source_id": source_id,
                    "hard_block": hard_block,
                }
            )

        weather = card.get("weather") or {}
        warnings = card.get("weather_warnings") or []
        stop_warnings = [item for item in warnings if item.get("level") == "stop"]
        warning_warnings = [item for item in warnings if item.get("level") == "warning"]
        if stop_warnings:
            trigger(
                "WEATHER_STOP",
                "red",
                "天气达到禁止或暂停条件",
                "；".join(item["message"] for item in stop_warnings),
                source_type="weather",
                hard_block=True,
            )
        elif weather.get("status") != "ok":
            trigger(
                "WEATHER_UNKNOWN",
                "yellow",
                "天气数据暂不可用",
                str(weather.get("summary") or "天气服务未返回有效结果").replace(
                    "本次天气条件需要人工确认", "本次按天气数据缺失保守评估"
                ),
                source_type="weather",
            )
        elif warning_warnings:
            trigger(
                "WEATHER_ADVERSE",
                "yellow",
                "存在不利天气因素",
                "；".join(item["message"] for item in warning_warnings),
                source_type="weather",
            )

        duplicate_ids = list(task.get("_duplicate_task_ids") or [task["id"]])
        if len(duplicate_ids) > 1:
            trigger(
                "DATA_DUPLICATE",
                "info",
                "已合并重复任务记录",
                f"检测到{len(duplicate_ids)}条相同工人、作业、位置和时段的任务记录；"
                "评估保留风险更充分的代表记录，原始记录仍完整保留。",
                source_type="data_quality",
                source_id=task["id"],
            )
            triggers[-1]["duplicate_task_ids"] = duplicate_ids

        for issue in audit_issues:
            critical = issue["risk_level"] == "重大" or "禁止" in str(issue["rule_effect"])
            trigger(
                "AUDIT_CRITICAL" if critical else "AUDIT_OPEN",
                "red" if critical else "yellow",
                "关联方案存在审计风险项",
                f"{issue['scene']}：{issue['final_text'] or issue['issue']}",
                source_type="audit",
                source_id=issue["id"],
            )

        if wrong:
            count = len(wrong)
            trigger(
                "LEARNING_WEAKNESS",
                "yellow" if count >= 2 else "info",
                "班组近期存在相关知识薄弱项",
                f"当前作业场景有{count}个仍需巩固的错题知识点，仅用于提高培训和检查优先级。",
                source_type="learning",
            )

        level = "green"
        for item in triggers:
            if RISK_ORDER.get(item["level"], 0) > RISK_ORDER[level]:
                level = item["level"]
        if level == "green":
            trigger(
                "ROUTINE_CONTROL",
                "green",
                "未触发红黄条件",
                "仍须执行风险卡中的常规作业前检查，本结论不代表批准作业。",
                source_type="rule",
            )

        interventions = []
        if level == "red":
            interventions.append(
                "显示为红色高优先级风险；建议现场负责人依据项目制度决定是否暂停或调整。"
            )
        elif level == "yellow":
            interventions.append("作业前优先完成黄色触发项的现场核查和班前交底。")
        else:
            interventions.append("按任务风险卡执行常规控制措施。")
        interventions.extend(_display_text(text) for text in (card.get("pre_job_checks") or [])[:4])
        interventions.extend(
            _display_text(text) for text in (card.get("prohibited_behaviors") or [])[:2]
        )
        interventions = list(dict.fromkeys(text for text in interventions if text))[:7]
        score = {"red": 300, "yellow": 200, "green": 100}[level]
        score += sum(
            20 if item["level"] == "red" else 8 if item["level"] == "yellow" else 1
            for item in triggers
        )
        evidences = list(card.get("evidences") or [])
        for issue in audit_issues:
            evidences.append(
                {
                    "evidence_type": "audit",
                    "source_id": issue["id"],
                    "title": f"方案审计 · {issue['scene']}",
                    "quote": issue["original_text"] or issue["issue"],
                    "location": f"{issue['standard_code']} {issue['clause']}",
                    "source_url": None,
                }
            )
        summary = {
            "red": "已有数据触发红色高优先级条件，结果用于风险排序与展示。",
            "yellow": "存在需要作业前重点核查和干预的风险因素。",
            "green": "未触发红黄条件，仍需执行常规控制措施。",
        }[level]
        return {
            "task": task,
            "risk_level": level,
            "priority_score": score,
            "summary": summary,
            "triggers": triggers,
            "evidences": evidences[:16],
            "interventions": interventions,
            "review_questions": self._review_questions(task, wrong),
            "weather": weather,
        }

    @staticmethod
    def _input_fingerprint(
        assessment_date: str, results: list[dict[str, Any]], include_test: bool
    ) -> str:
        items = []
        for result in sorted(results, key=lambda entry: entry["task"]["id"]):
            task = result["task"]
            items.append(
                {
                    "task": {
                        key: task.get(key)
                        for key in (
                            "id",
                            "worker_ref",
                            "team_ref",
                            "audit_run_id",
                            "work_content",
                            "work_location",
                            "work_floor",
                            "work_time",
                            "normalized_task",
                            "task_action",
                            "equipment_type",
                            "scenes",
                            "scheduled_date",
                            "time_window",
                            "_duplicate_task_ids",
                        )
                    },
                    "risk_level": result["risk_level"],
                    "triggers": result["triggers"],
                    "interventions": result["interventions"],
                    "weather": result["weather"],
                    "review_question_ids": [
                        item["id"] for item in result["review_questions"]
                    ],
                }
            )
        canonical = json.dumps(
            {
                "assessment_date": assessment_date,
                "include_test": include_test,
                "items": items,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def evaluate(
        self,
        assessment_date: str | None = None,
        *,
        trigger_type: str = "data_refresh",
        refresh_weather: bool = True,
        include_test: bool = False,
    ) -> dict[str, Any]:
        target_date = assessment_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        try:
            date.fromisoformat(target_date)
        except ValueError as exc:
            raise ValueError("评估日期必须使用YYYY-MM-DD格式") from exc
        tasks = self._tasks_for_date(target_date, include_test=include_test)
        results = [self._evaluate_task(task, refresh_weather=refresh_weather) for task in tasks]
        fingerprint = self._input_fingerprint(target_date, results, include_test)
        existing = self.db.fetch_one(
            """SELECT id FROM dynamic_risk_runs
               WHERE assessment_date = ? AND include_test = ? AND status = 'completed'
                 AND input_fingerprint = ?
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (target_date, int(include_test), fingerprint),
        )
        if existing:
            stored = self.get_run(existing["id"])
            stored["version_created"] = False
            return stored
        run_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO dynamic_risk_runs
               (id, assessment_date, trigger_type, include_test, input_fingerprint,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, target_date, trigger_type, int(include_test), fingerprint, now),
        )
        counts = Counter(item["risk_level"] for item in results)
        weather_snapshots = {item["task"]["id"]: item["weather"] for item in results}
        item_rows = []
        for result in results:
            item_id = uuid4().hex
            item_rows.append(
                (
                    item_id,
                    run_id,
                    result["task"]["id"],
                    result["risk_level"],
                    result["priority_score"],
                    result["summary"],
                    _json(result["triggers"]),
                    _json(result["evidences"]),
                    _json(result["interventions"]),
                    _json(result["review_questions"]),
                    now,
                )
            )
        if item_rows:
            self.db.executemany(
                """INSERT INTO dynamic_risk_items
                   (id, run_id, task_id, risk_level, priority_score, summary,
                    triggers_json, evidences_json, interventions_json,
                    review_questions_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                item_rows,
            )
        completed_at = utc_now()
        self.db.execute(
            """UPDATE dynamic_risk_runs SET status = 'completed', task_count = ?,
                      red_count = ?, yellow_count = ?, green_count = ?, weather_json = ?,
                      completed_at = ? WHERE id = ?""",
            (
                len(results),
                counts["red"],
                counts["yellow"],
                counts["green"],
                _json(weather_snapshots),
                completed_at,
                run_id,
            ),
        )
        created = self.get_run(run_id)
        created["version_created"] = True
        return created

    def _load_run_items(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT i.*, t.worker_ref, t.team_ref, t.work_content, t.work_location,
                      t.work_floor, t.work_time, t.normalized_task, t.task_action,
                      t.equipment_type, t.scenes_json, t.scheduled_date, t.time_window
               FROM dynamic_risk_items i JOIN work_tasks t ON t.id = i.task_id
               WHERE i.run_id = ?
               ORDER BY i.priority_score DESC, i.created_at, i.id""",
            (run_id,),
        )
        items = []
        for row in rows:
            for field in ("triggers", "evidences", "interventions", "review_questions"):
                row[field] = json_load(row.pop(f"{field}_json"), [])
            row["scenes"] = json_load(row.pop("scenes_json"), [])
            items.append(row)
        return items

    @staticmethod
    def _dedupe_run_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(logical_task_key(item), []).append(item)
        result = []
        for candidates in grouped.values():
            representative = max(
                candidates,
                key=lambda item: (
                    RISK_ORDER.get(str(item.get("risk_level")), 0),
                    int(item.get("priority_score") or 0),
                    str(item.get("created_at") or ""),
                ),
            ).copy()
            merged_ids = {str(item["task_id"]) for item in candidates}
            for item in candidates:
                for trigger in item.get("triggers") or []:
                    merged_ids.update(
                        str(task_id) for task_id in trigger.get("duplicate_task_ids") or []
                    )
            merged_ids.discard("")
            representative["merged_task_ids"] = sorted(merged_ids)
            representative["duplicate_count"] = len(merged_ids)
            if len(merged_ids) > 1 and not any(
                trigger.get("code") == "DATA_DUPLICATE"
                for trigger in representative.get("triggers") or []
            ):
                representative.setdefault("triggers", []).append(
                    {
                        "code": "DATA_DUPLICATE",
                        "level": "info",
                        "title": "已合并重复任务记录",
                        "detail": (
                            f"当前视图已合并{len(merged_ids)}条语义相同的任务记录，"
                            "原始记录未删除。"
                        ),
                        "source_type": "data_quality",
                        "source_id": representative["task_id"],
                        "hard_block": False,
                        "duplicate_task_ids": sorted(merged_ids),
                    }
                )
            result.append(representative)
        return sorted(
            result,
            key=lambda item: (
                -int(item.get("priority_score") or 0),
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
            ),
        )

    def _previous_run_id(self, run: dict[str, Any]) -> str | None:
        row = self.db.fetch_one(
            """SELECT id FROM dynamic_risk_runs
               WHERE assessment_date = ? AND include_test = ? AND status = 'completed'
                 AND input_fingerprint <> ''
                 AND (created_at < ? OR (created_at = ? AND id < ?))
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (
                run["assessment_date"],
                int(run.get("include_test") or 0),
                run["created_at"],
                run["created_at"],
                run["id"],
            ),
        )
        return str(row["id"]) if row else None

    @staticmethod
    def _change_details(
        current: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        if previous is None:
            return {
                "status": "new",
                "direction": "new",
                "previous_level": None,
                "current_level": current["risk_level"],
                "score_delta": current["priority_score"],
                "added_triggers": [item["code"] for item in current.get("triggers") or []],
                "removed_triggers": [],
                "explanation": "该逻辑任务首次进入当前日期的动态风险评估。",
            }
        current_codes = {item["code"] for item in current.get("triggers") or []}
        previous_codes = {item["code"] for item in previous.get("triggers") or []}
        added = sorted(current_codes - previous_codes)
        removed = sorted(previous_codes - current_codes)
        current_order = RISK_ORDER[current["risk_level"]]
        previous_order = RISK_ORDER[previous["risk_level"]]
        direction = (
            "up"
            if current_order > previous_order
            else "down"
            if current_order < previous_order
            else "changed"
            if added or removed or current["priority_score"] != previous["priority_score"]
            else "same"
        )
        status = {
            "up": "escalated",
            "down": "reduced",
            "changed": "changed",
            "same": "unchanged",
        }[direction]
        title_by_code = {
            item["code"]: item.get("title") or item["code"]
            for item in [*(current.get("triggers") or []), *(previous.get("triggers") or [])]
        }
        parts = []
        if current["risk_level"] != previous["risk_level"]:
            parts.append(f"风险等级由{previous['risk_level']}变为{current['risk_level']}")
        if added:
            parts.append("新增：" + "、".join(title_by_code[code] for code in added))
        if removed:
            parts.append("解除：" + "、".join(title_by_code[code] for code in removed))
        if not parts:
            parts.append("风险等级与触发条件均未发生变化")
        return {
            "status": status,
            "direction": direction,
            "previous_level": previous["risk_level"],
            "current_level": current["risk_level"],
            "score_delta": int(current["priority_score"])
            - int(previous["priority_score"]),
            "added_triggers": added,
            "removed_triggers": removed,
            "explanation": "；".join(parts) + "。",
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.fetch_one("SELECT * FROM dynamic_risk_runs WHERE id = ?", (run_id,))
        if run is None:
            raise KeyError("动态风险评估记录不存在")
        run["weather"] = json_load(run.pop("weather_json"), {})
        raw_items = self._load_run_items(run_id)
        items = self._dedupe_run_items(raw_items)
        previous_run_id = self._previous_run_id(run)
        previous_items = (
            self._dedupe_run_items(self._load_run_items(previous_run_id))
            if previous_run_id
            else []
        )
        previous_by_key = {logical_task_key(item): item for item in previous_items}
        current_keys = {logical_task_key(item) for item in items}
        for item in items:
            item["change"] = self._change_details(
                item, previous_by_key.get(logical_task_key(item))
            )
        changes = Counter(item["change"]["direction"] for item in items)
        removed_count = sum(
            1 for item in previous_items if logical_task_key(item) not in current_keys
        )
        source_record_count = sum(int(item.get("duplicate_count") or 1) for item in items)
        run["raw_task_count"] = source_record_count
        run["task_count"] = len(items)
        counts = Counter(item["risk_level"] for item in items)
        run["red_count"] = counts["red"]
        run["yellow_count"] = counts["yellow"]
        run["green_count"] = counts["green"]
        run["items"] = items
        run["previous_run_id"] = previous_run_id
        run["data_quality"] = {
            "raw_task_count": source_record_count,
            "logical_task_count": len(items),
            "merged_record_count": max(0, source_record_count - len(items)),
            "status": "merged" if source_record_count != len(items) else "clean",
        }
        trigger_labels = {
            "task_changed": "任务数据变化",
            "weather_changed": "天气数据变化",
            "learning_changed": "学习记录变化",
            "audit_changed": "方案审计变化",
            "data_refresh": "数据刷新",
        }
        run["version_change"] = {
            "reason": trigger_labels.get(run.get("trigger_type"), run.get("trigger_type")),
            "is_baseline": previous_run_id is None,
            "escalated_count": changes["up"],
            "reduced_count": changes["down"],
            "changed_count": changes["changed"],
            "unchanged_count": changes["same"],
            "new_count": changes["new"],
            "removed_count": removed_count,
            "summary": (
                "这是当日首个评估版本。"
                if previous_run_id is None
                else f"较上一版本：升高{changes['up']}项、降低{changes['down']}项、"
                f"条件变化{changes['changed']}项、新增{changes['new']}项、移除{removed_count}项。"
            ),
        }
        run["project_name"] = self.project_name
        return run

    def latest(
        self, assessment_date: str | None = None, *, include_test: bool = False
    ) -> dict[str, Any] | None:
        target = assessment_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        row = self.db.fetch_one(
            """SELECT id FROM dynamic_risk_runs
               WHERE assessment_date = ? AND status = 'completed' AND include_test = ?
                 AND input_fingerprint <> ''
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (target, int(include_test)),
        )
        return self.get_run(row["id"]) if row else None

    def list_runs(self, limit: int = 20, *, include_test: bool = False) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT id, assessment_date, trigger_type, status, task_count, red_count,
                      yellow_count, green_count, include_test, created_at, completed_at
               FROM dynamic_risk_runs
               WHERE include_test = ? AND input_fingerprint <> ''
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (int(include_test), limit),
        )

    def recalculate(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return self.evaluate(
            run["assessment_date"],
            trigger_type="data_refresh",
            refresh_weather=True,
            include_test=bool(run["include_test"]),
        )

    def refresh_for_worker(
        self, worker_ref: str, *, task_id: str | None = None
    ) -> list[dict[str, Any]]:
        if task_id:
            rows = self.db.fetch_all(
                "SELECT DISTINCT scheduled_date FROM work_tasks WHERE id = ?",
                (task_id,),
            )
        else:
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            rows = self.db.fetch_all(
                """SELECT DISTINCT scheduled_date FROM work_tasks
                   WHERE worker_ref = ? AND scheduled_date >= ?
                   ORDER BY scheduled_date""",
                (worker_ref.strip(), today),
            )
        return [
            self.evaluate(
                row["scheduled_date"],
                trigger_type="learning_changed",
                refresh_weather=True,
            )
            for row in rows
            if row["scheduled_date"]
        ]

    def refresh_for_audit(self, audit_run_id: str) -> list[dict[str, Any]]:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        rows = self.db.fetch_all(
            """SELECT DISTINCT scheduled_date FROM work_tasks
               WHERE audit_run_id = ? AND scheduled_date >= ?
               ORDER BY scheduled_date""",
            (audit_run_id, today),
        )
        return [
            self.evaluate(
                row["scheduled_date"],
                trigger_type="audit_changed",
                refresh_weather=True,
            )
            for row in rows
            if row["scheduled_date"]
        ]
