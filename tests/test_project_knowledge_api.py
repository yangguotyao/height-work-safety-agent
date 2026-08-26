from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_project_knowledge_starts_with_traceable_rules_and_accidents(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        page = client.get("/project-knowledge")
        overview = client.get("/project-knowledge/overview")
        search = client.get("/project-knowledge/search", params={"q": "脚手架拆除"})
        first_sync = app.state.project_knowledge_service.sync()
        second_sync = app.state.project_knowledge_service.sync()

    assert page.status_code == 200
    assert "项目安全知识库" in page.text
    assert first_sync == second_sync
    assert overview.status_code == 200, overview.text
    body = overview.json()
    counts = {item["entity_type"]: item["count"] for item in body["entity_type_counts"]}
    assert counts["accident"] == 55
    assert counts["rule"] >= 100
    assert body["relation_count"] > body["entity_count"]
    assert search.status_code == 200, search.text
    assert any(item["entity_type"] in {"accident", "rule"} for item in search.json()["items"])


def test_completed_task_is_written_to_search_and_relation_graph(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        task_response = client.post(
            "/worker-assistant/messages",
            json={
                "worker_ref": "知识正式工人",
                "message": "明天下午在12层拆除外墙模板",
                "use_llm": False,
            },
        )
        task_id = task_response.json()["task_id"]
        search = client.get(
            "/project-knowledge/search",
            params={"q": "拆除外墙模板", "entity_type": "task"},
        )
        task_item = next(
            item for item in search.json()["items"] if item["source_id"] == task_id
        )
        graph = client.get(f"/project-knowledge/entities/{task_item['id']}")
        worker = client.get("/project-knowledge/workers/知识正式工人")

    assert task_response.status_code == 200, task_response.text
    assert search.status_code == 200, search.text
    relation_types = {item["relation_type"] for item in graph.json()["relations"]}
    node_types = {item["entity_type"] for item in graph.json()["nodes"]}
    assert "TASK_IN_SCENE" in relation_types
    assert "TASK_HAS_RISK" in relation_types
    assert "TASK_SIMILAR_TO_ACCIDENT" in relation_types
    assert "TASK_ASSIGNED_TO" not in relation_types
    assert "worker" not in node_types
    assert worker.status_code == 200, worker.text
    assert worker.json()["summary"]["task_count"] == 1


def test_test_scope_is_hidden_but_explicit_worker_lookup_is_allowed(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        task_response = client.post(
            "/worker-assistant/messages",
            json={
                "worker_ref": "知识测试工人",
                "message": "后天下午在5层清理电梯井洞口",
                "use_llm": False,
            },
        )
        task_id = task_response.json()["task_id"]
        hidden = client.get(
            "/project-knowledge/search",
            params={"q": "清理电梯井洞口", "entity_type": "task"},
        )
        visible = client.get(
            "/project-knowledge/search",
            params={
                "q": "清理电梯井洞口",
                "entity_type": "task",
                "include_test": True,
            },
        )
        worker_search = client.get(
            "/project-knowledge/search", params={"q": "知识测试工人"}
        )
        worker = client.get("/project-knowledge/workers/知识测试工人")

    assert task_id
    assert not any(item["source_id"] == task_id for item in hidden.json()["items"])
    assert any(item["source_id"] == task_id for item in visible.json()["items"])
    assert all(item["entity_type"] != "worker" for item in worker_search.json()["items"])
    assert worker.status_code == 200, worker.text
    assert worker.json()["summary"]["task_count"] == 1


def test_wrong_quiz_answers_update_personal_knowledge(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        attempt = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "知识正式工人", "scene": "opening_work"},
        ).json()
        answers = []
        for index, item in enumerate(attempt["questions"]):
            correct = app.state.question_bank.get(item["id"])["answer"]
            option_keys = [option["key"] for option in item["options"]]
            submitted = next(key for key in option_keys if key != correct) if index < 2 else correct
            answers.append({"question_id": item["id"], "answer": submitted})
        submitted = client.post(
            f"/worker-assistant/quizzes/{attempt['id']}/submit",
            json={"answers": answers},
        )
        worker = client.get("/project-knowledge/workers/知识正式工人")
        overview = client.get("/project-knowledge/overview")

    assert submitted.status_code == 200, submitted.text
    assert worker.json()["summary"]["active_wrong_count"] == 2
    assert len(worker.json()["active_wrong_questions"]) == 2
    assert overview.json()["common_wrong_questions"]
