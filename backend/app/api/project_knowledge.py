from fastapi import APIRouter, Query, Request

from ..schemas import (
    KnowledgeGraphOut,
    KnowledgeSearchOut,
    ProjectKnowledgeOverviewOut,
    WorkerKnowledgeOut,
)
from ..services.project_knowledge import ProjectKnowledgeService

router = APIRouter(prefix="/project-knowledge", tags=["项目安全知识库"])


@router.get("/overview", response_model=ProjectKnowledgeOverviewOut)
def get_overview(request: Request, include_test: bool = False) -> dict:
    service: ProjectKnowledgeService = request.app.state.project_knowledge_service
    return service.overview(include_test=include_test)


@router.get("/search", response_model=KnowledgeSearchOut)
def search_knowledge(
    request: Request,
    q: str = Query(default="", max_length=200),
    entity_type: str = Query(default="", max_length=50),
    scene: str = Query(default="", max_length=100),
    include_test: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    service: ProjectKnowledgeService = request.app.state.project_knowledge_service
    return service.search(
        query=q,
        entity_type=entity_type,
        scene=scene,
        include_test=include_test,
        limit=limit,
    )


@router.get("/entities/{entity_id}", response_model=KnowledgeGraphOut)
def get_entity_graph(
    entity_id: str,
    request: Request,
    include_test: bool = False,
    limit: int = Query(default=30, ge=1, le=60),
) -> dict:
    service: ProjectKnowledgeService = request.app.state.project_knowledge_service
    return service.entity_graph(entity_id, include_test=include_test, limit=limit)


@router.get("/workers/{worker_ref}", response_model=WorkerKnowledgeOut)
def get_worker_knowledge(worker_ref: str, request: Request) -> dict:
    service: ProjectKnowledgeService = request.app.state.project_knowledge_service
    return service.worker_knowledge(worker_ref)
