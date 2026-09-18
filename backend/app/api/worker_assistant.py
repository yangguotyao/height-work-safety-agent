from fastapi import APIRouter, Request

from ..demo_session import browser_session_id
from ..schemas import (
    SafetyQuestionCreate,
    SafetyQuestionOut,
    TaskRiskCardOut,
    WorkerAssistantMessageCreate,
    WorkerAssistantMessageOut,
    WorkerAssistantSessionOut,
)
from ..services.safety_learning import SafetyQAService
from ..services.worker_assistant import WorkerAssistantService

router = APIRouter(prefix="/worker-assistant", tags=["班前任务"])


@router.post("/messages", response_model=WorkerAssistantMessageOut)
def send_message(payload: WorkerAssistantMessageCreate, request: Request) -> dict:
    service: WorkerAssistantService = request.app.state.worker_assistant_service
    session_scope = browser_session_id(request)
    result = service.process_message(payload, browser_session_id=session_scope)
    if result.get("task_id") and result.get("assessment_required"):
        task = service.worker_repository.get_task(result["task_id"])
        request.app.state.dynamic_risk_service.evaluate(
            task["scheduled_date"],
            trigger_type="task_changed",
            refresh_weather=False,
            browser_session_id=session_scope,
        )
        request.app.state.project_knowledge_service.sync()
    return result


@router.get("/sessions/{session_id}", response_model=WorkerAssistantSessionOut)
def get_session(session_id: str, request: Request) -> dict:
    service: WorkerAssistantService = request.app.state.worker_assistant_service
    return service.get_session(session_id)


@router.get("/risk-cards/{task_id}", response_model=TaskRiskCardOut)
def get_risk_card(task_id: str, request: Request) -> dict:
    service: WorkerAssistantService = request.app.state.worker_assistant_service
    return service.get_risk_card(task_id)


@router.post("/qa", response_model=SafetyQuestionOut)
def ask_safety_question(payload: SafetyQuestionCreate, request: Request) -> dict:
    service: SafetyQAService = request.app.state.safety_qa_service
    result = service.ask(
        worker_ref=payload.worker_ref,
        question=payload.question,
        task_id=payload.task_id,
        use_llm=payload.use_llm,
    )
    request.app.state.project_knowledge_service.sync()
    return result
