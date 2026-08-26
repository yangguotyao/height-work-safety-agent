from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.risk_card_service import (
    _rule_matches_task_action,
    filter_card_for_task_action,
)


def test_scaffold_action_filter_infers_legacy_task_action():
    legacy_task = {
        "task_action": "",
        "normalized_task": "脚手架拆除",
        "work_content": "拆除脚手架",
    }
    erection_rule = {
        "rule_id": "JSJ-006",
        "requirement": "落地作业脚手架应与工程施工同步搭设，一次搭设高度不得超过规定。",
    }
    dismantling_rule = {
        "rule_id": "JSJ-020",
        "requirement": "脚手架拆除时，连墙件应随架体逐层同步拆除。",
    }
    assert not _rule_matches_task_action(legacy_task, erection_rule)
    assert _rule_matches_task_action(legacy_task, dismantling_rule)
    filtered = filter_card_for_task_action(
        legacy_task,
        {
            "pre_job_checks": [
                erection_rule["requirement"],
                dismantling_rule["requirement"],
                "搭设和拆除脚手架时均应设置警戒区。",
            ],
            "prohibited_behaviors": [],
        },
    )
    assert erection_rule["requirement"] not in filtered["pre_job_checks"]
    assert dismantling_rule["requirement"] in filtered["pre_job_checks"]
    assert "搭设和拆除脚手架时均应设置警戒区。" in filtered["pre_job_checks"]

def test_worker_assistant_conversation_and_risk_card(test_settings):
    app = create_app(test_settings)

    with TestClient(app) as client:
        first = client.post(
            "/worker-assistant/messages",
            json={
                "message": "今天下午我要去拆模板了",
                "worker_ref": "工人01",
                "team_ref": "模板班组",
                "use_llm": False,
            },
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["status"] == "collecting"
        assert first_body["missing_fields"] == ["location", "floor"]
        assert "具体作业位置" in first_body["assistant_message"]
        assert "作业楼层" in first_body["assistant_message"]

        second = client.post(
            "/worker-assistant/messages",
            json={
                "session_id": first_body["session_id"],
                "message": "12层外墙",
                "use_llm": False,
            },
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["status"] == "completed"
        assert body["missing_fields"] == []
        assert body["draft"]["location"] == "外墙"
        assert body["draft"]["floor"] == "12层"
        card = body["risk_card"]
        assert card["work_content"] == "拆模板"
        assert card["weather"]["status"] == "unconfigured"
        assert card["main_risks"]
        assert card["pre_job_checks"]
        assert all(not item.endswith(">") for item in card["pre_job_checks"])
        assert not any(
            term in " ".join(card["prohibited_behaviors"])
            for term in ("轻质型材", "门窗作业", "自制吊篮")
        )
        assert card["similar_accidents"]
        assert any(item["evidence_type"] == "standard" for item in card["evidences"])
        assert any("天气" in item for item in card["human_confirmations"])

        stored = client.get(f"/worker-assistant/sessions/{body['session_id']}")
        assert stored.status_code == 200
        assert stored.json()["risk_card"]["id"] == card["id"]

        risk_card = client.get(f"/worker-assistant/risk-cards/{body['task_id']}")
        assert risk_card.status_code == 200
        assert risk_card.json()["task_id"] == body["task_id"]


def test_worker_assistant_builds_card_from_complete_message(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "今天下午在12层拆除外墙模板",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["draft"] == {
        "work_content": "拆除外墙模板",
        "location": "外墙",
        "floor": "12层",
        "work_time": "今天下午",
        "normalized_task": "模板拆除",
        "task_action": "拆除",
        "equipment_type": None,
        "scenes": body["draft"]["scenes"],
    }


def test_scaffold_dismantling_card_excludes_erection_only_rules(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "明天上午拆除悬挑式脚手架",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["missing_fields"] == []
    assert body["draft"]["location"] == "脚手架作业区（现场确认）"
    assert body["draft"]["floor"] == "不按楼层定位"
    assert body["draft"]["equipment_type"] == "悬挑式脚手架"
    card = body["risk_card"]
    reminders = " ".join([*card["pre_job_checks"], *card["prohibited_behaviors"]])
    assert "自上而下" in reminders or "逐层" in reminders
    assert "一次搭设高度" not in reminders
    assert "同步搭设" not in reminders
    assert "随架体同步安装" not in reminders
    assert any("警戒范围" in item for item in card["human_confirmations"])


def test_colloquial_scaffold_task_only_asks_for_scaffold_type(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "明天上午去拆架子",
                "worker_ref": "工人01",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "collecting"
    assert body["missing_fields"] == ["equipment_type"]
    assert body["draft"]["normalized_task"] == "脚手架拆除"
    assert "什么类型的脚手架" in body["assistant_message"]


def test_scaffold_erection_card_excludes_dismantling_only_rules(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "今晚搭设落地式脚手架",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["draft"]["work_time"] == "今天晚上"
    card = body["risk_card"]
    reminders = " ".join([*card["pre_job_checks"], *card["prohibited_behaviors"]])
    assert "同步搭设" in reminders or "一次搭设高度" in reminders
    assert "自上而下" not in reminders
    assert "逐层拆除" not in reminders
    assert "高空抛掷拆除" not in reminders


def test_basket_cleaning_card_excludes_dismantling_only_rules(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "后天下午在东立面用吊篮清洗外墙30米高处",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    card = body["risk_card"]
    reminders = " ".join([*card["pre_job_checks"], *card["prohibited_behaviors"]])
    assert "拆卸下的物料" not in reminders
    assert "自上而下" not in reminders


def test_roof_skylight_replacement_is_normalized_as_repair_action(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "后天下午在屋面更换采光板",
                "use_llm": False,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["draft"]["work_content"] == "更换采光板"
    assert body["draft"]["normalized_task"] == "采光板更换"
    assert body["draft"]["task_action"] == "维修"
    assert body["risk_card"]["similar_accidents"][0]["case_id"] == "GZSG-042"
