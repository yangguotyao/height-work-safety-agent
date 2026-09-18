from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMA_DIR = Path(os.environ.get("LOCALAPPDATA", PROJECT_ROOT / "runtime")) / (
    "height-work-agent/chroma"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    enforce_auth: bool = False
    auth_session_hours: int = Field(default=12, ge=1, le=168)
    auth_bootstrap_username: str = "admin"
    auth_bootstrap_password: str | None = None
    auth_bootstrap_display_name: str = "项目管理员"
    cors_origins: str = ""
    agent_context_message_limit: int = Field(default=12, ge=4, le=40)
    agent_context_char_budget: int = Field(default=12000, ge=2000, le=40000)
    mcp_enabled: bool = True

    bocha_search_enabled: bool = True
    bocha_api_key: str | None = None
    bocha_search_url: str = "https://api.bochaai.com/v1/web-search"
    bocha_search_timeout_seconds: float = Field(default=12.0, ge=2, le=30)
    bocha_search_max_results: int = Field(default=5, ge=1, le=10)

    model_provider: str = "mock"
    model_api_key: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    model_timeout_seconds: float = 180.0
    # These options are only forwarded to DashScope's OpenAI-compatible endpoint.
    model_enable_thinking: bool = False
    model_thinking_budget: int | None = Field(default=None, ge=1, le=262144)
    model_routing_thinking_budget: int | None = Field(default=256, ge=1, le=262144)
    model_audit_thinking_budget: int | None = Field(default=768, ge=1, le=262144)
    model_resolution_thinking_budget: int | None = Field(default=512, ge=1, le=262144)
    model_deepseek_enable_thinking: bool = False
    model_routing_max_tokens: int = Field(default=5000, ge=512, le=65536)
    model_audit_max_tokens: int = Field(default=8000, ge=512, le=65536)
    model_resolution_max_tokens: int = Field(default=5000, ge=512, le=65536)

    # The onsite hazard vision model is intentionally isolated from the plan-audit
    # model so changing one workflow cannot silently change the other.
    hazard_vision_api_key: str | None = None
    hazard_vision_base_url: str | None = None
    hazard_vision_model: str = "qwen3.7-plus"
    hazard_vision_timeout_seconds: float = Field(default=180.0, ge=10, le=300)
    hazard_image_max_mb: int = Field(default=10, ge=1, le=20)

    database_path: Path = Path("runtime/height_work_agent.sqlite")
    upload_dir: Path = Path("runtime/uploads")
    rule_workbook_path: Path = Path("data/规则化数据库/高处作业结构化规则数据库.xlsx")
    gold_workbook_path: Path = Path(
        "data/方案审计标准数据集/《高支模专项方案》高处作业风险审计.xlsx"
    )
    standard_pdf_dir: Path = Path("data/规范或标准")
    # Chroma's Windows HNSW reader can fail when its persistence path contains CJK characters.
    chroma_dir: Path = DEFAULT_CHROMA_DIR
    standard_collection: str = "standards_v1"
    embedding_provider: str = "hashing"
    embedding_model: str | None = None
    embedding_dimension: int = Field(default=384, ge=128, le=3072)
    # Independent, plan-driven standard discovery per confirmed scene instance.
    # This is deliberately small because exact rule-clause binding is retained
    # for the final authoritative audit basis.
    scene_rag_limit: int = Field(default=4, ge=1, le=12)
    max_upload_mb: int = Field(default=30, ge=1, le=200)
    audit_concurrency: int = Field(default=4, ge=1, le=8)

    project_name: str = "XX综合医院扩建项目"
    project_longitude: float | None = Field(default=None, ge=-180, le=180)
    project_latitude: float | None = Field(default=None, ge=-90, le=90)
    accident_graph_path: Path = Path(
        "data/高处作业事故知识图谱_55例/高处作业事故知识图谱.sqlite"
    )
    question_bank_path: Path = Path("data/安全学习题库/高处作业安全题库.json")
    caiyun_weather_token: str | None = None
    caiyun_app_key: str | None = None
    caiyun_app_secret: str | None = None
    weather_timeout_seconds: float = Field(default=10.0, ge=1, le=30)

    @field_validator("model_provider")
    @classmethod
    def validate_model_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"mock", "openai"}:
            raise ValueError("MODEL_PROVIDER 只支持 mock 或 openai")
        return normalized

    @field_validator("embedding_provider")
    @classmethod
    def validate_embedding_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"hashing", "openai"}:
            raise ValueError("EMBEDDING_PROVIDER 只支持 hashing 或 openai")
        return normalized

    @field_validator("project_longitude", "project_latitude", mode="before")
    @classmethod
    def blank_coordinate_to_none(cls, value: object) -> object:
        return None if value == "" else value

    def resolved_path(self, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def resolved_database_path(self) -> Path:
        return self.resolved_path(self.database_path)

    @property
    def resolved_upload_dir(self) -> Path:
        return self.resolved_path(self.upload_dir)

    @property
    def resolved_rule_workbook_path(self) -> Path:
        return self.resolved_path(self.rule_workbook_path)

    @property
    def resolved_gold_workbook_path(self) -> Path:
        return self.resolved_path(self.gold_workbook_path)

    @property
    def resolved_standard_pdf_dir(self) -> Path:
        return self.resolved_path(self.standard_pdf_dir)

    @property
    def resolved_chroma_dir(self) -> Path:
        return self.resolved_path(self.chroma_dir)

    @property
    def resolved_accident_graph_path(self) -> Path:
        return self.resolved_path(self.accident_graph_path)

    @property
    def resolved_question_bank_path(self) -> Path:
        return self.resolved_path(self.question_bank_path)

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def development_bootstrap_password(self) -> str | None:
        if self.auth_bootstrap_password:
            return self.auth_bootstrap_password
        if self.app_env.strip().lower() != "production":
            return "admin"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
