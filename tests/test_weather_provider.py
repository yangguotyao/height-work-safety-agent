import urllib.error
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from backend.app.config import Settings
from backend.app.services.risk_card_service import RiskCardService
from backend.app.services.weather_provider import CaiyunWeatherProvider, _select_hourly


class StubCaiyunWeatherProvider(CaiyunWeatherProvider):
    def _request(self):
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        return {
            "status": "ok",
            "server_time": int(datetime.now().timestamp()),
            "timezone": "Asia/Shanghai",
            "result": {
                "forecast_keypoint": "下午有雨并伴有较强风。",
                "realtime": {
                    "temperature": 26,
                    "wind": {"speed": 12},
                    "skycon": "CLOUDY",
                    "precipitation": {"local": {"intensity": 0}},
                },
                "hourly": {
                    "description": "下午有雨",
                    "wind": [
                        {"datetime": f"{today}T14:00+08:00", "speed": 42},
                    ],
                    "precipitation": [
                        {"datetime": f"{today}T14:00+08:00", "value": 0.5},
                    ],
                    "temperature": [
                        {"datetime": f"{today}T14:00+08:00", "value": 25},
                    ],
                    "skycon": [
                        {"datetime": f"{today}T14:00+08:00", "value": "RAIN"},
                    ],
                },
                "alert": {"content": []},
            },
        }


class RealtimeOnlyCaiyunWeatherProvider(CaiyunWeatherProvider):
    def _request(self):
        return {
            "status": "ok",
            "server_time": int(datetime.now().timestamp()),
            "timezone": "Asia/Shanghai",
            "result": {
                "realtime": {
                    "temperature": 29,
                    "wind": {"speed": 18},
                    "skycon": "CLOUDY",
                    "precipitation": {"local": {"intensity": 0}},
                },
                "hourly": {},
                "alert": {"content": []},
            },
        }


def test_caiyun_snapshot_and_scaffold_weather_rules():
    settings = Settings(
        model_provider="mock",
        caiyun_weather_token="test-token",
        project_longitude=116.3,
        project_latitude=39.9,
    )
    weather = StubCaiyunWeatherProvider(settings).get_forecast("今天下午")

    assert weather["status"] == "ok"
    assert weather["max_wind_speed_kmh"] == 42
    assert weather["precipitation"] == 0.5

    service = RiskCardService(None, None, None, None)  # type: ignore[arg-type]
    warnings = service._weather_warnings(  # noqa: SLF001
        {"scenes": ["施工脚手架", "脚手架搭设与拆除"]}, weather
    )
    assert any(item["rule_id"] == "JSJ-011" and item["level"] == "stop" for item in warnings)
    assert any(item["rule_id"] == "JSJ-012" and item["level"] == "stop" for item in warnings)


def test_selects_day_after_tomorrow_without_falling_back_to_today():
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    entries = [
        {"datetime": f"{(today + timedelta(days=offset)).isoformat()}T14:00+08:00", "value": offset}
        for offset in range(3)
    ]

    selected = _select_hourly(entries, "后天下午")

    assert [item["value"] for item in selected] == [2]


def test_elapsed_today_window_uses_realtime_instead_of_service_error():
    settings = Settings(
        model_provider="mock",
        caiyun_weather_token="test-token",
        project_longitude=116.3,
        project_latitude=39.9,
    )
    provider = RealtimeOnlyCaiyunWeatherProvider(settings)

    weather = provider.get_forecast("今天下午")
    future_weather = provider.get_forecast("明天下午")

    assert weather["status"] == "ok"
    assert weather["max_wind_speed_kmh"] == 18
    assert weather["temperature_c"] == 29
    assert "当前实况" in weather["summary"]
    assert future_weather["status"] == "error"
    assert "未返回该作业时段" in future_weather["summary"]


def test_weather_authentication_failure_has_actionable_summary(monkeypatch):
    settings = Settings(
        model_provider="mock",
        caiyun_weather_token="bad-token",
        project_longitude=116.3,
        project_latitude=39.9,
    )
    provider = CaiyunWeatherProvider(settings)

    def deny_request(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://example.test", 403, "forbidden", {}, None)

    monkeypatch.setattr(
        "backend.app.services.weather_provider.urllib.request.urlopen", deny_request
    )
    weather = provider.get_forecast("明天下午")

    assert weather["status"] == "error"
    assert "认证失败" in weather["summary"]
    assert "凭证" in weather["summary"]


def test_caiyun_request_retries_rate_limit_then_reuses_cache(monkeypatch):
    settings = Settings(
        model_provider="mock",
        caiyun_weather_token="test-token",
        project_longitude=116.3,
        project_latitude=39.9,
    )
    provider = CaiyunWeatherProvider(settings)
    calls = 0
    payload = b'{"status":"ok","result":{}}'

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(_request, *, timeout):
        nonlocal calls
        assert timeout == settings.weather_timeout_seconds
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError("https://example.test", 429, "rate", {}, None)
        return Response(payload)

    monkeypatch.setattr("backend.app.services.weather_provider.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "backend.app.services.weather_provider.urllib.request.urlopen", fake_urlopen
    )

    first = provider._request()  # noqa: SLF001
    second = provider._request()  # noqa: SLF001

    assert first == second
    assert calls == 2


def test_trace_precipitation_below_no_rain_threshold_does_not_stop_scaffold():
    weather = {
        "status": "ok",
        "summary": "多云",
        "max_wind_speed_kmh": 14.3,
        "precipitation": 0.045,
        "sky_conditions": ["PARTLY_CLOUDY_DAY", "CLEAR_DAY"],
        "alerts": [],
    }
    service = RiskCardService(None, None, None, None)  # type: ignore[arg-type]

    warnings = service._weather_warnings(  # noqa: SLF001
        {"scenes": ["施工脚手架", "脚手架搭设与拆除"]}, weather
    )

    assert not any(item["rule_id"] == "JSJ-012" for item in warnings)
    assert any(item["level"] == "info" for item in warnings)
