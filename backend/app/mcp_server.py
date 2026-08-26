from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .config import Settings
from .db import Database, json_load


def build_mcp_server(settings: Settings) -> MCPServer:
    """Expose bounded, read-only project tools through the official MCP SDK."""
    server = MCPServer(
        name="height-work-safety",
        title="高处作业安全审查与预警智能体 MCP",
        description="高处作业项目规范、知识和动态风险的只读工具面。",
        instructions=(
            "这些工具提供安全辅助证据，不代表开工许可、停工指令或法定审查结论。"
            "不得根据缺失数据补造规范条款。"
        ),
        version="1.0.0",
    )

    def database() -> Database:
        return Database(settings.resolved_database_path)

    @server.tool(description="读取项目核心数据量与最新动态风险摘要。")
    def project_status() -> dict[str, Any]:
        db = database()
        counts = {}
        for key, table in {
            "documents": "documents",
            "audit_runs": "audit_runs",
            "work_tasks": "work_tasks",
            "knowledge_entities": "knowledge_entities",
        }.items():
            counts[key] = int(
                (db.fetch_one(f"SELECT COUNT(*) count FROM {table}") or {"count": 0})["count"]
            )
        latest = db.fetch_one(
            """SELECT * FROM dynamic_risk_runs WHERE include_test = 0
               ORDER BY assessment_date DESC, created_at DESC LIMIT 1"""
        )
        if latest:
            latest["weather"] = json_load(latest.pop("weather_json"), {})
        return {"project_name": settings.project_name, "counts": counts, "latest_risk": latest}

    @server.tool(description="检索项目知识图谱实体，返回来源、场景和摘要。")
    def search_project_knowledge(query: str, limit: int = 10) -> dict[str, Any]:
        normalized = query.strip()
        if not normalized:
            return {"query": query, "items": []}
        safe_limit = max(1, min(limit, 20))
        rows = database().fetch_all(
            """SELECT id, entity_type, name, summary, scene, source_type, source_id,
                      metadata_json, updated_at
               FROM knowledge_entities
               WHERE data_scope <> 'test' AND entity_type <> 'worker'
                 AND (name LIKE ? OR summary LIKE ? OR scene LIKE ?)
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{normalized}%", f"%{normalized}%", f"%{normalized}%", safe_limit),
        )
        for row in rows:
            row["metadata"] = json_load(row.pop("metadata_json"), {})
        return {"query": query, "items": rows}

    @server.tool(description="按关键词检索已启用的高处作业规则及其规范证据。")
    def search_safety_rules(query: str, limit: int = 8) -> dict[str, Any]:
        normalized = query.strip()
        safe_limit = max(1, min(limit, 20))
        rows = database().fetch_all(
            """SELECT rule_id, scene, process, requirement, hazards, risk_level,
                      standard_name, standard_code, clause, original_text, pdf_page
               FROM audit_rules
               WHERE enabled_status = '启用'
                 AND (scene LIKE ? OR process LIKE ? OR requirement LIKE ? OR hazards LIKE ?)
               ORDER BY risk_level DESC, rule_id LIMIT ?""",
            (
                f"%{normalized}%",
                f"%{normalized}%",
                f"%{normalized}%",
                f"%{normalized}%",
                safe_limit,
            ),
        )
        return {"query": query, "items": rows}

    @server.tool(description="读取指定日期的最新动态风险评估与触发证据，不重新计算。")
    def latest_dynamic_risk(assessment_date: str = "") -> dict[str, Any]:
        db = database()
        if assessment_date:
            run = db.fetch_one(
                """SELECT * FROM dynamic_risk_runs
                   WHERE assessment_date = ? AND include_test = 0
                   ORDER BY created_at DESC LIMIT 1""",
                (assessment_date,),
            )
        else:
            run = db.fetch_one(
                """SELECT * FROM dynamic_risk_runs WHERE include_test = 0
                   ORDER BY assessment_date DESC, created_at DESC LIMIT 1"""
            )
        if run is None:
            return {"status": "not_found", "items": []}
        run["weather"] = json_load(run.pop("weather_json"), {})
        items = db.fetch_all(
            """SELECT * FROM dynamic_risk_items WHERE run_id = ?
               ORDER BY priority_score DESC""",
            (run["id"],),
        )
        for item in items:
            item["triggers"] = json_load(item.pop("triggers_json"), [])
            item["evidences"] = json_load(item.pop("evidences_json"), [])
            item["interventions"] = json_load(item.pop("interventions_json"), [])
            item.pop("review_questions_json", None)
        return {**run, "items": items}

    return server
