from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..config import Settings


class WeatherProvider(Protocol):
    provider_name: str

    def get_forecast(self, work_time: str) -> dict[str, Any]: ...


def _time_window(work_time: str) -> tuple[int, tuple[int, int] | None]:
    day_offset = 2 if "后天" in work_time else 1 if "明天" in work_time else 0
    windows = {
        "早上": (5, 10),
        "上午": (6, 12),
        "中午": (11, 14),
        "下午": (12, 18),
        "晚上": (18, 24),
        "夜间": (18, 24),
    }
    return day_offset, next((value for key, value in windows.items() if key in work_time), None)


def _select_hourly(entries: list[dict[str, Any]], work_time: str) -> list[dict[str, Any]]:
    if not entries:
        return []
    day_offset, hours = _time_window(work_time)
    target_date = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=day_offset)).date()
    selected: list[dict[str, Any]] = []
    for entry in entries:
        try:
            moment = datetime.fromisoformat(str(entry.get("datetime") or ""))
        except ValueError:
            continue
        if moment.date() != target_date:
            continue
        if hours is not None and not (hours[0] <= moment.hour < hours[1]):
            continue
        selected.append(entry)
    return selected


def _sky_condition_labels(values: list[str]) -> list[str]:
    labels = {
        "CLEAR_DAY": "晴",
        "CLEAR_NIGHT": "晴",
        "PARTLY_CLOUDY_DAY": "多云",
        "PARTLY_CLOUDY_NIGHT": "多云",
        "CLOUDY": "阴",
        "LIGHT_HAZE": "轻度霾",
        "MODERATE_HAZE": "中度霾",
        "HEAVY_HAZE": "重度霾",
        "LIGHT_RAIN": "小雨",
        "MODERATE_RAIN": "中雨",
        "HEAVY_RAIN": "大雨",
        "STORM_RAIN": "暴雨",
        "FOG": "雾",
        "LIGHT_SNOW": "小雪",
        "MODERATE_SNOW": "中雪",
        "HEAVY_SNOW": "大雪",
        "STORM_SNOW": "暴雪",
        "DUST": "浮尘",
        "SAND": "沙尘",
    }
    return list(dict.fromkeys(labels.get(value, value) for value in values))


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weather_error_summary(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code in {401, 403}:
            return "彩云天气认证失败，请检查天气凭证配置；本次天气条件需要人工确认。"
        if error.code == 429:
            return "彩云天气请求频率受限，请稍后重试；本次天气条件需要人工确认。"
        return f"彩云天气接口返回HTTP {error.code}；本次天气条件需要人工确认。"
    if isinstance(error, (TimeoutError, urllib.error.URLError)):
        return "连接彩云天气超时或网络不可达；本次天气条件需要人工确认。"
    if isinstance(error, json.JSONDecodeError):
        return "彩云天气返回数据格式异常；本次天气条件需要人工确认。"
    if "未取得任务时段小时预报" in str(error):
        return "彩云天气未返回该作业时段的小时预报；本次天气条件需要人工确认。"
    return "彩云天气返回失败状态；本次天气条件需要人工确认。"


class UnconfiguredWeatherProvider:
    provider_name = "caiyun-unconfigured"

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_forecast(self, work_time: str) -> dict[str, Any]:
        missing = []
        if not (
            self.settings.caiyun_weather_token
            or (self.settings.caiyun_app_key and self.settings.caiyun_app_secret)
        ):
            missing.append("彩云天气凭证")
        if self.settings.project_longitude is None or self.settings.project_latitude is None:
            missing.append("项目经纬度")
        return {
            "status": "unconfigured",
            "source": "彩云天气 v2.6",
            "project_name": self.settings.project_name,
            "summary": f"尚未配置{'、'.join(missing)}，本次天气条件需要人工确认。",
            "forecast_window": work_time,
            "temperature_c": None,
            "max_wind_speed_kmh": None,
            "precipitation": None,
            "sky_conditions": [],
            "alerts": [],
            "observed_at": None,
        }


class CaiyunWeatherProvider:
    provider_name = "caiyun-v2.6"
    base_url = "https://api.caiyunapp.com"
    cache_seconds = 300

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cached_payload: dict[str, Any] | None = None
        self._cache_expires_at = 0.0

    def _credential(self) -> str:
        return str(self.settings.caiyun_app_key or self.settings.caiyun_weather_token)

    def _headers(self, path: str, query: dict[str, str]) -> dict[str, str]:
        if not (self.settings.caiyun_app_key and self.settings.caiyun_app_secret):
            return {}
        nonce = str(uuid4())
        timestamp = str(int(time.time()))
        query_string = urllib.parse.urlencode(sorted(query.items()))
        string_to_sign = ":".join(
            ["GET", path, query_string, self.settings.caiyun_app_key, nonce, timestamp]
        )
        digest = hmac.new(
            self.settings.caiyun_app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return {
            "x-cy-nonce": nonce,
            "x-cy-timestamp": timestamp,
            "x-cy-signature": base64.urlsafe_b64encode(digest).decode("utf-8"),
        }

    def _request(self) -> dict[str, Any]:
        if self._cached_payload is not None and time.monotonic() < self._cache_expires_at:
            return self._cached_payload
        longitude = f"{self.settings.project_longitude:.6f}".rstrip("0").rstrip(".")
        latitude = f"{self.settings.project_latitude:.6f}".rstrip("0").rstrip(".")
        credential = urllib.parse.quote(self._credential(), safe="")
        path = f"/v2.6/{credential}/{longitude},{latitude}/weather"
        query = {
            "alert": "true",
            "dailysteps": "3",
            "hourlysteps": "72",
            "lang": "zh_CN",
            "unit": "metric:v2",
        }
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(sorted(query.items()))}"
        request = urllib.request.Request(url, headers=self._headers(path, query))
        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.settings.weather_timeout_seconds
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise
                time.sleep(1.0)
        if payload.get("status") != "ok":
            raise ValueError("彩云天气返回失败状态")
        self._cached_payload = payload
        self._cache_expires_at = time.monotonic() + self.cache_seconds
        return payload

    def get_forecast(self, work_time: str) -> dict[str, Any]:
        try:
            payload = self._request()
            result = payload.get("result") or {}
            realtime = result.get("realtime") or {}
            hourly = result.get("hourly") or {}
            selected_wind = _select_hourly(hourly.get("wind") or [], work_time)
            selected_precipitation = _select_hourly(
                hourly.get("precipitation") or [], work_time
            )
            selected_temperature = _select_hourly(
                hourly.get("temperature") or [], work_time
            )
            selected_skycon = _select_hourly(hourly.get("skycon") or [], work_time)
            day_offset, hours = _time_window(work_time)
            has_selected_hourly = any(
                (selected_wind, selected_precipitation, selected_temperature, selected_skycon)
            )
            realtime_wind = _float((realtime.get("wind") or {}).get("speed"))
            local_precipitation = _float(
                ((realtime.get("precipitation") or {}).get("local") or {}).get("intensity")
            )
            realtime_temperature = _float(realtime.get("temperature"))
            realtime_skycon = str(realtime.get("skycon") or "")
            has_realtime = any(
                value is not None
                for value in (realtime_wind, local_precipitation, realtime_temperature)
            ) or bool(realtime_skycon)
            use_realtime_fallback = day_offset == 0 and not has_selected_hourly and has_realtime
            if not has_selected_hourly and not use_realtime_fallback:
                raise ValueError("未取得任务时段小时预报")

            wind_values = [
                value
                for item in selected_wind
                if (value := _float(item.get("speed"))) is not None
            ]
            if day_offset == 0 and realtime_wind is not None:
                wind_values.append(realtime_wind)
            precipitation_values = [
                value
                for item in selected_precipitation
                if (value := _float(item.get("value"))) is not None
            ]
            if day_offset == 0 and local_precipitation is not None:
                precipitation_values.append(local_precipitation)
            temperatures = [
                value
                for item in selected_temperature
                if (value := _float(item.get("value"))) is not None
            ]
            if day_offset == 0 and not temperatures and realtime_temperature is not None:
                temperatures.append(realtime_temperature)
            sky_conditions = list(
                dict.fromkeys(
                    str(item.get("value") or "")
                    for item in selected_skycon
                    if str(item.get("value") or "")
                )
            )
            if (
                day_offset == 0
                and realtime_skycon
                and realtime_skycon not in sky_conditions
            ):
                sky_conditions.append(realtime_skycon)

            alert_block = result.get("alert") or {}
            alert_entries = alert_block.get("content") if isinstance(alert_block, dict) else []
            alerts = []
            for entry in alert_entries or []:
                if not isinstance(entry, dict):
                    continue
                title = str(entry.get("title") or entry.get("description") or "").strip()
                if title:
                    alerts.append(title)

            server_time = payload.get("server_time")
            observed_at = None
            if isinstance(server_time, (int, float)):
                observed_at = datetime.fromtimestamp(
                    server_time, ZoneInfo(str(payload.get("timezone") or "Asia/Shanghai"))
                ).isoformat()
            temperature_c = (
                round(sum(temperatures) / len(temperatures), 1) if temperatures else None
            )
            max_wind_speed_kmh = round(max(wind_values), 1) if wind_values else None
            precipitation = (
                round(max(precipitation_values), 3) if precipitation_values else None
            )
            if use_realtime_fallback:
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                reason = (
                    "任务时段已过"
                    if hours is not None and now.hour >= hours[1]
                    else "未取得任务时段小时预报"
                )
                summary_parts = [f"{work_time}{reason}，以下采用当前实况"]
            else:
                summary_parts = [f"{work_time}项目位置预报"]
            condition_labels = _sky_condition_labels(sky_conditions)
            if condition_labels:
                summary_parts.append("、".join(condition_labels))
            if temperature_c is not None:
                summary_parts.append(f"平均温度约{temperature_c}℃")
            if max_wind_speed_kmh is not None:
                summary_parts.append(f"最大风速约{max_wind_speed_kmh} km/h")
            summary = "，".join(summary_parts) + "。"
            return {
                "status": "ok",
                "source": "彩云天气 v2.6",
                "project_name": self.settings.project_name,
                "summary": summary,
                "forecast_window": work_time,
                "temperature_c": temperature_c,
                "max_wind_speed_kmh": max_wind_speed_kmh,
                "precipitation": precipitation,
                "sky_conditions": sky_conditions,
                "alerts": alerts,
                "observed_at": observed_at,
            }
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            return {
                "status": "error",
                "source": "彩云天气 v2.6",
                "project_name": self.settings.project_name,
                "summary": _weather_error_summary(exc),
                "forecast_window": work_time,
                "temperature_c": None,
                "max_wind_speed_kmh": None,
                "precipitation": None,
                "sky_conditions": [],
                "alerts": [],
                "observed_at": None,
            }


def build_weather_provider(settings: Settings) -> WeatherProvider:
    has_credentials = bool(
        settings.caiyun_weather_token
        or (settings.caiyun_app_key and settings.caiyun_app_secret)
    )
    has_location = settings.project_longitude is not None and settings.project_latitude is not None
    if has_credentials and has_location:
        return CaiyunWeatherProvider(settings)
    return UnconfiguredWeatherProvider(settings)
