from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from cursor_myagent_base.agents.fallback_Agent import fallback_node
from cursor_myagent_base.agents.intent_Agent import _intent_failed, _intent_success
from cursor_myagent_base.agents.intent_Agent import RouteDecision
from cursor_myagent_base.fallback import format_fallback_reply, is_internal_message
from cursor_myagent_base.skills.guard import compact_skill_node


def test_fallback_reply_is_fixed() -> None:
    text = format_fallback_reply("route_unknown")
    assert text.startswith("这次没办成")
    assert "查天气" in text
    assert "stack" not in text
    assert format_fallback_reply("llm_error", detail="secret traceback").startswith("这次没办成")
    assert "traceback" not in format_fallback_reply("llm_error", detail="secret traceback")
    print("通过：兜底话术固定且不泄露内部细节")


def test_internal_handover_is_detected() -> None:
    assert is_internal_message("【行程领域已完成】请办公领域继续处理用户请求中尚未完成的部分。")
    assert not is_internal_message("上海明天多云，最高气温 18 度。")
    print("通过：领域交接话不会当成用户可见结果")


def test_fallback_node_does_not_call_llm() -> None:
    out = fallback_node({"fallback_reason": "unhandled"})
    assert out["fallback_reason"] == "unhandled"
    assert "这次没办成" in out["messages"][0].content
    print("通过：兜底节点直接回复，不调模型")


def test_unknown_skill_route_sets_fallback() -> None:
    decision = RouteDecision(intent="skill", domains=[], reason="拿不准")
    out = _intent_success(decision)
    assert out["fallback_reason"] == "route_unknown"
    assert out["domains"] == []
    print("通过：不确定领域时走系统兜底，不猜两个专家")


def test_intent_backfills_city_from_user_text() -> None:
    decision = RouteDecision(
        intent="skill",
        domains=["trip"],
        skill_name="weather",
        city=None,
        needs_clarify=True,
        clarify_question="请问您要查哪座城市的天气？",
        reason="漏填城市",
    )
    out = _intent_success(decision, user_text="帮我查询北京明天的天气")
    assert out["city"] == "北京"
    assert out["when"] == "tomorrow"
    assert out["needs_clarify"] is False
    assert not out["clarify_question"]
    print("通过：用户已说城市时不会再反问")


def test_intent_failure_sets_llm_error() -> None:
    out = _intent_failed(RuntimeError("timeout"))
    assert out["fallback_reason"] == "llm_error"
    print("通过：意图识别异常会标记 llm_error")


def test_compact_adds_fallback_when_no_reply() -> None:
    out = compact_skill_node({"messages": [HumanMessage(content="查天气")]})
    assert out.get("fallback_reason") == "empty_reply"
    texts = [
        str(getattr(msg, "content", ""))
        for msg in out["messages"]
        if not isinstance(msg, RemoveMessage)
    ]
    assert any("这次没办成" in text for text in texts)
    print("通过：没有有效回复时收尾会补上兜底")


def test_compact_keeps_real_reply() -> None:
    out = compact_skill_node(
        {
            "messages": [
                HumanMessage(content="上海天气"),
                AIMessage(content="上海今天晴，气温 22 度。"),
            ]
        }
    )
    assert out.get("fallback_reason") is None
    print("通过：已有正常回复时不会覆盖成兜底")


if __name__ == "__main__":
    test_fallback_reply_is_fixed()
    test_internal_handover_is_detected()
    test_fallback_node_does_not_call_llm()
    test_unknown_skill_route_sets_fallback()
    test_intent_backfills_city_from_user_text()
    test_intent_failure_sets_llm_error()
    test_compact_adds_fallback_when_no_reply()
    test_compact_keeps_real_reply()
    print("全部通过")
