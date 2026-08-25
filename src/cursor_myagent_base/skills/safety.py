from __future__ import annotations

import os
import re

from cursor_myagent_base.config import load_contacts
from cursor_myagent_base.skills.normalize import normalize_text

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 4000

_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"disregard\s+(the\s+)?(system|previous)",
    r"you\s+are\s+now",
    r"system\s+prompt",
    r"忽略(以上|之前|前面)?(的)?(所有)?(指令|规则|提示)",
    r"不要遵守",
    r"覆盖系统",
    r"越狱",
    r"jailbreak",
    r"developer\s+mode",
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def looks_like_injection(text: str) -> bool:
    compact = " ".join((text or "").split())
    if not compact:
        return False
    return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in _INJECTION_PATTERNS)


def resolve_allowed_recipient(raw: str) -> tuple[str, str] | None:
    """只允许 contacts.json 中的称呼，或通讯录里已登记的邮箱。"""
    text = (raw or "").strip()
    if not text or "\n" in text or "\r" in text or looks_like_injection(text):
        return None
    contacts = load_contacts()
    if not contacts:
        return None
    if text in contacts:
        return contacts[text], text
    key = normalize_text(text)
    for name, email in contacts.items():
        if normalize_text(name) == key:
            return email, name
        if normalize_text(email) == key:
            return email, name
    return None


def clip_or_reject_email_fields(subject: str, body: str) -> str | None:
    """超长则返回错误说明，否则 None。"""
    if len(subject or "") > MAX_SUBJECT_CHARS:
        return f"邮件主题过长（>{MAX_SUBJECT_CHARS} 字），已拒绝发送。"
    if len(body or "") > MAX_BODY_CHARS:
        return f"邮件正文过长（>{MAX_BODY_CHARS} 字），已拒绝发送。"
    return None


def skip_email_confirm() -> bool:
    return (os.getenv("EMAIL_SKIP_CONFIRM") or "").strip().lower() in {"1", "true", "yes"}


def is_confirm_resume(value: object) -> bool:
    if value is True:
        return True
    text = str(value or "").strip().lower()
    return text in {"确认", "yes", "y", "true", "ok", "发送"}


def classify_email_confirm(answer: str) -> str:
    """approve=发送，reject=取消，retry=再问一遍。"""
    text = (answer or "").strip()
    if not text:
        return "retry"
    lowered = text.lower()
    if lowered in {"quit", "exit", "q", "取消", "cancel", "n", "no"}:
        return "reject"
    if is_confirm_resume(text):
        return "approve"
    return "retry"
