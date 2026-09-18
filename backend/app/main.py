from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from threading import RLock
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .api.auth import COOKIE_NAME
from .api.auth import router as auth_router
from .api.dynamic_risk import router as dynamic_risk_router
from .api.hazard_inspection import router as hazard_inspection_router
from .api.model_call_monitor import router as model_call_monitor_router
from .api.platform import router as platform_router
from .api.project_knowledge import router as project_knowledge_router
from .api.worker_assistant import router as worker_assistant_router
from .config import PROJECT_ROOT, Settings, get_settings
from .db import Database
from .demo_session import browser_session_id
from .graph.audit_graph import AuditGraph
from .mcp_server import build_mcp_server
from .platform.agents import UnifiedAgentOrchestrator
from .platform.auth import require_roles
from .platform.context import ContextBuilder, MemoryManager
from .platform.repository import PlatformRepository
from .platform.runtime import RuntimeProxy, reset_runtime, set_runtime
from .platform.tools import build_tool_registry
from .platform.workspaces import ProjectWorkspaceManager
from .repositories import Repository
from .schemas import (
    AuditCreate,
    AuditRunOut,
    DocumentMarkdownOut,
    DocumentOut,
    ParseResult,
    PlanRevisionOut,
    ReviewRequest,
    RuleImportOut,
    SceneListOut,
)
from .services.accident_knowledge import AccidentKnowledgeRepository
from .services.assistant_model import AssistantModelService
from .services.document_service import DocumentService
from .services.dynamic_risk import DynamicRiskService
from .services.hazard_inspection import HazardInspectionService
from .services.learning_repository import LearningRepository
from .services.model_call_monitor import ModelCallMonitor
from .services.model_provider import build_audit_model
from .services.project_knowledge import ProjectKnowledgeService
from .services.risk_card_service import RiskCardService
from .services.rule_importer import import_rules
from .services.safety_learning import SafetyQAService
from .services.safety_log import SafetyLogService
from .services.standard_rag import StandardRAGService
from .services.task_chain import TaskChainService
from .services.task_schedule import backfill_task_schedules
from .services.weather_provider import build_weather_provider
from .services.web_search import BochaWebSearchService
from .services.worker_assistant import WorkerAssistantService
from .services.worker_repository import WorkerAssistantRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    mcp_server = build_mcp_server(app_settings) if app_settings.mcp_enabled else None
    workspace_manager = ProjectWorkspaceManager(app_settings)
    workspace_switch_lock = RLock()
    workspace_runtimes: dict[str, dict] = {}

    def build_workspace_runtime(workspace: dict) -> dict:
        workspace_settings = workspace_manager.settings_for(workspace)
        database = Database(workspace_settings.resolved_database_path)
        database.initialize()
        backfill_task_schedules(database)
        repository = Repository(database)
        if repository.count_rules() == 0:
            import_rules(database, workspace_settings.resolved_rule_workbook_path)
        standard_rag = StandardRAGService(workspace_settings, database)
        if repository.count_standard_chunks() == 0 or standard_rag.vector_count() == 0:
            standard_rag.reindex()
        worker_repository = WorkerAssistantRepository(database)
        learning_repository = LearningRepository(database)
        accident_repository = AccidentKnowledgeRepository(
            workspace_settings.resolved_accident_graph_path
        )
        weather_provider = build_weather_provider(workspace_settings)
        risk_cards = RiskCardService(
            repository,
            standard_rag,
            accident_repository,
            weather_provider,
        )
        project_knowledge = ProjectKnowledgeService(
            database,
            accident_repository,
            workspace_settings.project_name,
        )
        dynamic_risk = DynamicRiskService(
            database, risk_cards, workspace_settings.project_name
        )
        task_chain = TaskChainService(database, dynamic_risk)
        safety_log = SafetyLogService(
            workspace_settings,
            database,
            repository,
            dynamic_risk,
            task_chain,
            project_knowledge,
        )
        project_knowledge.sync()
        return {
            "settings": workspace_settings,
            "active_workspace": workspace,
            "database": database,
            "repository": repository,
            "document_service": DocumentService(repository, workspace_settings),
            "hazard_inspection_service": HazardInspectionService(
                database, workspace_settings, standard_rag=standard_rag
            ),
            "standard_rag": standard_rag,
            "model_call_monitor": ModelCallMonitor(database),
            "accident_repository": accident_repository,
            "worker_assistant_service": WorkerAssistantService(
                workspace_settings, repository, worker_repository, risk_cards
            ),
            "safety_qa_service": SafetyQAService(
                workspace_settings,
                repository,
                worker_repository,
                learning_repository,
                standard_rag,
            ),
            "project_knowledge_service": project_knowledge,
            "dynamic_risk_service": dynamic_risk,
            "task_chain_service": task_chain,
            "safety_log_service": safety_log,
            "web_search_service": BochaWebSearchService(workspace_settings),
            "weather_provider": weather_provider,
        }

    def get_workspace_runtime(project_id: str) -> dict:
        with workspace_switch_lock:
            runtime = workspace_runtimes.get(project_id)
            if runtime is None:
                runtime = build_workspace_runtime(workspace_manager.get(project_id))
                workspace_runtimes[project_id] = runtime
            return runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            central_database = Database(app_settings.resolved_database_path)
            central_database.initialize()
            auth_repository = PlatformRepository(
                central_database,
                project_name=workspace_manager.get("default-project")["name"],
                session_hours=app_settings.auth_session_hours,
            )
            auth_repository.bootstrap(
                username="admin",
                password="admin",
                display_name="Admin",
            )
            admin_id = auth_repository.db.fetch_one(
                "SELECT id FROM platform_users WHERE username='admin'"
            )["id"]
            for workspace in workspace_manager.list()["projects"]:
                raw = workspace_manager.get(workspace["id"])
                known_project = auth_repository.db.fetch_one(
                    "SELECT id FROM platform_projects WHERE id=?", (raw["id"],)
                )
                if raw["id"] == "default-project" or known_project is None:
                    auth_repository.register_project(
                        project_id=raw["id"],
                        name=raw["name"],
                        code=raw["code"],
                        owner_user_id=admin_id,
                    )
                else:
                    auth_repository.update_project(raw["id"], raw["name"])
            app.state.workspace_manager = workspace_manager
            app.state.workspace_switch_lock = workspace_switch_lock
            app.state.base_settings = app_settings
            app.state.auth_repository = auth_repository
            app.state.platform_repository = auth_repository
            app.state.assistant_model_service = AssistantModelService(app_settings)
            app.state.workspace_runtimes = workspace_runtimes
            app.state.get_workspace_runtime = get_workspace_runtime
            default_runtime = get_workspace_runtime("default-project")
            proxy_fallback = dict(default_runtime)
            app.state.proxy_fallback = proxy_fallback
            for key in default_runtime:
                setattr(app.state, key, RuntimeProxy(key, proxy_fallback))
            agent_tools = build_tool_registry(auth_repository)
            app.state.agent_tools = agent_tools
            app.state.agent_orchestrator = UnifiedAgentOrchestrator(
                auth_repository,
                agent_tools,
                ContextBuilder(
                    auth_repository,
                    message_limit=app_settings.agent_context_message_limit,
                    char_budget=app_settings.agent_context_char_budget,
                ),
                MemoryManager(auth_repository),
                app.state.assistant_model_service,
            )
            yield

    app = FastAPI(
        title="高处作业全过程风险管控智能体 API",
        version="1.0.0",
        description="高处作业安全辅助平台；风险结果不替代法定审查和责任人员决策。",
        lifespan=lifespan,
    )
    if app_settings.parsed_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.parsed_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def account_workspace_context(request: Request, call_next):
        identity = None
        repository = getattr(request.app.state, "auth_repository", None)
        if repository is not None:
            identity = repository.identity_for_token(
                request.cookies.get(COOKIE_NAME, "")
            )
        request.state.identity = identity
        runtime_token = None
        if identity and identity.get("project_id"):
            try:
                runtime_token = set_runtime(
                    request.app.state.get_workspace_runtime(identity["project_id"])
                )
            except KeyError:
                return JSONResponse(status_code=409, content={"detail": "当前项目不存在"})
        protected = request.url.path.startswith(
            (
                "/api/",
                "/admin/",
                "/documents",
                "/audits",
                "/audit-",
                "/project-knowledge/",
                "/rules/",
                "/standards/",
                "/worker-assistant/",
                "/dynamic-risks",
            )
        )
        public_api = request.url.path.startswith("/api/v1/auth/")
        if app_settings.enforce_auth and protected and not public_api and identity is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        if (
            app_settings.enforce_auth
            and protected
            and not public_api
            and identity is not None
            and not identity.get("project_id")
            and request.url.path != "/api/v1/projects"
        ):
            return JSONResponse(
                status_code=409,
                content={"detail": "请先创建项目", "code": "project_required"},
            )
        try:
            return await call_next(request)
        finally:
            if runtime_token is not None:
                reset_runtime(runtime_token)

    app.include_router(auth_router)
    app.include_router(model_call_monitor_router)
    app.include_router(dynamic_risk_router)
    app.include_router(hazard_inspection_router)
    app.include_router(worker_assistant_router)
    app.include_router(project_knowledge_router)
    app.include_router(platform_router)
    if mcp_server is not None:
        app.mount(
            "/mcp",
            mcp_server.streamable_http_app(
                streamable_http_path="/", json_response=True, stateless_http=True
            ),
            name="mcp",
        )
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "frontend"), name="assets")
    vue_dist = PROJECT_ROOT / "frontend-vue" / "dist"
    if vue_dist.exists():
        app.mount("/ui", StaticFiles(directory=vue_dist, html=True), name="vue-ui")

    def frontend_file(legacy_name: str) -> FileResponse:
        vue_index = vue_dist / "index.html"
        if vue_index.exists():
            return FileResponse(
                vue_index,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return FileResponse(PROJECT_ROOT / "frontend" / legacy_name)

    @app.exception_handler(KeyError)
    async def handle_not_found(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(ValueError)
    async def handle_bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def handle_forbidden(_: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/health/live", include_in_schema=False)
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False, response_model=None)
    def readiness(request: Request) -> dict[str, str] | JSONResponse:
        try:
            row = request.app.state.database.fetch_one("SELECT 1 AS ready")
            if not row or row["ready"] != 1:
                raise RuntimeError("数据库检查未返回预期结果")
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "database_unavailable"},
            )
        return {"status": "ok"}

    @app.get("/health")
    def health(request: Request) -> dict:
        repository: Repository = request.app.state.repository
        settings_: Settings = request.app.state.settings
        return {
            "status": "ok",
            "rule_count": repository.count_rules(),
            "standard_count": repository.count_standards(),
            "standard_chunk_count": repository.count_standard_chunks(),
            "active_standard_chunk_count": repository.count_standard_chunks(active_only=True),
            "vector_chunk_count": request.app.state.standard_rag.vector_count(),
            "model_provider": settings_.model_provider,
            "embedding_provider": settings_.embedding_provider,
            "platform_version": "1.0.0",
            "workspace_mode": "account_scoped_projects",
            "active_project": request.app.state.workspace_manager.public(
                request.app.state.active_workspace
            ),
            "auth_enforced": app_settings.enforce_auth,
            "mcp_enabled": settings_.mcp_enabled,
        }

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return frontend_file("index.html")

    @app.get("/worker-assistant", include_in_schema=False)
    def worker_assistant_page() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "frontend" / "worker-assistant.html")

    @app.get("/project-knowledge", include_in_schema=False)
    def project_knowledge_page() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "frontend" / "project-knowledge.html")

    @app.get("/daily-risk", include_in_schema=False)
    def daily_risk_page() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "frontend" / "daily-risk.html")

    @app.get("/standards/status")
    def standards_status(request: Request) -> dict:
        repository: Repository = request.app.state.repository
        standards = repository.db.fetch_all(
            """SELECT standard_code, standard_name, status, active, page_count,
                      text_page_count, parse_status, parse_warning
               FROM standards ORDER BY standard_code"""
        )
        return {
            "standard_count": repository.count_standards(),
            "chunk_count": repository.count_standard_chunks(),
            "active_chunk_count": repository.count_standard_chunks(active_only=True),
            "standards": standards,
        }

    @app.post("/admin/standards/reindex")
    def reindex_standards(request: Request) -> dict:
        rag: StandardRAGService = request.app.state.standard_rag
        return rag.reindex()

    @app.post("/admin/rules/import-default", response_model=RuleImportOut)
    def import_default_rules(request: Request) -> RuleImportOut:
        settings_: Settings = request.app.state.settings
        imported, skipped = import_rules(
            request.app.state.database, settings_.resolved_rule_workbook_path
        )
        return RuleImportOut(
            imported=imported, skipped=skipped, source=str(settings_.resolved_rule_workbook_path)
        )

    @app.get("/rules/scenes", response_model=SceneListOut)
    def list_scenes(request: Request) -> SceneListOut:
        repository: Repository = request.app.state.repository
        return SceneListOut(scenes=repository.list_scenes(), rule_count=repository.count_rules())

    @app.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
    async def upload_document(
        request: Request, file: Annotated[UploadFile, File(description="DOCX 或 DOC 施工方案")]
    ) -> dict:
        service: DocumentService = request.app.state.document_service
        return await service.save_upload(file)

    @app.get("/documents/{document_id}", response_model=DocumentOut)
    def get_document(document_id: str, request: Request) -> dict:
        return request.app.state.repository.get_document(document_id)

    @app.post("/documents/{document_id}/parse", response_model=ParseResult)
    def parse_document(document_id: str, request: Request) -> ParseResult:
        document = request.app.state.document_service.parse_document(document_id)
        return ParseResult(
            document_id=document_id,
            status=document["status"],
            segment_count=document["segment_count"],
            warnings=document["parse_warnings"],
        )

    @app.get("/documents/{document_id}/markdown", response_model=DocumentMarkdownOut)
    def get_document_markdown(document_id: str, request: Request) -> DocumentMarkdownOut:
        markdown = request.app.state.repository.get_document_markdown(document_id)
        return DocumentMarkdownOut(
            document_id=document_id,
            markdown=markdown,
            char_count=len(markdown),
        )

    def run_graph(request: Request, run_id: str, payload: AuditCreate) -> None:
        settings_: Settings = request.app.state.settings
        repository: Repository = request.app.state.repository
        model = build_audit_model(
            settings_,
            payload.use_llm,
            call_monitor=request.app.state.model_call_monitor,
            run_id=run_id,
        )
        graph = AuditGraph(
            repository,
            model,
            request.app.state.standard_rag,
            max_workers=settings_.audit_concurrency,
        )
        graph.invoke(run_id=run_id, document_id=payload.document_id)
        request.app.state.project_knowledge_service.sync()

    def run_revision_graph(
        request: Request,
        run_id: str,
        payload: AuditCreate,
        revision_id: str,
    ) -> None:
        repository: Repository = request.app.state.repository
        try:
            run_graph(request, run_id, payload)
            revised = repository.get_audit_run(run_id)
            if revised["status"] != "completed":
                raise ValueError(revised.get("error") or "修订方案审查未完成")
            repository.complete_plan_revision(revision_id)
        except Exception as exc:
            repository.fail_plan_revision(revision_id, str(exc))

    @app.post("/audits", response_model=AuditRunOut, status_code=status.HTTP_202_ACCEPTED)
    def create_audit(
        payload: AuditCreate, background_tasks: BackgroundTasks, request: Request
    ) -> dict:
        repository: Repository = request.app.state.repository
        settings_: Settings = request.app.state.settings
        document = repository.get_document(payload.document_id)
        if document["status"] != "parsed":
            raise HTTPException(status_code=409, detail="请先解析施工方案")
        model = build_audit_model(settings_, payload.use_llm)
        provider = model.provider_name
        run = repository.create_audit_run(
            document_id=payload.document_id,
            model_provider=provider,
            model_name=model.model_name,
            rule_limit=0,
            browser_session_id=browser_session_id(request),
        )
        background_tasks.add_task(run_graph, request, run["id"], payload)
        return run

    @app.post("/audit-documents", response_model=AuditRunOut, status_code=status.HTTP_202_ACCEPTED)
    async def audit_uploaded_document(
        request: Request,
        background_tasks: BackgroundTasks,
        file: Annotated[UploadFile, File(description="DOCX 或 DOC 施工方案")],
        use_llm: Annotated[bool, Form()] = True,
    ) -> dict:
        service: DocumentService = request.app.state.document_service
        repository: Repository = request.app.state.repository
        settings_: Settings = request.app.state.settings
        document = await service.save_upload(file)
        await run_in_threadpool(service.parse_document, document["id"])
        payload = AuditCreate(document_id=document["id"], use_llm=use_llm)
        model = build_audit_model(settings_, use_llm)
        run = repository.create_audit_run(
            document_id=document["id"],
            model_provider=model.provider_name,
            model_name=model.model_name,
            rule_limit=0,
            browser_session_id=browser_session_id(request),
        )
        background_tasks.add_task(run_graph, request, run["id"], payload)
        return run

    @app.get("/audits/{run_id}", response_model=AuditRunOut)
    def get_audit(run_id: str, request: Request) -> dict:
        return request.app.state.repository.get_audit_run(run_id, include_items=True)

    @app.post(
        "/api/v1/audits/{run_id}/revisions",
        response_model=PlanRevisionOut,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_plan_revision(
        run_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        file: Annotated[UploadFile, File(description="修订后的 DOCX 或 DOC 施工方案")],
        identity: Annotated[
            dict, Depends(require_roles("admin", "safety_officer", "team_leader"))
        ],
        use_llm: Annotated[bool, Form()] = True,
    ) -> dict:
        service: DocumentService = request.app.state.document_service
        repository: Repository = request.app.state.repository
        settings_: Settings = request.app.state.settings
        source = repository.get_audit_run(run_id, include_items=True)
        if source["status"] != "completed":
            raise ValueError("原方案审查完成后才能提交修订方案")
        if not source["findings"]:
            raise ValueError("原方案没有需要整改的问题")
        document = await service.save_upload(file)
        await run_in_threadpool(service.parse_document, document["id"])
        payload = AuditCreate(document_id=document["id"], use_llm=use_llm)
        model = build_audit_model(settings_, use_llm)
        revised_run = repository.create_audit_run(
            document_id=document["id"],
            model_provider=model.provider_name,
            model_name=model.model_name,
            rule_limit=0,
            browser_session_id=browser_session_id(request),
        )
        revision = repository.create_plan_revision(
            run_id, revised_run["id"], identity["display_name"]
        )
        background_tasks.add_task(
            run_revision_graph,
            request,
            revised_run["id"],
            payload,
            revision["id"],
        )
        return revision

    @app.patch("/audit-items/{item_id}/review", response_model=AuditRunOut)
    def review_item(item_id: str, payload: ReviewRequest, request: Request) -> dict:
        repository: Repository = request.app.state.repository
        item = repository.db.fetch_one("SELECT run_id FROM audit_items WHERE id = ?", (item_id,))
        if item is None:
            raise KeyError("审计项不存在")
        repository.review_item(
            item_id=item_id,
            action=payload.action.value,
            reviewer=payload.reviewer,
            final_result=payload.final_result.value if payload.final_result else None,
            final_text=payload.final_text,
            comment=payload.comment,
        )
        request.app.state.dynamic_risk_service.refresh_for_audit(item["run_id"])
        request.app.state.project_knowledge_service.sync()
        return repository.get_audit_run(item["run_id"], include_items=True)

    return app


app = create_app()
