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


def test_workspace_mode_removes_login_and_ignores_legacy_auth_flag(test_settings):
    app = create_app(test_settings.model_copy(update={"enforce_auth": True}))
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "no-store" in root.headers["cache-control"]
        assert client.get("/rules/scenes").status_code == 200
        assert client.get("/api/v1/projects").status_code == 200
        assert client.post("/api/v1/auth/login", json={}).status_code == 404
        assert client.get("/api/v1/users").status_code == 404
        health = client.get("/health").json()
        assert health["workspace_mode"] == "single_active_project"
        assert health["auth_enforced"] is False
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ok"}
