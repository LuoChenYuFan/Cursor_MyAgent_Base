from langchain_core.messages import AIMessage

from cursor_myagent_base.agents.clarify_Agent import clarify_node
from cursor_myagent_base.domains import advance_domain, route_after_advance
from cursor_myagent_base.skills.normalize import as_question


def test_as_question_adds_mark() -> None:
    assert as_question("请问您要查哪座城市的天气") == "请问您要查哪座城市的天气？"
    assert as_question("哪一座？") == "哪一座？"
    assert as_question("  ") == ""
    print("通过：反问会补上问号")


def test_clarify_node_replies_with_question() -> None:
    out = clarify_node({"clarify_question": "请问您要查哪座城市的天气"})
    assert out["pending_clarify"] is True
    assert out["needs_clarify"] is False
    message = out["messages"][0]
    assert isinstance(message, AIMessage)
    assert message.content.endswith("？")
    assert "哪座城市" in message.content
    print("通过：反问节点会直接问用户")


def test_pending_clarify_skips_later_domains() -> None:
    out = advance_domain(
        {"domains": ["trip", "office"], "domain_index": 0, "pending_clarify": True}
    )
    assert out["current_domain"] is None
    assert route_after_advance({"domains": ["trip", "office"], "domain_index": out["domain_index"]}) == (
        "compact_skill"
    )
    print("通过：反问后不会继续跑办公领域")


if __name__ == "__main__":
    test_as_question_adds_mark()
    test_clarify_node_replies_with_question()
    test_pending_clarify_skips_later_domains()
    print("全部通过")
