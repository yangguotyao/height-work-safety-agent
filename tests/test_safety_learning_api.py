import statistics
from collections import Counter

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.safety_learning import (
    _has_unsupported_opening_orientation_inference,
)


def test_internal_question_bank_has_required_scene_coverage(test_settings):
    app = create_app(test_settings)
    with TestClient(app):
        assert len(app.state.question_bank.questions) == 130

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


def test_quiz_and_learning_endpoints_are_removed(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        quiz = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "工人01", "scene": "opening_work"},
        )
        learning = client.get("/worker-assistant/workers/工人01/learning-records")
        bank = client.get("/worker-assistant/question-bank/status")

    assert quiz.status_code == 404
    assert learning.status_code == 404
    assert bank.status_code == 404


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
