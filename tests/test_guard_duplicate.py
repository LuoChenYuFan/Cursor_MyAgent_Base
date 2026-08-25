from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cursor_myagent_base.skills.guard import post_skill_model, run_cache_key, run_call_key


def test_duplicate_run_skill_keeps_turn_open() -> None:
    cache_key = run_cache_key("weather", {"city": "广州", "when": "tomorrow"})
    cached = "地点：广州\n最高气温：29°C"
    state = {
        "messages": [
            HumanMessage(content="查广州再发信"),
            AIMessage(
                content="",
                id="ai-1",
                tool_calls=[
                    {
                        "name": "run_skill",
                        "id": "call-weather",
                        "args": {"name": "weather", "city": "广州", "when": "tomorrow"},
                    }
                ],
            ),
        ],
        "run_results": {cache_key: cached},
        "skill_call_count": 2,
        "loaded_skills": ["weather"],
    }
    out = post_skill_model(state)
    assert out.get("skill_stop_reason") != "duplicate"
    messages = out.get("messages") or []
    assert messages, "重复查询应回传缓存 ToolMessage，而不是结束本轮"
    assert isinstance(messages[0], ToolMessage)
    assert "29°C" in str(messages[0].content)
    print("通过：重复 run_skill 不会整轮停掉，模型还能继续发邮件")


def test_amap_cache_key_ignores_weather_city() -> None:
    call = {
        "args": {
            "name": "amap",
            "origin": "深圳北站",
            "destination": "深圳市政府",
            "city": "上海",
        }
    }
    state = {"city": "上海", "origin": "深圳北站", "destination": "深圳市政府"}
    key = run_call_key(call, state)
    assert "上海" not in key
    assert "深圳" in key
    weather_key = run_call_key(
        {"args": {"name": "weather"}},
        {"city": "上海"},
    )
    assert "上海" in weather_key
    print("通过：路线缓存键不带上前面的天气城市")


if __name__ == "__main__":
    test_duplicate_run_skill_keeps_turn_open()
    test_amap_cache_key_ignores_weather_city()
    print("全部通过")
