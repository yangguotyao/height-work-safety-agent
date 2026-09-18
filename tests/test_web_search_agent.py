from fastapi.testclient import TestClient

from backend.app.main import create_app
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
        assert "测试来源" in general.json()["answer"]

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
