from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.types import Overwrite

from cursor_myagent_base.checkpointer import CHECKPOINT_DURABILITY
from cursor_myagent_base.fallback import format_fallback_reply, is_internal_message
from cursor_myagent_base.resume import (
    aget_snapshot,
    aresume_pending,
    aresume_with,
    first_email_confirm,
    get_snapshot,
    is_run_pending,
    resume_pending,
    resume_with,
    thread_config,
)

TurnStatus = Literal["ok", "needs_confirmation", "pending_run", "idle"]


def message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content) if content else ""


def last_ai_reply(messages) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if getattr(message, "tool_calls", None):
            continue
        text = message_text(message).strip()
        if text and not is_internal_message(text):
            return text
    return "(没有生成回复)"


def last_human_text(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message_text(message).strip()
    return ""


def new_turn_input(user_text: str) -> dict:
    return {
        "messages": [HumanMessage(content=user_text)],
        "skill_call_count": Overwrite(0),
        "loaded_skills": Overwrite([]),
        "run_results": Overwrite({}),
        "last_tool": Overwrite(""),
        "city": Overwrite(None),
        "when": Overwrite(None),
        "origin": Overwrite(None),
        "destination": Overwrite(None),
        "needs_clarify": Overwrite(False),
        "clarify_question": Overwrite(""),
        "pending_clarify": Overwrite(False),
        "fallback_reason": Overwrite(None),
        "skill_token_count": 0,
        "skill_stop_reason": None,
    }


def inspect_from_snapshot(snapshot, thread_id: str) -> dict[str, Any]:
    values = snapshot.values or {}
    messages = list(values.get("messages") or [])
    confirm = first_email_confirm(snapshot)
    pending_run = is_run_pending(snapshot)
    if confirm:
        status: TurnStatus = "needs_confirmation"
    elif pending_run:
        status = "pending_run"
    elif not messages:
        status = "idle"
    else:
        status = "ok"
    return {
        "status": status,
        "thread_id": thread_id,
        "next": list(snapshot.next or ()),
        "message_count": len(messages),
        "confirm": confirm,
        "last_user": last_human_text(messages) or None,
        "reply": last_ai_reply(messages) if messages else None,
        "intent": values.get("intent"),
        "skill_name": values.get("skill_name"),
        "domains": list(values.get("domains") or []),
        "current_domain": values.get("current_domain"),
        "city": values.get("city"),
        "origin": values.get("origin"),
        "destination": values.get("destination"),
        "when": values.get("when"),
        "needs_clarify": bool(values.get("needs_clarify")),
        "clarify_question": values.get("clarify_question"),
        "fallback_reason": values.get("fallback_reason"),
        "reason": values.get("reason"),
        "values": values,
    }


def inspect_session(graph, thread_id: str) -> dict[str, Any]:
    return inspect_from_snapshot(get_snapshot(graph, thread_id), thread_id)


async def ainspect_session(graph, thread_id: str) -> dict[str, Any]:
    snapshot = await aget_snapshot(graph, thread_id)
    return inspect_from_snapshot(snapshot, thread_id)


def _outcome_from_snapshot(graph, thread_id: str, result: dict | None) -> dict[str, Any]:
    info = inspect_session(graph, thread_id)
    values = result if isinstance(result, dict) else info["values"]
    messages = list(values.get("messages") or [])
    info["values"] = values
    if info["status"] == "ok" and messages:
        info["reply"] = last_ai_reply(messages)
    return info


async def _aoutcome_from_snapshot(graph, thread_id: str, result: dict | None) -> dict[str, Any]:
    info = await ainspect_session(graph, thread_id)
    values = result if isinstance(result, dict) else info["values"]
    messages = list(values.get("messages") or [])
    info["values"] = values
    if info["status"] == "ok" and messages:
        info["reply"] = last_ai_reply(messages)
    return info


def _apply_fallback_info(info: dict[str, Any], text: str, reason: str) -> dict[str, Any]:
    values = dict(info.get("values") or {})
    messages = list(values.get("messages") or [])
    if last_ai_reply(messages) != text:
        messages = [*messages, AIMessage(content=text)]
    values["messages"] = messages
    values["fallback_reason"] = reason
    info["values"] = values
    info["reply"] = text
    info["fallback_reason"] = reason
    return info


def _commit_fallback(graph, thread_id: str, reason: str) -> dict[str, Any]:
    text = format_fallback_reply(reason)
    print(f"[兜底] {reason}")
    try:
        graph.update_state(
            thread_config(thread_id),
            {
                "messages": [AIMessage(content=text)],
                "fallback_reason": reason,
            },
        )
    except Exception as exc:
        print(f"[兜底] 写入会话失败: {type(exc).__name__}: {exc}")
    return _apply_fallback_info(inspect_session(graph, thread_id), text, reason)


async def _acommit_fallback(graph, thread_id: str, reason: str) -> dict[str, Any]:
    text = format_fallback_reply(reason)
    print(f"[兜底] {reason}")
    try:
        await graph.aupdate_state(
            thread_config(thread_id),
            {
                "messages": [AIMessage(content=text)],
                "fallback_reason": reason,
            },
        )
    except Exception as exc:
        print(f"[兜底] 写入会话失败: {type(exc).__name__}: {exc}")
    info = await ainspect_session(graph, thread_id)
    return _apply_fallback_info(info, text, reason)


def run_new_turn(graph, thread_id: str, user_text: str) -> dict[str, Any]:
    """跑一轮用户输入。遇到发信确认则停住，不在服务端替用户点确认。"""
    try:
        result = graph.invoke(
            new_turn_input(user_text),
            thread_config(thread_id),
            durability=CHECKPOINT_DURABILITY,
        )
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return _commit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 本轮执行失败: {type(exc).__name__}: {exc}")
        return _commit_fallback(graph, thread_id, "unhandled")
    return _outcome_from_snapshot(graph, thread_id, result if isinstance(result, dict) else None)


_SKIP_STREAM_NODES = {"intent", "advance_domain", "compact_skill"}
_SKILL_STATUS = {
    "weather": "正在查询天气",
    "amap": "正在规划路线",
    "email": "正在准备邮件",
}


def _unpack_stream_item(item) -> tuple[tuple, str, Any]:
    if not isinstance(item, tuple):
        return (), "updates", item
    if len(item) == 3:
        namespace, mode, data = item
        if isinstance(namespace, str) and namespace in {"messages", "updates", "values"}:
            return (), str(namespace), mode
        ns = namespace if isinstance(namespace, tuple) else (namespace,)
        return ns, str(mode), data
    if len(item) == 2:
        first, second = item
        if first in {"messages", "updates", "values", "custom", "debug"}:
            return (), str(first), second
        ns = first if isinstance(first, tuple) else (first,)
        return ns, "messages", second
    return (), "updates", item


def _node_from_meta(namespace: tuple, meta: Any) -> str:
    if isinstance(meta, dict):
        node = str(meta.get("langgraph_node") or "").strip()
        if node:
            return node
        path = meta.get("langgraph_checkpoint_ns") or meta.get("checkpoint_ns")
        if isinstance(path, str) and path:
            return path.split(":")[0] or path.split("|")[0]
    if namespace:
        return str(namespace[0] or "")
    return ""


def _tool_status(name: str, args: Any) -> str:
    skill = ""
    if isinstance(args, dict):
        skill = str(args.get("name") or "").strip()
    if skill in _SKILL_STATUS:
        return _SKILL_STATUS[skill]
    if name == "load_skill":
        return "正在加载技能说明"
    if name == "run_skill":
        return "正在调用工具"
    if name == "ask_user":
        return "正在整理要问你的问题"
    return "正在处理"


def _message_status(message) -> str | None:
    for call in getattr(message, "tool_calls", None) or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "")
        if name:
            return _tool_status(name, call.get("args"))
    for chunk in getattr(message, "tool_call_chunks", None) or []:
        if not isinstance(chunk, dict):
            continue
        name = str(chunk.get("name") or "")
        if name:
            return _tool_status(name, None)
    if isinstance(message, ToolMessage):
        tool = str(getattr(message, "name", "") or "")
        if tool:
            return _tool_status(tool, None)
    return None


def _token_text(message) -> str:
    if getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None):
        return ""
    text = message_text(message).strip("\x00")
    if not text or is_internal_message(text):
        return ""
    return text


def _events_from_stream_item(item) -> list[dict[str, Any]]:
    namespace, mode, data = _unpack_stream_item(item)
    events: list[dict[str, Any]] = []
    if mode == "messages":
        message = data
        meta = {}
        if isinstance(data, tuple) and len(data) >= 1:
            message = data[0]
            meta = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
        node = _node_from_meta(namespace, meta)
        status = _message_status(message)
        if status:
            events.append({"type": "status", "text": status})
        if node not in _SKIP_STREAM_NODES and isinstance(message, (AIMessage, AIMessageChunk)):
            token = _token_text(message)
            if token:
                events.append({"type": "token", "text": token})
        return events
    if mode == "updates" and isinstance(data, dict):
        for value in data.values():
            if not isinstance(value, dict):
                continue
            for message in value.get("messages") or []:
                status = _message_status(message)
                if status:
                    events.append({"type": "status", "text": status})
    return events


def iter_turn_events(graph, thread_id: str, user_text: str) -> Iterator[dict[str, Any]]:
    """边跑图边产出 status / token，最后一条为 done。"""
    yield {"type": "status", "text": "正在理解你的问题"}
    try:
        for item in graph.stream(
            new_turn_input(user_text),
            thread_config(thread_id),
            stream_mode=["messages", "updates"],
            subgraphs=True,
            durability=CHECKPOINT_DURABILITY,
        ):
            yield from _events_from_stream_item(item)
    except GraphInterrupt:
        pass
    except GraphRecursionError:
        info = _commit_fallback(graph, thread_id, "recursion")
        yield {"type": "done", "info": info}
        return
    except Exception as exc:
        print(f"[兜底] 本轮执行失败: {type(exc).__name__}: {exc}")
        info = _commit_fallback(graph, thread_id, "unhandled")
        yield {"type": "done", "info": info}
        return
    yield {"type": "done", "info": _outcome_from_snapshot(graph, thread_id, None)}


def continue_pending(graph, thread_id: str) -> dict[str, Any]:
    """宕机后续跑。若正等发信确认则不自动 resume。"""
    info = inspect_session(graph, thread_id)
    if info["status"] == "needs_confirmation":
        return info
    if info["status"] != "pending_run":
        return info
    try:
        result = resume_pending(graph, thread_id)
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return _commit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 续跑失败: {type(exc).__name__}: {exc}")
        return _commit_fallback(graph, thread_id, "unhandled")
    return _outcome_from_snapshot(graph, thread_id, result if isinstance(result, dict) else None)


def confirm_email(graph, thread_id: str, approved: bool) -> dict[str, Any]:
    info = inspect_session(graph, thread_id)
    if info["status"] != "needs_confirmation":
        return info
    try:
        result = resume_with(graph, thread_id, approved)
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return _commit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 发信确认后续失败: {type(exc).__name__}: {exc}")
        return _commit_fallback(graph, thread_id, "unhandled")
    return _outcome_from_snapshot(graph, thread_id, result if isinstance(result, dict) else None)


async def arun_new_turn(graph, thread_id: str, user_text: str) -> dict[str, Any]:
    try:
        result = await graph.ainvoke(
            new_turn_input(user_text),
            thread_config(thread_id),
            durability=CHECKPOINT_DURABILITY,
        )
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return await _acommit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 本轮执行失败: {type(exc).__name__}: {exc}")
        return await _acommit_fallback(graph, thread_id, "unhandled")
    return await _aoutcome_from_snapshot(
        graph, thread_id, result if isinstance(result, dict) else None
    )


async def aiter_turn_events(graph, thread_id: str, user_text: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "status", "text": "正在理解你的问题"}
    try:
        async for item in graph.astream(
            new_turn_input(user_text),
            thread_config(thread_id),
            stream_mode=["messages", "updates"],
            subgraphs=True,
            durability=CHECKPOINT_DURABILITY,
        ):
            for event in _events_from_stream_item(item):
                yield event
    except GraphInterrupt:
        pass
    except GraphRecursionError:
        info = await _acommit_fallback(graph, thread_id, "recursion")
        yield {"type": "done", "info": info}
        return
    except Exception as exc:
        print(f"[兜底] 本轮执行失败: {type(exc).__name__}: {exc}")
        info = await _acommit_fallback(graph, thread_id, "unhandled")
        yield {"type": "done", "info": info}
        return
    yield {"type": "done", "info": await _aoutcome_from_snapshot(graph, thread_id, None)}


async def acontinue_pending(graph, thread_id: str) -> dict[str, Any]:
    info = await ainspect_session(graph, thread_id)
    if info["status"] == "needs_confirmation":
        return info
    if info["status"] != "pending_run":
        return info
    try:
        result = await aresume_pending(graph, thread_id)
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return await _acommit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 续跑失败: {type(exc).__name__}: {exc}")
        return await _acommit_fallback(graph, thread_id, "unhandled")
    return await _aoutcome_from_snapshot(
        graph, thread_id, result if isinstance(result, dict) else None
    )


async def aconfirm_email(graph, thread_id: str, approved: bool) -> dict[str, Any]:
    info = await ainspect_session(graph, thread_id)
    if info["status"] != "needs_confirmation":
        return info
    try:
        result = await aresume_with(graph, thread_id, approved)
    except GraphInterrupt:
        result = None
    except GraphRecursionError:
        return await _acommit_fallback(graph, thread_id, "recursion")
    except Exception as exc:
        print(f"[兜底] 发信确认后续失败: {type(exc).__name__}: {exc}")
        return await _acommit_fallback(graph, thread_id, "unhandled")
    return await _aoutcome_from_snapshot(
        graph, thread_id, result if isinstance(result, dict) else None
    )
