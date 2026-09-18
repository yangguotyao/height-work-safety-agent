from __future__ import annotations

import json
import re
from typing import Any

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, Field

from ..config import Settings

ALLOWED_ASSISTANT_AGENTS = frozenset(
    {
        "coordinator",
        "audit_agent",
        "worker_agent",
        "knowledge_agent",
        "risk_agent",
        "weather_agent",
        "safety_log_agent",
        "web_agent",
        "general_agent",
    }
)


class AssistantPlan(BaseModel):
    agents: list[str] = Field(min_length=1, max_length=2)
    reason: str = ""


class AssistantModelService:
    """Bounded model planner and general-answer adapter for the unified assistant."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: OpenAI | None = None
        if (
            settings.model_provider == "openai"
            and settings.model_api_key
            and settings.model_name
        ):
            arguments: dict[str, Any] = {
                "api_key": settings.model_api_key,
                "timeout": settings.model_timeout_seconds,
                "max_retries": 0,
            }
            if settings.model_base_url:
                arguments["base_url"] = settings.model_base_url
            self.client = OpenAI(**arguments)

    @property
    def configured(self) -> bool:
        return self.client is not None and bool(self.settings.model_name)

    def _completion(
        self, messages: list[dict[str, str]], *, json_response: bool, max_tokens: int
    ) -> str:
        if not self.configured or self.client is None:
            raise RuntimeError("智能助手模型未配置")
        arguments: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_response:
            arguments["response_format"] = {"type": "json_object"}
        try:
            response = self.client.chat.completions.create(**arguments)
        except BadRequestError:
            arguments.pop("response_format", None)
            response = self.client.chat.completions.create(**arguments)
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _context_text(context_packets: list[dict[str, Any]], limit: int = 6) -> str:
        messages = [
            str(packet.get("content") or "").strip()
            for packet in context_packets
            if packet.get("source") == "short_term_message"
        ]
        return "\n".join(item for item in messages[-limit:] if item)[-4000:]

    def plan(
        self,
        *,
        message: str,
        context_packets: list[dict[str, Any]],
        project_name: str,
    ) -> list[str] | None:
        if not self.configured:
            return None
        system = (
            "你是施工安全平台智能助手的意图规划器。你只能选择下列 Agent，不回答问题，"
            "返回JSON：{\"agents\":[\"agent_name\"],\"reason\":\"简短原因\"}。\n"
            "coordinator：当前项目总体概览；audit_agent：已上传方案的审查状态和问题；"
            "worker_agent：录入班前任务，或回答高处作业规范、安全措施；"
            "knowledge_agent：项目知识图谱、事故案例检索；risk_agent：项目动态风险清单和等级；"
            "weather_agent：当前项目位置今天、明天或后天的天气；"
            "safety_log_agent：查看或生成项目安全日志；web_agent：需要最新外部事实、其他地区天气，"
            "或用户明确要求联网搜索；general_agent：闲聊、写作、解释等无需项目数据和实时网页的通用问题。\n"
            "关键边界：单独问‘今天天气怎么样’属于weather_agent，不属于risk_agent；"
            "只有明确询问风险评估、风险等级或风险清单才选risk_agent。"
            "描述具体日期、时段、楼层和施工动作且意图是登记任务时选worker_agent。"
            "项目内部问题不应使用web_agent。一般只选一个；仅当用户明确要求组合多个业务结果时最多选两个。"
            "不得输出清单以外的名称。"
        )
        payload = {
            "current_project": project_name,
            "recent_conversation": self._context_text(context_packets),
            "user_message": message,
        }
        try:
            content = self._completion(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                json_response=True,
                max_tokens=220,
            )
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
            plan = AssistantPlan.model_validate(json.loads(content))
        except Exception:
            return None
        agents = list(dict.fromkeys(plan.agents))[:2]
        if not agents or any(agent not in ALLOWED_ASSISTANT_AGENTS for agent in agents):
            return None
        return agents

    def answer_general(
        self,
        *,
        question: str,
        context_packets: list[dict[str, Any]],
        project_name: str,
    ) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "unconfigured",
                "answer": "我可以查询项目数据、施工安全知识和联网资料。请把问题再说具体一些。",
            }
        system = (
            "你是高处作业安全平台中的通用智能助手。用简洁、自然的中文直接回答。"
            "只有在问题确实涉及当前项目时才使用对话中的项目信息。不得声称已经联网或读取了"
            "未提供的数据；涉及实时外部事实时应建议由联网检索工具处理。"
            "安全相关回答属于辅助建议，不得弱化法定要求。"
        )
        user = (
            f"当前项目：{project_name}\n"
            f"近期对话：\n{self._context_text(context_packets)}\n\n"
            f"当前问题：{question}"
        )
        try:
            answer = self._completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                json_response=False,
                max_tokens=900,
            )
        except Exception:
            answer = "通用问答模型暂时不可用。你仍可查询项目审查、班前任务、风险、天气和安全日志。"
            return {"status": "unavailable", "answer": answer}
        return {"status": "ok", "answer": answer}
