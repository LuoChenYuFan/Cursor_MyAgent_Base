from __future__ import annotations

import smtplib
from email.message import EmailMessage

from cursor_myagent_base.config import (
    get_smtp_auth_code,
    get_smtp_host,
    get_smtp_port,
    get_smtp_user,
)
from cursor_myagent_base.skills.email.receipts import (
    delete_receipt,
    find_receipt,
    receipt_key,
    save_receipt,
)
from cursor_myagent_base.skills.email.verify import EMAIL_SENT_MARK
from cursor_myagent_base.skills.errors import (
    CONTENT_TOO_LONG,
    FORBIDDEN_RECIPIENT,
    INJECTION_BLOCKED,
    MISSING_BODY,
    MISSING_SMTP_CONFIG,
    MISSING_SUBJECT,
    MISSING_TO,
    SMTP_AUTH,
    SMTP_CONNECT,
    SMTP_SEND,
    skill_error,
)
from cursor_myagent_base.skills.safety import (
    clip_or_reject_email_fields,
    looks_like_injection,
    resolve_allowed_recipient,
)


def _resolve_recipient(raw: str) -> tuple[str, str]:
    allowed = resolve_allowed_recipient(raw)
    if allowed:
        return allowed
    return "", raw


def run(arguments: dict | None = None) -> str:
    """向指定邮箱发送一封纯文本邮件。arguments 需包含 to、subject、body。"""
    arguments = arguments or {}
    to = str(
        arguments.get("to")
        or arguments.get("receiver")
        or arguments.get("email")
        or ""
    ).strip()
    subject = str(arguments.get("subject") or arguments.get("title") or "").strip()
    body = str(arguments.get("body") or arguments.get("content") or "").strip()

    user = get_smtp_user()
    auth_code = get_smtp_auth_code()
    host = get_smtp_host()
    port = get_smtp_port()
    if not user or not auth_code:
        return skill_error(
            MISSING_SMTP_CONFIG,
            "未配置 SMTP_USER 或 SMTP_AUTH_CODE。请在 .env 中填入发信邮箱和授权码。",
        )
    if not to:
        return skill_error(
            MISSING_TO,
            "缺少收件人。请询问用户邮箱，或使用 contacts.json 里已登记的称呼。",
        )
    resolved, display = _resolve_recipient(to)
    if not resolved:
        print(f"[安全] 拒绝发信：收件人「{to}」不在通讯录白名单")
        return skill_error(
            FORBIDDEN_RECIPIENT,
            f"收件人「{to}」不在通讯录中，已拒绝发送。只能发给 contacts.json 里已登记的称呼或邮箱。",
        )
    to = resolved
    too_long = clip_or_reject_email_fields(subject, body)
    if too_long:
        return skill_error(CONTENT_TOO_LONG, too_long)
    if looks_like_injection(subject) or looks_like_injection(body):
        print("[安全] 拒绝发信：主题或正文疑似提示词注入")
        return skill_error(
            INJECTION_BLOCKED,
            "邮件主题或正文疑似提示词注入，已拒绝发送。",
        )
    if not subject:
        return skill_error(
            MISSING_SUBJECT,
            "缺少邮件主题。请询问用户主题是什么，不要编造后再重试。",
        )
    if not body:
        return skill_error(
            MISSING_BODY,
            "缺少邮件正文。请询问用户要写什么内容，不要编造后再重试。",
        )

    thread_id = str(arguments.get("thread_id") or "").strip()
    key = receipt_key(thread_id=thread_id, to=to, subject=subject, body=body)
    existing = find_receipt(key)
    if existing:
        target = existing.get("target") or to
        print(f"[邮件] 幂等跳过，未重复发送 to={target} subject={subject}")
        return (
            f"{EMAIL_SENT_MARK} 此前已发送邮件到 {target}，主题：{subject}。"
            "本次为断点续跑，未重复投递。"
        )

    payload = {
        "thread_id": thread_id,
        "to": to,
        "display": display,
        "target": f"{display} <{to}>" if display != to else to,
        "subject": subject,
        "body": body,
    }
    save_receipt(key, {**payload, "status": "pending"})

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, auth_code)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        delete_receipt(key)
        return skill_error(
            SMTP_AUTH,
            "SMTP 认证失败。请检查 .env 中的 SMTP_USER 和 SMTP_AUTH_CODE。",
        )
    except (smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
        delete_receipt(key)
        return skill_error(SMTP_CONNECT, f"无法连接 SMTP 服务器（{exc}）。")
    except smtplib.SMTPException as exc:
        delete_receipt(key)
        return skill_error(SMTP_SEND, f"发送失败（{exc}）。")

    target = payload["target"]
    save_receipt(key, {**payload, "status": "sent"})
    return f"{EMAIL_SENT_MARK} 已发送邮件到 {target}，主题：{subject}"


if __name__ == "__main__":
    import sys

    to = sys.argv[1] if len(sys.argv) > 1 else ""
    subject = sys.argv[2] if len(sys.argv) > 2 else ""
    body = sys.argv[3] if len(sys.argv) > 3 else ""
    print(run({"to": to, "subject": subject, "body": body}))
