from __future__ import annotations

import os
from unittest.mock import patch

from cursor_myagent_base.skills.email.receipts import find_receipt, receipt_key
from cursor_myagent_base.skills.email.scripts.send_email import run


class _FakeSMTP:
    sent = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, *args) -> None:
        return None

    def send_message(self, _msg) -> None:
        type(self).sent += 1


def test_email_resume_does_not_send_twice(tmp_path: str) -> None:
    os.environ["EMAIL_RECEIPTS_PATH"] = tmp_path
    os.environ["SMTP_USER"] = "sender@example.com"
    os.environ["SMTP_AUTH_CODE"] = "secret"
    _FakeSMTP.sent = 0
    args = {
        "thread_id": "cli-local",
        "to": "雨帆",
        "subject": "天气提醒",
        "body": "明天香港不下雪",
    }
    with (
        patch(
            "cursor_myagent_base.skills.email.scripts.send_email.smtplib.SMTP_SSL",
            _FakeSMTP,
        ),
        patch(
            "cursor_myagent_base.skills.email.scripts.send_email._resolve_recipient",
            lambda raw: ("731425764@qq.com", "雨帆"),
        ),
    ):
        first = run(args)
        second = run(args)
    assert "已发送" in first, first
    assert "未重复投递" in second, second
    assert _FakeSMTP.sent == 1, _FakeSMTP.sent
    key = receipt_key(
        thread_id="cli-local",
        to="731425764@qq.com",
        subject="天气提醒",
        body="明天香港不下雪",
    )
    assert find_receipt(key) is not None
    print("通过：同一封邮件续跑时不会再次调用 SMTP")


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        test_email_resume_does_not_send_twice(str(__import__("pathlib").Path(folder) / "r.json"))
    print("全部通过")
