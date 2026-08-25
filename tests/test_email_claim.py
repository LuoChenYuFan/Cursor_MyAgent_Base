from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cursor_myagent_base.skills.email.verify import (
    EMAIL_SENT_MARK,
    correct_false_email_claim,
    email_sent_this_turn,
    is_email_sent_result,
    looks_like_sent_claim,
)
from cursor_myagent_base.skills.errors import skill_error
from cursor_myagent_base.skills.guard import compact_skill_node, post_skill_model


def test_claim_detector() -> None:
    assert looks_like_sent_claim("已向雨帆发送邮件，主题是调休通知。")
    assert looks_like_sent_claim("邮件已发送到雨帆。")
    assert not looks_like_sent_claim("条件不成立，未发信。")
    assert not looks_like_sent_claim("上海明天 18 度，多云。")
    print("通过：能区分「宣称已发送」和「明确说没发」")


def test_sent_result_needs_marker_or_script_text() -> None:
    assert is_email_sent_result(f"{EMAIL_SENT_MARK} 已发送邮件到 雨帆 <a@b.com>，主题：通知")
    assert is_email_sent_result("已发送邮件到 雨帆，主题：通知")
    assert not is_email_sent_result(skill_error("EMAIL_CANCELLED", "用户未确认，已取消发送邮件。"))
    assert not is_email_sent_result("已向雨帆发送邮件")
    print("通过：只有脚本成功回执才算真正发出")


def test_rewrite_keeps_weather_summary() -> None:
    text = "上海明天最高 18 度。已向雨帆发送调休通知。"
    out = correct_false_email_claim(text)
    assert "18 度" in out
    assert "没有实际发出" in out
    assert "已向雨帆发送调休通知" not in out
    print("通过：假发信句子会被拿掉，其它汇总还在")


def test_post_model_blocks_false_sent() -> None:
    state = {
        "messages": [
            HumanMessage(content="给雨帆发信"),
            AIMessage(content="已向雨帆发送邮件。", id="ai-final"),
        ],
        "run_results": {},
        "current_domain": "office",
    }
    out = post_skill_model(state)
    messages = out.get("messages") or []
    assert messages, out
    assert "没有实际发出" in str(messages[0].content)
    print("通过：办公 Agent 没调工具就说已发送会被拦截")


def test_post_model_allows_real_receipt() -> None:
    state = {
        "messages": [
            HumanMessage(content="给雨帆发信"),
            AIMessage(content="", id="ai-tool", tool_calls=[{"name": "run_skill", "id": "c1", "args": {}}]),
            ToolMessage(content=f"{EMAIL_SENT_MARK} 已发送邮件到 雨帆，主题：通知", tool_call_id="c1"),
            AIMessage(content="已向雨帆发送邮件，主题：通知。", id="ai-final"),
        ],
        "run_results": {"email|sent": f"{EMAIL_SENT_MARK} 已发送邮件到 雨帆，主题：通知"},
        "current_domain": "office",
    }
    assert email_sent_this_turn(state)
    out = post_skill_model(state)
    assert not out.get("messages")
    print("通过：有发信成功回执时不会误拦")


def test_compact_blocks_false_sent() -> None:
    out = compact_skill_node(
        {
            "messages": [
                HumanMessage(content="给雨帆发信"),
                AIMessage(content="已向雨帆发送邮件。"),
            ],
            "run_results": {},
        }
    )
    texts = [str(getattr(msg, "content", "")) for msg in out["messages"]]
    assert any("没有实际发出" in text for text in texts)
    print("通过：收尾节点也会拦住假发信")


if __name__ == "__main__":
    test_claim_detector()
    test_sent_result_needs_marker_or_script_text()
    test_rewrite_keeps_weather_summary()
    test_post_model_blocks_false_sent()
    test_post_model_allows_real_receipt()
    test_compact_blocks_false_sent()
    print("全部通过")
