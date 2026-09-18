from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.config import PROJECT_ROOT, Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        enforce_auth=False,
        model_provider="mock",
        model_api_key=None,
        model_base_url=None,
        model_name=None,
        caiyun_weather_token=None,
        caiyun_app_key=None,
        caiyun_app_secret=None,
        project_longitude=None,
        project_latitude=None,
        database_path=tmp_path / "test.sqlite",
        upload_dir=tmp_path / "uploads",
        chroma_dir=tmp_path / "chroma",
        rule_workbook_path=(
            PROJECT_ROOT / "data" / "规则化数据库" / "高处作业结构化规则数据库.xlsx"
        ),
        gold_workbook_path=(
            PROJECT_ROOT
            / "data"
            / "方案审计标准数据集"
            / "《高支模专项方案》高处作业风险审计.xlsx"
        ),
        audit_concurrency=3,
    )


@pytest.fixture
def sample_docx() -> Path:
    return PROJECT_ROOT / "data" / "方案审计标准数据集" / "高支模专项方案.docx"


@pytest.fixture
def sample_doc() -> Path:
    return PROJECT_ROOT / "data" / "施工方案" / "桥梁工程高空作业专项安全施工方案.doc"
