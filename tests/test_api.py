from __future__ import annotations

import os

from cursor_myagent_base.asyncio_compat import use_selector_event_loop_policy

use_selector_event_loop_policy()
os.environ["API_TOKEN"] = "test-ci-token"

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from cursor_myagent_base.api import app
from cursor_myagent_base.turns import last_ai_reply, message_text

_AUTH = {"Authorization": "Bearer test-ci-token"}


def test_message_helpers() -> None:
    assert message_text(HumanMessage(content="你好")) == "你好"
    reply = last_ai_reply(
        [
            HumanMessage(content="问"),
            AIMessage(content="答"),
        ]
    )
    assert reply == "答"
    print("通过：turns 消息摘要")


def test_health_open_v1_requires_token() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json().get("ok") is True
        assert health.json().get("graph") is True

        denied = client.get("/v1/skills")
        assert denied.status_code == 401

        stream_denied = client.post("/v1/chat/stream", json={"message": "你好"})
        assert stream_denied.status_code == 401

        wrong = client.get("/v1/skills", headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401

        skills = client.get("/v1/skills", headers=_AUTH)
        assert skills.status_code == 200
        names = {item["name"] for item in skills.json()["skills"]}
        assert "weather" in names
        assert "email" in names
        assert "amap" in names
        domain_names = {item["name"] for item in skills.json()["domains"]}
        assert domain_names == {"trip", "office"}

        denied_contacts = client.get("/v1/contacts")
        assert denied_contacts.status_code == 401
        contacts = client.get("/v1/contacts", headers=_AUTH)
        assert contacts.status_code == 200
        book = contacts.json()["contacts"]
        assert isinstance(book, list)
        assert all("name" in item and "email" in item for item in book)

        idle = client.get("/v1/threads/test-api-idle", headers=_AUTH)
        assert idle.status_code == 200
        assert idle.json()["status"] in {"idle", "ok"}

        home = client.get("/")
        assert home.status_code == 200
        assert "text/html" in home.headers.get("content-type", "")
        assert "MyAgent" in home.text
        css = client.get("/static/app.css")
        assert css.status_code == 200
        js = client.get("/static/app.js")
        assert js.status_code == 200
    print("通过：/health 公开，业务接口需要 Bearer")


if __name__ == "__main__":
    test_message_helpers()
    test_health_open_v1_requires_token()
    print("全部通过")
