import json

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.platform.workspaces import ProjectWorkspaceManager


def test_legacy_default_project_name_is_migrated(test_settings):
    manager = ProjectWorkspaceManager(test_settings)
    payload = json.loads(manager.catalog_path.read_text(encoding="utf-8"))
    payload["projects"][0]["name"] = "武汉市XX区写字楼施工项目"
    manager.catalog_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    migrated = ProjectWorkspaceManager(test_settings)
    assert migrated.active()["name"] == "XX综合医院扩建项目"


def test_workspace_agent_tools_and_memory_without_login(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/projects")
        assert catalog.status_code == 200
        assert catalog.json()["active_project"]["name"] == "XX综合医院扩建项目"
        assert "database_path" not in catalog.json()["active_project"]

        task = client.post(
            "/api/v1/agent/messages", json={"message": "今天下午在12层拆除外墙模板"}
        )
        assert task.status_code == 200, task.text
        task_body = task.json()
        assert task_body["agent_name"] == "worker_agent"
        assert "worker.task_intake" in task_body["metadata"]["tools"]
        conversation_id = task_body["conversation_id"]

        remembered = client.post(
            "/api/v1/agent/messages",
            json={"conversation_id": conversation_id, "message": "请记住模板拆除前检查临边防护"},
        )
        assert remembered.status_code == 200
        with app.state.database.connect() as connection:
            memory_count = connection.execute(
                "SELECT COUNT(*) FROM agent_memories WHERE active = 1"
            ).fetchone()[0]
            tool_count = connection.execute(
                "SELECT COUNT(*) FROM agent_tool_runs"
            ).fetchone()[0]
        assert memory_count == 1
        assert tool_count >= 2

        risk = client.post(
            "/api/v1/agent/messages", json={"message": "查看今天的动态风险"}
        )
        assert risk.status_code == 200, risk.text
        assert risk.json()["metadata"]["tools"] == ["risk.latest"]

        accidents = client.post(
            "/api/v1/agent/messages", json={"message": "检索外墙模板拆除相似事故"}
        )
        assert accidents.status_code == 200, accidents.text
        accident_body = accidents.json()
        assert accident_body["metadata"]["tools"] == ["knowledge.accident_search"]
        accident_items = accident_body["metadata"]["results"][0]["items"]
        assert 1 <= len(accident_items) <= 3
        assert all(item["case_id"] for item in accident_items)
        assert all("模板" in f"{item['task']} {item['scene']}" for item in accident_items)

        tools = client.get("/api/v1/agent/tools", params={"agent_name": "audit_agent"})
        assert tools.status_code == 200
        assert tools.json()
        assert client.get("/api/v1/dynamic-risk/graph").status_code == 200
        assert client.get(f"/api/v1/agent/conversations/{conversation_id}").status_code == 200


def test_account_mode_requires_login_when_auth_is_enabled(test_settings):
    app = create_app(test_settings.model_copy(update={"enforce_auth": True}))
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "no-store" in root.headers["cache-control"]
        assert client.get("/rules/scenes").status_code == 401
        assert client.get("/api/v1/projects").status_code == 401
        login = client.post(
            "/api/v1/auth/login", json={"username": "Admin", "password": "admin"}
        )
        assert login.status_code == 200
        assert client.get("/rules/scenes").status_code == 200
        assert client.get("/api/v1/projects").status_code == 200
        health = client.get("/health").json()
        assert health["workspace_mode"] == "account_scoped_projects"
        assert health["auth_enforced"] is True
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ok"}


def test_weather_question_uses_weather_tool_instead_of_dynamic_risk(test_settings):
    app = create_app(test_settings)

    class FakeWeather:
        provider_name = "test-weather"

        def get_forecast(self, work_time):
            return {
                "status": "ok",
                "source": "测试天气",
                "project_name": "XX综合医院扩建项目",
                "summary": f"{work_time}项目位置预报，晴，平均温度约26℃，最大风速约8 km/h。",
                "forecast_window": work_time,
                "temperature_c": 26,
                "max_wind_speed_kmh": 8,
                "precipitation": 0,
                "sky_conditions": ["CLEAR_DAY"],
                "alerts": [],
                "observed_at": "2026-09-19T08:00:00+08:00",
            }

    with TestClient(app) as client:
        app.state.weather_provider = FakeWeather()
        response = client.post(
            "/api/v1/agent/messages", json={"message": "今天天气怎么样？"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_name"] == "weather_agent"
        assert body["metadata"]["tools"] == ["weather.forecast"]
        assert body["metadata"]["routing_mode"] == "deterministic_fallback"
        assert "26℃" in body["answer"]
        assert "动态评估" not in body["answer"]


def test_model_planner_choice_is_used_before_keyword_fallback(test_settings):
    app = create_app(test_settings)

    class FakePlanner:
        def plan(self, **_):
            return ["general_agent"]

    class FakeAssistantModel:
        def answer_general(self, *, question, context_packets, project_name):
            assert question == "请用一句话介绍你自己"
            assert project_name == "XX综合医院扩建项目"
            return {"status": "ok", "answer": "我是可以按意图调用项目工具的智能助手。"}

    with TestClient(app) as client:
        app.state.agent_orchestrator.planner = FakePlanner()
        app.state.assistant_model_service = FakeAssistantModel()
        response = client.post(
            "/api/v1/agent/messages", json={"message": "请用一句话介绍你自己"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_name"] == "general_agent"
        assert body["metadata"]["tools"] == ["assistant.answer"]
        assert body["metadata"]["routing_mode"] == "model"
        assert "按意图调用项目工具" in body["answer"]
