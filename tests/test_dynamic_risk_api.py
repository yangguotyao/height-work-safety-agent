import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.task_schedule import resolve_task_schedule


class MutableWeather:
    provider_name = "test-weather"

    def __init__(self, wind_speed: float = 8):
        self.wind_speed = wind_speed

    def get_forecast(self, work_time: str) -> dict:
        return {
            "status": "ok",
            "source": "测试天气",
            "project_name": "测试项目",
            "summary": f"{work_time}晴，最大风速约{self.wind_speed} km/h。",
            "forecast_window": work_time,
            "temperature_c": 24,
            "max_wind_speed_kmh": self.wind_speed,
            "precipitation": 0,
            "sky_conditions": ["CLEAR_DAY"],
            "alerts": [],
            "observed_at": "2026-08-23T08:00:00+08:00",
        }


def _create_task(client: TestClient, message: str, worker: str = "动态工人01") -> dict:
    response = client.post(
        "/worker-assistant/messages",
        json={
            "message": message,
            "worker_ref": worker,
            "team_ref": "模板班组",
            "use_llm": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_schedule_resolves_relative_and_explicit_dates():
    now = datetime(2026, 8, 23, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert resolve_task_schedule("明天下午", now=now) == ("2026-08-24", "afternoon")
    assert resolve_task_schedule("8月25日上午", now=now) == ("2026-08-25", "morning")
    assert resolve_task_schedule("2026-08-26夜间", now=now) == ("2026-08-26", "evening")


def test_default_dynamic_risk_dataset_has_40_reviewable_scenarios():
    path = Path("data/动态风险评估测试集/动态风险评估默认测试集.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    assert payload["level_policy"] == "deterministic"
    assert payload["review_status"] == "default_pending_professional_review"
    assert len(scenarios) == 40
    assert len({item["id"] for item in scenarios}) == 40
    assert {item["expected"] for item in scenarios} == {"red", "yellow", "green"}
    assert len({item["scene"] for item in scenarios}) >= 10
    assert all("controls" not in item for item in scenarios)


def test_safe_task_is_green_without_manual_confirmation(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        task = _create_task(client, "今天下午在12层拆除外墙模板")
        latest = client.get("/dynamic-risks/latest")

    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body["task_count"] == 1
    assert body["items"][0]["task_id"] == task["task_id"]
    assert body["items"][0]["risk_level"] == "green"
    assert "confirmations" not in body["items"][0]
    assert not any(
        trigger["source_type"] == "confirmation"
        for trigger in body["items"][0]["triggers"]
    )
    assert len(body["items"][0]["review_questions"]) == 5
    assert all(not text.endswith(">") for text in body["items"][0]["interventions"])


def test_same_input_does_not_create_another_version(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        _create_task(client, "今天下午在12层拆除外墙模板")
        first = client.get("/dynamic-risks/latest").json()
        refreshed = client.post(
            "/dynamic-risks/evaluate",
            json={"trigger_type": "weather_changed", "refresh_weather": True},
        ).json()
        runs = client.get("/dynamic-risks/runs").json()

    assert refreshed["id"] == first["id"]
    assert refreshed["version_created"] is False
    assert len(runs) == 1


def test_weather_change_creates_new_red_version(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        weather = MutableWeather()
        app.state.dynamic_risk_service.risk_cards.weather = weather
        _create_task(client, "今天上午拆除悬挑式脚手架")
        first = client.get("/dynamic-risks/latest").json()
        weather.wind_speed = 42
        changed = client.post(
            "/dynamic-risks/evaluate",
            json={"trigger_type": "weather_changed", "refresh_weather": True},
        ).json()

    assert changed["id"] != first["id"]
    assert changed["version_created"] is True
    assert changed["previous_run_id"] == first["id"]
    assert changed["version_change"]["escalated_count"] == 1
    assert changed["items"][0]["risk_level"] == "red"
    assert changed["items"][0]["change"]["status"] == "escalated"
    assert "风险等级由green变为red" in changed["items"][0]["change"]["explanation"]
    assert any(
        trigger["code"] == "WEATHER_STOP" and trigger["hard_block"]
        for trigger in changed["items"][0]["triggers"]
    )


def test_quiz_submission_automatically_creates_learning_changed_version(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        task = _create_task(client, "今天下午在12层拆除外墙模板", "动态学习工人")
        first_id = client.get("/dynamic-risks/latest").json()["id"]
        quiz = client.post(
            "/worker-assistant/quizzes",
            json={"worker_ref": "动态学习工人", "task_id": task["task_id"]},
        ).json()
        answers = []
        for index, question in enumerate(quiz["questions"]):
            correct = app.state.question_bank.get(question["id"])["answer"]
            keys = [option["key"] for option in question["options"]]
            submitted = next(key for key in keys if key != correct) if index < 2 else correct
            answers.append({"question_id": question["id"], "answer": submitted})
        submitted = client.post(
            f"/worker-assistant/quizzes/{quiz['id']}/submit",
            json={"answers": answers},
        )
        latest = client.get("/dynamic-risks/latest").json()

    assert submitted.status_code == 200, submitted.text
    assert latest["id"] != first_id
    assert latest["trigger_type"] == "learning_changed"
    assert latest["items"][0]["risk_level"] == "yellow"
    assert any(
        trigger["code"] == "LEARNING_WEAKNESS"
        for trigger in latest["items"][0]["triggers"]
    )


def test_daily_run_aggregates_multiple_teams_once(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        _create_task(client, "明天下午在12层拆除外墙模板", "动态工人甲")
        response = client.post(
            "/worker-assistant/messages",
            json={
                "message": "明天下午在屋面更换采光板",
                "worker_ref": "动态工人乙",
                "team_ref": "维修班组",
                "use_llm": False,
            },
        )
        assert response.status_code == 200, response.text
        date_value = app.state.database.fetch_one(
            "SELECT scheduled_date FROM work_tasks WHERE worker_ref = '动态工人甲'"
        )["scheduled_date"]
        run = client.post(
            "/dynamic-risks/evaluate",
            json={"assessment_date": date_value, "refresh_weather": True},
        )

    assert run.status_code == 200, run.text
    body = run.json()
    assert body["task_count"] == 2
    assert len({item["task_id"] for item in body["items"]}) == 2
    assert {item["team_ref"] for item in body["items"]} == {"模板班组", "维修班组"}


def test_test_workers_are_hidden_from_project_run_by_default(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        _create_task(client, "今天下午在12层拆除外墙模板", "页面测试工人")
        default_run = client.post(
            "/dynamic-risks/evaluate", json={"refresh_weather": False}
        ).json()
        explicit_run = client.post(
            "/dynamic-risks/evaluate",
            json={"refresh_weather": False, "include_test": True},
        ).json()
        latest_official = client.get("/dynamic-risks/latest").json()

    assert default_run["task_count"] == 0
    assert explicit_run["task_count"] == 1
    assert latest_official["id"] == default_run["id"]


def test_confirmation_update_endpoint_is_removed(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.patch(
            "/dynamic-risks/items/not-used/confirmations/not-used",
            json={"status": "confirmed", "confirmer_ref": "安全员01"},
        )

    assert response.status_code == 404


def test_semantic_duplicate_tasks_are_merged_without_deleting_sources(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        app.state.dynamic_risk_service.risk_cards.weather = MutableWeather()
        first = _create_task(client, "今天下午在12层拆除外墙模板", "重复工人")
        second = _create_task(client, "今天下午在12层拆除外墙模板", "重复工人")
        latest = client.get("/dynamic-risks/latest").json()
        stored = app.state.database.fetch_one("SELECT COUNT(*) count FROM work_tasks")

    assert first["task_id"] != second["task_id"]
    assert stored["count"] == 2
    assert latest["task_count"] == 1
    assert latest["raw_task_count"] == 2
    assert latest["data_quality"]["merged_record_count"] == 1
    assert latest["items"][0]["duplicate_count"] == 2
    assert set(latest["items"][0]["merged_task_ids"]) == {
        first["task_id"],
        second["task_id"],
    }
    assert any(
        trigger["code"] == "DATA_DUPLICATE"
        for trigger in latest["items"][0]["triggers"]
    )
