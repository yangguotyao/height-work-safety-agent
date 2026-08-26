from __future__ import annotations

import json
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from ..db import Database

SCENE_RULE_MAPPINGS: dict[str, set[str]] = {
    "general": {"高处作业综合管理"},
    "personal_protection": {
        "高处作业综合管理",
        "个体防护",
        "安全帽使用",
        "安全带使用",
    },
    "edge_work": {"临边作业", "防护栏杆"},
    "opening_work": {"洞口作业"},
    "climbing_work": {"攀登作业"},
    "suspended_work": {"悬空作业"},
    "cross_work": {"交叉作业"},
    "scaffold_erection": {"施工脚手架", "脚手架搭设与拆除"},
    "scaffold_dismantling": {"施工脚手架", "脚手架搭设与拆除"},
    "work_platform": {
        "操作平台通用",
        "移动式操作平台",
        "落地式操作平台",
        "悬挑式操作平台",
    },
    "suspended_basket": {"高处作业综合管理", "高处作业吊篮"},
    "formwork": {"高处作业综合管理", "悬空作业"},
    "curtain_wall": {
        "高处作业综合管理",
        "个体防护",
        "安全帽使用",
        "安全带使用",
        "临边作业",
        "防护栏杆",
        "悬空作业",
        "建筑幕墙设计",
        "幕墙安装与验收",
    },
}

GENERIC_BAD_PHRASES = (
    "只要作业人员经验丰富",
    "经班组口头同意",
    "只在发生事故后",
)


class QuestionBank:
    def __init__(self, path: Path, database: Database):
        self.path = Path(path)
        self.database = database
        self.version = ""
        self.scene_catalog: dict[str, dict[str, Any]] = {}
        self.questions: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"安全学习题库不存在：{self.path}")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.version = str(payload.get("version") or "")
        self.scene_catalog = dict(payload.get("scenes") or {})
        raw_questions = list(payload.get("questions") or [])
        rule_ids = [str(item.get("rule_id") or "") for item in raw_questions]
        placeholders = ",".join("?" for _ in rule_ids)
        rules = self.database.fetch_all(
            f"SELECT * FROM audit_rules WHERE rule_id IN ({placeholders})", rule_ids
        ) if rule_ids else []
        rules_by_id = {row["rule_id"]: row for row in rules}
        loaded: dict[str, dict[str, Any]] = {}
        for item in raw_questions:
            question_id = str(item.get("id") or "").strip()
            scene = str(item.get("scene") or "").strip()
            question_type = str(item.get("type") or "").strip()
            options = item.get("options")
            answer = str(item.get("answer") or "").strip()
            rule_id = str(item.get("rule_id") or "").strip()
            rule = rules_by_id.get(rule_id)
            if not question_id or question_id in loaded:
                raise ValueError(f"题库存在空ID或重复ID：{question_id}")
            if scene not in self.scene_catalog or scene not in SCENE_RULE_MAPPINGS:
                raise ValueError(f"题目 {question_id} 使用了未知场景：{scene}")
            if question_type not in {"single_choice", "true_false"}:
                raise ValueError(f"题目 {question_id} 类型不受支持：{question_type}")
            if not isinstance(options, list) or len(options) < 2:
                raise ValueError(f"题目 {question_id} 缺少有效选项")
            option_keys = {str(option.get("key") or "") for option in options}
            option_texts = [str(option.get("text") or "").strip() for option in options]
            if answer not in option_keys:
                raise ValueError(f"题目 {question_id} 的答案不在选项中")
            if any(not text for text in option_texts) or len(set(option_texts)) != len(
                option_texts
            ):
                raise ValueError(f"题目 {question_id} 存在空白或重复选项")
            if question_type == "single_choice" and any(
                phrase in text for phrase in GENERIC_BAD_PHRASES for text in option_texts
            ):
                raise ValueError(f"题目 {question_id} 仍包含通用套话干扰项")
            if question_type == "single_choice":
                correct_text = next(
                    str(option.get("text") or "").strip()
                    for option in options
                    if str(option.get("key") or "") == answer
                )
                distractor_lengths = [
                    len(str(option.get("text") or "").strip())
                    for option in options
                    if str(option.get("key") or "") != answer
                ]
                length_ratio = len(correct_text) / max(
                    1.0, statistics.mean(distractor_lengths)
                )
                if length_ratio > 1.8:
                    raise ValueError(f"题目 {question_id} 的正确项存在明显长度提示")
            if any("相可不" in text for text in option_texts):
                raise ValueError(f"题目 {question_id} 存在错误的自动变体")
            if question_type == "true_false" and option_keys != {"A", "B"}:
                raise ValueError(f"判断题 {question_id} 必须使用A/B两个选项")
            if rule is None or rule["enabled_status"] != "启用":
                raise ValueError(f"题目 {question_id} 引用了不存在或未启用的规则：{rule_id}")
            if rule["scene"] not in SCENE_RULE_MAPPINGS[scene]:
                raise ValueError(
                    f"题目 {question_id} 的规则场景 {rule['scene']} 与题库场景 {scene} 不匹配"
                )
            loaded[question_id] = {
                **item,
                "evidence": {
                    "rule_id": rule_id,
                    "standard_code": rule["standard_code"],
                    "standard_name": rule["standard_name"],
                    "clause": rule["clause"],
                    "page": rule["pdf_page"],
                    "quote": rule["original_text"] or rule["requirement"],
                },
                "explanation": str(item.get("explanation") or rule["requirement"]),
            }
        self.questions = loaded
        self._validate_coverage()

    def _validate_coverage(self) -> None:
        for scene in self.scene_catalog:
            items = [item for item in self.questions.values() if item["scene"] == scene]
            type_counts = Counter(item["type"] for item in items)
            if len(items) < 10:
                raise ValueError(f"题库场景 {scene} 少于10题")
            if type_counts["single_choice"] < 3 or type_counts["true_false"] < 2:
                raise ValueError(f"题库场景 {scene} 不能组成3道单选和2道判断")

    def status(self) -> dict[str, Any]:
        scenes = []
        for key, metadata in self.scene_catalog.items():
            items = [item for item in self.questions.values() if item["scene"] == key]
            counts = Counter(item["type"] for item in items)
            scenes.append(
                {
                    "key": key,
                    "name": metadata["name"],
                    "question_count": len(items),
                    "single_choice_count": counts["single_choice"],
                    "true_false_count": counts["true_false"],
                }
            )
        return {"version": self.version, "question_count": len(self.questions), "scenes": scenes}

    def get(self, question_id: str) -> dict[str, Any]:
        try:
            return self.questions[question_id]
        except KeyError as exc:
            raise KeyError("题目不存在") from exc

    def public(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "scene": item["scene"],
            "scene_name": self.scene_catalog[item["scene"]]["name"],
            "type": item["type"],
            "category": item["category"],
            "stem": item["stem"],
            "options": item["options"],
        }

    def infer_scene(self, task: dict[str, Any] | None) -> str:
        if not task:
            return "general"
        text = " ".join(
            str(task.get(field) or "")
            for field in ("normalized_task", "work_content", "task_action", "equipment_type")
        )
        scenes = set(task.get("scenes") or [])
        action = str(task.get("task_action") or "")
        if "脚手架" in text or scenes & {"施工脚手架", "脚手架搭设与拆除"}:
            return "scaffold_dismantling" if action == "拆除" else "scaffold_erection"
        if "模板" in text:
            return "formwork"
        if "吊篮" in text or "高处作业吊篮" in scenes:
            return "suspended_basket"
        if "幕墙" in text or scenes & {"玻璃幕墙", "幕墙安装与验收"}:
            return "curtain_wall"
        mapping = (
            ("洞口作业", "opening_work"),
            ("临边作业", "edge_work"),
            ("攀登作业", "climbing_work"),
            ("交叉作业", "cross_work"),
            ("悬空作业", "suspended_work"),
            ("操作平台通用", "work_platform"),
            ("安全带使用", "personal_protection"),
            ("安全帽使用", "personal_protection"),
        )
        for source_scene, bank_scene in mapping:
            if source_scene in scenes:
                return bank_scene
        return "general"

    def select(
        self,
        scene: str,
        *,
        preferred_ids: list[str] | None = None,
        excluded_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if scene not in self.scene_catalog:
            raise ValueError("不支持的测验场景")
        preferred = [item for item in (preferred_ids or []) if item in self.questions]
        excluded = excluded_ids or set()
        rng = random.SystemRandom()

        def choose(question_type: str, count: int) -> list[dict[str, Any]]:
            candidates = [
                item
                for item in self.questions.values()
                if item["scene"] == scene and item["type"] == question_type
            ]
            preferred_items = [
                self.questions[item_id]
                for item_id in preferred
                if self.questions[item_id]["scene"] == scene
                and self.questions[item_id]["type"] == question_type
            ]
            unseen = [item for item in candidates if item["id"] not in excluded]
            rng.shuffle(preferred_items)
            rng.shuffle(unseen)
            rng.shuffle(candidates)
            result: list[dict[str, Any]] = []
            for pool in (preferred_items, unseen, candidates):
                for item in pool:
                    if item["id"] not in {chosen["id"] for chosen in result}:
                        result.append(item)
                    if len(result) == count:
                        return result
            raise ValueError(f"场景 {scene} 的{question_type}题数量不足")

        selected = [*choose("single_choice", 3), *choose("true_false", 2)]
        rng.shuffle(selected)
        return selected
