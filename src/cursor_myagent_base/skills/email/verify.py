from __future__ import annotations

import re

from langchain_core.messages import AIMessage, ToolMessage

from cursor_myagent_base.skills.errors import skill_error_code

EMAIL_SENT_MARK = "#EMAIL_SENT"

_CLAIM_RE = re.compile(
    r"(已经?发送|已经?发出|已经?发信|已向.{0,12}发|已给.{0,12}发|邮件已发|发信成功|发送成功)"
)
_DENY_RE = re.compile(r"(未|没有|尚未|不会|不能|无法|不要)(发送|发信|发出|发邮件)")


def is_email_sent_result(text: str) -> bool:
    raw = text or ""
    if skill_error_code(raw):
        return False
    stripped = raw.lstrip()
    if stripped.startswith(EMAIL_SENT_MARK):
        return True
    return "已发送邮件到" in raw or "此前已发送邮件到" in raw


def email_sent_this_turn(state: dict) -> bool:
    for text in (state.get("run_results") or {}).values():
        if is_email_sent_result(str(text)):
            return True
    for message in state.get("messages") or []:
        if isinstance(message, ToolMessage) and is_email_sent_result(str(message.content or "")):
            return True
    return False


def looks_like_sent_claim(text: str) -> bool:
    raw = text or ""
    if not _CLAIM_RE.search(raw):
        return False
    if _DENY_RE.search(raw) and "已发送" not in raw and "已经发送" not in raw:
        return False
    return True


def correct_false_email_claim(text: str) -> str:
    parts = re.split(r"(?<=[。！？\n])", text or "")
    kept = [part for part in parts if part.strip() and not looks_like_sent_claim(part)]
    rest = "".join(kept).strip()
    notice = "邮件没有实际发出：本轮没有发信成功回执，不能说已经发送。"
    if rest:
        return f"{rest}\n{notice}"
    return notice


def block_false_email_claim(state: dict, last_ai: AIMessage) -> AIMessage | None:
    content = last_ai.content if isinstance(last_ai.content, str) else ""
    if not looks_like_sent_claim(content):
        return None
    if email_sent_this_turn(state):
        return None
    print("[安全] 拦截未发信却宣称已发送")
    return AIMessage(content=correct_false_email_claim(content), id=last_ai.id)
