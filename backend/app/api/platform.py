from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import chromadb
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db import json_load
from ..demo_session import browser_session_id, cleanup_browser_session
from ..platform.auth import current_identity, require_roles
from .auth import COOKIE_NAME
from ..services.dynamic_risk import logical_task_key

router = APIRouter(prefix="/api/v1", tags=["统一 Agent 平台"])


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", max_length=80)


class AgentMessageCreate(BaseModel):
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    message: str = Field(min_length=1, max_length=4000)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=4, max_length=80)
    city: str = Field(default="未填写", min_length=2, max_length=60)
    address: str = Field(default="", max_length=160)


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=4, max_length=80)


class SafetyLogCreate(BaseModel):
    assessment_date: date = Field(default_factory=date.today)


@router.get("/projects")
def projects(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    manager = request.app.state.workspace_manager
    memberships = request.app.state.auth_repository.projects_for_user(identity["id"])
    items = []
    for membership in memberships:
        try:
            item = manager.public(manager.get(membership["id"]))
        except KeyError:
            continue
        items.append(
            item
            | {
                "project_role": membership["project_role"],
                "active": item["id"] == identity.get("project_id"),
            }
        )
    active = next((item for item in items if item["active"]), None)
    return {
        "active_project_id": active["id"] if active else None,
        "active_project": active,
        "projects": items,
    }


@router.post("/projects", status_code=201)
def create_project(
    payload: ProjectCreate,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    manager = request.app.state.workspace_manager
    project = manager.create(payload.name, city=payload.city, address=payload.address)
    request.app.state.auth_repository.register_project(
        project_id=project["id"],
        name=project["name"],
        code=project["code"],
        owner_user_id=identity["id"],
    )
    request.app.state.get_workspace_runtime(project["id"])
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        request.app.state.auth_repository.set_session_project(
            token, identity["id"], project["id"]
        )
    return manager.public(project) | {"active": True, "project_role": "admin"}


@router.post("/projects/{project_id}/activate")
def activate_project(
    project_id: str,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    manager = request.app.state.workspace_manager
    project = manager.get(project_id)
    if request.app.state.base_settings.enforce_auth:
        request.app.state.auth_repository.set_session_project(
            request.cookies.get(COOKIE_NAME, ""), identity["id"], project_id
        )
    else:
        project = manager.activate(project_id)
        runtime = request.app.state.get_workspace_runtime(project_id)
        request.app.state.proxy_fallback.clear()
        request.app.state.proxy_fallback.update(runtime)
    return {
        "active_project": {**manager.public(project), "active": True},
        "cache_action": "reload",
    }


@router.patch("/projects/{project_id}")
def rename_project(
    project_id: str,
    payload: ProjectUpdate,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    if not request.app.state.auth_repository.user_has_project(identity["id"], project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    manager = request.app.state.workspace_manager
    project = manager.rename(project_id, payload.name)
    request.app.state.auth_repository.update_project(project_id, project["name"])
    request.app.state.workspace_runtimes.pop(project_id, None)
    runtime = request.app.state.get_workspace_runtime(project_id)
    is_active = project_id == identity.get("project_id")
    if not request.app.state.base_settings.enforce_auth:
        is_active = project_id == manager.list()["active_project_id"]
        if is_active:
            request.app.state.proxy_fallback.clear()
            request.app.state.proxy_fallback.update(runtime)
    return {**manager.public(project), "active": is_active}


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    manager = request.app.state.workspace_manager
    if not request.app.state.auth_repository.user_has_project(identity["id"], project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    project = manager.get(project_id)
    if project_id == identity.get("project_id"):
        raise ValueError("当前项目不能直接删除，请先切换到其他项目")

    project_settings = manager.settings_for(project)
    client = chromadb.PersistentClient(path=str(project_settings.resolved_chroma_dir))
    collection_names = {
        str(getattr(item, "name", item)) for item in client.list_collections()
    }
    if project_settings.standard_collection in collection_names:
        client.delete_collection(project_settings.standard_collection)

    deleted = manager.delete(project_id)
    request.app.state.auth_repository.delete_project(project_id)
    request.app.state.workspace_runtimes.pop(project_id, None)
    return {
        "deleted_project": manager.public(deleted),
        "remaining_projects": len(
            request.app.state.auth_repository.projects_for_user(identity["id"])
        ),
    }


@router.post("/agent/conversations", status_code=201)
def create_conversation(
    payload: ConversationCreate,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    return request.app.state.platform_repository.create_conversation(identity, payload.title)


@router.get("/agent/conversations")
def conversations(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> list[dict[str, Any]]:
    return request.app.state.platform_repository.list_conversations(identity)


@router.get("/agent/conversations/{conversation_id}")
def conversation(
    conversation_id: str,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    item = request.app.state.platform_repository.get_conversation(conversation_id, identity)
    item["messages"] = request.app.state.platform_repository.messages(conversation_id)
    return item


@router.post("/agent/messages")
def agent_message(
    payload: AgentMessageCreate,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    repository = request.app.state.platform_repository
    if payload.conversation_id:
        conversation = repository.get_conversation(payload.conversation_id, identity)
    else:
        conversation = repository.create_conversation(identity, payload.message[:40])
    user_message = repository.add_message(conversation["id"], "user", payload.message)
    result = request.app.state.agent_orchestrator.invoke(
        app=request.app,
        identity=identity,
        conversation_id=conversation["id"],
        message_id=user_message["id"],
        message=payload.message,
    )
    assistant = repository.add_message(
        conversation["id"],
        "assistant",
        result["answer"],
        agent_name=result["agent_name"],
        metadata=result["metadata"],
    )
    return {"conversation_id": conversation["id"], "message": assistant, **result}


@router.post("/safety-logs", status_code=201)
def create_safety_log(
    payload: SafetyLogCreate,
    request: Request,
    response: Response,
    _: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    result = request.app.state.safety_log_service.generate(
        payload.assessment_date.isoformat(),
        browser_session_id=browser_session_id(request),
    )
    if not result["version_created"]:
        response.status_code = 200
    return result


@router.get("/safety-logs")
def safety_logs(
    request: Request,
    _: Annotated[dict, Depends(current_identity)],
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict[str, Any]]:
    return request.app.state.safety_log_service.list(
        limit, browser_session_id=browser_session_id(request)
    )


@router.get("/safety-logs/latest")
def latest_safety_log(
    request: Request,
    _: Annotated[dict, Depends(current_identity)],
    assessment_date: str | None = Query(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$"),
) -> dict[str, Any] | None:
    return request.app.state.safety_log_service.latest(
        assessment_date, browser_session_id=browser_session_id(request)
    )


@router.delete("/browser-session", status_code=204)
def clear_browser_session(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> Response:
    # Admin is the competition demo account and intentionally resets to its
    # curated baseline when the page closes. Registered users keep project data.
    if identity["username"].lower() == "admin":
        cleanup_browser_session(request.app.state.database, browser_session_id(request))
        request.app.state.project_knowledge_service.sync()
    return Response(status_code=204)


@router.get("/safety-logs/{log_id}")
def safety_log(
    log_id: str,
    request: Request,
    _: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    return request.app.state.safety_log_service.get(log_id)


@router.get("/safety-logs/{log_id}/download", response_class=FileResponse)
def download_safety_log(
    log_id: str,
    request: Request,
    _: Annotated[dict, Depends(current_identity)],
) -> FileResponse:
    path, filename = request.app.state.safety_log_service.document_path(log_id)
    return FileResponse(
        path,
        filename=filename,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


def _risk_graph(run: dict[str, Any] | None, project_name: str) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "project", "name": project_name, "category": "项目", "symbolSize": 64}
    ]
    links: list[dict[str, str]] = []
    if not run:
        return {"run": None, "nodes": nodes, "links": links, "categories": ["项目"]}
    run_id = f"run:{run['id']}"
    nodes.append(
        {
            "id": run_id,
            "name": f"{run['assessment_date']} 动态评估",
            "category": "评估",
            "symbolSize": 58,
            "riskLevel": (
                "red"
                if run.get("red_count")
                else "yellow"
                if run.get("yellow_count")
                else "green"
            ),
        }
    )
    links.append({"source": "project", "target": run_id, "label": "生成"})
    seen: set[str] = {"project", run_id}

    def add_node(node: dict[str, Any]) -> None:
        if node["id"] not in seen:
            seen.add(node["id"])
            nodes.append(node)

    for item in run.get("items", [])[:20]:
        item_id = f"risk:{item['id']}"
        task = {
            key: item.get(key)
            for key in (
                "task_id",
                "worker_ref",
                "team_ref",
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
            )
        }
        task["id"] = item.get("task_id")
        task_id = f"task:{item.get('task_id', 'unknown')}"
        add_node(
            {
                "id": task_id,
                "name": task.get("normalized_task") or task.get("work_content") or "作业任务",
                "category": "任务",
                "symbolSize": 44,
                "detail": task,
            }
        )
        add_node(
            {
                "id": item_id,
                "name": item.get("summary", "风险项"),
                "category": "风险",
                "symbolSize": 52,
                "riskLevel": item.get("risk_level"),
                "detail": item,
            }
        )
        links.extend(
            [
                {"source": run_id, "target": item_id, "label": "包含"},
                {"source": item_id, "target": task_id, "label": "评估"},
            ]
        )
        for index, trigger in enumerate(item.get("triggers", [])[:5]):
            trigger_id = f"trigger:{item['id']}:{index}"
            add_node(
                {
                    "id": trigger_id,
                    "name": str(trigger.get("message") or trigger.get("title") or trigger)[:80],
                    "category": "触发因素",
                    "symbolSize": 30,
                    "detail": trigger,
                }
            )
            links.append({"source": trigger_id, "target": item_id, "label": "触发"})
        for index, intervention in enumerate(item.get("interventions", [])[:4]):
            intervention_id = f"intervention:{item['id']}:{index}"
            add_node(
                {
                    "id": intervention_id,
                    "name": str(intervention)[:80],
                    "category": "干预措施",
                    "symbolSize": 28,
                }
            )
            links.append({"source": item_id, "target": intervention_id, "label": "建议"})
    categories = ["项目", "评估", "任务", "风险", "触发因素", "干预措施"]
    return {"run": run, "nodes": nodes, "links": links, "categories": categories}


@router.get("/dashboard")
def dashboard(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    db = request.app.state.database
    session_scope = browser_session_id(request)
    risk = request.app.state.dynamic_risk_service.latest(
        include_test=False, browser_session_id=session_scope
    )
    counts = {
        "documents": int(
            (
                db.fetch_one(
                    """SELECT COUNT(DISTINCT document_id) count FROM audit_runs
                       WHERE browser_session_id IN ('baseline', 'legacy', ?)""",
                    (session_scope,),
                )
                or {"count": 0}
            )["count"]
        ),
        "audits": int(
            (
                db.fetch_one(
                    """SELECT COUNT(*) count FROM audit_runs
                       WHERE browser_session_id IN ('baseline', 'legacy', ?)""",
                    (session_scope,),
                )
                or {"count": 0}
            )["count"]
        ),
        "tasks": int(
            (
                db.fetch_one(
                    """SELECT COUNT(*) count FROM work_tasks
                       WHERE browser_session_id IN ('baseline', 'legacy', ?)""",
                    (session_scope,),
                )
                or {"count": 0}
            )["count"]
        ),
        "qa_records": int(
            (db.fetch_one("SELECT COUNT(*) count FROM safety_qa_records") or {"count": 0})[
                "count"
            ]
        ),
    }
    return {
        "project": identity["project_name"],
        "counts": counts,
        "dynamic_risk": risk,
        "knowledge": request.app.state.project_knowledge_service.overview(include_test=False),
    }


@router.get("/dynamic-risk/graph")
def dynamic_risk_graph(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
    assessment_date: str | None = Query(default=None),
) -> dict[str, Any]:
    target = assessment_date or date.today().isoformat()
    run = request.app.state.dynamic_risk_service.latest(
        target,
        include_test=False,
        browser_session_id=browser_session_id(request),
    )
    return _risk_graph(run, identity["project_name"])


@router.get("/audits/recent")
def recent_audits(
    request: Request,
    _: Annotated[dict, Depends(require_roles("admin", "safety_officer", "team_leader"))],
    limit: int = Query(default=12, ge=1, le=50),
) -> list[dict[str, Any]]:
    rows = request.app.state.database.fetch_all(
        """SELECT a.id, a.document_id, d.filename, a.status, a.current_node,
                  a.completed_rules, a.bundle_count, a.model_provider,
                  a.created_at, a.completed_at,
                  (SELECT COUNT(*) FROM audit_items i WHERE i.run_id = a.id) item_count,
                  (SELECT COUNT(*) FROM plan_revisions p
                   WHERE p.source_run_id = a.id) revision_count,
                  (SELECT p.status FROM plan_revisions p
                   WHERE p.source_run_id = a.id
                   ORDER BY p.attempt_no DESC LIMIT 1) revision_status
           FROM audit_runs a JOIN documents d ON d.id = a.document_id
           WHERE a.browser_session_id IN ('baseline', 'legacy', ?)
             AND NOT EXISTS (
               SELECT 1 FROM plan_revisions p WHERE p.revised_run_id = a.id
           )
           ORDER BY a.created_at DESC LIMIT ?""",
        (browser_session_id(request), limit),
    )
    return rows


@router.get("/tasks/recent")
def recent_tasks(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
    limit: int = Query(default=12, ge=1, le=50),
) -> list[dict[str, Any]]:
    if identity["project_role"] == "worker":
        worker_ref = identity.get("worker_ref") or identity["username"]
        rows = request.app.state.database.fetch_all(
            """SELECT * FROM work_tasks WHERE worker_ref = ?
               AND browser_session_id IN ('baseline', 'legacy', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (worker_ref, browser_session_id(request), min(200, limit * 4)),
        )
    else:
        rows = request.app.state.database.fetch_all(
            """SELECT * FROM work_tasks
               WHERE browser_session_id IN ('baseline', 'legacy', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (browser_session_id(request), min(200, limit * 4)),
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["scenes"] = json_load(row.pop("scenes_json"), [])
        grouped.setdefault(logical_task_key(row), []).append(row)
    tasks = []
    for candidates in grouped.values():
        representative = max(
            candidates,
            key=lambda task: (
                bool(task.get("audit_run_id")),
                str(task.get("created_at") or ""),
                task["id"],
            ),
        )
        tasks.append(
            {
                **representative,
                "duplicate_count": len(candidates),
                "merged_task_ids": sorted(task["id"] for task in candidates),
            }
        )
    return sorted(tasks, key=lambda task: task["created_at"], reverse=True)[:limit]


@router.get("/tasks/{task_id}/chain")
def task_chain(
    task_id: str,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict[str, Any]:
    chain = request.app.state.task_chain_service.get(task_id)
    if identity["project_role"] == "worker":
        worker_ref = identity.get("worker_ref") or identity["username"]
        if chain["task"].get("worker_ref") != worker_ref:
            raise HTTPException(status_code=404, detail="作业任务不存在")
    return chain


@router.get("/agent/tools")
def tool_catalog(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
    agent_name: str = Query(default="coordinator"),
) -> list[dict[str, Any]]:
    return request.app.state.agent_tools.catalog(
        agent_name=agent_name, project_role=identity["project_role"]
    )
