from enum import Enum


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class AuditStatus(str, Enum):
    CREATED = "created"
    ROUTING = "routing"
    AUDITING = "auditing"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditResult(str, Enum):
    COMPLIANT = "符合"
    NONCOMPLIANT = "不符合"
    NOT_SPECIFIED = "未说明"
    NOT_APPLICABLE = "不适用"
    OUT_OF_SCOPE = "方案外核验"
    NEEDS_HUMAN_REVIEW = "待人工确认"


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNCERTAIN = "uncertain"


class PlanObligation(str, Enum):
    MUST_STATE = "must_state"
    CONDITIONAL_MUST_STATE = "conditional_must_state"
    REFERENCE_ONLY = "reference_only"
    EXTERNAL_ONLY = "external_only"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EDITED = "edited"
    REJECTED = "rejected"


class ReviewAction(str, Enum):
    CONFIRM = "confirm"
    EDIT = "edit"
    REJECT = "reject"
