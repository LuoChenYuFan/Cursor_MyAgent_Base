from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from cursor_myagent_base.config import get_openweather_api_key
from cursor_myagent_base.skills.errors import (
    CITY_NOT_FOUND,
    FORECAST_NOT_FOUND,
    HTTP_STATUS,
    INVALID_WHEN,
    MISSING_API_KEY,
    MISSING_CITY,
    NETWORK,
    skill_error,
)
from cursor_myagent_base.skills.normalize import (
    canonical_when,
    city_country,
    city_geocode_query,
    pick_geo_place,
)

GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


def _format_current(data: dict, location: str) -> str:
    weather_list = data.get("weather") or []
    description = weather_list[0].get("description", "未知") if weather_list else "未知"
    main = data.get("main") or {}
    wind = data.get("wind") or {}
    return (
        f"地点：{location}\n"
        f"时段：今天实况\n"
        f"天气：{description}\n"
        f"气温：{main.get('temp', '—')}°C（体感 {main.get('feels_like', '—')}°C）\n"
        f"湿度：{main.get('humidity', '—')}%\n"
        f"风速：{wind.get('speed', '—')} m/s"
    )


def _format_forecast(data: dict, location: str, when: str) -> str:
    tz_offset = int((data.get("city") or {}).get("timezone") or 0)
    tz = timezone(timedelta(seconds=tz_offset))
    today = datetime.now(tz).date()
    target = today + timedelta(days=1) if when == "tomorrow" else today
    label = "明天预报" if when == "tomorrow" else "今天预报"

    items: list[dict] = []
    for item in data.get("list") or []:
        dt = datetime.fromtimestamp(int(item["dt"]), tz=tz)
        if dt.date() == target:
            items.append(item)
    if not items:
        return skill_error(
            FORECAST_NOT_FOUND,
            f"没有「{label}」的数据。请把这句话告诉用户，不要用同一地点重试。",
        )

    temps = [float((item.get("main") or {}).get("temp")) for item in items if item.get("main")]
    mid = min(
        items,
        key=lambda item: abs(datetime.fromtimestamp(int(item["dt"]), tz=tz).hour - 12),
    )
    weather_list = mid.get("weather") or []
    description = weather_list[0].get("description", "未知") if weather_list else "未知"
    wind = mid.get("wind") or {}
    high = max(temps) if temps else "—"
    low = min(temps) if temps else "—"
    midday = (mid.get("main") or {}).get("temp", "—")
    return (
        f"地点：{location}\n"
        f"时段：{label}\n"
        f"天气：{description}\n"
        f"最高气温：{high}°C\n"
        f"最低气温：{low}°C\n"
        f"中午气温：{midday}°C\n"
        f"风速：{wind.get('speed', '—')} m/s"
    )


def run(arguments: dict | None = None) -> str:
    """查询指定城市的天气。arguments 需包含 city，可选 when=today/tomorrow。"""
    arguments = arguments or {}
    city = str(arguments.get("city") or arguments.get("location") or "").strip()
    when_raw = str(arguments.get("when") or arguments.get("date") or arguments.get("day") or "").strip()
    when = canonical_when(when_raw)
    api_key = get_openweather_api_key()
    if not api_key:
        return skill_error(
            MISSING_API_KEY,
            "未配置 OPENWEATHER_API_KEY。请在 .env 中填入 OpenWeatherMap 密钥。",
        )
    if not city:
        return skill_error(
            MISSING_CITY,
            "未提供城市名。请询问用户要查哪个城市，不要猜测，也不要再次 load_skill。",
        )
    if when_raw and not when:
        return skill_error(
            INVALID_WHEN,
            f"不支持的时段「{when_raw}」。目前只支持今天或明天。",
        )

    try:
        with httpx.Client(timeout=15.0) as client:
            query = city_geocode_query(city) or city
            expected_country = city_country(city)
            geo_resp = client.get(
                GEO_URL,
                params={"q": query, "limit": 5, "appid": api_key},
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            if not geo_data:
                return skill_error(
                    CITY_NOT_FOUND,
                    f"找不到城市「{city}」。请把这句话告诉用户并请其确认地名，不要用同一城市名重试。",
                )

            place = pick_geo_place(geo_data, expected_country=expected_country)
            if expected_country and (place.get("country") or "").upper() != expected_country:
                return skill_error(
                    CITY_NOT_FOUND,
                    f"找不到「{city}」在目标国家（{expected_country}）的地点，请确认地名。",
                )
            lat = place["lat"]
            lon = place["lon"]
            place_name = place.get("local_names", {}).get("zh") or place.get("name") or city
            country = place.get("country", "")
            admin = place.get("state") or ""
            location = " ".join(part for part in (place_name, admin, country) if part)

            if when == "tomorrow":
                forecast_resp = client.get(
                    FORECAST_URL,
                    params={
                        "lat": lat,
                        "lon": lon,
                        "appid": api_key,
                        "units": "metric",
                        "lang": "zh_cn",
                    },
                )
                forecast_resp.raise_for_status()
                return _format_forecast(forecast_resp.json(), location, when)

            weather_resp = client.get(
                WEATHER_URL,
                params={
                    "lat": lat,
                    "lon": lon,
                    "appid": api_key,
                    "units": "metric",
                    "lang": "zh_cn",
                },
            )
            weather_resp.raise_for_status()
            return _format_current(weather_resp.json(), location)
    except httpx.HTTPStatusError as exc:
        return skill_error(
            HTTP_STATUS,
            f"OpenWeatherMap 返回 HTTP {exc.response.status_code}。",
        )
    except httpx.HTTPError as exc:
        return skill_error(NETWORK, f"网络错误（{exc}）。")


if __name__ == "__main__":
    import sys

    city = sys.argv[1] if len(sys.argv) > 1 else ""
    when = sys.argv[2] if len(sys.argv) > 2 else ""
    print(run({"city": city, "when": when}))
