from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .retrieval import similarity

OBJECT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("template", ("模板", "支模", "拆模")),
    ("scaffold", ("脚手架", "外架", "排栅")),
    ("basket", ("吊篮", "悬吊", "座板")),
    ("elevator_shaft", ("电梯井", "电梯井口")),
    ("opening", ("洞口", "井口")),
    ("roof", ("屋面", "屋顶", "采光板", "采光瓦")),
    ("curtain_wall", ("幕墙", "外墙", "外立面")),
    ("platform", ("操作平台", "升降平台", "移动平台", "高空作业车")),
    ("edge", ("临边", "楼层边")),
    ("steel", ("钢结构", "钢架", "屋架", "檩条")),
    ("equipment", ("设备", "管廊", "吊架", "水箱")),
)

ACTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dismantle", ("拆除", "拆卸", "拆模")),
    ("erect", ("搭设", "架设")),
    ("install", ("安装", "固定", "吊装", "砌筑", "浇筑")),
    ("clean", ("清洗", "清理", "清扫")),
    ("repair", ("维修", "检修", "更换", "修补", "补漏")),
)


def _semantic_groups(text: str, groups: tuple[tuple[str, tuple[str, ...]], ...]) -> set[str]:
    return {name for name, terms in groups if any(term in text for term in terms)}


def _split_terms(value: str | None) -> list[str]:
    if not value or value.strip() == "未说明":
        return []
    return list(
        dict.fromkeys(
            part.strip()
            for part in re.split(r"[；;、\n]", value)
            if part.strip() and part.strip() != "未说明"
        )
    )


class AccidentKnowledgeRepository:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise FileNotFoundError(f"事故知识图谱不存在：{self.path}")
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def list_cases(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT * FROM cases ORDER BY case_id")
            ]

    def search(
        self,
        *,
        normalized_task: str,
        work_content: str,
        location: str,
        scenes: list[str],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        query = " ".join(
            value for value in (normalized_task, work_content, location, *scenes) if value
        )
        query_objects = _semantic_groups(query, OBJECT_GROUPS)
        query_actions = _semantic_groups(query, ACTION_GROUPS)
        primary_object = next(
            (name for name, _ in OBJECT_GROUPS if name in query_objects), None
        )
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM cases")]

        ranked: list[tuple[float, bool, dict[str, Any]]] = []
        scene_set = set(scenes)
        for row in rows:
            target = " ".join(
                str(row.get(field) or "")
                for field in (
                    "task",
                    "scene",
                    "original_scene",
                    "risk_factors",
                    "unsafe_behaviors",
                    "measures",
                )
            )
            case_core = " ".join(
                str(row.get(field) or "")
                for field in ("task", "scene", "original_scene")
            )
            lexical = similarity(query, target)
            case_objects = _semantic_groups(case_core, OBJECT_GROUPS)
            case_actions = _semantic_groups(case_core, ACTION_GROUPS)
            object_match = bool(primary_object and primary_object in case_objects)
            secondary_object_matches = len(
                (query_objects & case_objects) - ({primary_object} if primary_object else set())
            )
            action_match = bool(query_actions & case_actions)
            task_bonus = 0.0
            case_task = str(row.get("task") or "")
            if normalized_task and (
                normalized_task in case_task or case_task in normalized_task
            ):
                task_bonus = 0.30
            scene_bonus = 0.18 if str(row.get("scene") or "") in scene_set else 0.0
            object_bonus = 0.52 if object_match else 0.0
            secondary_object_bonus = min(secondary_object_matches, 2) * 0.12
            action_bonus = 0.16 if action_match else 0.0
            mismatch_penalty = (
                0.24
                if primary_object and case_objects and not object_match
                else 0.0
            )
            score = min(
                1.0,
                max(
                    0.0,
                    0.30 * lexical
                    + task_bonus
                    + scene_bonus
                    + object_bonus
                    + secondary_object_bonus
                    + action_bonus
                    - mismatch_penalty,
                ),
            )
            if score <= 0:
                continue
            strong_match = bool(
                task_bonus >= 0.30
                or object_match
                or (not primary_object and scene_bonus and action_match)
            )
            ranked.append((score, strong_match, row))
        ranked.sort(key=lambda item: (-item[0], item[2]["case_id"]))

        strong = [item for item in ranked if item[1]]
        selected = strong[:limit] if strong else ranked[:1]

        results: list[dict[str, Any]] = []
        for score, _, row in selected:
            evidence = str(row.get("evidence") or row.get("direct_cause") or "").strip()
            if len(evidence) > 900:
                evidence = evidence[:900].rstrip() + "……"
            results.append(
                {
                    "case_id": row["case_id"],
                    "title": row["title"],
                    "task": str(row.get("task") or ""),
                    "scene": str(row.get("scene") or ""),
                    "consequence": str(row.get("consequence") or ""),
                    "risk_factors": _split_terms(row.get("risk_factors")),
                    "unsafe_behaviors": _split_terms(row.get("unsafe_behaviors")),
                    "measures": _split_terms(row.get("measures")),
                    "evidence": evidence,
                    "source_agency": str(row.get("source_agency") or ""),
                    "source_url": str(row.get("source_url") or ""),
                    "score": round(score, 4),
                }
            )
        return results
