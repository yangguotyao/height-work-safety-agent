from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..schemas import WorkerAssistantMessageCreate
from .repository import PlatformRepository


class EmptyInput(BaseModel):
    pass


class QueryInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class AccidentQueryInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=3)


class SafetyQuestionInput(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    task_id: str | None = None


class TaskMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class AssessmentInput(BaseModel):
    assessment_date: str | None = None
    refresh_weather: bool = False


class SafetyLogInput(BaseModel):
    assessment_date: str | None = None


class WebSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    count: int = Field(default=5, ge=1, le=10)


class WeatherInput(BaseModel):
    work_time: str = Field(default="今天", min_length=1, max_length=50)


class GeneralQuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    context_packets: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class ToolContext:
    app: Any
    identity: dict[str, Any]
    conversation_id: str
    message_id: str | None
    agent_name: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable[[ToolContext, dict[str, Any]], Any]
    agents: frozenset[str]
    roles: frozenset[str]
    mutating: bool = False

    def langchain_contract(self) -> StructuredTool:
        def contract_only(**_: Any) -> str:
            return "该工具由平台工具运行器在已认证上下文中执行。"

        return StructuredTool.from_function(
            func=contract_only,
            name=self.name.replace(".", "_"),
            description=self.description,
            args_schema=self.args_schema,
        )


class ToolRegistry:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具已注册：{spec.name}")
        self._tools[spec.name] = spec

    def catalog(self, *, agent_name: str, project_role: str) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "mutating": spec.mutating,
                "input_schema": spec.args_schema.model_json_schema(),
            }
            for spec in self._tools.values()
            if agent_name in spec.agents and project_role in spec.roles
        ]

    def invoke(self, name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"未知工具：{name}")
        if context.agent_name not in spec.agents:
            raise PermissionError(f"{context.agent_name} 无权调用 {name}")
        role = str(context.identity["project_role"])
        if context.identity["system_role"] != "admin" and role not in spec.roles:
            raise PermissionError(f"角色 {role} 无权调用 {name}")
        validated = spec.args_schema.model_validate(arguments).model_dump()
        started = time.perf_counter()
        status = "succeeded"
        output: Any = {}
        error = ""
        try:
            output = spec.handler(context, validated)
            return output
        except Exception as exc:
            status = "failed"
            error = str(exc)
            raise
        finally:
            self.repository.log_tool_run(
                conversation_id=context.conversation_id,
                message_id=context.message_id,
                agent_name=context.agent_name,
                tool_name=name,
                status=status,
                input_data=validated,
                output_data=output,
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


ALL_ROLES = frozenset({"admin", "safety_officer", "team_leader", "worker"})
MANAGER_ROLES = frozenset({"admin", "safety_officer", "team_leader"})


def build_tool_registry(repository: PlatformRepository) -> ToolRegistry:
    registry = ToolRegistry(repository)

    def system_overview(context: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
        app = context.app
        db = app.state.database
        latest = app.state.dynamic_risk_service.latest(include_test=False)
        return {
            "project": context.identity["project_name"],
            "rules": app.state.repository.count_rules(),
            "standards": app.state.repository.count_standards(),
            "tasks": int((db.fetch_one("SELECT COUNT(*) count FROM work_tasks") or {})["count"]),
            "latest_dynamic_risk": latest,
            "knowledge": app.state.project_knowledge_service.overview(include_test=False),
        }

    def knowledge_search(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        return context.app.state.project_knowledge_service.search(
            query=data["query"], limit=data["limit"], include_test=False
        )

    def accident_search(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        query = data["query"]
        for marker in ("检索", "查找", "搜索", "帮我找", "相似事故", "相关事故", "事故案例"):
            query = query.replace(marker, " ")
        task = " ".join(query.split()).strip("，。？? ") or data["query"]
        cases = context.app.state.accident_repository.search(
            normalized_task=task,
            work_content=task,
            location="",
            scenes=[],
            limit=data["limit"],
        )
        return {"query": task, "total": len(cases), "items": cases}

    def latest_audit(context: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
        row = context.app.state.database.fetch_one(
            """SELECT id FROM audit_runs WHERE status = 'completed'
               ORDER BY completed_at DESC LIMIT 1"""
        )
        if row is None:
            return {"status": "not_found", "message": "当前没有已完成的方案审计"}
        run = context.app.state.repository.get_audit_run(row["id"], include_items=True)
        run["items"] = run.get("items", [])[:20]
        return run

    def latest_risk(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        target = data.get("assessment_date") or date.today().isoformat()
        current = context.app.state.dynamic_risk_service.latest(target, include_test=False)
        if current is not None:
            return current
        return context.app.state.dynamic_risk_service.evaluate(
            target,
            trigger_type="agent_request",
            refresh_weather=bool(data.get("refresh_weather")),
            include_test=False,
        )

    def safety_question(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        worker_ref = context.identity.get("worker_ref") or context.identity["username"]
        return context.app.state.safety_qa_service.ask(
            worker_ref=worker_ref,
            question=data["question"],
            task_id=data.get("task_id"),
            use_llm=None,
        )

    def task_intake(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        previous = context.app.state.database.fetch_one(
            """SELECT output_json FROM agent_tool_runs
               WHERE conversation_id = ? AND tool_name = 'worker.task_intake'
                 AND status = 'succeeded'
               ORDER BY created_at DESC LIMIT 1""",
            (context.conversation_id,),
        )
        session_id = None
        if previous:
            prior_output = json.loads(previous["output_json"])
            if prior_output.get("status") == "collecting":
                session_id = prior_output.get("session_id")
        identity = context.identity
        payload = WorkerAssistantMessageCreate(
            session_id=session_id,
            message=data["message"],
            worker_ref=identity.get("worker_ref") or identity["username"],
            team_ref=identity.get("team_ref") or "",
            use_llm=None,
        )
        result = context.app.state.worker_assistant_service.process_message(payload)
        if result.get("task_id") and result.get("assessment_required"):
            task = context.app.state.worker_assistant_service.worker_repository.get_task(
                result["task_id"]
            )
            context.app.state.dynamic_risk_service.evaluate(
                task["scheduled_date"], trigger_type="task_changed", refresh_weather=False
            )
            context.app.state.project_knowledge_service.sync()
        return result

    def generate_safety_log(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        return context.app.state.safety_log_service.generate(data.get("assessment_date"))

    def latest_safety_log(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        result = context.app.state.safety_log_service.latest(data.get("assessment_date"))
        return result or {"status": "not_found", "message": "当前还没有安全日志。"}

    def web_search(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        return context.app.state.web_search_service.answer(
            data["query"], count=data.get("count")
        )

    def weather_forecast(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        return context.app.state.weather_provider.get_forecast(data["work_time"])

    def general_answer(context: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
        return context.app.state.assistant_model_service.answer_general(
            question=data["question"],
            context_packets=data.get("context_packets") or [],
            project_name=context.identity["project_name"],
        )

    specs = [
        ToolSpec(
            "system.overview",
            "读取项目、规范、任务、知识库和动态风险的统一概览。",
            EmptyInput,
            system_overview,
            frozenset({"coordinator"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "knowledge.search",
            "在项目知识库中检索规范、规则、事故、任务和风险实体。",
            QueryInput,
            knowledge_search,
            frozenset({"knowledge_agent", "worker_agent", "risk_agent"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "knowledge.accident_search",
            "按作业对象和动作检索1至3个最相关的可追溯事故案例。",
            AccidentQueryInput,
            accident_search,
            frozenset({"knowledge_agent", "worker_agent"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "audit.latest",
            "读取最新已完成的施工方案审计及其可追溯问题。",
            EmptyInput,
            latest_audit,
            frozenset({"audit_agent", "risk_agent"}),
            MANAGER_ROLES,
        ),
        ToolSpec(
            "risk.latest",
            "读取指定日期的最新动态风险评估；没有版本时按当前数据生成。",
            AssessmentInput,
            latest_risk,
            frozenset({"risk_agent", "coordinator"}),
            ALL_ROLES,
            mutating=True,
        ),
        ToolSpec(
            "worker.safety_qa",
            "使用规范证据回答当前工人的高处作业安全问题。",
            SafetyQuestionInput,
            safety_question,
            frozenset({"worker_agent"}),
            ALL_ROLES,
            mutating=True,
        ),
        ToolSpec(
            "worker.task_intake",
            "通过连续对话采集每日任务并生成风险卡。",
            TaskMessageInput,
            task_intake,
            frozenset({"worker_agent"}),
            ALL_ROLES,
            mutating=True,
        ),
        ToolSpec(
            "safety_log.generate",
            "综合项目审查、任务、知识和动态风险数据生成版本化当日安全日志。",
            SafetyLogInput,
            generate_safety_log,
            frozenset({"safety_log_agent"}),
            ALL_ROLES,
            mutating=True,
        ),
        ToolSpec(
            "safety_log.latest",
            "读取指定日期的最新安全日志。",
            SafetyLogInput,
            latest_safety_log,
            frozenset({"safety_log_agent", "coordinator"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "web.search",
            "使用博查 Web Search 回答非项目通用问题；不得写入项目风险和安全日志。",
            WebSearchInput,
            web_search,
            frozenset({"web_agent"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "weather.forecast",
            "读取当前项目位置今天、明天或后天的彩云天气预报。",
            WeatherInput,
            weather_forecast,
            frozenset({"weather_agent"}),
            ALL_ROLES,
        ),
        ToolSpec(
            "assistant.answer",
            "回答不需要项目业务数据或实时联网检索的通用问题。",
            GeneralQuestionInput,
            general_answer,
            frozenset({"general_agent"}),
            ALL_ROLES,
        ),
    ]
    for spec in specs:
        registry.register(spec)
        spec.langchain_contract()
    return registry
