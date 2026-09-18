from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    ApplicabilityStatus,
    AuditResult,
    PlanObligation,
    ReviewAction,
    ReviewStatus,
)


class DocumentOut(BaseModel):
    id: str
    filename: str
    sha256: str
    status: str
    segment_count: int = 0
    parse_warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class ParseResult(BaseModel):
    document_id: str
    status: str
    segment_count: int
    warnings: list[str] = Field(default_factory=list)


class DocumentMarkdownOut(BaseModel):
    document_id: str
    markdown: str
    char_count: int = Field(ge=0)


class AuditCreate(BaseModel):
    document_id: str
    use_llm: bool | None = None


class SceneInstanceOut(BaseModel):
    id: str
    scene: str
    title: str
    location: str
    segment_ids: list[str] = Field(default_factory=list)
    anchor_segment_ids: list[str] = Field(default_factory=list)
    object_type: str = "general"
    object_instance_key: str | None = None
    scaffold_subtype: str | None = None
    location_label: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class EvidenceOut(BaseModel):
    evidence_type: Literal["plan", "standard"]
    source_id: str
    quote: str
    location: str | None = None
    score: float | None = None


class AuditItemOut(BaseModel):
    id: str
    rule_id: str
    scene: str
    plan_quote: str
    source_location: str
    issue: str
    basis: list[dict[str, Any]]
    risk_consequence: str
    result: AuditResult
    applicability_status: ApplicabilityStatus = ApplicabilityStatus.UNCERTAIN
    applicability_reason: str = ""
    plan_obligation: PlanObligation = PlanObligation.CONDITIONAL_MUST_STATE
    plan_obligation_reason: str = ""
    bundle_id: str = ""
    business_group_key: str = ""
    control_title: str = ""
    suggestion: str
    confidence: float
    review_status: ReviewStatus
    final_result: str | None = None
    final_text: str | None = None
    evidences: list[EvidenceOut] = Field(default_factory=list)


class BusinessFindingOut(BaseModel):
    id: str
    scene: str
    title: str
    result: AuditResult
    issue: str
    risk_consequence: str
    suggestion: str
    plan_quote: str
    source_location: str
    confidence: float = Field(ge=0, le=1)
    rule_ids: list[str] = Field(default_factory=list)
    basis: list[dict[str, Any]] = Field(default_factory=list)
    atomic_items: list[AuditItemOut] = Field(default_factory=list)


class PlanRevisionOut(BaseModel):
    id: str
    source_run_id: str
    revised_run_id: str
    attempt_no: int
    status: str
    comparison: dict[str, Any] = Field(default_factory=dict)
    revised_filename: str = ""
    submitted_by: str
    created_at: datetime
    completed_at: datetime | None = None


class AuditRunOut(BaseModel):
    id: str
    document_id: str
    status: str
    current_node: str | None = None
    scenes: list[str] = Field(default_factory=list)
    scene_instances: list[SceneInstanceOut] = Field(default_factory=list)
    candidate_rule_count: int = Field(default=0, ge=0)
    completed_rules: int = Field(default=0, ge=0)
    bundle_count: int = Field(default=0, ge=0)
    completed_bundles: int = Field(default=0, ge=0)
    model_call_count: int = Field(default=0, ge=0)
    model_provider: str
    model_name: str | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    elapsed_seconds: float = Field(ge=0, description="审计任务耗时，不含文档上传解析")
    total_elapsed_seconds: float = Field(ge=0, description="从文档保存到审计结束的处理总耗时")
    items: list[AuditItemOut] = Field(default_factory=list)
    findings: list[BusinessFindingOut] = Field(default_factory=list)
    revisions: list[PlanRevisionOut] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    action: ReviewAction
    reviewer: str = Field(min_length=1, max_length=100)
    final_result: AuditResult | None = None
    final_text: str | None = Field(default=None, max_length=5000)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_edit_payload(self) -> ReviewRequest:
        if self.action is ReviewAction.EDIT and self.final_result is None:
            raise ValueError("编辑审计项时必须提供 final_result")
        return self


class SceneListOut(BaseModel):
    scenes: list[str]
    rule_count: int


class RuleImportOut(BaseModel):
    imported: int
    skipped: int
    source: str


class WorkerTaskDraftOut(BaseModel):
    work_content: str | None = None
    location: str | None = None
    floor: str | None = None
    work_time: str | None = None
    normalized_task: str | None = None
    task_action: str | None = None
    equipment_type: str | None = None
    scenes: list[str] = Field(default_factory=list)


class WeatherSnapshotOut(BaseModel):
    status: Literal["ok", "unconfigured", "error"]
    source: str = "彩云天气 v2.6"
    project_name: str
    summary: str
    forecast_window: str | None = None
    temperature_c: float | None = None
    max_wind_speed_kmh: float | None = None
    precipitation: float | None = None
    sky_conditions: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None


class WeatherWarningOut(BaseModel):
    level: Literal["stop", "warning", "info", "confirm"]
    message: str
    rule_id: str | None = None
    standard_code: str | None = None
    clause: str | None = None


class SimilarAccidentOut(BaseModel):
    case_id: str
    title: str
    task: str
    scene: str
    consequence: str
    risk_factors: list[str] = Field(default_factory=list)
    unsafe_behaviors: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    evidence: str
    source_agency: str
    source_url: str
    score: float = Field(ge=0, le=1)


class RiskEvidenceOut(BaseModel):
    evidence_type: Literal["standard", "audit", "accident", "weather"]
    source_id: str
    title: str
    quote: str
    location: str | None = None
    source_url: str | None = None


class TaskRiskCardOut(BaseModel):
    id: str
    task_id: str
    worker_ref: str
    team_ref: str
    audit_run_id: str | None = None
    work_content: str
    location: str
    floor: str
    work_time: str
    normalized_task: str
    task_action: str = ""
    equipment_type: str = ""
    scenes: list[str] = Field(default_factory=list)
    main_risks: list[str] = Field(default_factory=list)
    pre_job_checks: list[str] = Field(default_factory=list)
    prohibited_behaviors: list[str] = Field(default_factory=list)
    weather: WeatherSnapshotOut
    weather_warnings: list[WeatherWarningOut] = Field(default_factory=list)
    similar_accidents: list[SimilarAccidentOut] = Field(default_factory=list)
    human_confirmations: list[str] = Field(default_factory=list)
    evidences: list[RiskEvidenceOut] = Field(default_factory=list)
    safety_notice: str
    created_at: datetime


class WorkerAssistantMessageCreate(BaseModel):
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    message: str = Field(min_length=1, max_length=2000)
    worker_ref: str = Field(default="", max_length=100)
    team_ref: str = Field(default="", max_length=100)
    audit_run_id: str | None = Field(default=None, max_length=100)
    use_llm: bool | None = None


class WorkerAssistantMessageOut(BaseModel):
    session_id: str
    status: Literal["collecting", "completed"]
    assistant_message: str
    draft: WorkerTaskDraftOut
    missing_fields: list[str] = Field(default_factory=list)
    model_provider: str
    task_id: str | None = None
    risk_card: TaskRiskCardOut | None = None


class WorkerAssistantSessionOut(BaseModel):
    session_id: str
    status: Literal["collecting", "completed"]
    worker_ref: str
    team_ref: str
    audit_run_id: str | None = None
    draft: WorkerTaskDraftOut
    messages: list[dict[str, Any]] = Field(default_factory=list)
    task_id: str | None = None
    risk_card: TaskRiskCardOut | None = None


class SafetyQuestionCreate(BaseModel):
    worker_ref: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=2, max_length=2000)
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    use_llm: bool | None = None


class SafetyQuestionOut(BaseModel):
    id: str
    worker_ref: str
    task_id: str | None = None
    question: str
    answer: str
    answer_status: Literal["answered", "insufficient_evidence"]
    evidences: list[RiskEvidenceOut] = Field(default_factory=list)
    model_provider: str
    created_at: datetime


class QuizOptionOut(BaseModel):
    key: str
    text: str


class QuizQuestionOut(BaseModel):
    id: str
    scene: str
    scene_name: str
    type: Literal["single_choice", "true_false"]
    category: Literal["basic_rule", "prohibited_behavior", "misconception", "scenario"]
    stem: str
    options: list[QuizOptionOut]


class DynamicRiskEvaluateCreate(BaseModel):
    assessment_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    trigger_type: Literal[
        "data_refresh",
        "task_changed",
        "weather_changed",
        "learning_changed",
        "audit_changed",
    ] = "data_refresh"
    refresh_weather: bool = True
    include_test: bool = False


class DynamicRiskTriggerOut(BaseModel):
    code: str
    level: Literal["red", "yellow", "green", "info"]
    title: str
    detail: str
    source_type: str
    source_id: str = ""
    hard_block: bool = False
    duplicate_task_ids: list[str] = Field(default_factory=list)


class DynamicRiskItemOut(BaseModel):
    id: str
    run_id: str
    task_id: str
    worker_ref: str
    team_ref: str
    work_content: str
    work_location: str
    work_floor: str
    work_time: str
    normalized_task: str
    task_action: str = ""
    equipment_type: str = ""
    scenes: list[str] = Field(default_factory=list)
    scheduled_date: str
    time_window: str
    risk_level: Literal["red", "yellow", "green"]
    priority_score: int
    summary: str
    triggers: list[DynamicRiskTriggerOut] = Field(default_factory=list)
    evidences: list[dict[str, Any]] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    review_questions: list[QuizQuestionOut] = Field(default_factory=list)
    duplicate_count: int = 1
    merged_task_ids: list[str] = Field(default_factory=list)
    change: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DynamicRiskRunOut(BaseModel):
    id: str
    project_name: str
    assessment_date: str
    trigger_type: str
    include_test: bool = False
    input_fingerprint: str = ""
    version_created: bool = False
    previous_run_id: str | None = None
    raw_task_count: int = 0
    data_quality: dict[str, Any] = Field(default_factory=dict)
    version_change: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"]
    task_count: int
    red_count: int
    yellow_count: int
    green_count: int
    weather: dict[str, Any] = Field(default_factory=dict)
    items: list[DynamicRiskItemOut] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class KnowledgeEntityOut(BaseModel):
    id: str
    entity_type: str
    name: str
    summary: str
    scene: str
    data_scope: Literal["official", "demo", "test"]
    source_type: str
    source_id: str
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRelationOut(BaseModel):
    id: str
    source_entity_id: str
    relation_type: str
    target_entity_id: str
    label: str
    data_scope: Literal["official", "demo", "test"]
    source_type: str
    source_id: str
    created_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchItemOut(KnowledgeEntityOut):
    relation_count: int = 0


class KnowledgeSearchOut(BaseModel):
    query: str
    total: int
    items: list[KnowledgeSearchItemOut] = Field(default_factory=list)


class KnowledgeGraphOut(BaseModel):
    center: KnowledgeEntityOut
    nodes: list[KnowledgeEntityOut] = Field(default_factory=list)
    relations: list[KnowledgeRelationOut] = Field(default_factory=list)


class ProjectKnowledgeOverviewOut(BaseModel):
    project_name: str
    entity_count: int
    relation_count: int
    entity_type_counts: list[dict[str, Any]] = Field(default_factory=list)
    data_scope_counts: list[dict[str, Any]] = Field(default_factory=list)
    top_scenes: list[dict[str, Any]] = Field(default_factory=list)
    top_risks: list[dict[str, Any]] = Field(default_factory=list)
    common_wrong_questions: list[dict[str, Any]] = Field(default_factory=list)
    recent_items: list[KnowledgeEntityOut] = Field(default_factory=list)


class WorkerKnowledgeOut(BaseModel):
    worker: KnowledgeEntityOut
    summary: dict[str, int]
    tasks: list[KnowledgeEntityOut] = Field(default_factory=list)
    qa_records: list[KnowledgeEntityOut] = Field(default_factory=list)
    quiz_attempts: list[KnowledgeEntityOut] = Field(default_factory=list)
    active_wrong_questions: list[KnowledgeEntityOut] = Field(default_factory=list)


class NumericComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: str = Field(min_length=1, max_length=200)
    plan_value: float
    plan_unit: str = Field(min_length=1, max_length=20)
    operator: Literal[">=", "<=", ">", "<", "=="]
    standard_value: float
    standard_unit: str = Field(min_length=1, max_length=20)
    plan_segment_id: str = Field(min_length=1, max_length=100)
    standard_source_id: str = Field(min_length=1, max_length=100)


class LLMDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: AuditResult
    applicability_status: ApplicabilityStatus = ApplicabilityStatus.UNCERTAIN
    applicability_reason: str = Field(default="模型未单独说明规则适用性。", max_length=1000)
    plan_obligation: PlanObligation = PlanObligation.CONDITIONAL_MUST_STATE
    plan_obligation_reason: str = Field(
        default="需结合场景判断是否必须在方案中说明。", max_length=1000
    )
    issue: str = Field(min_length=1, max_length=1000)
    risk_consequence: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(min_length=1, max_length=1000)
    selected_plan_segment_ids: list[str] = Field(default_factory=list)
    decision_basis: Literal["text", "numeric", "missing", "scope"] = "text"
    numeric_comparison: NumericComparison | None = None
    requirement_checks: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class LLMBundleRuleDecision(LLMDecision):
    rule_id: str = Field(min_length=1, max_length=100)
