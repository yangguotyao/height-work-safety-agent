from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from backend.app.db import Database
from backend.app.main import create_app
from backend.app.services.hazard_inspection import (
    HazardInspectionService,
    QwenHazardVisionProvider,
    _description_focus,
    _friendly_vision_error,
)


class FakeHazardVision:
    model_name = "fake-qwen3.7-plus"

    def analyze(self, *, image_path: Path, mime_type: str, context: dict) -> dict:
        assert image_path.is_file()
        assert mime_type == "image/png"
        assert context["onsite_description"] == "检查脚手架临边防护"
        return {
            "image_quality": "usable",
            "overall_visible_facts": ["作业面临边位置可见杆件和脚手板"],
            "unable_to_confirm": ["无法从单张图片确认杆件尺寸"],
            "candidates": [
                {
                    "scene": "脚手架作业",
                    "hazard_type": "临边防护",
                    "suspected_hazard": "临边防护可能不连续，需现场核查",
                    "visible_facts": ["图片右侧临边位置未见连续封闭防护"],
                    "unable_to_confirm": ["无法确认图片范围外的防护情况"],
                    "recommended_checks": ["现场核查临边防护连续性和固定情况"],
                }
            ],
        }

    def compare(
        self,
        *,
        before_path: Path,
        before_mime_type: str,
        after_path: Path,
        after_mime_type: str,
        context: dict,
    ) -> dict:
        assert before_path.is_file() and after_path.is_file()
        assert before_mime_type == after_mime_type == "image/png"
        assert context["confirmed_hazard"]
        return {
            "rectification_result": "基本完成",
            "main_improvements": ["整改后图片中临边位置新增连续杆件"],
            "remaining_concern": "未发现与原问题直接相关的明显遗留问题",
            "review_suggestion": "现场确认新增杆件固定可靠后完成人工复核。",
        }


class FakeStandardRag:
    def __init__(self):
        self.query = ""
        self.limit = 0
        self.queries: list[str] = []
        self.limits: list[int] = []

    def discover(self, query: str, limit: int = 4) -> list[dict]:
        self.query = query
        self.limit = limit
        self.queries.append(query)
        self.limits.append(limit)
        return [
            {
                "id": "unrelated",
                "standard_code": "JGJ 130-2011",
                "standard_name": "建筑施工扣件式钢管脚手架安全技术规范",
                "clause": "6.3.1",
                "text": "JGJ 130-2011｜6.3.1\n脚手架立杆纵距应符合设计要求。",
                "score": 0.99,
            },
            {
                "id": "safety-net-1",
                "standard_code": "JGJ 80-2016",
                "standard_name": "建筑施工高处作业安全技术规范",
                "clause": "8.1.2",
                "text": "JGJ 80-2016｜8.1.2\n安全网安装应牢固、严密。",
                "score": 0.78,
            },
            {
                "id": "safety-net-2",
                "standard_code": "GB 5725-2025",
                "standard_name": "坠落防护 安全网",
                "clause": "5.2",
                "text": "GB 5725-2025｜5.2\n安全网连接处不应存在明显开口。",
                "score": 0.72,
            },
            {
                "id": "safety-net-3",
                "standard_code": "JGJ 59-2011",
                "standard_name": "建筑施工安全检查标准",
                "clause": "3.3.4",
                "text": "JGJ 59-2011｜3.3.4\n防护网应保持完整。",
                "score": 0.65,
            },
        ]


def test_description_focus_prefers_specific_component_over_broad_scene():
    assert _description_focus("内部脚手架挡脚板变形松脱") == ["挡脚板"]
    assert _description_focus("检查脚手架临边区域") == ["脚手架", "临边"]


def test_qwen_vision_disables_thinking_and_configures_one_retry(test_settings, tmp_path):
    settings = test_settings.model_copy(
        update={
            "hazard_vision_api_key": "test-key",
            "hazard_vision_base_url": "https://example.com/v1",
            "hazard_vision_timeout_seconds": 180.0,
        }
    )
    image_path = tmp_path / "现场.png"
    image_path.write_bytes(b"image")
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"image_quality":"usable","overall_visible_facts":[],"unable_to_confirm":[],'
                        '"candidates":[]}'
                    )
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = completion

    with patch("backend.app.services.hazard_inspection.OpenAI", return_value=client) as factory:
        provider = QwenHazardVisionProvider(settings)
        provider.analyze(image_path=image_path, mime_type="image/png", context={})

    assert factory.call_args.kwargs["timeout"] == 180.0
    assert factory.call_args.kwargs["max_retries"] == 1
    assert client.chat.completions.create.call_args.kwargs["extra_body"] == {
        "enable_thinking": False
    }


def test_vision_transport_errors_are_translated_to_chinese():
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response_500 = httpx.Response(500, request=request)
    response_429 = httpx.Response(429, request=request)

    errors = [
        APITimeoutError(request),
        APIConnectionError(request=request),
        InternalServerError("server error", response=response_500, body=None),
        RateLimitError("rate limited", response=response_429, body=None),
    ]
    messages = [_friendly_vision_error(error) for error in errors]

    assert messages == [
        "AI识别服务响应超时，请稍后重新提交。",
        "暂时无法连接AI识别服务，请检查网络后重试。",
        "AI识别服务暂时异常，请稍后重试。",
        "AI识别服务当前繁忙，请稍后重试。",
    ]
    assert all("Request timed out" not in message for message in messages)


def test_standard_basis_combines_context_and_keeps_most_relevant_one(test_settings):
    rag = FakeStandardRag()
    service = HazardInspectionService(
        database=Database(test_settings.resolved_database_path),
        settings=test_settings,
        standard_rag=rag,
    )
    basis = service._standard_basis(
        {
            "onsite_description": "检查脚手架防护网板连接情况",
            "scene": "防护网安装区域",
            "hazard_type": "防护网连接缺陷",
            "suspected_hazard": "防护网板连接处可能存在明显缝隙",
        },
        {
            "hazard_category": "防护网连接",
            "fact_description": "相邻防护网板连接处存在明显开口",
            "rectification_requirement": "调整网板并紧固连接件",
        },
    )
    assert 40 in rag.limits
    assert any("防护网板连接处可能存在明显缝隙" in query for query in rag.queries)
    assert [item["source_id"] for item in basis] == ["safety-net-2"]
    assert all(item["retrieval_version"] == "onsite_v6" for item in basis)


def test_standard_basis_retrieves_drainage_clause_from_ai_and_human_description(
    test_settings,
):
    class DrainageRag:
        query = ""
        limit = 0
        queries: list[str] = []
        limits: list[int] = []

        def discover(self, query: str, limit: int = 4) -> list[dict]:
            self.query = query
            self.limit = limit
            self.queries.append(query)
            self.limits.append(limit)
            return [
                {
                    "id": "wrong-structural-clause",
                    "standard_code": "GB 51210-2016",
                    "standard_name": "建筑施工脚手架安全技术统一标准",
                    "clause": "3.2",
                    "text": "GB 51210-2016｜3.2\n安全等级和安全系数。",
                    "score": 0.96,
                },
                {
                    "id": "drainage-clause",
                    "standard_code": "GB 55023-2022",
                    "standard_name": "施工脚手架通用规范",
                    "clause": "4.1.3",
                    "text": "GB 55023-2022｜4.1.3\n4.1.3 第2款 应设置排水措施，搭设场地不应积水。",
                    "score": 0.81,
                },
            ]

    rag = DrainageRag()
    service = HazardInspectionService(
        database=Database(test_settings.resolved_database_path),
        settings=test_settings,
        standard_rag=rag,
    )
    basis = service._standard_basis(
        {
            "onsite_description": "脚手架搭设场地存在积水",
            "scene": "脚手架基础",
            "hazard_type": "场地积水",
            "suspected_hazard": "脚手架基础周边有明显积水",
        },
        {
            "hazard_category": "脚手架基础与排水",
            "fact_description": "搭设场地可见积水",
            "rectification_requirement": "清理积水并设置排水措施",
        },
    )
    assert basis[0]["source_id"] == "drainage-clause"
    assert basis[0]["standard_code"] == "GB 55023-2022"
    assert basis[0]["clause"] == "4.1.3"
    assert basis[0]["quote"] == "第2款 应设置排水措施，搭设场地不应积水。"
    assert basis[0]["retrieval_version"] == "onsite_v6"
    assert 40 in rag.limits
    assert any("场地积水" in query and "排水" in query for query in rag.queries)


def test_image_analysis_requires_human_confirmation_before_safety_item(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.hazard_inspection_service.provider = FakeHazardVision()
        created = client.post(
            "/api/v1/hazard-inspections",
            files={"file": ("现场.png", b"\x89PNG\r\n\x1a\nmock-image", "image/png")},
            data={"description": "检查脚手架临边防护"},
        )
        assert created.status_code == 202, created.text
        inspection = client.get(
            f"/api/v1/hazard-inspections/{created.json()['id']}"
        ).json()
        assert inspection["analysis_status"] == "ready"
        assert len(inspection["candidates"]) == 1
        candidate = inspection["candidates"][0]
        assert candidate["review_status"] == "pending"
        assert candidate["safety_item"] is None
        assert client.get("/api/v1/safety-items").json() == []

        confirmed = client.patch(
            f"/api/v1/hazard-candidates/{candidate['id']}/review",
            json={
                "action": "confirm",
                "title": "脚手架临边防护不连续",
                "fact_description": "右侧临边位置未见连续封闭防护",
                "hazard_category": "临边防护",
                "risk_level": "yellow",
                "location": "12层东侧",
                "rectification_requirement": "核查并连续设置符合方案要求的防护",
                "responsible_ref": "架子班",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        reviewed = confirmed.json()["candidates"][0]
        assert reviewed["review_status"] == "confirmed"
        assert reviewed["safety_item"]["status"] == "processing"
        assert reviewed["safety_item"]["created_by"] == "项目工作空间"
        detail = client.get(
            f"/api/v1/safety-items/{reviewed['safety_item']['id']}"
        ).json()
        assert detail["order_status"] == "pending_rectification"
        assert detail["ai_original_result"]["candidates"][0]["suspected_hazard"]


def test_rectification_can_be_returned_resubmitted_and_closed(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.hazard_inspection_service.provider = FakeHazardVision()
        created = client.post(
            "/api/v1/hazard-inspections",
            files={"file": ("整改前.png", b"\x89PNG\r\n\x1a\nbefore", "image/png")},
            data={"description": "检查脚手架临边防护"},
        ).json()
        candidate = client.get(
            f"/api/v1/hazard-inspections/{created['id']}"
        ).json()["candidates"][0]
        reviewed = client.patch(
            f"/api/v1/hazard-candidates/{candidate['id']}/review",
            json={
                "action": "confirm",
                "title": "脚手架临边防护不连续",
                "fact_description": "右侧临边位置未见连续封闭防护",
                "hazard_category": "临边防护",
                "risk_level": "yellow",
                "location": "12层东侧",
                "rectification_requirement": "连续设置临边防护并可靠固定",
                "responsible_ref": "架子班",
            },
        ).json()
        safety_item = reviewed["candidates"][0]["safety_item"]
        detail = client.get(f"/api/v1/safety-items/{safety_item['id']}").json()
        order_id = detail["order_id"]

        first = client.post(
            f"/api/v1/rectification-orders/{order_id}/submissions",
            files={"file": ("整改后1.png", b"\x89PNG\r\n\x1a\nafter-1", "image/png")},
            data={"description": "已设置临边防护杆件"},
        )
        assert first.status_code == 202, first.text
        detail = client.get(f"/api/v1/safety-items/{safety_item['id']}").json()
        assert detail["order_status"] == "pending_review"
        assert detail["submissions"][0]["comparison_status"] == "ready"
        comparison = detail["submissions"][0]["comparison"]
        assert comparison["rectification_result"] == "基本完成"
        assert len(comparison["main_improvements"]) <= 2
        assert comparison["remaining_concern"] == "未发现与原问题直接相关的明显遗留问题"

        returned = client.post(
            f"/api/v1/rectification-orders/{order_id}/review",
            json={"result": "return", "reason": "固定情况证据不足，请补拍"},
        )
        assert returned.status_code == 200, returned.text
        assert returned.json()["order_status"] == "rectifying"

        second = client.post(
            f"/api/v1/rectification-orders/{order_id}/submissions",
            files={"file": ("整改后2.png", b"\x89PNG\r\n\x1a\nafter-2", "image/png")},
            data={"description": "已补充固定节点并重新拍摄"},
        )
        assert second.status_code == 202, second.text
        closed = client.post(
            f"/api/v1/rectification-orders/{order_id}/review",
            json={"result": "pass", "reason": "现场复核符合整改要求"},
        )
        assert closed.status_code == 200, closed.text
        result = closed.json()
        assert result["status"] == "closed"
        assert result["order_status"] == "closed"
        assert len(result["submissions"]) == 2
        assert len(result["reviews"]) == 2
        assert [item["result"] for item in result["reviews"]] == ["return", "pass"]
        assert result["ai_original_result"]["candidates"][0]["suspected_hazard"]
        assert result["timeline"][-1]["type"] == "safety_item_closed"


def test_rejects_disguised_non_image(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/hazard-inspections",
            files={"file": ("伪装.png", b"not-an-image", "image/png")},
            data={"description": "检查现场防护"},
        )
    assert response.status_code == 400
    assert "文件内容不匹配" in response.json()["detail"]
