import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services import web_search as web_search_module
from backend.app.services.web_search import BochaWebSearchService


def test_bocha_search_normalizes_results(test_settings):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "住房城乡建设部发布页面",
                            "url": "https://www.mohurd.gov.cn/example",
                            "snippet": "这是官方页面摘要。",
                            "siteName": "住房城乡建设部",
                            "datePublished": "2026-01-02T08:00:00+08:00",
                        }
                    ]
                }
            }
        }

    settings = test_settings.model_copy(
        update={"bocha_api_key": "test-key", "bocha_search_enabled": True}
    )
    service = BochaWebSearchService(settings, http_post=fake_post)
    result = service.search("查询建筑行业最新信息", count=3)

    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["query"] == "查询建筑行业最新信息"
    assert result["provider"] == "bocha"
    assert result["items"][0]["title"] == "住房城乡建设部发布页面"
    assert result["items"][0]["url"] == "https://www.mohurd.gov.cn/example"
    assert result["items"][0]["quality"] == "high"


def test_relative_year_is_resolved_before_search(test_settings):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["query"] = payload["query"]
        return {"data": {"webPages": {"value": []}}}

    settings = test_settings.model_copy(
        update={"bocha_api_key": "test-key", "bocha_search_enabled": True}
    )
    BochaWebSearchService(settings, http_post=fake_post).search(
        "今年人工智能有哪些最新进展", count=3
    )

    year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    assert captured["query"].startswith(f"{year}年")
    assert captured["query"].endswith("权威来源")


def test_synthesis_failure_never_dumps_raw_search_snippets(test_settings, monkeypatch):
    def fake_post(url, headers, payload, timeout):
        return {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "测试来源",
                            "url": "https://example.com/source",
                            "snippet": "不应直接展示的超长原始搜索摘要和混乱格式。",
                        }
                    ]
                }
            }
        }

    settings = test_settings.model_copy(
        update={"bocha_api_key": "test-key", "bocha_search_enabled": True}
    )
    service = BochaWebSearchService(settings, http_post=fake_post)
    monkeypatch.setattr(
        service,
        "_model_answer",
        lambda query, search_query, items: {
            "status": "failed",
            "answer": "",
            "used_source_indexes": [],
        },
    )

    result = service.answer("查询最新进展")

    assert result["synthesis_status"] == "failed"
    assert "未能形成可靠结论" in result["answer"]
    assert "不应直接展示" not in result["answer"]
    assert "https://" not in result["answer"]


def test_model_synthesis_is_structured_and_disables_deepseek_thinking(
    test_settings, monkeypatch
):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        return {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "权威发布",
                            "url": "https://www.example.gov.cn/update",
                            "snippet": "来源中包含的事实依据。",
                            "siteName": "权威机构",
                        }
                    ]
                }
            }
        }

    class FakeCompletions:
        def create(self, **request):
            captured.update(request)
            content = json.dumps(
                {
                    "summary": "这是根据检索来源形成的直接结论。[1]",
                    "key_points": ["第一项进展有来源支持。[1]", "第二项仍需持续观察。[1]"],
                    "limitations": "当前可用来源数量有限。",
                    "used_source_indexes": [1],
                },
                ensure_ascii=False,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(web_search_module, "OpenAI", FakeOpenAI)
    settings = test_settings.model_copy(
        update={
            "bocha_api_key": "test-key",
            "bocha_search_enabled": True,
            "model_provider": "openai",
            "model_api_key": "model-key",
            "model_base_url": "https://api.deepseek.com",
            "model_name": "test-model",
        }
    )

    result = BochaWebSearchService(settings, http_post=fake_post).answer("查询最新进展")

    assert result["synthesis_status"] == "ok"
    assert "\n重点信息：\n- 第一项进展" in result["answer"]
    assert "\n\n说明：当前可用来源数量有限。" in result["answer"]
    assert "https://" not in result["answer"]
    assert result["used_source_indexes"] == [1]
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_unconfigured_search_hides_internal_setting_name(test_settings):
    settings = test_settings.model_copy(
        update={"bocha_api_key": None, "bocha_search_enabled": True}
    )
    result = BochaWebSearchService(settings).answer("查询最新建筑施工安全政策")

    assert result["status"] == "unconfigured"
    assert "BOCHA_API_KEY" not in result["answer"]
    assert "暂未启用" in result["answer"]


def test_general_question_uses_web_search_but_project_question_does_not(test_settings):
    app = create_app(test_settings)

    class FakeWebSearch:
        def answer(self, query, count=5):
            return {
                "query": query,
                "provider": "bocha",
                "answer": "根据联网结果，测试信息如下。[1]",
                "items": [
                    {
                        "title": "测试来源",
                        "url": "https://example.com/source",
                        "snippet": "测试摘要",
                        "site_name": "测试站点",
                        "published_at": "2026-01-02T08:00:00+08:00",
                    }
                ],
            }

    with TestClient(app) as client:
        app.state.web_search_service = FakeWebSearch()
        general = client.post(
            "/api/v1/agent/messages", json={"message": "联网查询今年人工智能有什么新进展？"}
        )
        assert general.status_code == 200, general.text
        assert general.json()["agent_name"] == "web_agent"
        assert general.json()["metadata"]["tools"] == ["web.search"]
        assert "测试信息" in general.json()["answer"]
        assert "https://example.com" not in general.json()["answer"]

        project = client.post(
            "/api/v1/agent/messages", json={"message": "查询项目今天的动态风险"}
        )
        assert project.status_code == 200, project.text
        assert project.json()["agent_name"] == "risk_agent"
        assert "web.search" not in project.json()["metadata"]["tools"]

        standard = client.post(
            "/api/v1/agent/messages", json={"message": "脚手架拆除有哪些规范要求？"}
        )
        assert standard.status_code == 200, standard.text
        assert standard.json()["agent_name"] == "worker_agent"
        assert standard.json()["metadata"]["tools"] == ["worker.safety_qa"]

        spoken_question = client.post(
            "/api/v1/agent/messages", json={"message": "脚手架拆除作业要注意什么？"}
        )
        assert spoken_question.status_code == 200, spoken_question.text
        assert spoken_question.json()["metadata"]["tools"] == ["worker.safety_qa"]

        stable_general = client.post(
            "/api/v1/agent/messages", json={"message": "什么是大语言模型？"}
        )
        assert stable_general.status_code == 200, stable_general.text
        assert stable_general.json()["agent_name"] == "general_agent"
        assert stable_general.json()["metadata"]["tools"] == ["assistant.answer"]

        current_external = client.post(
            "/api/v1/agent/messages", json={"message": "今年人工智能有哪些最新进展？"}
        )
        assert current_external.status_code == 200, current_external.text
        assert current_external.json()["agent_name"] == "web_agent"
        assert current_external.json()["metadata"]["tools"] == ["web.search"]
