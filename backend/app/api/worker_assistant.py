from fastapi import APIRouter, Request

from ..schemas import (
    QuestionBankStatusOut,
    QuizAttemptOut,
    QuizCreate,
    QuizResultOut,
    QuizSubmitCreate,
    SafetyQuestionCreate,
    SafetyQuestionOut,
    TaskRiskCardOut,
    WorkerAssistantMessageCreate,
    WorkerAssistantMessageOut,
    WorkerAssistantSessionOut,
    WorkerLearningRecordOut,
)
from ..services.safety_learning import QuizService, SafetyQAService
from ..services.worker_assistant import WorkerAssistantService

router = APIRouter(prefix="/worker-assistant", tags=["安全培训"])


@router.post("/messages", response_model=WorkerAssistantMessageOut)
def send_message(payload: WorkerAssistantMessageCreate, request: Request) -> dict:
    service: WorkerAssistantService = request.app.state.worker_assistant_service
    result = service.process_message(payload)
    if result.get("task_id") and result.get("assessment_required"):
        task = service.worker_repository.get_task(result["task_id"])
        request.app.state.dynamic_risk_service.evaluate(
            task["scheduled_date"],
            trigger_type="task_changed",
            refresh_weather=False,
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


@router.get("/question-bank/status", response_model=QuestionBankStatusOut)
def question_bank_status(request: Request) -> dict:
    return request.app.state.question_bank.status()


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


@router.post("/quizzes", response_model=QuizAttemptOut)
def create_quiz(payload: QuizCreate, request: Request) -> dict:
    service: QuizService = request.app.state.quiz_service
    result = service.create(
        worker_ref=payload.worker_ref, task_id=payload.task_id, scene=payload.scene
    )
    request.app.state.project_knowledge_service.sync()
    return result


@router.get("/quizzes/{attempt_id}", response_model=QuizAttemptOut)
def get_quiz(attempt_id: str, request: Request) -> dict:
    service: QuizService = request.app.state.quiz_service
    return service.get(attempt_id)


@router.post("/quizzes/{attempt_id}/submit", response_model=QuizResultOut)
def submit_quiz(attempt_id: str, payload: QuizSubmitCreate, request: Request) -> dict:
    service: QuizService = request.app.state.quiz_service
    result = service.submit(attempt_id, [item.model_dump() for item in payload.answers])
    request.app.state.dynamic_risk_service.refresh_for_worker(
        result["worker_ref"], task_id=result.get("task_id")
    )
    request.app.state.project_knowledge_service.sync()
    return result


@router.get(
    "/workers/{worker_ref}/learning-records", response_model=WorkerLearningRecordOut
)
def get_learning_record(worker_ref: str, request: Request) -> dict:
    service: QuizService = request.app.state.quiz_service
    return service.learning_record(worker_ref)
