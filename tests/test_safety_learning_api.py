import statistics
from collections import Counter

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.safety_learning import (
    _has_unsupported_opening_orientation_inference,
)


def test_default_question_bank_has_required_scene_coverage(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.get("/worker-assistant/question-bank/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question_count"] == 130
    assert len(body["scenes"]) == 13
    assert all(item["question_count"] >= 10 for item in body["scenes"])
    assert all(item["single_choice_count"] >= 3 for item in body["scenes"])
    assert all(item["true_false_count"] >= 2 for item in body["scenes"])

    banned_phrases = (
        "只要作业人员经验丰富",
        "经班组口头同意",
        "只在发生事故后",
        "相可不",
    )
    single_choice_questions = [
        item
        for item in app.state.question_bank.questions.values()
        if item["type"] == "single_choice"
    ]
    assert single_choice_questions
    answer_counts = Counter(item["answer"] for item in single_choice_questions)
    assert max(answer_counts.values()) - min(answer_counts.values()) <= 1
    option_sets_by_scene: dict[str, list[frozenset[str]]] = {}
    for item in single_choice_questions:
        option_texts = [option["text"] for option in item["options"]]
        correct_text = next(
            option["text"] for option in item["options"] if option["key"] == item["answer"]
        )
        distractor_lengths = [
            len(option["text"])
            for option in item["options"]
            if option["key"] != item["answer"]
        ]
        assert len(option_texts) == len(set(option_texts)) == 4
        assert len(correct_text) / statistics.mean(distractor_lengths) <= 1.8
        assert not any(
            phrase in option for phrase in banned_phrases for option in option_texts
        )
        distractor_set = frozenset(
            option["text"] for option in item["options"] if option["key"] != item["answer"]
        )
        option_sets_by_scene.setdefault(item["scene"], []).append(distractor_set)
    assert all(
        len(items) == len(set(items)) for items in option_sets_by_scene.values()
    )


def test_task_quiz_uses_scene_and_fixed_three_plus_two_mix(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        task_response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "明天上午拆除悬挑式脚手架",
                "worker_ref": "工人01",
                "use_llm": False,
            },
        )
        task_id = task_response.json()["task_id"]
        response = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "工人01", "task_id": task_id},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scene"] == "scaffold_dismantling"
    assert len(body["questions"]) == 5
    assert sum(item["type"] == "single_choice" for item in body["questions"]) == 3
    assert sum(item["type"] == "true_false" for item in body["questions"]) == 2
    assert all("answer" not in item and "explanation" not in item for item in body["questions"])


def test_quiz_submission_creates_personal_wrong_record_without_pass_result(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        attempt = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "工人A", "scene": "opening_work"},
        ).json()
        answers = []
        for index, item in enumerate(attempt["questions"]):
            correct = app.state.question_bank.get(item["id"])["answer"]
            if index < 2:
                option_keys = [option["key"] for option in item["options"]]
                submitted = next(key for key in option_keys if key != correct)
            else:
                submitted = correct
            answers.append({"question_id": item["id"], "answer": submitted})
        result = client.post(
            f"/worker-assistant/quizzes/{attempt['id']}/submit",
            json={"answers": answers},
        )
        record = client.get("/worker-assistant/workers/工人A/learning-records")
        other = client.get("/worker-assistant/workers/工人B/learning-records")

    assert result.status_code == 200, result.text
    result_body = result.json()
    assert result_body["total"] == 5
    assert result_body["correct_count"] == 3
    assert "passed" not in result_body and "score" not in result_body
    assert all(item["evidence"]["clause"] for item in result_body["answers"])
    assert record.json()["active_wrong_count"] == 2
    assert len(record.json()["recommendations"]) == 2
    assert other.json()["active_wrong_count"] == 0


def test_learning_cannot_attach_another_workers_task(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        task = client.post(
            "/worker-assistant/messages",
            json={
                "worker_ref": "工人甲",
                "message": "明天下午在12层拆除外墙模板",
                "use_llm": False,
            },
        ).json()
        quiz = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "工人乙", "task_id": task["task_id"]},
        )
        qa = client.post(
            "/worker-assistant/qa",
            json={
                "worker_ref": "工人乙",
                "task_id": task["task_id"],
                "question": "模板拆除前要检查什么？",
                "use_llm": False,
            },
        )

    assert quiz.status_code == 400
    assert qa.status_code == 400
    assert "不属于当前工人标识" in quiz.json()["detail"]
    assert "不属于当前工人标识" in qa.json()["detail"]


def test_safety_qa_is_traceable_and_does_not_mix_scaffold_erection_rule(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/qa",
            json={
                "worker_ref": "工人01",
                "question": "拆除脚手架时，连墙件能不能提前整层拆掉？",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_status"] == "answered"
    assert body["evidences"]
    combined = body["answer"] + " ".join(item["quote"] for item in body["evidences"])
    assert "逐层" in combined or "同步拆除" in combined
    assert "一次搭设高度" not in body["answer"]
    assert all(item["location"] for item in body["evidences"])


def test_safety_qa_refuses_unrelated_question_without_evidence(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/qa",
            json={
                "worker_ref": "工人01",
                "question": "食堂今天午餐吃什么菜？",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["answer_status"] == "insufficient_evidence"
    assert response.json()["evidences"] == []


def test_qa_rejects_opening_orientation_inferred_only_from_floor():
    task = {
        "work_content": "安装固定盖板",
        "work_location": "洞口",
        "work_floor": "10层",
    }

    assert _has_unsupported_opening_orientation_inference(
        "该洞口在10层，属于非竖向洞口。",
        "这个洞口盖板需要固定吗？",
        task,
    )
    assert _has_unsupported_opening_orientation_inference(
        "你要安装盖板，所以洞口应是非竖向且尺寸在适用范围内。",
        "这个洞口盖板需要固定吗？",
        task,
    )
    assert not _has_unsupported_opening_orientation_inference(
        "若为非竖向洞口，应先确认尺寸后按对应条款处理。",
        "这个洞口盖板需要固定吗？",
        task,
    )
