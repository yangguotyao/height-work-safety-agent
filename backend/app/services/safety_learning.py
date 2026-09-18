from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from ..config import Settings
from ..repositories import Repository
from .learning_repository import LearningRepository
from .retrieval import similarity
from .standard_rag import StandardRAGService
from .worker_repository import WorkerAssistantRepository

SAFETY_NOTICE = "本回答用于安全学习和辅助提示，不代替现场安全技术交底、作业许可或安全员确认。"
PRECISION_TERMS = (
    "连墙件",
    "上下同时",
    "安全绳",
    "安全锁扣",
    "吊篮",
    "6级",
    "大风",
    "模板",
    "盖板",
    "防护栏杆",
    "梯子",
    "操作平台",
)


def _has_unsupported_opening_orientation_inference(
    answer: str, question: str, task: dict[str, Any] | None
) -> bool:
    context = question
    if task:
        context += " " + " ".join(
            str(task.get(field) or "")
            for field in ("work_content", "work_location", "work_floor")
        )
    for orientation in ("非竖向", "竖向"):
        if orientation in context:
            continue
        for match in re.finditer(
            rf"(?:属于|应是|应为|是|为)(?:一个|该)?{orientation}(?:洞口)?", answer
        ):
            prefix = answer[max(0, match.start() - 3) : match.start()]
            if not prefix.endswith(("若", "如", "如果", "假如")):
                return True
    return False


class SafetyQAService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        worker_repository: WorkerAssistantRepository,
        learning_repository: LearningRepository,
        standard_rag: StandardRAGService,
    ):
        self.settings = settings
        self.repository = repository
        self.worker_repository = worker_repository
        self.learning_repository = learning_repository
        self.standard_rag = standard_rag

    def _rank_rules(self, question: str, task: dict[str, Any] | None) -> list[dict[str, Any]]:
        task_scenes = set(task.get("scenes") or []) if task else set()
        task_text = ""
        if task:
            task_text = " ".join(
                str(task.get(field) or "")
                for field in ("normalized_task", "work_content", "task_action", "equipment_type")
            )
        ranked: list[tuple[float, dict[str, Any]]] = []
        for rule in self.repository.list_enabled_rules():
            requirement = str(rule.get("requirement") or "")
            target = " ".join(
                str(rule.get(field) or "")
                for field in ("scene", "process", "requirement", "original_text")
            )
            score = 0.65 * similarity(question, requirement) + 0.35 * similarity(
                question, target
            )
            score += sum(
                0.16 if term in requirement else 0.03
                for term in PRECISION_TERMS
                if term in question and term in target
            )
            if "拆" in question:
                score += 0.18 if "拆" in requirement else 0
                if "搭设" in requirement and "拆" not in requirement:
                    score -= 0.16
            elif "搭设" in question:
                score += 0.18 if "搭设" in requirement else 0
                if "拆" in requirement and "搭设" not in requirement:
                    score -= 0.16
            if rule["scene"] in task_scenes:
                score += 0.12
            if task_text:
                score += 0.08 * similarity(task_text, target)
            if score > 0.08:
                ranked.append((score, rule))
        ranked.sort(key=lambda item: (-item[0], item[1]["rule_id"]))
        return [{**rule, "score": round(score, 4)} for score, rule in ranked[:4]]

    def _evidences(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidences: list[dict[str, Any]] = []
        for rule in rules[:3]:
            chunk = self.standard_rag.retrieve(
                rule["requirement"],
                standard_code=rule["standard_code"],
                clause=rule["clause"],
                expected_page=rule["pdf_page"],
                limit=1,
            )
            source = chunk[0] if chunk else None
            evidences.append(
                {
                    "evidence_type": "standard",
                    "source_id": rule["rule_id"],
                    "title": f"{rule['standard_name']}（{rule['standard_code']}）",
                    "quote": str(
                        (source or {}).get("text")
                        or rule["original_text"]
                        or rule["requirement"]
                    ),
                    "location": f"第{rule['clause']}条，PDF第{rule['pdf_page']}页",
                    "source_url": None,
                }
            )
        return evidences

    def _model_answer(
        self, question: str, task: dict[str, Any] | None, evidences: list[dict[str, Any]]
    ) -> str:
        if self.settings.model_provider != "openai":
            raise ValueError("未启用外部模型")
        client_args: dict[str, Any] = {
            "api_key": self.settings.model_api_key,
            "timeout": self.settings.model_timeout_seconds,
            "max_retries": 0,
        }
        if self.settings.model_base_url:
            client_args["base_url"] = self.settings.model_base_url
        client = OpenAI(**client_args)
        context = "\n\n".join(
            f"证据{i + 1}：{item['title']}，{item['location']}\n{item['quote']}"
            for i, item in enumerate(evidences)
        )
        task_context = ""
        if task:
            task_context = (
                f"当天任务：{task['work_time']}，{task['work_floor']}，"
                f"{task['work_location']}，{task['work_content']}。"
            )
        system = (
            "你是面向一线工人的高处作业安全问答助手。只能依据给定证据回答，"
            "不得补造标准、条款、数值或现场事实。先直接回答问题，再用简短条目说明怎么做。"
            "必须区分搭设、使用和拆除阶段。证据不能支持时要明确说需安全员确认。"
            "若问题明确涉及某一阶段，只回答该阶段，不要为凑齐结构扩写其他阶段。"
            "楼层和位置不能证明洞口是竖向或非竖向，也不能证明洞口尺寸；用户未说明时"
            "必须按不同条件分别说明并要求现场确认，不得替用户推断。"
            "不能因为用户说要安装盖板，就倒推出洞口应为非竖向。"
            "不要宣布允许开工、验收合格或必须停工；这些结论交由有职责的现场人员确认。"
            "用简洁、易懂的纯文本中文，不使用Markdown加粗符号或标题，控制在350字以内。"
        )
        request: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"{task_context}\n问题：{question}\n\n可用证据：\n{context}",
                },
            ],
            "temperature": 0,
            "max_tokens": 800,
        }
        base_url = (self.settings.model_base_url or "").lower()
        if "dashscope" in base_url:
            request["extra_body"] = {"enable_thinking": False}
        elif "api.deepseek.com" in base_url:
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        response = client.chat.completions.create(**request)
        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            raise ValueError("模型未返回回答")
        return answer

    def ask(
        self,
        *,
        worker_ref: str,
        question: str,
        task_id: str | None,
        use_llm: bool | None,
    ) -> dict[str, Any]:
        task = self.worker_repository.get_task(task_id) if task_id else None
        if task and task.get("worker_ref") and task["worker_ref"] != worker_ref:
            raise ValueError("该任务不属于当前工人标识")
        rules = self._rank_rules(question, task)
        if not rules or float(rules[0]["score"]) < 0.12:
            return self.learning_repository.save_qa(
                worker_ref=worker_ref,
                task_id=task_id,
                question=question,
                answer=(
                    "现有规范证据不足以可靠回答这个问题。请补充具体作业场景、设备类型或"
                    f"作业阶段，并请现场安全员确认。\n\n{SAFETY_NOTICE}"
                ),
                answer_status="insufficient_evidence",
                evidence=[],
                model_provider="deterministic",
            )
        evidences = self._evidences(rules)
        should_use_llm = self.settings.model_provider == "openai" if use_llm is None else use_llm
        provider = "deterministic"
        if should_use_llm:
            try:
                answer = self._model_answer(question, task, evidences)
                if _has_unsupported_opening_orientation_inference(answer, question, task):
                    raise ValueError("模型推断了用户未提供的洞口方向")
                provider = "openai"
            except Exception:
                fallback_lines = [
                    f"- {rule['requirement']}" for rule in rules[: min(2, len(rules))]
                ]
                if "盖板" in question and not any(
                    term in " ".join(
                        [
                            question,
                            str((task or {}).get("work_content") or ""),
                            str((task or {}).get("work_location") or ""),
                        ]
                    )
                    for term in ("竖向洞口", "非竖向洞口")
                ):
                    fallback_lines.insert(
                        0,
                        "当前信息未说明洞口方向和尺寸，不能直接判定适用哪一档要求；"
                        "请先现场确认，再按对应条款执行。",
                    )
                answer = "\n".join(fallback_lines)
                provider = "deterministic-fallback"
        else:
            answer = "\n".join(f"- {rule['requirement']}" for rule in rules[:2])
        answer = f"{answer}\n\n{SAFETY_NOTICE}"
        return self.learning_repository.save_qa(
            worker_ref=worker_ref,
            task_id=task_id,
            question=question,
            answer=answer,
            answer_status="answered",
            evidence=evidences,
            model_provider=provider,
        )
