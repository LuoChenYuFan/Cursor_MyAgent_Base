from langchain_core.messages import AIMessageChunk

from cursor_myagent_base.turns import _events_from_stream_item, _tool_status


def test_stream_tokens_and_skips_intent() -> None:
    visible = _events_from_stream_item(
        ("messages", (AIMessageChunk(content="北京明天小雨"), {"langgraph_node": "chat_agent"}))
    )
    assert visible == [{"type": "token", "text": "北京明天小雨"}]
    nested = _events_from_stream_item(
        (("trip_agent",), "messages", (AIMessageChunk(content="路线如下"), {"langgraph_node": "agent"}))
    )
    assert nested == [{"type": "token", "text": "路线如下"}]
    hidden = _events_from_stream_item(
        ("messages", (AIMessageChunk(content='{"intent":"skill"}'), {"langgraph_node": "intent"}))
    )
    assert hidden == []
    print("通过：流式只推可见回复，不推意图识别 JSON")


def test_stream_tool_status() -> None:
    assert _tool_status("run_skill", {"name": "weather"}) == "正在查询天气"
    assert _tool_status("run_skill", {"name": "amap"}) == "正在规划路线"
    chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[{"name": "load_skill", "args": "", "id": "1", "index": 0}],
    )
    events = _events_from_stream_item(("messages", (chunk, {"langgraph_node": "agent"})))
    assert any(item.get("text") == "正在加载技能说明" for item in events)
    print("通过：工具调用会推状态文案")


if __name__ == "__main__":
    test_stream_tokens_and_skips_intent()
    test_stream_tool_status()
    print("全部通过")
