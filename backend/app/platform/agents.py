from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .context import ContextBuilder, MemoryManager
from .repository import PlatformRepository
from .tools import ToolContext, ToolRegistry

AGENT_LABELS = {
    "coordinator": "总协调 Agent",
    "audit_agent": "方案审查 Agent",
    "worker_agent": "安全培训 Agent",
    "knowledge_agent": "安全知识图谱 Agent",
    "risk_agent": "风险分析 Agent",
    "safety_log_agent": "安全日志 Agent",
    "web_agent": "联网问答 Agent",
}


class UnifiedAgentState(TypedDict, total=False):
    app: Any
    identity: dict[str, Any]
    conversation_id: str
    message_id: str
    user_message: str
    context_packets: list[dict[str, Any]]
    agent_names: list[str]
    tool_results: list[dict[str, Any]]
    answer: str
    metadata: dict[str, Any]


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


class SpecialistAgent:
    name = "coordinator"

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def invoke_tool(
        self, state: UnifiedAgentState, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        return self.tools.invoke(
            tool_name,
            arguments,
            ToolContext(
                app=state["app"],
                identity=state["identity"],
                conversation_id=state["conversation_id"],
                message_id=state.get("message_id"),
                agent_name=self.name,
            ),
        )

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        result = self.invoke_tool(state, "system.overview", {})
        latest = result.get("latest_dynamic_risk") or {}
        return {
            "agent": self.name,
            "tool": "system.overview",
            "result": result,
            "text": (
                f"当前项目已加载 {result['rules']} 条规则、{result['standards']} 份规范，"
                f"累计 {result['tasks']} 项作业任务。"
                + (
                    f"最新动态评估包含红色 {latest.get('red_count', 0)} 项、"
                    f"黄色 {latest.get('yellow_count', 0)} 项、"
                    f"绿色 {latest.get('green_count', 0)} 项。"
                    if latest
                    else "当前还没有风险分析版本。"
                )
            ),
        }


class AuditAgent(SpecialistAgent):
    name = "audit_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        result = self.invoke_tool(state, "audit.latest", {})
        if result.get("status") == "not_found":
            text = result["message"]
        else:
            items = result.get("items", [])
            text = (
                f"最新方案审计状态为 {result.get('status')}，"
                f"发现 {len(items)} 项需要关注的结果。"
            )
            if items:
                lines = [
                    f"{index + 1}. {item.get('control_title') or item.get('issue', '审计问题')}"
                    for index, item in enumerate(items[:5])
                ]
                text += "\n" + "\n".join(lines)
        return {"agent": self.name, "tool": "audit.latest", "result": result, "text": text}


class WorkerAgent(SpecialistAgent):
    name = "worker_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        message = state["user_message"]
        if _contains_any(message, ("测验", "测试题", "做题", "练习题")):
            scene_match = re.search(r"(?:关于|来一组|做一组)([^，。]{2,20})(?:的|测验|题)", message)
            arguments = {"scene": scene_match.group(1).strip() if scene_match else None}
            result = self.invoke_tool(state, "worker.create_quiz", arguments)
            scene_name = result.get("scene_name", result.get("scene", "当前场景"))
            text = f"已生成 {scene_name} 5题测验，请在测验页面完成。"
            tool = "worker.create_quiz"
        elif _contains_any(message, ("学习记录", "错题", "学习情况", "复习")):
            result = self.invoke_tool(state, "worker.learning_profile", {})
            text = (
                f"你目前有 {len(result.get('active_wrong_questions', []))} 道待复习错题，"
                f"累计完成 {len(result.get('quiz_history', []))} 次测验。"
            )
            tool = "worker.learning_profile"
        elif _contains_any(
            message,
            ("今天", "明天", "上午", "下午", "晚上", "层", "楼", "去拆", "去装", "作业"),
        ) and not _contains_any(
            message, ("为什么", "能不能", "是否", "要求", "怎么", "吗？", "吗")
        ):
            result = self.invoke_tool(state, "worker.task_intake", {"message": message})
            text = result.get("assistant_message", "任务信息已处理。")
            tool = "worker.task_intake"
        else:
            result = self.invoke_tool(
                state, "worker.safety_qa", {"question": message, "task_id": None}
            )
            text = result.get("answer", "暂时没有找到足够证据回答。")
            tool = "worker.safety_qa"
        return {"agent": self.name, "tool": tool, "result": result, "text": text}


class KnowledgeAgent(SpecialistAgent):
    name = "knowledge_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        if _contains_any(state["user_message"], ("相似事故", "相关事故", "事故案例")):
            result = self.invoke_tool(
                state,
                "knowledge.accident_search",
                {"query": state["user_message"], "limit": 3},
            )
            items = result.get("items", [])
            if not items:
                text = "事故知识库中没有找到可追溯的相关案例。"
            else:
                query_action = "拆除" if _contains_any(
                    state["user_message"], ("拆除", "拆卸", "拆模", "拆架")
                ) else ""
                exact_action = any(
                    not query_action
                    or _contains_any(
                        f"{item.get('task', '')} {item.get('scene', '')}",
                        ("拆除", "拆卸", "拆模", "拆架"),
                    )
                    for item in items
                )
                lines = []
                for index, item in enumerate(items):
                    detail = "；".join(
                        part
                        for part in (
                            f"作业：{item.get('task')}" if item.get("task") else "",
                            f"场景：{item.get('scene')}" if item.get("scene") else "",
                            f"后果：{item.get('consequence')}" if item.get("consequence") else "",
                        )
                        if part
                    )
                    lines.append(f"{index + 1}. {item['title']}\n   {detail}")
                heading = (
                    "找到以下相似事故案例："
                    if exact_action
                    else "没有找到作业动作完全一致的事故，返回当前最相关的1个案例："
                )
                text = heading + "\n" + "\n".join(lines)
            return {
                "agent": self.name,
                "tool": "knowledge.accident_search",
                "result": result,
                "text": text,
            }
        result = self.invoke_tool(
            state, "knowledge.search", {"query": state["user_message"], "limit": 8}
        )
        items = result.get("items", [])
        if not items:
            text = "项目知识库中没有找到足够相关的实体，请补充具体场景、设备或作业动作。"
        else:
            lines = [
                f"{index + 1}. {item['name']}：{item.get('summary') or '查看关联证据'}"
                for index, item in enumerate(items[:5])
            ]
            text = "找到以下相关项目知识：\n" + "\n".join(lines)
        return {
            "agent": self.name,
            "tool": "knowledge.search",
            "result": result,
            "text": text,
        }


class RiskAgent(SpecialistAgent):
    name = "risk_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", state["user_message"])
        result = self.invoke_tool(
            state,
            "risk.latest",
            {
                "assessment_date": date_match.group(0) if date_match else None,
                "refresh_weather": False,
            },
        )
        items = result.get("items", [])
        text = (
            f"{result.get('assessment_date', '今日')}动态评估："
            f"红色 {result.get('red_count', 0)} 项、"
            f"黄色 {result.get('yellow_count', 0)} 项、绿色 {result.get('green_count', 0)} 项。"
        )
        for item in items[:3]:
            level = {"red": "红色", "yellow": "黄色", "green": "绿色"}.get(
                item.get("risk_level"), item.get("risk_level", "")
            )
            text += f"\n- {item.get('summary', '风险项')}（{level}）"
        return {"agent": self.name, "tool": "risk.latest", "result": result, "text": text}


class SafetyLogAgent(SpecialistAgent):
    name = "safety_log_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        message = state["user_message"]
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", message)
        arguments = {"assessment_date": date_match.group(0) if date_match else None}
        generate = _contains_any(message, ("生成", "编制", "创建", "更新", "重新"))
        tool = "safety_log.generate" if generate else "safety_log.latest"
        result = self.invoke_tool(state, tool, arguments)
        if result.get("status") == "not_found":
            text = result["message"]
        else:
            created = result.get("version_created", False)
            action = "已生成" if created else ("已读取" if not generate else "数据未变化，已复用")
            summary = (result.get("content") or {}).get("summary") or {}
            text = (
                f"{action}{result.get('assessment_date')}安全日志 V{result.get('version')}。"
                f"日志汇总{summary.get('task_count', 0)}项任务，"
                f"红色{summary.get('red_count', 0)}项、黄色{summary.get('yellow_count', 0)}项、"
                f"绿色{summary.get('green_count', 0)}项风险。可在安全日志页面预览并下载 Word。"
            )
        return {"agent": self.name, "tool": tool, "result": result, "text": text}


class WebAgent(SpecialistAgent):
    name = "web_agent"

    def run(self, state: UnifiedAgentState) -> dict[str, Any]:
        result = self.invoke_tool(
            state, "web.search", {"query": state["user_message"], "count": 5}
        )
        text = result.get("answer") or result.get("message") or "联网问答暂时不可用。"
        items = result.get("items") or []
        if items and not any(item["url"] in text for item in items):
            text += "\n\n来源：\n" + "\n".join(
                f"[{index}] {item['title']}\n{item['url']}"
                for index, item in enumerate(items, 1)
            )
        return {"agent": self.name, "tool": "web.search", "result": result, "text": text}


class UnifiedAgentOrchestrator:
    def __init__(
        self,
        repository: PlatformRepository,
        tools: ToolRegistry,
        context_builder: ContextBuilder,
        memory: MemoryManager,
    ):
        self.repository = repository
        self.tools = tools
        self.context_builder = context_builder
        self.memory = memory
        self.agents: dict[str, SpecialistAgent] = {
            "coordinator": SpecialistAgent(tools),
            "audit_agent": AuditAgent(tools),
            "worker_agent": WorkerAgent(tools),
            "knowledge_agent": KnowledgeAgent(tools),
            "risk_agent": RiskAgent(tools),
            "safety_log_agent": SafetyLogAgent(tools),
            "web_agent": WebAgent(tools),
        }
        builder = StateGraph(UnifiedAgentState)
        builder.add_node("route", self._route)
        builder.add_node("build_context", self._build_context)
        builder.add_node("delegate", self._delegate)
        builder.add_node("synthesize", self._synthesize)
        builder.add_edge(START, "route")
        builder.add_edge("route", "build_context")
        builder.add_edge("build_context", "delegate")
        builder.add_edge("delegate", "synthesize")
        builder.add_edge("synthesize", END)
        self.graph = builder.compile()

    def _route(self, state: UnifiedAgentState) -> dict[str, Any]:
        text = state["user_message"]
        selected: list[str] = []
        log_query = _contains_any(text, ("安全日志", "作业日志"))
        if log_query:
            selected.append("safety_log_agent")
        if not log_query and _contains_any(
            text, ("方案", "审计", "审查", "条款问题", "未说明", "不符合")
        ):
            selected.append("audit_agent")
        if not log_query and _contains_any(
            text, ("动态风险", "风险分析", "风险评估", "红色", "黄色", "天气", "今日风险")
        ):
            selected.append("risk_agent")
        accident_query = _contains_any(text, ("相似事故", "相关事故", "事故案例"))
        if not log_query and _contains_any(
            text, ("知识图谱", "知识库", "相似事故", "相关事故", "事故案例", "关联")
        ):
            selected.append("knowledge_agent")
        if not log_query and not accident_query and _contains_any(
            text,
            (
                "作业",
                "安全带",
                "脚手架",
                "模板",
                "临边",
                "洞口",
                "测验",
                "错题",
                "复习",
                "规范",
                "标准",
                "条款",
                "要求",
            ),
        ):
            selected.append("worker_agent")
        allowed = state["identity"]["project_role"]
        if allowed == "worker":
            selected = [item for item in selected if item != "audit_agent"]
        if not selected:
            project_terms = (
                "项目", "施工", "高处", "作业", "安全", "方案", "审查", "审计", "风险",
                "任务", "培训", "测验", "错题", "规范", "条款", "脚手架", "模板", "临边",
                "洞口", "吊篮", "屋面", "天气", "事故", "知识图谱",
            )
            web_markers = ("联网", "网页", "网上", "全网", "新闻", "最新消息")
            question_markers = ("？", "?", "什么", "为什么", "如何", "怎么", "谁", "哪", "是否")
            if _contains_any(text, web_markers) or (
                _contains_any(text, question_markers) and not _contains_any(text, project_terms)
            ):
                selected = ["web_agent"]
            else:
                selected = ["coordinator"]
        return {"agent_names": list(dict.fromkeys(selected))[:2]}

    def _build_context(self, state: UnifiedAgentState) -> dict[str, Any]:
        return {
            "context_packets": self.context_builder.build(
                conversation_id=state["conversation_id"],
                identity=state["identity"],
                query=state["user_message"],
            )
        }

    def _delegate(self, state: UnifiedAgentState) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for agent_name in state["agent_names"]:
            try:
                results.append(self.agents[agent_name].run(state))
            except PermissionError:
                results.append(
                    {
                        "agent": agent_name,
                        "tool": "permission_check",
                        "result": {},
                        "text": "当前账号角色不能读取该类项目管理信息。",
                    }
                )
        return {"tool_results": results}

    def _synthesize(self, state: UnifiedAgentState) -> dict[str, Any]:
        texts = [item["text"] for item in state["tool_results"] if item.get("text")]
        answer = "\n\n".join(texts)
        metadata = {
            "agents": [item["agent"] for item in state["tool_results"]],
            "agent_labels": [AGENT_LABELS[item["agent"]] for item in state["tool_results"]],
            "tools": [item["tool"] for item in state["tool_results"]],
            "context_packet_count": len(state.get("context_packets", [])),
            "results": [item["result"] for item in state["tool_results"]],
        }
        return {"answer": answer, "metadata": metadata}

    def invoke(
        self,
        *,
        app: Any,
        identity: dict[str, Any],
        conversation_id: str,
        message_id: str,
        message: str,
    ) -> dict[str, Any]:
        self.memory.remember_explicit_profile(
            identity, conversation_id=conversation_id, message=message
        )
        state = self.graph.invoke(
            {
                "app": app,
                "identity": identity,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "user_message": message,
            }
        )
        primary_agent = state["metadata"]["agents"][0]
        self.memory.update_conversation_summary(
            conversation_id, message, primary_agent, state["answer"]
        )
        return {
            "answer": state["answer"],
            "agent_name": primary_agent,
            "agent_label": AGENT_LABELS[primary_agent],
            "metadata": state["metadata"],
        }
