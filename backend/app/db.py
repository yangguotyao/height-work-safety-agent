from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL,
    segment_count INTEGER NOT NULL DEFAULT 0,
    parse_warnings_json TEXT NOT NULL DEFAULT '[]',
    markdown_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);

CREATE TABLE IF NOT EXISTS document_segments (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    segment_type TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    location TEXT NOT NULL,
    text TEXT NOT NULL,
    markdown_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_segments_document ON document_segments(document_id, sequence_no);

CREATE TABLE IF NOT EXISTS audit_rules (
    rule_id TEXT PRIMARY KEY,
    enabled_status TEXT NOT NULL,
    scene TEXT NOT NULL,
    process TEXT NOT NULL,
    trigger_condition TEXT NOT NULL,
    requirement TEXT NOT NULL,
    threshold TEXT NOT NULL,
    rule_effect TEXT NOT NULL,
    hazards TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    roles TEXT NOT NULL,
    inspection_method TEXT NOT NULL,
    standard_name TEXT NOT NULL,
    standard_code TEXT NOT NULL,
    standard_level TEXT NOT NULL,
    standard_status TEXT NOT NULL,
    clause TEXT NOT NULL,
    original_text TEXT NOT NULL,
    pdf_page INTEGER,
    printed_page INTEGER,
    source_workbook TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_scene_status ON audit_rules(scene, enabled_status);

CREATE TABLE IF NOT EXISTS standards (
    id TEXT PRIMARY KEY,
    standard_code TEXT NOT NULL UNIQUE,
    standard_name TEXT NOT NULL,
    status TEXT NOT NULL,
    active INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    text_page_count INTEGER NOT NULL,
    parse_status TEXT NOT NULL,
    parse_warning TEXT,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_chunks (
    id TEXT PRIMARY KEY,
    standard_id TEXT NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
    standard_code TEXT NOT NULL,
    standard_name TEXT NOT NULL,
    clause TEXT NOT NULL,
    title_path TEXT NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    active INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_standard_chunks_code_clause
ON standard_chunks(standard_code, clause, active);

CREATE TABLE IF NOT EXISTS audit_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    status TEXT NOT NULL,
    current_node TEXT,
    scenes_json TEXT NOT NULL DEFAULT '[]',
    scene_instances_json TEXT NOT NULL DEFAULT '[]',
    model_provider TEXT NOT NULL,
    model_name TEXT,
    rule_limit INTEGER NOT NULL,
    completed_rules INTEGER NOT NULL DEFAULT 0,
    bundle_count INTEGER NOT NULL DEFAULT 0,
    completed_bundles INTEGER NOT NULL DEFAULT 0,
    model_call_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL REFERENCES audit_rules(rule_id),
    scene TEXT NOT NULL,
    plan_quote TEXT NOT NULL,
    source_location TEXT NOT NULL,
    issue TEXT NOT NULL,
    basis_json TEXT NOT NULL,
    risk_consequence TEXT NOT NULL,
    result TEXT NOT NULL,
    applicability_status TEXT NOT NULL DEFAULT 'uncertain',
    applicability_reason TEXT NOT NULL DEFAULT '',
    plan_obligation TEXT NOT NULL DEFAULT 'conditional_must_state',
    plan_obligation_reason TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    business_group_key TEXT NOT NULL DEFAULT '',
    control_title TEXT NOT NULL DEFAULT '',
    suggestion TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL,
    final_result TEXT,
    final_text TEXT,
    model_raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_run ON audit_items(run_id);

CREATE TABLE IF NOT EXISTS audit_evidences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_item_id TEXT NOT NULL REFERENCES audit_items(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    location TEXT,
    score REAL
);

CREATE TABLE IF NOT EXISTS human_reviews (
    id TEXT PRIMARY KEY,
    audit_item_id TEXT NOT NULL REFERENCES audit_items(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    original_result TEXT NOT NULL,
    final_result TEXT,
    final_text TEXT,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_revisions (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    revised_run_id TEXT NOT NULL UNIQUE REFERENCES audit_runs(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'analyzing',
    comparison_json TEXT NOT NULL DEFAULT '{}',
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(source_run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_source
ON plan_revisions(source_run_id, attempt_no DESC);

CREATE TABLE IF NOT EXISTS gold_cases (
    id TEXT PRIMARY KEY,
    source_workbook TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    scene TEXT NOT NULL,
    plan_quote TEXT NOT NULL,
    source_location TEXT NOT NULL,
    issue TEXT NOT NULL,
    basis TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    risk_consequence TEXT NOT NULL,
    expected_result TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE(source_workbook, source_row)
);

CREATE TABLE IF NOT EXISTS test_runs (
    id TEXT PRIMARY KEY,
    audit_run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    gold_source TEXT NOT NULL,
    total_gold INTEGER NOT NULL,
    generated_items INTEGER NOT NULL,
    matched_cases INTEGER NOT NULL,
    correct_results INTEGER NOT NULL,
    result_accuracy REAL NOT NULL,
    average_match_score REAL NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_sessions (
    id TEXT PRIMARY KEY,
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    worker_ref TEXT NOT NULL DEFAULT '',
    team_ref TEXT NOT NULL DEFAULT '',
    audit_run_id TEXT REFERENCES audit_runs(id),
    status TEXT NOT NULL,
    draft_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES worker_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_messages_session
ON worker_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS work_tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES worker_sessions(id) ON DELETE CASCADE,
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    worker_ref TEXT NOT NULL DEFAULT '',
    team_ref TEXT NOT NULL DEFAULT '',
    audit_run_id TEXT REFERENCES audit_runs(id),
    work_content TEXT NOT NULL,
    work_location TEXT NOT NULL,
    work_floor TEXT NOT NULL,
    work_time TEXT NOT NULL,
    normalized_task TEXT NOT NULL,
    task_action TEXT NOT NULL DEFAULT '',
    equipment_type TEXT NOT NULL DEFAULT '',
    scenes_json TEXT NOT NULL DEFAULT '[]',
    scheduled_date TEXT NOT NULL DEFAULT '',
    time_window TEXT NOT NULL DEFAULT 'all_day',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_risk_cards (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES work_tasks(id) ON DELETE CASCADE,
    card_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS safety_qa_records (
    id TEXT PRIMARY KEY,
    worker_ref TEXT NOT NULL,
    task_id TEXT REFERENCES work_tasks(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    answer_status TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    model_provider TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_safety_qa_worker
ON safety_qa_records(worker_ref, created_at);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id TEXT PRIMARY KEY,
    worker_ref TEXT NOT NULL,
    task_id TEXT REFERENCES work_tasks(id) ON DELETE SET NULL,
    scene TEXT NOT NULL,
    question_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    submitted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_worker
ON quiz_attempts(worker_ref, started_at);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    worker_ref TEXT NOT NULL,
    question_id TEXT NOT NULL,
    submitted_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(attempt_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_quiz_answers_worker_question
ON quiz_answers(worker_ref, question_id, created_at);

CREATE TABLE IF NOT EXISTS worker_learning_events (
    id TEXT PRIMARY KEY,
    worker_ref TEXT NOT NULL,
    event_type TEXT NOT NULL,
    resource_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_events_worker
ON worker_learning_events(worker_ref, created_at);

CREATE TABLE IF NOT EXISTS dynamic_risk_runs (
    id TEXT PRIMARY KEY,
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    assessment_date TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    include_test INTEGER NOT NULL DEFAULT 0,
    input_fingerprint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    task_count INTEGER NOT NULL DEFAULT 0,
    red_count INTEGER NOT NULL DEFAULT 0,
    yellow_count INTEGER NOT NULL DEFAULT 0,
    green_count INTEGER NOT NULL DEFAULT 0,
    weather_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dynamic_risk_runs_date
ON dynamic_risk_runs(assessment_date, created_at);

CREATE TABLE IF NOT EXISTS dynamic_risk_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES dynamic_risk_runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES work_tasks(id) ON DELETE CASCADE,
    risk_level TEXT NOT NULL,
    priority_score INTEGER NOT NULL,
    summary TEXT NOT NULL,
    triggers_json TEXT NOT NULL DEFAULT '[]',
    evidences_json TEXT NOT NULL DEFAULT '[]',
    interventions_json TEXT NOT NULL DEFAULT '[]',
    review_questions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_dynamic_risk_items_run_level
ON dynamic_risk_items(run_id, risk_level, priority_score);

CREATE TABLE IF NOT EXISTS dynamic_risk_confirmations (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES dynamic_risk_items(id) ON DELETE CASCADE,
    check_key TEXT NOT NULL,
    check_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    confirmer_ref TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(item_id, check_key)
);
CREATE INDEX IF NOT EXISTS idx_dynamic_confirmations_item
ON dynamic_risk_confirmations(item_id, status);

CREATE TABLE IF NOT EXISTS hazard_inspections (
    id TEXT PRIMARY KEY,
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    task_id TEXT REFERENCES work_tasks(id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'queued',
    model_name TEXT NOT NULL,
    image_quality TEXT,
    overall_visible_facts_json TEXT NOT NULL DEFAULT '[]',
    unable_to_confirm_json TEXT NOT NULL DEFAULT '[]',
    ai_result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hazard_inspections_created
ON hazard_inspections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hazard_inspections_task
ON hazard_inspections(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hazard_candidates (
    id TEXT PRIMARY KEY,
    inspection_id TEXT NOT NULL REFERENCES hazard_inspections(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    scene TEXT NOT NULL DEFAULT '',
    hazard_type TEXT NOT NULL DEFAULT '',
    suspected_hazard TEXT NOT NULL,
    visible_facts_json TEXT NOT NULL DEFAULT '[]',
    unable_to_confirm_json TEXT NOT NULL DEFAULT '[]',
    recommended_checks_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewer_ref TEXT,
    review_comment TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(inspection_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_hazard_candidates_review
ON hazard_candidates(review_status, created_at DESC);

CREATE TABLE IF NOT EXISTS safety_items (
    id TEXT PRIMARY KEY,
    item_no TEXT NOT NULL DEFAULT '',
    source_candidate_id TEXT NOT NULL UNIQUE
        REFERENCES hazard_candidates(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES work_tasks(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'onsite_multimodal',
    title TEXT NOT NULL,
    fact_description TEXT NOT NULL,
    hazard_category TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    location TEXT NOT NULL,
    basis_json TEXT NOT NULL DEFAULT '[]',
    basis_retrieval_version TEXT NOT NULL DEFAULT '',
    rectification_requirement TEXT NOT NULL,
    responsible_ref TEXT NOT NULL,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_safety_items_status
ON safety_items(status, created_at DESC);

CREATE TABLE IF NOT EXISTS rectification_orders (
    id TEXT PRIMARY KEY,
    order_no TEXT NOT NULL UNIQUE,
    safety_item_id TEXT NOT NULL UNIQUE REFERENCES safety_items(id) ON DELETE CASCADE,
    responsible_ref TEXT NOT NULL,
    team_or_area TEXT NOT NULL DEFAULT '',
    requirement TEXT NOT NULL,
    due_at TEXT,
    risk_level TEXT NOT NULL,
    immediate INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending_rectification',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rectification_orders_status
ON rectification_orders(status, created_at DESC);

CREATE TABLE IF NOT EXISTS rectification_submissions (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES rectification_orders(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    description TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    comparison_status TEXT NOT NULL DEFAULT 'queued',
    comparison_model TEXT NOT NULL,
    comparison_json TEXT NOT NULL DEFAULT '{}',
    comparison_error TEXT,
    UNIQUE(order_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_rectification_submissions_order
ON rectification_submissions(order_id, attempt_no DESC);

CREATE TABLE IF NOT EXISTS rectification_reviews (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES rectification_orders(id) ON DELETE CASCADE,
    submission_id TEXT NOT NULL REFERENCES rectification_submissions(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    result TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    reviewer_ref TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    UNIQUE(order_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_rectification_reviews_order
ON rectification_reviews(order_id, sequence_no);

CREATE TABLE IF NOT EXISTS knowledge_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    scene TEXT NOT NULL DEFAULT '',
    data_scope TEXT NOT NULL DEFAULT 'official',
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(entity_type, source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_type_scope
ON knowledge_entities(entity_type, data_scope);
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_scene
ON knowledge_entities(scene);

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    target_entity_id TEXT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    data_scope TEXT NOT NULL DEFAULT 'official',
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
ON knowledge_relations(source_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
ON knowledge_relations(target_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_scope
ON knowledge_relations(data_scope);

CREATE TABLE IF NOT EXISTS platform_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    system_role TEXT NOT NULL DEFAULT 'user',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_users_active
ON platform_users(active, username);

CREATE TABLE IF NOT EXISTS platform_project_members (
    project_id TEXT NOT NULL REFERENCES platform_projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    project_role TEXT NOT NULL,
    worker_ref TEXT NOT NULL DEFAULT '',
    team_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_members_user
ON platform_project_members(user_id, project_id);

CREATE TABLE IF NOT EXISTS platform_auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    active_project_id TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    remote_addr TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_user_expires
ON platform_auth_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES platform_projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    active_agent TEXT NOT NULL DEFAULT 'coordinator',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_owner
ON agent_conversations(project_id, user_id, updated_at);

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    agent_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_conversation
ON agent_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS agent_memories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES platform_projects(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES platform_users(id) ON DELETE CASCADE,
    memory_type TEXT NOT NULL,
    subject_key TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    importance REAL NOT NULL DEFAULT 0.5,
    source_type TEXT NOT NULL DEFAULT 'conversation',
    source_id TEXT NOT NULL DEFAULT '',
    supersedes_id TEXT REFERENCES agent_memories(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
ON agent_memories(project_id, user_id, memory_type, active, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_memories_subject
ON agent_memories(project_id, subject_key, active);

CREATE TABLE IF NOT EXISTS agent_tool_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    message_id TEXT REFERENCES agent_messages(id) ON DELETE SET NULL,
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_tool_runs_conversation
ON agent_tool_runs(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS platform_data_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES platform_projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    version_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_data_events_scope
ON platform_data_events(project_id, event_type, created_at);

CREATE TABLE IF NOT EXISTS daily_safety_logs (
    id TEXT PRIMARY KEY,
    browser_session_id TEXT NOT NULL DEFAULT 'legacy',
    assessment_date TEXT NOT NULL,
    project_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    input_fingerprint TEXT NOT NULL,
    content_json TEXT NOT NULL DEFAULT '{}',
    source_snapshot_json TEXT NOT NULL DEFAULT '{}',
    change_json TEXT NOT NULL DEFAULT '{}',
    docx_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(assessment_date, input_fingerprint),
    UNIQUE(assessment_date, version)
);
CREATE INDEX IF NOT EXISTS idx_daily_safety_logs_date
ON daily_safety_logs(assessment_date, version DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(audit_runs)").fetchall()
            }
            if "scene_instances_json" not in columns:
                connection.execute(
                    "ALTER TABLE audit_runs ADD COLUMN scene_instances_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "completed_rules" not in columns:
                connection.execute(
                    "ALTER TABLE audit_runs ADD COLUMN completed_rules INTEGER NOT NULL DEFAULT 0"
                )
            for name in ("bundle_count", "completed_bundles", "model_call_count"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE audit_runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
                    )
            item_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(audit_items)").fetchall()
            }
            if "applicability_status" not in item_columns:
                connection.execute(
                    "ALTER TABLE audit_items ADD COLUMN applicability_status "
                    "TEXT NOT NULL DEFAULT 'uncertain'"
                )
            if "applicability_reason" not in item_columns:
                connection.execute(
                    "ALTER TABLE audit_items ADD COLUMN applicability_reason "
                    "TEXT NOT NULL DEFAULT ''"
                )
            item_defaults = {
                "plan_obligation": "'conditional_must_state'",
                "plan_obligation_reason": "''",
                "bundle_id": "''",
                "business_group_key": "''",
                "control_title": "''",
            }
            for name, default in item_defaults.items():
                if name not in item_columns:
                    connection.execute(
                        f"ALTER TABLE audit_items ADD COLUMN {name} "
                        f"TEXT NOT NULL DEFAULT {default}"
                    )
            document_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "markdown_text" not in document_columns:
                connection.execute(
                    "ALTER TABLE documents ADD COLUMN markdown_text TEXT NOT NULL DEFAULT ''"
                )
            segment_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(document_segments)").fetchall()
            }
            if "markdown_text" not in segment_columns:
                connection.execute(
                    "ALTER TABLE document_segments ADD COLUMN markdown_text "
                    "TEXT NOT NULL DEFAULT ''"
                )
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(work_tasks)").fetchall()
            }
            for name in ("task_action", "equipment_type"):
                if name not in task_columns:
                    connection.execute(
                        f"ALTER TABLE work_tasks ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )
            task_defaults = {
                "scheduled_date": "''",
                "time_window": "'all_day'",
            }
            for name, default in task_defaults.items():
                if name not in task_columns:
                    connection.execute(
                        f"ALTER TABLE work_tasks ADD COLUMN {name} TEXT NOT NULL DEFAULT {default}"
                    )
            risk_run_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(dynamic_risk_runs)"
                ).fetchall()
            }
            if "include_test" not in risk_run_columns:
                connection.execute(
                    "ALTER TABLE dynamic_risk_runs "
                    "ADD COLUMN include_test INTEGER NOT NULL DEFAULT 0"
                )
            if "input_fingerprint" not in risk_run_columns:
                connection.execute(
                    "ALTER TABLE dynamic_risk_runs "
                    "ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """UPDATE dynamic_risk_runs SET include_test = 1
                       WHERE EXISTS (
                           SELECT 1 FROM dynamic_risk_items i
                           JOIN work_tasks t ON t.id = i.task_id
                           WHERE i.run_id = dynamic_risk_runs.id
                             AND (
                               t.worker_ref IN ('工人01', '工人A', '工人B')
                               OR t.worker_ref LIKE '%测试%'
                               OR t.worker_ref LIKE '%验收%'
                               OR t.worker_ref LIKE '%页面%'
                               OR lower(t.worker_ref) LIKE '%test%'
                               OR t.worker_ref LIKE '演示%'
                             )
                    )"""
                )
            safety_item_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(safety_items)").fetchall()
            }
            safety_item_defaults = {
                "item_no": "''",
                "source": "'onsite_multimodal'",
                "basis_json": "'[]'",
                "basis_retrieval_version": "''",
                "confirmed_by": "''",
            }
            for name, default in safety_item_defaults.items():
                if name not in safety_item_columns:
                    connection.execute(
                        f"ALTER TABLE safety_items ADD COLUMN {name} "
                        f"TEXT NOT NULL DEFAULT {default}"
                    )
            if "confirmed_at" not in safety_item_columns:
                connection.execute("ALTER TABLE safety_items ADD COLUMN confirmed_at TEXT")
            connection.execute(
                "UPDATE safety_items SET status='processing' WHERE status='open'"
            )
            connection.execute(
                """UPDATE safety_items
                   SET item_no='AQ-' || replace(substr(created_at, 1, 10), '-', '') || '-' ||
                       upper(substr(id, 1, 6))
                   WHERE item_no=''"""
            )
            connection.execute(
                """UPDATE safety_items
                   SET confirmed_by=created_by,
                       confirmed_at=COALESCE(confirmed_at, created_at)
                   WHERE confirmed_by='' OR confirmed_at IS NULL"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO rectification_orders
                   (id, order_no, safety_item_id, responsible_ref, team_or_area,
                    requirement, due_at, risk_level, immediate, status, created_by,
                    created_at, updated_at)
                   SELECT lower(hex(randomblob(16))),
                          'ZG-' || replace(substr(s.created_at, 1, 10), '-', '') || '-' ||
                          upper(substr(s.id, 1, 6)),
                          s.id, s.responsible_ref, s.responsible_ref,
                          s.rectification_requirement, s.deadline, s.risk_level,
                          CASE WHEN s.risk_level='red' THEN 1 ELSE 0 END,
                          CASE WHEN s.status='closed' THEN 'closed'
                               ELSE 'pending_rectification' END,
                          s.created_by, s.created_at, s.updated_at
                   FROM safety_items s
                   WHERE NOT EXISTS (
                       SELECT 1 FROM rectification_orders o WHERE o.safety_item_id=s.id
                   )"""
            )
            inspection_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(hazard_inspections)"
                ).fetchall()
            }
            if "ai_result_json" not in inspection_columns:
                connection.execute(
                    "ALTER TABLE hazard_inspections ADD COLUMN ai_result_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
            for table in (
                "audit_runs",
                "worker_sessions",
                "work_tasks",
                "dynamic_risk_runs",
                "hazard_inspections",
                "daily_safety_logs",
            ):
                scoped_columns = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "browser_session_id" not in scoped_columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN browser_session_id "
                        "TEXT NOT NULL DEFAULT 'legacy'"
                    )
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_browser_session "
                    f"ON {table}(browser_session_id)"
                )
            session_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(platform_auth_sessions)"
                ).fetchall()
            }
            if "active_project_id" not in session_columns:
                connection.execute(
                    "ALTER TABLE platform_auth_sessions ADD COLUMN active_project_id TEXT"
                )
            connection.execute("PRAGMA optimize")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, tuple(parameters))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.connect() as connection:
            connection.executemany(sql, rows)

    def fetch_one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
