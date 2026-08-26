from fastapi import APIRouter, Request

router = APIRouter(tags=["模型调用监控"])


@router.get("/audits/{run_id}/model-calls")
def list_model_calls(run_id: str, request: Request) -> dict:
    return request.app.state.model_call_monitor.list_for_run(run_id)


@router.get("/model-calls/{call_id}")
def get_model_call(call_id: str, request: Request) -> dict:
    return request.app.state.model_call_monitor.get_call(call_id)
