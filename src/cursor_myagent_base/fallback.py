from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

FallbackReason = str

REASON_LABELS: dict[str, str] = {
    "route_unknown": "暂时无法判断你要做什么",
    "llm_error": "系统暂时无法完成这一步",
    "budget": "本轮步骤或额度已经用完",
    "recursion": "处理步骤过多，已停止",
    "empty_reply": "没有生成有效回复",
    "unhandled": "遇到未预期的错误",
}

_CAPABILITIES = "我目前可以帮你查天气、规划路线、给通讯录联系人发邮件，或随便聊聊。"


def format_fallback_reply(reason: str, detail: str = "") -> str:
    """固定话术，不让模型自由发挥。detail 只打日志，不展示给用户。"""
    _ = detail
    key = reason if reason in REASON_LABELS else "unhandled"
    label = REASON_LABELS[key]
    if key == "route_unknown":
        return f"这次没办成。原因：{label}。{_CAPABILITIES}请换一种说法试试。"
    return f"这次没办成。原因：{label}。请换一种说法，或稍后再试。"


def last_plain_text(messages: list[BaseMessage] | None) -> str:
    for message in reversed(list(messages or [])):
        if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
            continue
        content = message.content if isinstance(message.content, str) else ""
        text = content.strip()
        if text:
            return text
    return ""


def is_internal_message(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("【") and "领域已完成】" in stripped


def fallback_messages(reason: str) -> dict:
    text = format_fallback_reply(reason)
    print(f"[兜底] {reason}")
    return {
        "messages": [AIMessage(content=text)],
        "fallback_reason": reason,
        "needs_clarify": False,
        "pending_clarify": False,
    }
