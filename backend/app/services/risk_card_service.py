from __future__ import annotations

import re
from typing import Any

from ..repositories import Repository
from .accident_knowledge import AccidentKnowledgeRepository
from .retrieval import similarity
from .standard_rag import StandardRAGService
from .weather_provider import WeatherProvider


def _unique(values: list[str], limit: int) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:limit]


def _split(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[；;、]", value or "") if part.strip()]


def _display_requirement(value: str) -> str:
    return re.sub(r"[：:]\s*>$", "。", value.strip())


def _scaffold_type_keys(text: str) -> set[str]:
    keys = set()
    mappings = (
        (("落地",), "ground"),
        (("悬挑",), "cantilever"),
        (("附着式升降", "爬架"), "climbing"),
        (("支撑脚手架", "满堂"), "support"),
    )
    for terms, key in mappings:
        if any(term in text for term in terms):
            keys.add(key)
    return keys


def _rule_matches_scaffold_type(task: dict[str, Any], rule: dict[str, Any]) -> bool:
    if "脚手架" not in str(task.get("normalized_task") or ""):
        return True
    rule_text = f"{rule.get('process', '')} {rule.get('requirement', '')}"
    rule_types = _scaffold_type_keys(rule_text)
    if not rule_types:
        return True
    equipment_type = str(task.get("equipment_type") or "")
    if not equipment_type:
        return True
    return bool(_scaffold_type_keys(equipment_type) & rule_types)


def _task_action(task: dict[str, Any]) -> str:
    action = str(task.get("task_action") or "")
    if action:
        return action
    text = f"{task.get('normalized_task', '')} {task.get('work_content', '')}"
    if any(term in text for term in ("拆除", "拆卸", "拆架", "拆模")):
        return "拆除"
    if any(term in text for term in ("搭设", "架设", "组装")):
        return "搭设"
    return ""


def _rule_matches_task_action(task: dict[str, Any], rule: dict[str, Any]) -> bool:
    action = _task_action(task)
    if action not in {
        "拆除",
        "搭设",
        "安装",
        "使用",
        "升降",
        "检查",
        "维修",
        "清理",
        "吊装",
    }:
        return True
    if rule.get("rule_id") in {"JSJ-001", "JSJ-002", "JSJ-003", "JSJ-004", "JSJ-011"}:
        return True
    text = str(rule.get("requirement") or "")
    erection = any(term in text for term in ("搭设", "架设", "组装", "安装"))
    dismantling = any(term in text for term in ("拆除", "拆卸", "拆运"))
    use_only = any(
        term in text
        for term in (
            "作业层上的荷载",
            "动火作业",
            "升降作业",
            "提升作业",
            "完工后应进行验收",
        )
    )
    if action == "拆除":
        if erection and not dismantling:
            return False
        return not (use_only and not dismantling)
    if action in {"搭设", "安装"}:
        if dismantling and not erection:
            return False
        return not (use_only and not erection)
    if action == "升降":
        return not (dismantling or (erection and "升降" not in text))
    return not (erection or dismantling)


def filter_card_for_task_action(
    task: dict[str, Any], card: dict[str, Any]
) -> dict[str, Any]:
    """Remove lifecycle-specific reminders that conflict with the task action.

    This also repairs cards stored before ``task_action`` was persisted.
    """
    action = _task_action(task)
    if action not in {"拆除", "搭设", "安装"}:
        return card

    def relevant(value: str) -> bool:
        text = str(value or "")
        erection = any(term in text for term in ("搭设", "架设", "组装"))
        dismantling = any(term in text for term in ("拆除", "拆卸", "拆运"))
        if action == "拆除":
            return not (erection and not dismantling)
        return not (dismantling and not erection)

    return {
        **card,
        "pre_job_checks": [
            item for item in card.get("pre_job_checks") or [] if relevant(item)
        ],
        "prohibited_behaviors": [
            item for item in card.get("prohibited_behaviors") or [] if relevant(item)
        ],
    }


class RiskCardService:
    def __init__(
        self,
        repository: Repository,
        standard_rag: StandardRAGService,
        accidents: AccidentKnowledgeRepository,
        weather: WeatherProvider,
    ):
        self.repository = repository
        self.standard_rag = standard_rag
        self.accidents = accidents
        self.weather = weather

    def _select_rules(self, task: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        rules = [
            rule
            for rule in self.repository.list_enabled_rules(task["scenes"])
            if _rule_matches_task_action(task, rule)
            and _rule_matches_scaffold_type(task, rule)
        ]
        query = " ".join(
            str(task.get(field) or "")
            for field in (
                "normalized_task",
                "work_content",
                "work_location",
                "work_floor",
                "task_action",
                "equipment_type",
            )
        )
        ranked: list[tuple[float, dict[str, Any]]] = []
        for rule in rules:
            target = " ".join(
                str(rule.get(field) or "")
                for field in (
                    "trigger_condition",
                    "requirement",
                )
            )
            score = similarity(query, target)
            if score > 0 and rule.get("risk_level") == "重大":
                score += 0.05
            ranked.append((score, rule))
        ranked.sort(key=lambda item: (-item[0], item[1]["rule_id"]))
        action_priority_ids = {
            "拆除": {"JSJ-018", "JSJ-020", "JSJ-022"},
            "搭设": {"JSJ-005", "JSJ-006", "JSJ-007", "JSJ-009"},
            "升降": {"JSJ-017", "JSJ-040", "JSJ-043", "JSJ-044"},
        }.get(_task_action(task), set())
        selected = [rule for rule in rules if rule["rule_id"] in action_priority_ids]
        selected.extend(
            rule
            for score, rule in ranked
            if score > 0 and rule not in selected
        )
        selected = selected[:5]
        core_rule_ids = {
            "GZ-002",
            "GZ-004",
            "GZ-007",
            "GZ-008",
            "PPE-001",
            "PPE-002",
            "PPE-007",
        }
        if "拆" in query:
            core_rule_ids.update({"GZ-013", "GZ-014"})
        for rule in rules:
            if rule["rule_id"] in core_rule_ids and rule not in selected:
                selected.append(rule)
        weather_rule_ids = {"GZ-016", "GZ-018"}
        if "施工脚手架" in task["scenes"] or "脚手架搭设与拆除" in task["scenes"]:
            weather_rule_ids.update({"JSJ-011", "JSJ-012"})
        for rule in rules:
            if rule["rule_id"] in weather_rule_ids and rule not in selected:
                selected.append(rule)
        return selected[:limit] + [
            rule
            for rule in selected[limit:]
            if rule["rule_id"] in weather_rule_ids
        ]

    def _audit_findings(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = task.get("audit_run_id")
        if not run_id:
            return []
        run = self.repository.get_audit_run(run_id, include_items=True)
        scenes = set(task["scenes"])
        return [finding for finding in run["findings"] if finding["scene"] in scenes][:5]

    def _standard_evidence(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidences: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for rule in rules:
            key = (str(rule.get("standard_code") or ""), str(rule.get("clause") or ""))
            if key in seen:
                continue
            seen.add(key)
            hits = self.standard_rag.retrieve(
                str(rule.get("requirement") or ""),
                standard_code=key[0] or None,
                clause=key[1] or None,
                expected_page=rule.get("pdf_page"),
                limit=1,
            )
            quote = str(hits[0].get("text") or "") if hits else str(rule.get("original_text") or "")
            location = None
            if hits:
                location = f"PDF第{hits[0].get('page_start')}页"
            elif rule.get("pdf_page"):
                location = f"PDF第{rule['pdf_page']}页"
            evidences.append(
                {
                    "evidence_type": "standard",
                    "source_id": rule["rule_id"],
                    "title": f"{key[0]} {key[1]}".strip(),
                    "quote": quote[:1000],
                    "location": location,
                    "source_url": None,
                }
            )
            if len(evidences) >= 6:
                break
        return evidences

    def weather_warnings(
        self, task: dict[str, Any], weather: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if weather["status"] != "ok":
            return [
                {
                    "level": "confirm",
                    "message": weather["summary"],
                    "rule_id": None,
                    "standard_code": None,
                    "clause": None,
                }
            ]
        warnings: list[dict[str, Any]] = []
        scenes = set(task["scenes"])
        max_wind = weather.get("max_wind_speed_kmh")
        scaffold = bool(scenes & {"施工脚手架", "脚手架搭设与拆除"})
        if max_wind is not None and max_wind >= 39 and scaffold:
            warnings.append(
                {
                    "level": "stop",
                    "message": (
                        f"作业时段最大风速约{max_wind} km/h，已进入6级风区间；"
                        "脚手架架上作业应停止。"
                    ),
                    "rule_id": "JSJ-011",
                    "standard_code": "GB 55023-2022",
                    "clause": "5.3.2",
                }
            )
        elif max_wind is not None and max_wind >= 39:
            warnings.append(
                {
                    "level": "confirm",
                    "message": (
                        f"作业时段最大风速约{max_wind} km/h，已进入6级风区间；"
                        "请安全员结合具体工种规则确认是否调整或暂停。"
                    ),
                    "rule_id": None,
                    "standard_code": None,
                    "clause": None,
                }
            )
        conditions = " ".join(weather.get("sky_conditions") or []).upper()
        precipitation = weather.get("precipitation")
        wet = bool(precipitation is not None and precipitation >= 0.0606) or any(
            token in conditions for token in ("RAIN", "SNOW", "FOG", "SLEET")
        )
        if wet and "脚手架搭设与拆除" in scenes:
            warnings.append(
                {
                    "level": "stop",
                    "message": "预报作业时段存在雨、雪或雾条件；应停止脚手架搭设和拆除作业。",
                    "rule_id": "JSJ-012",
                    "standard_code": "GB 55023-2022",
                    "clause": "5.3.2",
                }
            )
        elif wet:
            warnings.append(
                {
                    "level": "warning",
                    "message": (
                        "预报作业时段存在降水、雾或湿滑条件；应采取防滑措施，"
                        "并确认作业面无积水、冰雪或霜。"
                    ),
                    "rule_id": "GZ-016",
                    "standard_code": "JGJ 80-2016",
                    "clause": "3.0.8",
                }
            )
        for alert in weather.get("alerts") or []:
            warnings.append(
                {
                    "level": "confirm",
                    "message": f"气象预警：{alert}。请安全员确认其对本次高处作业的影响。",
                    "rule_id": None,
                    "standard_code": None,
                    "clause": None,
                }
            )
        if not warnings:
            warnings.append(
                {
                    "level": "info",
                    "message": (
                        "当前天气数据未触发已配置的高处作业天气限制，"
                        "作业前仍需复核现场实况。"
                    ),
                    "rule_id": None,
                    "standard_code": None,
                    "clause": None,
                }
            )
        return warnings

    # Backward-compatible alias retained for existing tests and integrations.
    _weather_warnings = weather_warnings

    def build(self, task: dict[str, Any]) -> dict[str, Any]:
        rules = self._select_rules(task)
        audit_findings = self._audit_findings(task)
        accidents = self.accidents.search(
            normalized_task=task["normalized_task"],
            work_content=task["work_content"],
            location=task["work_location"],
            scenes=task["scenes"],
        )
        weather = self.weather.get_forecast(task["work_time"])
        weather_warnings = self.weather_warnings(task, weather)

        main_risks = []
        for finding in audit_findings:
            main_risks.extend(_split(str(finding.get("risk_consequence") or "")))
        for rule in rules:
            main_risks.extend(_split(str(rule.get("hazards") or "")))
        for accident in accidents:
            main_risks.extend(accident["risk_factors"])

        prohibited = [
            _display_requirement(str(rule["requirement"]))
            for rule in rules
            if any(
                term in str(rule.get("requirement") or "")
                for term in ("严禁", "不得", "禁止", "停止")
            )
        ]
        for accident in accidents:
            prohibited.extend(accident["unsafe_behaviors"])

        pre_job_checks = [
            _display_requirement(str(rule["requirement"]))
            for rule in rules
            if not any(
                term in str(rule.get("requirement") or "")
                for term in ("严禁", "不得", "禁止", "停止")
            )
        ]
        pre_job_checks.extend(
            str(finding.get("suggestion") or "") for finding in audit_findings
        )

        if "施工脚手架" in task["scenes"]:
            human_confirmations = [
                "确认脚手架类型、实际作业区域、警戒范围和作业时间与现场安排一致。",
                "确认脚手架搭拆人员持有与登高架设作业相符的有效特种作业操作证。",
            ]
            if task.get("equipment_type") == "类型待现场确认":
                human_confirmations.append("作业前由现场负责人确认脚手架结构类型。")
        else:
            human_confirmations = [
                "确认任务描述、作业楼层/高度、位置和时间与现场安排一致。"
            ]
        if "安全带使用" in task["scenes"]:
            human_confirmations.append("确认作业面已设置与现场条件匹配的可靠安全带挂点。")
        if set(task["scenes"]) & {"临边作业", "洞口作业", "悬空作业"}:
            human_confirmations.append("确认临边、洞口及作业立足面防护已经到位且未被擅自拆除。")
        if audit_findings:
            human_confirmations.append("确认关联方案审计问题已整改或已采取经责任人员确认的现场措施。")
        if weather["status"] != "ok":
            human_confirmations.append(
                "作业前由现场人员复核天气条件，包括实时风力、降水和气象预警。"
            )
        if any(item["level"] == "stop" for item in weather_warnings):
            human_confirmations.append("涉及暂停、调整作业或重新许可的决定必须由有职责人员确认。")

        evidences = self._standard_evidence(rules)
        for finding in audit_findings:
            evidences.append(
                {
                    "evidence_type": "audit",
                    "source_id": finding["id"],
                    "title": finding["title"],
                    "quote": str(finding.get("issue") or ""),
                    "location": str(finding.get("source_location") or ""),
                    "source_url": None,
                }
            )
        for accident in accidents:
            evidences.append(
                {
                    "evidence_type": "accident",
                    "source_id": accident["case_id"],
                    "title": accident["title"],
                    "quote": accident["evidence"],
                    "location": accident["source_agency"],
                    "source_url": accident["source_url"],
                }
            )
        evidences.append(
            {
                "evidence_type": "weather",
                "source_id": "caiyun-v2.6",
                "title": f"{weather['project_name']}天气",
                "quote": weather["summary"],
                "location": weather.get("forecast_window"),
                "source_url": "https://docs.caiyunapp.com/weather-api/v2/v2.6/6-weather.html",
            }
        )

        return {
            "worker_ref": task["worker_ref"],
            "team_ref": task["team_ref"],
            "audit_run_id": task.get("audit_run_id"),
            "work_content": task["work_content"],
            "location": task["work_location"],
            "floor": task["work_floor"],
            "work_time": task["work_time"],
            "normalized_task": task["normalized_task"],
            "task_action": task.get("task_action", ""),
            "equipment_type": task.get("equipment_type", ""),
            "scenes": task["scenes"],
            "main_risks": _unique(main_risks, 6),
            "pre_job_checks": _unique(pre_job_checks, 7),
            "prohibited_behaviors": _unique(prohibited, 6),
            "weather": weather,
            "weather_warnings": weather_warnings,
            "similar_accidents": accidents,
            "human_confirmations": _unique(human_confirmations, 8),
            "evidences": evidences[:15],
            "safety_notice": (
                "本风险卡仅用于辅助班前提醒；现场状态、作业许可、"
                "暂停或调整作业必须由有职责人员确认。"
            ),
        }
