from __future__ import annotations

from typing import Any

from ..config import Settings
from ..repositories import Repository
from ..schemas import WorkerAssistantMessageCreate
from .risk_card_service import RiskCardService, filter_card_for_task_action
from .worker_repository import WorkerAssistantRepository
from .worker_task_intake import (
    build_worker_task_model,
    deterministic_extract,
    finalize_task_draft,
    follow_up_question,
    fuse_task_extractions,
    merge_task_draft,
    missing_task_fields,
)


class WorkerAssistantService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        worker_repository: WorkerAssistantRepository,
        risk_cards: RiskCardService,
    ):
        self.settings = settings
        self.repository = repository
        self.worker_repository = worker_repository
        self.risk_cards = risk_cards

    def _validate_audit_run(self, audit_run_id: str | None) -> str | None:
        if not audit_run_id:
            return None
        run = self.repository.get_audit_run(audit_run_id)
        if run["status"] != "completed":
            raise ValueError("每日任务只能关联已经完成的方案审计")
        return audit_run_id

    def process_message(self, payload: WorkerAssistantMessageCreate) -> dict[str, Any]:
        if payload.session_id:
            session = self.worker_repository.get_session(payload.session_id)
            if session["status"] == "completed":
                task = self.worker_repository.get_task_for_session(session["id"])
                card = self.worker_repository.get_risk_card(task["id"]) if task else None
                return {
                    "session_id": session["id"],
                    "status": "completed",
                    "assistant_message": (
                        "这项任务的风险卡已经生成。"
                        "如需录入新任务，请开始一个新会话。"
                    ),
                    "draft": session["draft"],
                    "missing_fields": [],
                    "model_provider": "stored",
                    "task_id": task["id"] if task else None,
                    "risk_card": card,
                    "assessment_required": False,
                }
            audit_run_id = self._validate_audit_run(payload.audit_run_id)
            self.worker_repository.update_session(
                session["id"],
                draft=session["draft"],
                status="collecting",
                worker_ref=payload.worker_ref or None,
                team_ref=payload.team_ref or None,
                audit_run_id=audit_run_id,
            )
            session = self.worker_repository.get_session(session["id"])
        else:
            audit_run_id = self._validate_audit_run(payload.audit_run_id)
            if audit_run_id is None:
                audit_run_id = self.worker_repository.latest_completed_audit_run_id()
            session = self.worker_repository.create_session(
                worker_ref=payload.worker_ref,
                team_ref=payload.team_ref,
                audit_run_id=audit_run_id,
            )

        self.worker_repository.add_message(session["id"], "user", payload.message)
        available_scenes = self.repository.list_scenes()
        deterministic = deterministic_extract(
            session["draft"], payload.message, available_scenes
        )
        try:
            model = build_worker_task_model(self.settings, payload.use_llm)
            provider_name = model.provider_name
            extracted = model.extract(
                current_draft=session["draft"],
                message=payload.message,
                available_scenes=available_scenes,
            )
            extracted = fuse_task_extractions(extracted, deterministic)
        except Exception:
            extracted = deterministic
            provider_name = "deterministic-fallback"
        draft = merge_task_draft(session["draft"], extracted, available_scenes)
        missing = missing_task_fields(draft)
        if missing:
            assistant_message = follow_up_question(missing)
            self.worker_repository.update_session(
                session["id"], draft=draft, status="collecting"
            )
            self.worker_repository.add_message(session["id"], "assistant", assistant_message)
            return {
                "session_id": session["id"],
                "status": "collecting",
                "assistant_message": assistant_message,
                "draft": draft,
                "missing_fields": missing,
                "model_provider": provider_name,
                "task_id": None,
                "risk_card": None,
            }

        draft = finalize_task_draft(draft)
        self.worker_repository.update_session(session["id"], draft=draft, status="completed")
        completed_session = self.worker_repository.get_session(session["id"])
        task = self.worker_repository.create_task(completed_session, draft)
        card = self.worker_repository.save_risk_card(task["id"], self.risk_cards.build(task))
        work_area = (
            draft["location"]
            if draft["floor"] == "不按楼层定位"
            else f"{draft['floor']}{draft['location']}"
        )
        assistant_message = (
            f"信息已完整：{draft['work_time']}在{work_area}进行{draft['work_content']}。"
            "我已生成任务风险卡，请在作业前逐项确认。"
        )
        self.worker_repository.add_message(session["id"], "assistant", assistant_message)
        return {
            "session_id": session["id"],
            "status": "completed",
            "assistant_message": assistant_message,
            "draft": draft,
            "missing_fields": [],
            "model_provider": provider_name,
            "task_id": task["id"],
            "risk_card": card,
            "assessment_required": True,
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        session = self.worker_repository.get_session(session_id)
        task = self.worker_repository.get_task_for_session(session_id)
        card = self.worker_repository.get_risk_card(task["id"]) if task else None
        if task and card:
            card = filter_card_for_task_action(task, card)
        return {
            "session_id": session["id"],
            "status": session["status"],
            "worker_ref": session["worker_ref"],
            "team_ref": session["team_ref"],
            "audit_run_id": session["audit_run_id"],
            "draft": session["draft"],
            "messages": self.worker_repository.list_messages(session_id),
            "task_id": task["id"] if task else None,
            "risk_card": card,
        }

    def get_risk_card(self, task_id: str) -> dict[str, Any]:
        task = self.worker_repository.get_task(task_id)
        card = self.worker_repository.get_risk_card(task_id)
        if card is None:
            raise KeyError("任务风险卡不存在")
        return filter_card_for_task_action(task, card)
