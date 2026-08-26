from fastapi import APIRouter, Query, Request

from ..schemas import DynamicRiskEvaluateCreate, DynamicRiskRunOut
from ..services.dynamic_risk import DynamicRiskService

router = APIRouter(prefix="/dynamic-risks", tags=["动态风险评估"])


@router.post("/evaluate", response_model=DynamicRiskRunOut)
def evaluate(payload: DynamicRiskEvaluateCreate, request: Request) -> dict:
    service: DynamicRiskService = request.app.state.dynamic_risk_service
    result = service.evaluate(
        payload.assessment_date,
        trigger_type=payload.trigger_type,
        refresh_weather=payload.refresh_weather,
        include_test=payload.include_test,
    )
    request.app.state.project_knowledge_service.sync()
    return result


@router.get("/latest", response_model=DynamicRiskRunOut | None)
def latest(
    request: Request, assessment_date: str | None = None, include_test: bool = False
) -> dict | None:
    service: DynamicRiskService = request.app.state.dynamic_risk_service
    return service.latest(assessment_date, include_test=include_test)


@router.get("/runs", response_model=list[dict])
def list_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    include_test: bool = False,
) -> list[dict]:
    service: DynamicRiskService = request.app.state.dynamic_risk_service
    return service.list_runs(limit, include_test=include_test)


@router.get("/runs/{run_id}", response_model=DynamicRiskRunOut)
def get_run(run_id: str, request: Request) -> dict:
    service: DynamicRiskService = request.app.state.dynamic_risk_service
    return service.get_run(run_id)


@router.post("/runs/{run_id}/recalculate", response_model=DynamicRiskRunOut)
def recalculate(run_id: str, request: Request) -> dict:
    service: DynamicRiskService = request.app.state.dynamic_risk_service
    result = service.recalculate(run_id)
    request.app.state.project_knowledge_service.sync()
    return result

