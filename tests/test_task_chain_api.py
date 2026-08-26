from fastapi.testclient import TestClient

from backend.app.main import create_app


def _task(client: TestClient, content: str = "今天下午在12层拆除外墙模板") -> dict:
    response = client.post(
        "/worker-assistant/messages",
        json={
            "message": content,
            "worker_ref": "项目作业人员",
            "team_ref": "",
            "use_llm": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_task_chain_aggregates_risk_card_versions_and_duplicate_quality(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        first = _task(client)
        second = _task(client)
        chain_response = client.get(f"/api/v1/tasks/{first['task_id']}/chain")

    assert chain_response.status_code == 200, chain_response.text
    chain = chain_response.json()
    assert chain["task"]["id"] in {first["task_id"], second["task_id"]}
    assert chain["duplicate_count"] == 2
    assert set(chain["merged_task_ids"]) == {first["task_id"], second["task_id"]}
    assert chain["data_quality"]["status"] == "merged"
    assert chain["risk_card"]["task_id"] in chain["merged_task_ids"]
    assert chain["current_dynamic_risk"]["change"]["explanation"]
    assert chain["dynamic_risk_versions"]
    assert any(item["type"] == "task" for item in chain["timeline"])
    assert any(item["type"] == "risk_card" for item in chain["timeline"])


def test_projects_use_independent_business_databases(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        default_catalog = client.get("/api/v1/projects").json()
        default_id = default_catalog["active_project_id"]
        office_task = _task(client)

        created = client.post(
            "/api/v1/projects", json={"name": "武汉市江岸住宅楼施工项目"}
        )
        assert created.status_code == 201, created.text
        residence = created.json()
        switched = client.post(f"/api/v1/projects/{residence['id']}/activate")
        assert switched.status_code == 200, switched.text
        assert app.state.settings.project_name == "武汉市江岸住宅楼施工项目"
        assert client.get("/api/v1/tasks/recent").json() == []

        residence_task = _task(client, "明天上午在屋面安装防水保温板")
        assert residence_task["task_id"] != office_task["task_id"]

        switched_back = client.post(f"/api/v1/projects/{default_id}/activate")
        assert switched_back.status_code == 200, switched_back.text
        task_ids = {item["id"] for item in client.get("/api/v1/tasks/recent").json()}
        assert office_task["task_id"] in task_ids
        assert residence_task["task_id"] not in task_ids
