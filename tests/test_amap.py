from __future__ import annotations

from unittest.mock import patch

from cursor_myagent_base.skills.errors import skill_error_code
from cursor_myagent_base.skills.loader import format_catalog, load_skills
from cursor_myagent_base.skills.normalize import canonical_mode
from cursor_myagent_base.skills.amap.scripts.plan_trip import (
    amap_text,
    format_distance,
    format_duration,
    looks_like_lnglat,
    parse_days,
    run,
    split_waypoints,
    _format_transit,
    _ride_stop_text,
    _short_line_name,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, params: dict | None = None):
        return self.handler(url, params or {})


def test_catalog_includes_amap() -> None:
    skills = load_skills()
    assert "amap" in skills
    catalog = format_catalog()
    assert "amap:" in catalog
    assert "行程" in catalog or "路线" in catalog
    print("通过：目录里能发现高德行程 Skill")


def test_canonical_mode() -> None:
    assert canonical_mode("") == "driving"
    assert canonical_mode("公交") == "transit"
    assert canonical_mode("开车") == "driving"
    assert canonical_mode("步行") == "walking"
    assert canonical_mode("骑行") == "bicycling"
    assert canonical_mode("飞船") == ""
    print("通过：出行方式能归一")


def test_helpers() -> None:
    assert amap_text([]) == ""
    assert amap_text("北京") == "北京"
    assert looks_like_lnglat("116.397026,39.918058")
    assert not looks_like_lnglat("故宫")
    assert split_waypoints("外滩，陆家嘴;豫园") == ["外滩", "陆家嘴", "豫园"]
    assert parse_days("两天") == 2
    assert format_distance(1500) == "1.5公里"
    assert format_duration(3600) == "1小时"
    print("通过：高德文本与格式化辅助函数")


def test_missing_api_key() -> None:
    with patch("cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key", return_value=""):
        text = run({"origin": "北京南站", "destination": "故宫"})
    assert skill_error_code(text) == "MISSING_API_KEY"
    print("通过：未配置 Key 会报错")


def test_missing_origin() -> None:
    with patch(
        "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
        return_value="test-key",
    ):
        text = run({"destination": "故宫"})
    assert skill_error_code(text) == "MISSING_ORIGIN"
    print("通过：只有终点时会要求补起点")


def test_invalid_mode() -> None:
    with patch(
        "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
        return_value="test-key",
    ):
        text = run({"origin": "北京南站", "destination": "故宫", "mode": "飞船"})
    assert skill_error_code(text) == "INVALID_MODE"
    print("通过：不支持的出行方式会报错")


def _geo_payload(address: str, location: str) -> dict:
    return {
        "status": "1",
        "info": "OK",
        "geocodes": [
            {
                "formatted_address": address,
                "location": location,
                "city": "北京市",
                "citycode": "010",
            }
        ],
    }


def test_driving_route_mocked() -> None:
    locations = {
        "北京南站": "116.378518,39.865246",
        "故宫": "116.397026,39.918058",
    }

    def handler(url: str, params: dict) -> FakeResponse:
        if "geocode/geo" in url:
            address = params.get("address") or ""
            return FakeResponse(_geo_payload(address, locations.get(address, "116.40,39.90")))
        if "place/text" in url:
            keyword = params.get("keywords") or ""
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "pois": [
                        {
                            "name": keyword,
                            "location": locations.get(keyword, "116.40,39.90"),
                            "address": keyword,
                            "cityname": "北京市",
                            "type": "交通设施服务",
                        }
                    ],
                }
            )
        if "direction/driving" in url:
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "route": {
                        "paths": [
                            {
                                "distance": "8500",
                                "duration": "1500",
                                "tolls": "0",
                                "steps": [
                                    {
                                        "instruction": "向北行驶进入南二环",
                                        "distance": "1200",
                                    },
                                    {
                                        "instruction": "到达故宫",
                                        "distance": "300",
                                    },
                                ],
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"未预期的请求：{url}")

    with (
        patch(
            "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
            return_value="test-key",
        ),
        patch("cursor_myagent_base.skills.amap.scripts.plan_trip.httpx.Client", lambda **_: FakeClient(handler)),
    ):
        text = run({"origin": "北京南站", "destination": "故宫", "mode": "驾车"})
    assert skill_error_code(text) is None
    assert "北京南站" in text
    assert "故宫" in text
    assert "8.5公里" in text
    assert "向北行驶进入南二环" in text
    print("通过：模拟驾车路线能格式化")


def test_route_geocode_ignores_weather_city() -> None:
    seen_cities: list[str] = []

    def handler(url: str, params: dict) -> FakeResponse:
        if "geocode/geo" in url:
            seen_cities.append(str(params.get("city") or ""))
            address = params.get("address") or ""
            city_name = "深圳市" if "深圳" in address else "北京市"
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "geocodes": [
                        {
                            "formatted_address": address,
                            "location": "114.06,22.55",
                            "city": city_name,
                            "citycode": "0755",
                        }
                    ],
                }
            )
        if "place/text" in url:
            seen_cities.append(str(params.get("city") or ""))
            address = params.get("keywords") or ""
            city_name = "深圳市" if "深圳" in address else "北京市"
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "pois": [
                        {
                            "name": address,
                            "location": "114.06,22.55",
                            "address": address,
                            "cityname": city_name,
                            "type": "交通设施服务",
                        }
                    ],
                }
            )
        if "direction/driving" in url:
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "route": {
                        "paths": [
                            {
                                "distance": "1000",
                                "duration": "300",
                                "steps": [{"instruction": "直行", "distance": "1000"}],
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"未预期的请求：{url}")

    with (
        patch(
            "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
            return_value="test-key",
        ),
        patch("cursor_myagent_base.skills.amap.scripts.plan_trip.httpx.Client", lambda **_: FakeClient(handler)),
    ):
        text = run(
            {
                "origin": "深圳北站",
                "destination": "深圳市政府",
                "city": "上海",
                "mode": "驾车",
            }
        )
    assert skill_error_code(text) is None
    assert seen_cities
    assert all(city != "上海" for city in seen_cities)
    assert any(city == "深圳" for city in seen_cities)
    print("通过：路线地理编码不用前面的天气城市")


def test_itinerary_mocked() -> None:
    def handler(url: str, params: dict) -> FakeResponse:
        if "place/text" in url:
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "pois": [
                        {
                            "name": "西湖",
                            "location": "120.148573,30.243512",
                            "address": "杭州市西湖区",
                            "cityname": "杭州市",
                            "type": "风景名胜",
                        },
                        {
                            "name": "断桥残雪",
                            "location": "120.151184,30.259244",
                            "address": "杭州市西湖区",
                            "cityname": "杭州市",
                            "type": "风景名胜",
                        },
                    ],
                }
            )
        if "direction/driving" in url:
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "route": {
                        "paths": [
                            {
                                "distance": "2400",
                                "duration": "600",
                                "steps": [{"instruction": "沿湖滨路行驶", "distance": "800"}],
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"未预期的请求：{url}")

    with (
        patch(
            "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
            return_value="test-key",
        ),
        patch("cursor_myagent_base.skills.amap.scripts.plan_trip.httpx.Client", lambda **_: FakeClient(handler)),
    ):
        text = run({"city": "杭州", "keywords": "西湖"})
    assert "西湖" in text
    assert "断桥残雪" in text
    assert "串联路线" in text
    print("通过：模拟城市行程能列出景点并串联")


def test_place_not_found() -> None:
    def handler(url: str, params: dict) -> FakeResponse:
        if "geocode/geo" in url:
            return FakeResponse({"status": "1", "info": "OK", "geocodes": []})
        if "place/text" in url:
            return FakeResponse({"status": "1", "info": "OK", "pois": []})
        raise AssertionError(f"未预期的请求：{url}")

    with (
        patch(
            "cursor_myagent_base.skills.amap.scripts.plan_trip.get_amap_api_key",
            return_value="test-key",
        ),
        patch("cursor_myagent_base.skills.amap.scripts.plan_trip.httpx.Client", lambda **_: FakeClient(handler)),
    ):
        text = run({"origin": "不存在的地方xyz", "destination": "故宫"})
    assert skill_error_code(text) == "PLACE_NOT_FOUND"
    print("通过：找不到地点时不会编造坐标")


def test_transit_does_not_say_zero_stops() -> None:
    assert _short_line_name("地铁1号线八通线(苹果园--环球度假区)") == "地铁1号线八通线"
    assert "1 站" in _ride_stop_text("0")
    assert "途经 0" not in _ride_stop_text("0")
    assert "3 站" in _ride_stop_text("2")
    text = _format_transit(
        {
            "route": {
                "transits": [
                    {
                        "duration": "3600",
                        "walking_distance": "800",
                        "cost": "4",
                        "segments": [
                            {
                                "walking": {"distance": "228"},
                                "bus": {
                                    "buslines": [
                                        {
                                            "name": "轨道交通4号线(重庆北站北广场--唐家沱)",
                                            "departure_stop": {"name": "重庆北站北广场"},
                                            "arrival_stop": {"name": "民安大道"},
                                            "via_num": "0",
                                        }
                                    ]
                                },
                            },
                            {
                                "walking": {"distance": "190"},
                                "bus": {
                                    "buslines": [
                                        {
                                            "name": "轨道交通环线外环",
                                            "departure_stop": {"name": "民安大道"},
                                            "arrival_stop": {"name": "重庆西站"},
                                            "via_num": "12",
                                        }
                                    ]
                                },
                            },
                            {"walking": {"distance": "504"}},
                        ],
                    }
                ]
            }
        },
        {"name": "重庆北站"},
        {"name": "重庆西站"},
    )
    assert "途经 0 站" not in text
    assert "坐 0 站" not in text
    assert "步行 228米 至 重庆北站北广场" in text
    assert "步行 504米 至 重庆西站" in text
    assert "乘坐 轨道交通4号线：重庆北站北广场 → 民安大道，乘坐 1 站（中间不停）" in text
    assert "乘坐 13 站" in text
    print("通过：公交方案不会写成途经0站，步行会写走到哪")


if __name__ == "__main__":
    test_catalog_includes_amap()
    test_canonical_mode()
    test_helpers()
    test_missing_api_key()
    test_missing_origin()
    test_invalid_mode()
    test_driving_route_mocked()
    test_route_geocode_ignores_weather_city()
    test_itinerary_mocked()
    test_place_not_found()
    test_transit_does_not_say_zero_stops()
    print("全部通过")
