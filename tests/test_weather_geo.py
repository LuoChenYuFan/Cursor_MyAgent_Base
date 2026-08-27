from langchain_core.messages import HumanMessage

from cursor_myagent_base.skills.normalize import (
    city_country,
    city_geocode_query,
    city_hint_for_route,
    infer_city_from_place,
    infer_weather_city_from_text,
    infer_when_from_text,
    is_city_clarify_question,
    last_human_content,
    looks_like_weather_query,
    pick_geo_place,
    resolve_run_city,
)


def test_tianjin_geocode_uses_china() -> None:
    assert city_country("天津") == "CN"
    assert city_geocode_query("天津") == "Tianjin,CN"
    print("通过：天津地理编码查询带 CN")


def test_hong_kong_uses_hk() -> None:
    assert city_country("香港") == "HK"
    assert city_geocode_query("香港") == "Hong Kong,HK"
    print("通过：香港地理编码查询带 HK")


def test_pick_china_over_japan_amatsu() -> None:
    places = [
        {"name": "Amatsu", "country": "JP", "lat": 35.1, "lon": 140.1},
        {
            "name": "Tianjin",
            "country": "CN",
            "lat": 39.14,
            "lon": 117.2,
            "local_names": {"zh": "天津"},
        },
    ]
    picked = pick_geo_place(places, expected_country="CN")
    assert picked["country"] == "CN"
    assert picked["name"] == "Tianjin"
    print("通过：有国家码时不会选用日本 Amatsu")


def test_route_city_does_not_reuse_weather_city() -> None:
    assert infer_city_from_place("深圳高铁站") == "深圳"
    assert infer_city_from_place("北京南站") == "北京"
    assert infer_city_from_place("故宫") == ""
    assert city_hint_for_route("深圳高铁站", "深圳人民政府", leaked_city="上海") == "深圳"
    assert city_hint_for_route("人民公园", "外滩", leaked_city="上海") == ""
    print("通过：路线城市不沿用前面的天气城市")


def test_resolve_run_city_keeps_two_clauses_apart() -> None:
    assert resolve_run_city("weather", city_param="", state_city="上海") == "上海"
    assert resolve_run_city("weather", city_param="北京", state_city="上海") == "北京"
    assert (
        resolve_run_city(
            "amap",
            city_param="上海",
            origin="深圳北站",
            destination="深圳市政府",
            state_city="上海",
        )
        == "深圳"
    )
    assert (
        resolve_run_city(
            "amap",
            city_param="上海",
            origin="人民公园",
            destination="外滩",
            state_city="上海",
        )
        == ""
    )
    assert (
        resolve_run_city(
            "amap",
            city_param="",
            origin="",
            destination="",
            state_city="上海",
        )
        == ""
    )
    assert (
        resolve_run_city(
            "amap",
            city_param="杭州",
            origin="",
            destination="",
            state_city="上海",
        )
        == "杭州"
    )
    print("通过：天气城市不会污染无关的路线调用")


def test_infer_weather_city_from_user_utterance() -> None:
    assert looks_like_weather_query("帮我查询北京明天的天气")
    assert infer_weather_city_from_text("帮我查询北京明天的天气") == "北京"
    assert infer_when_from_text("帮我查询北京明天的天气") == "tomorrow"
    assert infer_weather_city_from_text("查北京天气然后从上海到杭州") == "北京"
    assert infer_weather_city_from_text("从上海到北京怎么走") == ""
    assert infer_city_from_place("那上海呢") == "上海"
    assert is_city_clarify_question("请问您要查哪座城市的天气？")
    assert not is_city_clarify_question("请问从哪到哪？")
    assert last_human_content([HumanMessage(content="帮我查询北京明天的天气")]) == (
        "帮我查询北京明天的天气"
    )
    print("通过：天气问句能从原文抽出城市，不会和路线城市混用")


if __name__ == "__main__":
    test_tianjin_geocode_uses_china()
    test_hong_kong_uses_hk()
    test_pick_china_over_japan_amatsu()
    test_route_city_does_not_reuse_weather_city()
    test_resolve_run_city_keeps_two_clauses_apart()
    test_infer_weather_city_from_user_utterance()
    print("全部通过")
