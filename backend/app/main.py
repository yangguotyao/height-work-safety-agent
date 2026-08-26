from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from threading import RLock
from typing import Annotated

from fastapi import (
    BackgroundTasks,
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

from .api.dynamic_risk import router as dynamic_risk_router
from .api.model_call_monitor import router as model_call_monitor_router
from .api.platform import router as platform_router
from .api.project_knowledge import router as project_knowledge_router
from .api.worker_assistant import router as worker_assistant_router
from .config import PROJECT_ROOT, Settings, get_settings
from .db import Database
from .graph.audit_graph import AuditGraph
from .mcp_server import build_mcp_server
from .platform.agents import UnifiedAgentOrchestrator
from .platform.context import ContextBuilder, MemoryManager
from .platform.repository import PlatformRepository
from .platform.tools import build_tool_registry
from .platform.workspaces import ProjectWorkspaceManager
from .repositories import Repository
from .schemas import (
    AuditCreate,
    AuditRunOut,
    DocumentMarkdownOut,
    DocumentOut,
    GoldTestCreate,
    GoldTestOut,
    ParseResult,
    ReviewRequest,
    RuleImportOut,
    SceneListOut,
)
from .services.accident_knowledge import AccidentKnowledgeRepository
from .services.document_service import DocumentService
from .services.dynamic_risk import DynamicRiskService
from .services.gold_evaluation import GoldEvaluator, import_gold_cases
from .services.learning_repository import LearningRepository
from .services.model_call_monitor import ModelCallMonitor
from .services.model_provider import build_audit_model
from .services.project_knowledge import ProjectKnowledgeService
from .services.question_bank import QuestionBank
from .services.risk_card_service import RiskCardService
from .services.rule_importer import import_rules
from .services.safety_learning import QuizService, SafetyQAService
from .services.safety_log import SafetyLogService
from .services.standard_rag import StandardRAGService
from .services.task_chain import TaskChainService
from .services.task_schedule import backfill_task_schedules
from .services.weather_provider import build_weather_provider
from .services.web_search import BochaWebSearchService
from .services.worker_assistant import WorkerAssistantService
from .services.worker_repository import WorkerAssistantRepository

SCAFFOLD_AUDIT_SAMPLE = PROJECT_ROOT / "data" / "施工方案" / "脚手架工程施工方案_预置.docx"
SCAFFOLD_AUDIT_DISPLAY_NAME = "脚手架工程施工方案.doc"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    mcp_server = build_mcp_server(app_settings) if app_settings.mcp_enabled else None
    workspace_manager = ProjectWorkspaceManager(app_settings)
    workspace_switch_lock = RLock()

    def activate_workspace(app: FastAPI, workspace: dict, *, persist: bool = False) -> dict:
        workspace_settings = workspace_manager.settings_for(workspace)
        database = Database(workspace_settings.resolved_database_path)
        database.initialize()
        backfill_task_schedules(database)
        repository = Repository(database)
        if repository.count_rules() == 0:
            import_rules(database, workspace_settings.resolved_rule_workbook_path)
        gold_directory = workspace_settings.resolved_gold_workbook_path.parent
        gold_workbooks = (
            sorted(gold_directory.glob("*.xlsx")) if gold_directory.exists() else []
        )
        if not gold_workbooks and workspace_settings.resolved_gold_workbook_path.exists():
            gold_workbooks = [workspace_settings.resolved_gold_workbook_path]
        for gold_workbook in gold_workbooks:
            if not gold_workbook.name.startswith("~$"):
                import_gold_cases(database, gold_workbook)
        standard_rag = StandardRAGService(workspace_settings, database)
        if repository.count_standard_chunks() == 0 or standard_rag.vector_count() == 0:
            standard_rag.reindex()
        worker_repository = WorkerAssistantRepository(database)
        learning_repository = LearningRepository(database)
        question_bank = QuestionBank(
            workspace_settings.resolved_question_bank_path, database
        )
        accident_repository = AccidentKnowledgeRepository(
            workspace_settings.resolved_accident_graph_path
        )
        risk_cards = RiskCardService(
            repository,
            standard_rag,
            accident_repository,
            build_weather_provider(workspace_settings),
        )
        project_knowledge = ProjectKnowledgeService(
            database,
            accident_repository,
            question_bank,
            workspace_settings.project_name,
        )
        dynamic_risk = DynamicRiskService(
            database, risk_cards, question_bank, workspace_settings.project_name
        )
        task_chain = TaskChainService(
            database, dynamic_risk, learning_repository, question_bank
        )
        safety_log = SafetyLogService(
            workspace_settings,
            database,
            repository,
            dynamic_risk,
            task_chain,
            project_knowledge,
        )
        platform_repository = PlatformRepository(
            database,
            project_name=workspace_settings.project_name,
            session_hours=workspace_settings.auth_session_hours,
        )
        platform_repository.bootstrap(
            username=workspace_settings.auth_bootstrap_username,
            password=workspace_settings.development_bootstrap_password,
            display_name=workspace_settings.auth_bootstrap_display_name,
        )
        agent_tools = build_tool_registry(platform_repository)
        project_knowledge.sync()
        runtime_state = {
            "settings": workspace_settings,
            "active_workspace": workspace,
            "database": database,
            "repository": repository,
            "document_service": DocumentService(repository, workspace_settings),
            "gold_evaluator": GoldEvaluator(database, repository),
            "standard_rag": standard_rag,
            "model_call_monitor": ModelCallMonitor(database),
            "accident_repository": accident_repository,
            "worker_assistant_service": WorkerAssistantService(
                workspace_settings, repository, worker_repository, risk_cards
            ),
            "question_bank": question_bank,
            "safety_qa_service": SafetyQAService(
                workspace_settings,
                repository,
                worker_repository,
                learning_repository,
                standard_rag,
            ),
            "quiz_service": QuizService(
                question_bank, worker_repository, learning_repository
            ),
            "project_knowledge_service": project_knowledge,
            "dynamic_risk_service": dynamic_risk,
            "task_chain_service": task_chain,
            "safety_log_service": safety_log,
            "web_search_service": BochaWebSearchService(workspace_settings),
            "platform_repository": platform_repository,
            "agent_tools": agent_tools,
            "agent_orchestrator": UnifiedAgentOrchestrator(
                platform_repository,
                agent_tools,
                ContextBuilder(
                    platform_repository,
                    message_limit=workspace_settings.agent_context_message_limit,
                    char_budget=workspace_settings.agent_context_char_budget,
                ),
                MemoryManager(platform_repository),
            ),
        }
        with workspace_switch_lock:
            if persist:
                workspace = workspace_manager.activate(workspace["id"])
            runtime_state["active_workspace"] = workspace
            app.state._state.update(runtime_state)
            return workspace

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            app.state.workspace_manager = workspace_manager
            app.state.workspace_switch_lock = workspace_switch_lock
            app.state.activate_workspace = lambda workspace: activate_workspace(
                app, workspace, persist=True
            )
            activate_workspace(app, workspace_manager.active())
            yield

    app = FastAPI(
        title="高处作业安全审查与预警智能体 API",
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

    app.include_router(model_call_monitor_router)
    app.include_router(dynamic_risk_router)
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
            "workspace_mode": "single_active_project",
            "active_project": request.app.state.workspace_manager.public(
                request.app.state.active_workspace
            ),
            "auth_enforced": False,
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
        )
        background_tasks.add_task(run_graph, request, run["id"], payload)
        return run

    @app.post(
        "/api/v1/audit-samples/scaffold",
        response_model=AuditRunOut,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def audit_scaffold_sample(
        request: Request,
        background_tasks: BackgroundTasks,
        use_llm: Annotated[bool, Form()] = True,
    ) -> dict:
        service: DocumentService = request.app.state.document_service
        repository: Repository = request.app.state.repository
        settings_: Settings = request.app.state.settings
        document = await run_in_threadpool(
            service.save_bundled_document,
            SCAFFOLD_AUDIT_SAMPLE,
            display_name=SCAFFOLD_AUDIT_DISPLAY_NAME,
        )
        await run_in_threadpool(service.parse_document, document["id"])
        payload = AuditCreate(document_id=document["id"], use_llm=use_llm)
        model = build_audit_model(settings_, use_llm)
        run = repository.create_audit_run(
            document_id=document["id"],
            model_provider=model.provider_name,
            model_name=model.model_name,
            rule_limit=0,
        )
        background_tasks.add_task(run_graph, request, run["id"], payload)
        return run

    @app.get("/audits/{run_id}", response_model=AuditRunOut)
    def get_audit(run_id: str, request: Request) -> dict:
        return request.app.state.repository.get_audit_run(run_id, include_items=True)

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

    @app.post("/gold-tests/run", response_model=GoldTestOut)
    def run_gold_test(payload: GoldTestCreate, request: Request) -> dict:
        evaluator: GoldEvaluator = request.app.state.gold_evaluator
        settings_: Settings = request.app.state.settings
        return evaluator.evaluate(payload.audit_run_id, str(settings_.resolved_gold_workbook_path))

    @app.get("/gold-tests/{test_run_id}", response_model=GoldTestOut)
    def get_gold_test(test_run_id: str, request: Request) -> dict:
        evaluator: GoldEvaluator = request.app.state.gold_evaluator
        return evaluator.get_test_run(test_run_id)

    return app


app = create_app()
