from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from ..demo_session import browser_session_id
from ..platform.auth import require_roles

router = APIRouter(prefix="/api/v1", tags=["现场多模态巡检"])
ManagerIdentity = Annotated[dict, Depends(require_roles("admin", "safety_officer", "team_leader"))]
RectificationIdentity = Annotated[
    dict, Depends(require_roles("admin", "safety_officer", "team_leader", "worker"))
]


class HazardCandidateReview(BaseModel):
    action: Literal["confirm", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, min_length=2, max_length=200)
    fact_description: str | None = Field(default=None, min_length=2, max_length=3000)
    hazard_category: str | None = Field(default=None, min_length=2, max_length=100)
    risk_level: Literal["red", "yellow", "green"] | None = None
    location: str | None = Field(default=None, min_length=1, max_length=300)
    rectification_requirement: str | None = Field(default=None, min_length=2, max_length=3000)
    responsible_ref: str | None = Field(default=None, min_length=1, max_length=200)
    deadline: date | None = None

    @model_validator(mode="after")
    def confirmed_item_is_complete(self) -> HazardCandidateReview:
        if self.action == "confirm":
            required = (
                "title",
                "fact_description",
                "hazard_category",
                "risk_level",
                "location",
                "rectification_requirement",
                "responsible_ref",
            )
            missing = [name for name in required if not getattr(self, name)]
            if missing:
                raise ValueError("确认时必须补全正式安全事项信息")
        return self

    def formal_item(self) -> dict | None:
        if self.action != "confirm":
            return None
        return {
            "title": self.title,
            "fact_description": self.fact_description,
            "hazard_category": self.hazard_category,
            "risk_level": self.risk_level,
            "location": self.location,
            "rectification_requirement": self.rectification_requirement,
            "responsible_ref": self.responsible_ref,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }


class RectificationReview(BaseModel):
    result: Literal["pass", "return"]
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def returned_review_has_reason(self) -> RectificationReview:
        if self.result == "return" and len(self.reason.strip()) < 2:
            raise ValueError("退回整改时必须填写原因")
        return self


@router.post("/hazard-inspections", status_code=status.HTTP_202_ACCEPTED)
async def create_inspection(
    request: Request,
    background_tasks: BackgroundTasks,
    identity: ManagerIdentity,
    file: Annotated[UploadFile, File(description="JPG、PNG或WEBP现场图片")],
    description: Annotated[str, Form(min_length=2, max_length=2000)],
    task_id: Annotated[str | None, Form()] = None,
) -> dict:
    service = request.app.state.hazard_inspection_service
    inspection = await service.create(
        file,
        description=description,
        task_id=task_id or None,
        submitted_by=identity["display_name"],
        browser_session_id=browser_session_id(request),
    )
    background_tasks.add_task(service.analyze, inspection["id"])
    return inspection


@router.get("/hazard-inspections")
def list_inspections(
    request: Request,
    _: ManagerIdentity,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict]:
    return request.app.state.hazard_inspection_service.list(
        limit, browser_session_id(request)
    )


@router.get("/hazard-inspections/{inspection_id}")
def get_inspection(inspection_id: str, request: Request, _: ManagerIdentity) -> dict:
    return request.app.state.hazard_inspection_service.get(inspection_id)


@router.get("/hazard-inspections/{inspection_id}/image", response_class=FileResponse)
def get_inspection_image(inspection_id: str, request: Request, _: ManagerIdentity) -> FileResponse:
    path, mime_type = request.app.state.hazard_inspection_service.image_path(inspection_id)
    return FileResponse(path, media_type=mime_type, headers={"Cache-Control": "private, no-store"})


@router.patch("/hazard-candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: str,
    payload: HazardCandidateReview,
    request: Request,
    identity: ManagerIdentity,
) -> dict:
    return request.app.state.hazard_inspection_service.review(
        candidate_id,
        action=payload.action,
        reviewer_ref=identity["display_name"],
        comment=payload.comment,
        formal_item=payload.formal_item(),
    )


@router.get("/safety-items")
def safety_items(
    request: Request,
    _: ManagerIdentity,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    return request.app.state.hazard_inspection_service.safety_items(
        limit, browser_session_id(request)
    )


@router.get("/safety-items/{safety_item_id}")
def safety_item_detail(
    safety_item_id: str, request: Request, _: RectificationIdentity
) -> dict:
    return request.app.state.hazard_inspection_service.safety_item_detail(safety_item_id)


@router.post(
    "/rectification-orders/{order_id}/submissions",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_rectification(
    order_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    identity: RectificationIdentity,
    file: Annotated[UploadFile, File(description="整改后现场图片")],
    description: Annotated[str, Form(min_length=2, max_length=3000)],
) -> dict:
    service = request.app.state.hazard_inspection_service
    detail = await service.submit_rectification(
        order_id,
        file,
        description=description,
        submitted_by=identity["display_name"],
    )
    submission_id = detail["submissions"][-1]["id"]
    background_tasks.add_task(service.compare_submission, submission_id)
    return detail


@router.get(
    "/rectification-submissions/{submission_id}/image", response_class=FileResponse
)
def rectification_submission_image(
    submission_id: str, request: Request, _: RectificationIdentity
) -> FileResponse:
    path, mime_type = request.app.state.hazard_inspection_service.submission_image_path(
        submission_id
    )
    return FileResponse(path, media_type=mime_type, headers={"Cache-Control": "private, no-store"})


@router.post("/rectification-orders/{order_id}/review")
def review_rectification(
    order_id: str,
    payload: RectificationReview,
    request: Request,
    identity: ManagerIdentity,
) -> dict:
    return request.app.state.hazard_inspection_service.review_rectification(
        order_id,
        result=payload.result,
        reason=payload.reason,
        reviewer_ref=identity["display_name"],
    )
