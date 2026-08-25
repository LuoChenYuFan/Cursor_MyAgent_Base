from __future__ import annotations

import asyncio
import hmac
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from cursor_myagent_base.asyncio_compat import use_selector_event_loop_policy
from cursor_myagent_base.checkpointer import async_postgres_checkpointer
from cursor_myagent_base.config import (
    format_contact_names,
    get_agent_thread_id,
    get_api_token,
    get_max_concurrent_runs,
    list_contacts,
)
from cursor_myagent_base.domains import DOMAIN_DESCRIPTIONS, DOMAIN_ORDER, DOMAIN_SKILLS
from cursor_myagent_base.graph import build_graph
from cursor_myagent_base.skills.loader import load_skills
from cursor_myagent_base.skills.safety import classify_email_confirm
from cursor_myagent_base.turns import (
    aconfirm_email,
    acontinue_pending,
    ainspect_session,
    aiter_turn_events,
    arun_new_turn,
)

use_selector_event_loop_policy()

_THREAD_LOCKS: dict[str, asyncio.Lock] = {}
_bearer = HTTPBearer(auto_error=False, scheme_name="APIToken")
_WEB_DIR = Path(__file__).resolve().parent / "web"


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = get_api_token()
    if not expected:
        raise HTTPException(status_code=503, detail="未配置 API_TOKEN。请在 .env 中设置后再调用业务接口。")
    got = (creds.credentials if creds and creds.scheme.lower() == "bearer" else "") or ""
    if len(got) != len(expected) or not hmac.compare_digest(got, expected):
        raise HTTPException(
            status_code=401,
            detail="鉴权失败。请在请求头携带 Authorization: Bearer <API_TOKEN>",
        )


def _normalize_thread_id(raw: str | None) -> str:
    text = (raw or "").strip() or get_agent_thread_id()
    if len(text) > 128:
        raise HTTPException(status_code=400, detail="thread_id 过长")
    return text


def _lock_for(thread_id: str) -> asyncio.Lock:
    lock = _THREAD_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _THREAD_LOCKS[thread_id] = lock
    return lock


def _public_payload(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": info["status"],
        "thread_id": info["thread_id"],
        "reply": info.get("reply"),
        "confirm": info.get("confirm"),
        "next": info.get("next") or [],
        "message_count": info.get("message_count") or 0,
        "last_user": info.get("last_user"),
        "intent": info.get("intent"),
        "skill_name": info.get("skill_name"),
        "domains": info.get("domains") or [],
        "current_domain": info.get("current_domain"),
        "city": info.get("city"),
        "origin": info.get("origin"),
        "destination": info.get("destination"),
        "when": info.get("when"),
        "needs_clarify": bool(info.get("needs_clarify")),
        "clarify_question": info.get("clarify_question"),
        "fallback_reason": info.get("fallback_reason"),
        "reason": info.get("reason"),
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with async_postgres_checkpointer() as saver:
        app.state.graph = build_graph(saver)
        app.state.run_sema = asyncio.Semaphore(get_max_concurrent_runs())
        yield


app = FastAPI(
    title="Cursor MyAgent",
    description="LangGraph 多 Agent：意图识别后按领域调度（行程 / 办公）或闲聊。与 CLI 共用 PostgreSQL checkpoint。业务接口需要 Authorization: Bearer <API_TOKEN>。",
    version="0.1.0",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入")
    thread_id: str | None = Field(default=None, description="会话 id，默认用 AGENT_THREAD_ID")
    auto_resume: bool = Field(default=True, description="若该会话有未完成任务，先断点续跑再处理本轮")


class ConfirmRequest(BaseModel):
    thread_id: str | None = None
    approve: bool | None = Field(default=None, description="true=发送，false=取消")
    decision: str | None = Field(default=None, description="也可传「确认」或「取消」")


class ResumeRequest(BaseModel):
    thread_id: str | None = None


def _graph():
    graph = getattr(app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="图尚未就绪")
    return graph


def _run_sema() -> asyncio.Semaphore:
    sema = getattr(app.state, "run_sema", None)
    if sema is None:
        sema = asyncio.Semaphore(get_max_concurrent_runs())
        app.state.run_sema = sema
    return sema


@app.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    index = _WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="聊天页未找到")
    return FileResponse(index, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "graph": getattr(app.state, "graph", None) is not None}


@app.get("/v1/skills", dependencies=[Depends(require_api_token)])
def list_skills() -> dict[str, Any]:
    skills = [
        {"name": spec.name, "description": spec.description}
        for spec in load_skills().values()
    ]
    domains = [
        {
            "name": name,
            "description": DOMAIN_DESCRIPTIONS[name],
            "skills": list(DOMAIN_SKILLS[name]),
        }
        for name in DOMAIN_ORDER
    ]
    return {"skills": skills, "domains": domains, "contacts": format_contact_names()}


@app.get("/v1/contacts", dependencies=[Depends(require_api_token)])
def get_contacts() -> dict[str, Any]:
    items = list_contacts()
    return {"contacts": items, "count": len(items)}


@app.get("/v1/threads/{thread_id}", dependencies=[Depends(require_api_token)])
async def get_thread(thread_id: str) -> dict[str, Any]:
    tid = _normalize_thread_id(thread_id)
    graph = _graph()
    async with _lock_for(tid):
        info = await ainspect_session(graph, tid)
    return _public_payload(info)


@app.post("/v1/chat", dependencies=[Depends(require_api_token)])
async def chat(body: ChatRequest) -> dict[str, Any]:
    tid = _normalize_thread_id(body.thread_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message 不能为空")
    graph = _graph()
    async with _run_sema():
        async with _lock_for(tid):
            try:
                info = await ainspect_session(graph, tid)
                if info["status"] == "needs_confirmation":
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "该会话正在等待发信确认，请先调用 POST /v1/confirm",
                            **_public_payload(info),
                        },
                    )
                if info["status"] == "pending_run" and body.auto_resume:
                    info = await acontinue_pending(graph, tid)
                    if info["status"] == "needs_confirmation":
                        return _public_payload(info)
                elif info["status"] == "pending_run":
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "该会话有未完成任务，请先调用 POST /v1/resume",
                            **_public_payload(info),
                        },
                    )
                info = await arun_new_turn(graph, tid, text)
            except HTTPException:
                raise
            except GraphRecursionError as exc:
                raise HTTPException(status_code=429, detail="工具调用次数过多，请换一种问法") from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public_payload(info)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/stream", dependencies=[Depends(require_api_token)])
async def chat_stream(body: ChatRequest):
    """SSE：边跑边推 status / token，最后一条 type=done。"""
    tid = _normalize_thread_id(body.thread_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message 不能为空")
    graph = _graph()
    lock = _lock_for(tid)
    await _run_sema().acquire()
    sema_held = True
    await lock.acquire()
    released = False

    def _release() -> None:
        nonlocal released, sema_held
        if not released:
            released = True
            lock.release()
        if sema_held:
            sema_held = False
            _run_sema().release()

    try:
        info = await ainspect_session(graph, tid)
        if info["status"] == "needs_confirmation":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "该会话正在等待发信确认，请先调用 POST /v1/confirm",
                    **_public_payload(info),
                },
            )
        if info["status"] == "pending_run" and not body.auto_resume:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "该会话有未完成任务，请先调用 POST /v1/resume",
                    **_public_payload(info),
                },
            )
        if info["status"] == "pending_run" and body.auto_resume:
            info = await acontinue_pending(graph, tid)
            if info["status"] == "needs_confirmation":
                async def _confirm_only():
                    try:
                        yield _sse({"type": "done", "payload": _public_payload(info)})
                    finally:
                        _release()

                return StreamingResponse(
                    _confirm_only(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
    except HTTPException:
        _release()
        raise
    except Exception:
        _release()
        raise

    async def _generate():
        try:
            async for event in aiter_turn_events(graph, tid, text):
                if event.get("type") == "done":
                    raw = event.get("info")
                    if not isinstance(raw, dict) or "status" not in raw:
                        raw = await ainspect_session(graph, tid)
                    yield _sse({"type": "done", "payload": _public_payload(raw)})
                else:
                    yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc) or "本轮执行失败"})
        finally:
            _release()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/confirm", dependencies=[Depends(require_api_token)])
async def confirm(body: ConfirmRequest) -> dict[str, Any]:
    tid = _normalize_thread_id(body.thread_id)
    if body.approve is None and not (body.decision or "").strip():
        raise HTTPException(status_code=400, detail="请提供 approve 或 decision（确认/取消）")
    if body.approve is None:
        kind = classify_email_confirm(body.decision or "")
        if kind == "retry":
            raise HTTPException(status_code=400, detail="无法识别的确认语，请传「确认」或「取消」")
        approved = kind == "approve"
    else:
        approved = body.approve
    graph = _graph()
    async with _run_sema():
        async with _lock_for(tid):
            try:
                info = await ainspect_session(graph, tid)
                if info["status"] != "needs_confirmation":
                    raise HTTPException(
                        status_code=409,
                        detail={"message": "当前没有待确认的发信", **_public_payload(info)},
                    )
                info = await aconfirm_email(graph, tid, approved)
            except HTTPException:
                raise
            except GraphRecursionError as exc:
                raise HTTPException(status_code=429, detail="工具调用次数过多，请换一种问法") from exc
    return _public_payload(info)


@app.post("/v1/resume", dependencies=[Depends(require_api_token)])
async def resume(body: ResumeRequest) -> dict[str, Any]:
    tid = _normalize_thread_id(body.thread_id)
    graph = _graph()
    async with _run_sema():
        async with _lock_for(tid):
            try:
                info = await acontinue_pending(graph, tid)
            except GraphRecursionError as exc:
                raise HTTPException(status_code=429, detail="工具调用次数过多，请换一种问法") from exc
    return _public_payload(info)


app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")


def main() -> None:
    import uvicorn

    host = (os.getenv("API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int((os.getenv("API_PORT") or "8000").strip() or "8000")
    except ValueError:
        port = 8000
    uvicorn.run(
        "cursor_myagent_base.api:app",
        host=host,
        port=port,
        reload=False,
        loop="cursor_myagent_base.asyncio_compat:new_selector_event_loop",
    )


if __name__ == "__main__":
    main()
