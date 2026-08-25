from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

from cursor_myagent_base.config import get_amap_api_key, get_amap_secret
from cursor_myagent_base.skills.errors import (
    AMAP_ERROR,
    HTTP_STATUS,
    INVALID_MODE,
    MISSING_API_KEY,
    MISSING_DESTINATION,
    MISSING_ORIGIN,
    NETWORK,
    PLACE_NOT_FOUND,
    skill_error,
)
from cursor_myagent_base.skills.normalize import canonical_city, canonical_mode, infer_city_from_place

GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
PLACE_URL = "https://restapi.amap.com/v3/place/text"
DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"
WALKING_URL = "https://restapi.amap.com/v3/direction/walking"
TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
BICYCLING_URL = "https://restapi.amap.com/v4/direction/bicycling"

_COORD_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")
_WAYPOINT_SPLIT = re.compile(r"[,，;；|]+")
_DAYS_RE = re.compile(r"(\d+)")

_MODE_LABELS = {
    "driving": "驾车",
    "walking": "步行",
    "transit": "公交/地铁",
    "bicycling": "骑行",
}


def amap_text(value: Any) -> str:
    if value is None or isinstance(value, list):
        return ""
    return str(value).strip()


def looks_like_lnglat(value: str) -> bool:
    return bool(_COORD_RE.match(value or ""))


def format_lnglat(value: str) -> str:
    lng, lat = (part.strip() for part in value.split(",", 1))
    return f"{float(lng):.6f},{float(lat):.6f}"


def split_waypoints(raw: str) -> list[str]:
    parts = [item.strip() for item in _WAYPOINT_SPLIT.split(raw or "") if item.strip()]
    return parts[:16]


def parse_days(raw: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 1
    match = _DAYS_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)), 3))
    if "两" in text or "二" in text:
        return 2
    if "三" in text:
        return 3
    return 1


def format_distance(meters: Any) -> str:
    try:
        value = float(meters)
    except (TypeError, ValueError):
        return "—"
    if value >= 1000:
        return f"{value / 1000:.1f}公里"
    return f"{int(value)}米"


def format_duration(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    if total < 60:
        return f"{max(total, 0)}秒"
    minutes = total // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    return f"{minutes}分钟"


def _sign(params: dict[str, str], secret: str) -> str:
    pairs = sorted((key, value) for key, value in params.items() if key != "sig" and value)
    raw = "&".join(f"{key}={value}" for key, value in pairs) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _amap_get(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    api_key: str,
    secret: str,
) -> dict:
    query = {key: value for key, value in params.items() if value}
    query["key"] = api_key
    query["output"] = "JSON"
    if secret:
        query["sig"] = _sign(query, secret)
    response = client.get(url, params=query)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return {}
    return data


def _amap_failed(data: dict) -> str | None:
    status = amap_text(data.get("status"))
    if status == "1":
        return None
    info = amap_text(data.get("info") or data.get("errmsg")) or "高德接口调用失败"
    infocode = amap_text(data.get("infocode") or data.get("errcode"))
    extra = f"（{infocode}）" if infocode else ""
    if infocode in {"10001", "10004"} or "USERKEY" in info.upper() or "INVALID_USER_KEY" in info:
        return skill_error(
            AMAP_ERROR,
            f"高德 Key 无效或类型不匹配{extra}。请在 .env 填入 Web 服务类型的 AMAP_API_KEY。",
        )
    if infocode == "10003" or "OVER_LIMIT" in info:
        return skill_error(AMAP_ERROR, f"高德接口今日调用量已超限{extra}。")
    return skill_error(AMAP_ERROR, f"{info}{extra}")


def _geocode(
    client: httpx.Client,
    address: str,
    api_key: str,
    secret: str,
    city_hint: str = "",
) -> dict[str, str] | str:
    text = address.strip()
    if looks_like_lnglat(text):
        location = format_lnglat(text)
        return {
            "name": location,
            "location": location,
            "city": city_hint,
            "address": location,
        }
    data = _amap_get(
        client,
        GEO_URL,
        {"address": text, "city": city_hint},
        api_key,
        secret,
    )
    failed = _amap_failed(data)
    if failed:
        return failed
    geocodes = data.get("geocodes") or []
    if not geocodes:
        poi = _search_pois(client, text, city_hint, api_key, secret, limit=1)
        if isinstance(poi, str):
            return poi
        if poi:
            return poi[0]
        return skill_error(
            PLACE_NOT_FOUND,
            f"找不到地点「{text}」。请把这句话告诉用户并请其确认地名，不要用同一地名重试。",
        )
    item = geocodes[0]
    location = amap_text(item.get("location"))
    if not location:
        return skill_error(PLACE_NOT_FOUND, f"地点「{text}」没有坐标。")
    city = amap_text(item.get("city")) or amap_text(item.get("province")) or city_hint
    return {
        "name": amap_text(item.get("formatted_address")) or text,
        "location": location,
        "city": city,
        "address": amap_text(item.get("formatted_address")) or text,
    }


def _search_pois(
    client: httpx.Client,
    keywords: str,
    city: str,
    api_key: str,
    secret: str,
    *,
    limit: int = 8,
) -> list[dict[str, str]] | str:
    params = {
        "keywords": keywords or "风景名胜",
        "city": city,
        "citylimit": "true" if city else "false",
        "offset": str(max(1, min(limit, 20))),
        "page": "1",
        "extensions": "base",
    }
    data = _amap_get(client, PLACE_URL, params, api_key, secret)
    failed = _amap_failed(data)
    if failed:
        return failed
    results: list[dict[str, str]] = []
    for item in data.get("pois") or []:
        location = amap_text(item.get("location"))
        name = amap_text(item.get("name"))
        if not location or not name:
            continue
        results.append(
            {
                "name": name,
                "location": location,
                "city": amap_text(item.get("cityname")) or city,
                "address": amap_text(item.get("address")) or name,
                "type": amap_text(item.get("type")),
            }
        )
        if len(results) >= limit:
            break
    return results


def _direction_urls(mode: str) -> str:
    return {
        "walking": WALKING_URL,
        "transit": TRANSIT_URL,
        "bicycling": BICYCLING_URL,
    }.get(mode, DRIVING_URL)


def _plan_route(
    client: httpx.Client,
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    api_key: str,
    secret: str,
    waypoints: list[dict[str, str]] | None = None,
) -> dict | str:
    params = {
        "origin": origin["location"],
        "destination": destination["location"],
    }
    if mode == "transit":
        params["city"] = origin.get("city") or destination.get("city") or ""
        dest_city = destination.get("city") or ""
        if dest_city and dest_city != params["city"]:
            params["cityd"] = dest_city
        params["strategy"] = "3"
        params["nightflag"] = "0"
    elif mode == "driving":
        if waypoints:
            params["waypoints"] = ";".join(item["location"] for item in waypoints)
        params["extensions"] = "base"
        params["strategy"] = "0"
    data = _amap_get(client, _direction_urls(mode), params, api_key, secret)
    if mode == "bicycling":
        errcode = data.get("errcode")
        if errcode not in (0, "0", None):
            return skill_error(
                AMAP_ERROR,
                amap_text(data.get("errmsg") or data.get("errdetail")) or "骑行路径规划失败。",
            )
        return data
    failed = _amap_failed(data)
    return failed or data


def _step_lines(steps: list, *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for index, step in enumerate(steps[:limit], start=1):
        instruction = amap_text(step.get("instruction")) or amap_text(step.get("road"))
        if not instruction:
            continue
        distance = format_distance(step.get("distance"))
        lines.append(f"{index}. {instruction}（{distance}）")
    if len(steps) > limit:
        lines.append(f"……其余 {len(steps) - limit} 步已省略")
    return lines


def _short_line_name(name: str) -> str:
    text = (name or "").strip()
    if "（" in text:
        text = text.split("（", 1)[0]
    if "(" in text:
        text = text.split("(", 1)[0]
    return text.strip() or (name or "").strip()


def _ride_stop_text(via_num: Any) -> str:
    raw = amap_text(via_num)
    if raw == "":
        return ""
    try:
        via = int(float(raw))
    except ValueError:
        return ""
    rides = via + 1
    if rides <= 1:
        return "，乘坐 1 站（中间不停）"
    return f"，乘坐 {rides} 站"


def _next_board_stop(segments: list, start_index: int) -> str:
    for segment in segments[start_index:]:
        for line in (segment.get("bus") or {}).get("buslines") or []:
            stop = amap_text((line.get("departure_stop") or {}).get("name"))
            if stop:
                return stop
        railway = segment.get("railway") or {}
        stop = amap_text((railway.get("departure_stop") or {}).get("name"))
        if stop:
            return stop
    return ""


def _format_transit(data: dict, origin: dict[str, str], destination: dict[str, str]) -> str:
    route = data.get("route") or {}
    transits = route.get("transits") or []
    if not transits:
        return skill_error(AMAP_ERROR, "没有可用的公交/地铁方案。")
    blocks = [
        f"起点：{origin['name']}",
        f"终点：{destination['name']}",
        f"出行方式：{_MODE_LABELS['transit']}",
    ]
    for index, transit in enumerate(transits[:2], start=1):
        cost = amap_text(transit.get("cost")) or "—"
        blocks.append(
            f"\n方案{index}：约 {format_duration(transit.get('duration'))}，"
            f"步行 {format_distance(transit.get('walking_distance'))}，票价约 {cost} 元"
        )
        step_no = 1
        segments = list(transit.get("segments") or [])
        for seg_index, segment in enumerate(segments):
            walking = segment.get("walking") or {}
            walk_distance = walking.get("distance")
            if walk_distance and float(walk_distance or 0) > 0:
                toward = _next_board_stop(segments, seg_index) or destination.get("name") or "终点"
                blocks.append(f"{step_no}. 步行 {format_distance(walk_distance)} 至 {toward}")
                step_no += 1
            for line in (segment.get("bus") or {}).get("buslines") or []:
                name = _short_line_name(amap_text(line.get("name")))
                start = amap_text((line.get("departure_stop") or {}).get("name"))
                end = amap_text((line.get("arrival_stop") or {}).get("name"))
                extra = _ride_stop_text(line.get("via_num"))
                blocks.append(f"{step_no}. 乘坐 {name}：{start} → {end}{extra}")
                step_no += 1
            railway = segment.get("railway") or {}
            rail_name = amap_text(railway.get("name") or railway.get("trip"))
            if rail_name:
                blocks.append(f"{step_no}. 火车/城际：{rail_name}")
                step_no += 1
    return "\n".join(blocks)


def _format_bicycling(data: dict, origin: dict[str, str], destination: dict[str, str]) -> str:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    paths = (payload.get("paths") if isinstance(payload, dict) else None) or []
    if not paths:
        return skill_error(AMAP_ERROR, "没有可用的骑行路线。")
    path = paths[0]
    lines = [
        f"起点：{origin['name']}",
        f"终点：{destination['name']}",
        f"出行方式：{_MODE_LABELS['bicycling']}",
        f"距离：{format_distance(path.get('distance'))}",
        f"预计耗时：{format_duration(path.get('duration'))}",
        "主要路段：",
        *_step_lines(path.get("steps") or []),
    ]
    return "\n".join(lines)


def _format_path_route(
    data: dict,
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    waypoints: list[dict[str, str]] | None = None,
) -> str:
    route = data.get("route") or {}
    paths = route.get("paths") or []
    if not paths:
        return skill_error(AMAP_ERROR, "没有可用的路线方案。")
    path = paths[0]
    lines = [
        f"起点：{origin['name']}",
        f"终点：{destination['name']}",
        f"出行方式：{_MODE_LABELS.get(mode, mode)}",
    ]
    if waypoints:
        lines.append("途经：" + " → ".join(item["name"] for item in waypoints))
    lines.append(f"距离：{format_distance(path.get('distance'))}")
    lines.append(f"预计耗时：{format_duration(path.get('duration'))}")
    tolls = amap_text(path.get("tolls"))
    if mode == "driving" and tolls and tolls not in {"0", "0.00"}:
        lines.append(f"过路费：约 {tolls} 元")
    lines.append("主要路段：")
    lines.extend(_step_lines(path.get("steps") or []))
    return "\n".join(lines)


def _format_route(
    data: dict,
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    waypoints: list[dict[str, str]] | None = None,
) -> str:
    if mode == "transit":
        return _format_transit(data, origin, destination)
    if mode == "bicycling":
        return _format_bicycling(data, origin, destination)
    return _format_path_route(data, origin, destination, mode, waypoints)


_POI_NAME = re.compile(r"(站|机场|公园|广场|故宫|博物馆|大学|医院|景区|码头|政府|大厦)")


def _prefer_poi_search(name: str) -> bool:
    text = (name or "").strip()
    if not text or looks_like_lnglat(text):
        return False
    if re.search(r"\d+号", text):
        return False
    return bool(_POI_NAME.search(text)) or len(text) <= 8


def _resolve_place(
    client: httpx.Client,
    name: str,
    api_key: str,
    secret: str,
    city_hint: str = "",
) -> dict[str, str] | str:
    if _prefer_poi_search(name):
        pois = _search_pois(client, name, city_hint, api_key, secret, limit=1)
        if isinstance(pois, list) and pois:
            return pois[0]
    return _geocode(client, name, api_key, secret, city_hint=city_hint)


def _plan_itinerary(
    client: httpx.Client,
    *,
    city: str,
    keywords: str,
    days: int,
    mode: str,
    api_key: str,
    secret: str,
) -> str:
    limit = min(4 * days, 8)
    query = keywords or "风景名胜"
    pois = _search_pois(client, query, city, api_key, secret, limit=limit)
    if isinstance(pois, str):
        return pois
    if not pois:
        return skill_error(
            PLACE_NOT_FOUND,
            f"在「{city or '全国'}」找不到与「{query}」相关的地点。请换个关键词或城市。",
        )

    lines = [
        f"城市：{city or amap_text(pois[0].get('city')) or '—'}",
        f"主题：{query}",
        f"建议天数：{days} 天",
        "推荐地点：",
    ]
    per_day = max(1, (len(pois) + days - 1) // days)
    for index, poi in enumerate(pois):
        day = min(days, index // per_day + 1)
        extra = f"；{poi['type']}" if poi.get("type") else ""
        address = poi["address"]
        lines.append(f"第{day}天 {index + 1}. {poi['name']}（{address}{extra}）")

    if len(pois) >= 2 and mode != "transit":
        origin, *middle = pois
        destination = middle.pop() if middle else pois[-1]
        if destination is origin:
            destination = pois[-1]
        waypoints = middle if mode == "driving" else []
        routed = _plan_route(
            client,
            origin,
            destination,
            "driving" if mode == "transit" else mode,
            api_key,
            secret,
            waypoints=waypoints or None,
        )
        if isinstance(routed, str):
            lines.append("\n串联路线未能生成，已仅返回地点列表。")
            lines.append(routed)
        else:
            lines.append("\n串联路线（按推荐顺序）：")
            lines.append(
                _format_route(
                    routed,
                    origin,
                    destination,
                    "driving" if mode == "transit" else mode,
                    waypoints or None,
                )
            )
    return "\n".join(lines)


def run(arguments: dict | None = None) -> str:
    """高德路线 / 行程规划。路线需 origin+destination；行程需 city 或 keywords。"""
    arguments = arguments or {}
    origin_raw = str(
        arguments.get("origin")
        or arguments.get("from")
        or arguments.get("start")
        or ""
    ).strip()
    destination_raw = str(
        arguments.get("destination")
        or arguments.get("dest")
        or arguments.get("end")
        or ""
    ).strip()
    city = canonical_city(
        str(arguments.get("city") or arguments.get("location") or "").strip()
    )
    keywords = str(
        arguments.get("keywords") or arguments.get("theme") or arguments.get("query") or ""
    ).strip()
    waypoints_raw = str(arguments.get("waypoints") or arguments.get("via") or "").strip()
    mode_raw = str(arguments.get("mode") or arguments.get("travel_mode") or "").strip()
    days = parse_days(str(arguments.get("days") or arguments.get("day") or ""))
    mode = canonical_mode(mode_raw)
    api_key = get_amap_api_key()
    secret = get_amap_secret()

    if not api_key:
        return skill_error(
            MISSING_API_KEY,
            "未配置 AMAP_API_KEY。请在 .env 中填入高德开放平台 Web 服务 Key。",
        )
    if mode_raw and not mode:
        return skill_error(
            INVALID_MODE,
            f"不支持的出行方式「{mode_raw}」。请使用驾车、步行、公交或骑行。",
        )
    has_route = bool(origin_raw and destination_raw)
    has_itinerary = bool(city or keywords)
    if not has_route and not has_itinerary:
        if origin_raw and not destination_raw:
            return skill_error(
                MISSING_DESTINATION,
                "缺少终点。请询问用户要去哪里，不要猜测，也不要再次 load_skill。",
            )
        if destination_raw and not origin_raw:
            return skill_error(
                MISSING_ORIGIN,
                "缺少起点。请询问用户从哪里出发，不要猜测，也不要再次 load_skill。",
            )
        return skill_error(
            MISSING_ORIGIN,
            "缺少起点和终点（或城市）。请询问从哪到哪，或要规划哪座城市的行程。",
        )

    try:
        with httpx.Client(timeout=20.0) as client:
            if has_route:
                origin = _resolve_place(
                    client,
                    origin_raw,
                    api_key,
                    secret,
                    city_hint=infer_city_from_place(origin_raw) or city,
                )
                if isinstance(origin, str):
                    return origin
                destination = _resolve_place(
                    client,
                    destination_raw,
                    api_key,
                    secret,
                    city_hint=infer_city_from_place(destination_raw)
                    or origin.get("city")
                    or city,
                )
                if isinstance(destination, str):
                    return destination
                stops: list[dict[str, str]] = []
                for waypoint in split_waypoints(waypoints_raw):
                    resolved = _resolve_place(
                        client,
                        waypoint,
                        api_key,
                        secret,
                        city_hint=infer_city_from_place(waypoint),
                    )
                    if isinstance(resolved, str):
                        return resolved
                    stops.append(resolved)
                routed = _plan_route(
                    client,
                    origin,
                    destination,
                    mode,
                    api_key,
                    secret,
                    waypoints=stops or None,
                )
                if isinstance(routed, str):
                    return routed
                return _format_route(routed, origin, destination, mode, stops or None)

            return _plan_itinerary(
                client,
                city=city,
                keywords=keywords,
                days=days,
                mode=mode,
                api_key=api_key,
                secret=secret,
            )
    except httpx.HTTPStatusError as exc:
        return skill_error(HTTP_STATUS, f"高德接口返回 HTTP {exc.response.status_code}。")
    except httpx.HTTPError as exc:
        return skill_error(NETWORK, f"网络错误（{exc}）。")


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1].startswith("{"):
        print(run(json.loads(sys.argv[1])))
    else:
        origin = sys.argv[1] if len(sys.argv) > 1 else ""
        destination = sys.argv[2] if len(sys.argv) > 2 else ""
        mode = sys.argv[3] if len(sys.argv) > 3 else ""
        print(run({"origin": origin, "destination": destination, "mode": mode}))
