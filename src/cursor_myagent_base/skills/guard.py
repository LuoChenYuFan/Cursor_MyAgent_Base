from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from cursor_myagent_base.skills.budget import (
    MAX_TURN_TOKENS,
    TOKEN_STOP_REPLY,
    fit_messages_to_input_budget,
    tokens_used_this_turn,
    worker_overhead_tokens,
)
from langgraph.types import Overwrite

from cursor_myagent_base.skills.loader import _normalize_arguments
from cursor_myagent_base.skills.normalize import (
    normalize_args_for_cache,
    normalize_text,
    resolve_run_city,
)
from cursor_myagent_base.fallback import (
    format_fallback_reply,
    is_internal_message,
    last_plain_text,
)
from cursor_myagent_base.skills.email.verify import (
    block_false_email_claim,
    correct_false_email_claim,
    email_sent_this_turn,
    looks_like_sent_claim,
)
from cursor_myagent_base.state import AgentState

MAX_TOOL_CALLS = 8
MAX_USER_TURNS = 2
SKILL_RECURSION_LIMIT = 16
GRAPH_RECURSION_LIMIT = 48

_BUDGET_REPLY = "本轮工具调用已达上限，已停止，避免重复消耗。请换一种问法。"
_DUP_LOAD_STUB = "Skill 说明书已经加载过，请直接调用 run_skill，不要再次 load_skill。"


def run_cache_key(name: str, args: dict[str, Any]) -> str:
    packed = tuple(sorted(normalize_args_for_cache(name, args).items()))
    return f"{normalize_text(name)}|{packed}"


def _call_args(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args") or {}
    return args if isinstance(args, dict) else {}


def load_cache_name(call: dict[str, Any]) -> str:
    return normalize_text(str(_call_args(call).get("name") or ""))


def run_call_key(call: dict[str, Any], state: AgentState) -> str:
    args = _call_args(call)
    extra = _normalize_arguments(args.get("arguments_json") or "")
    skill_name = str(args.get("name") or state.get("skill_name") or "").strip()
    when = str(args.get("when") or extra.get("when") or extra.get("date") or state.get("when") or "").strip()
    if when:
        extra["when"] = when
    origin = str(
        args.get("origin") or extra.get("origin") or extra.get("from") or extra.get("start") or state.get("origin") or ""
    ).strip()
    destination = str(
        args.get("destination")
        or extra.get("destination")
        or extra.get("dest")
        or extra.get("end")
        or state.get("destination")
        or ""
    ).strip()
    city = resolve_run_city(
        skill_name,
        city_param=str(args.get("city") or extra.get("city") or ""),
        origin=origin,
        destination=destination,
        state_city=str(state.get("city") or ""),
    )
    if city:
        extra["city"] = city
    else:
        extra.pop("city", None)
    mode = str(args.get("mode") or extra.get("mode") or extra.get("travel_mode") or "").strip()
    waypoints = str(args.get("waypoints") or extra.get("waypoints") or extra.get("via") or "").strip()
    keywords = str(
        args.get("keywords") or extra.get("keywords") or extra.get("theme") or extra.get("query") or ""
    ).strip()
    days = str(args.get("days") or extra.get("days") or "").strip()
    if origin:
        extra["origin"] = origin
    if destination:
        extra["destination"] = destination
    if mode:
        extra["mode"] = mode
    if waypoints:
        extra["waypoints"] = waypoints
    if keywords:
        extra["keywords"] = keywords
    if days:
        extra["days"] = days
    to = str(args.get("to") or extra.get("to") or extra.get("receiver") or extra.get("email") or "").strip()
    subject = str(args.get("subject") or extra.get("subject") or extra.get("title") or "").strip()
    body = str(args.get("body") or extra.get("body") or extra.get("content") or "").strip()
    if to:
        extra["to"] = to
    if subject:
        extra["subject"] = subject
    if body:
        extra["body"] = body
    return run_cache_key(skill_name, extra)


def _is_tool_ai(message: BaseMessage) -> bool:
    return isinstance(message, AIMessage) and bool(getattr(message, "tool_calls", None))


def trim_worker_messages(
    messages: list[BaseMessage],
    *,
    max_user_turns: int = MAX_USER_TURNS,
) -> list[BaseMessage]:
    """给 Worker 看：最近 1–2 轮用户话 + 本轮工具过程。上一轮的 SKILL.md 丢掉。"""
    human_idxs = [i for i, msg in enumerate(messages) if isinstance(msg, HumanMessage)]
    if not human_idxs:
        return list(messages)
    start = human_idxs[max(0, len(human_idxs) - max_user_turns)]
    window = messages[start:]
    last_human = 0
    for i, msg in enumerate(window):
        if isinstance(msg, HumanMessage):
            last_human = i
    kept: list[BaseMessage] = []
    for i, msg in enumerate(window):
        if i >= last_human:
            kept.append(msg)
            continue
        if isinstance(msg, HumanMessage):
            kept.append(msg)
        elif isinstance(msg, AIMessage) and not _is_tool_ai(msg):
            kept.append(msg)
    return kept


def compact_conversation(
    messages: list[BaseMessage],
    *,
    max_user_turns: int = MAX_USER_TURNS,
) -> list[BaseMessage]:
    """跨轮只保留用户话和最终回复，SKILL.md / 工具原文不进下一轮。"""
    pairs: list[tuple[HumanMessage, AIMessage | None]] = []
    pending_human: HumanMessage | None = None
    last_final: AIMessage | None = None
    for msg in messages:
        if isinstance(msg, HumanMessage):
            if pending_human is not None:
                pairs.append((pending_human, last_final))
            pending_human = msg
            last_final = None
        elif isinstance(msg, AIMessage) and not _is_tool_ai(msg):
            last_final = msg
    if pending_human is not None:
        pairs.append((pending_human, last_final))

    compacted: list[BaseMessage] = []
    for human, ai in pairs[-max_user_turns:]:
        compacted.append(human)
        if ai is not None:
            compacted.append(ai)
    return compacted


def visible_chat_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    return compact_conversation(messages, max_user_turns=MAX_USER_TURNS)


def _fill_missing_tool_results(messages: list[BaseMessage]) -> list[BaseMessage]:
    have = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }
    filled = list(messages)
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            cid = str(call.get("id") or "")
            if not cid or cid in have:
                continue
            filled.append(
                ToolMessage(
                    content="（缺少对应工具结果，请根据已有信息继续，不要重复调用。）",
                    tool_call_id=cid,
                    name=str(call.get("name") or ""),
                )
            )
            have.add(cid)
    return filled


def pre_skill_model(state: AgentState) -> dict:
    trimmed = trim_worker_messages(list(state.get("messages") or []))
    fitted = fit_messages_to_input_budget(
        trimmed,
        overhead_tokens=worker_overhead_tokens(),
    )
    return {"llm_input_messages": _fill_missing_tool_results(fitted)}


def post_skill_model(state: AgentState) -> dict:
    """工具执行前拦截：超预算、超 token 则结束本轮；重复调用回传缓存，不中断后续 Skill。"""
    messages = list(state.get("messages") or [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    used = tokens_used_this_turn(messages)
    token_update = {"skill_token_count": used}
    if last_ai is None:
        return token_update
    if not last_ai.tool_calls:
        blocked = block_false_email_claim(state, last_ai)
        if blocked is not None:
            return {**token_update, "messages": [blocked]}
        return token_update
    if used >= MAX_TURN_TOKENS:
        print(f"[约束] 本轮 token 已 {used}/{MAX_TURN_TOKENS}，已结束本轮")
        return {
            **token_update,
            "messages": [AIMessage(content=TOKEN_STOP_REPLY, id=last_ai.id)],
            "skill_stop_reason": "token_budget",
        }

    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    pending = [c for c in last_ai.tool_calls if c.get("id") not in answered]
    if not pending:
        return token_update

    raw_count = state.get("skill_call_count") or 0
    if isinstance(raw_count, Overwrite):
        raw_count = raw_count.value
    count = int(raw_count or 0)
    loaded = set(state.get("loaded_skills") or [])
    results = dict(state.get("run_results") or {})
    allowed: list[dict] = []
    cache_stubs: list[ToolMessage] = []
    stop_reason = ""

    for call in pending:
        name = str(call.get("name") or "")
        cid = str(call.get("id") or "")
        if count + len(allowed) >= MAX_TOOL_CALLS:
            stop_reason = "budget"
            continue
        if name == "load_skill":
            skill_name = load_cache_name(call)
            if skill_name and skill_name in loaded:
                cache_stubs.append(
                    ToolMessage(
                        content=_DUP_LOAD_STUB,
                        tool_call_id=cid,
                        name="load_skill",
                    )
                )
                continue
        elif name == "run_skill":
            key = run_call_key(call, state)
            if key in results:
                print("[约束] 拦截重复 run_skill，回传缓存并继续后续步骤")
                cache_stubs.append(
                    ToolMessage(
                        content=results[key],
                        tool_call_id=cid,
                        name="run_skill",
                    )
                )
                continue
        allowed.append(call)

    deferred: list[dict] = []
    if len(allowed) > 1:
        print(f"[约束] 本步并行 {len(allowed)} 个工具，只执行 {allowed[0].get('name')}")
        deferred = allowed[1:]
        allowed = allowed[:1]
    cached_ids = {item.tool_call_id for item in cache_stubs if item.tool_call_id}
    skipped = [
        call
        for call in pending
        if call not in allowed
        and call not in deferred
        and str(call.get("id") or "") not in cached_ids
    ]
    stubs = list(cache_stubs)
    for call in [*deferred, *skipped]:
        cid = str(call.get("id") or "")
        if not cid or any(item.tool_call_id == cid for item in stubs):
            continue
        stubs.append(
            ToolMessage(
                content="一次只执行一个工具。请根据当前工具结果再决定下一步，不要并行调用。",
                tool_call_id=cid,
                name=str(call.get("name") or ""),
            )
        )

    if allowed:
        if stubs:
            return {**token_update, "messages": stubs}
        return token_update

    if cache_stubs and stop_reason != "budget":
        print("[约束] 重复工具已回传缓存，交回模型继续后续 Skill")
        return {**token_update, "messages": cache_stubs}

    if stop_reason == "budget":
        print(f"[约束] 本轮工具调用已达上限 {MAX_TOOL_CALLS}，已结束本轮")
        return {
            **token_update,
            "messages": [AIMessage(content=_BUDGET_REPLY, id=last_ai.id)],
            "skill_stop_reason": "budget",
        }

    return token_update


def compact_skill_node(state: AgentState) -> dict:
    compacted = compact_conversation(list(state.get("messages") or []))
    stop = state.get("skill_stop_reason")
    if isinstance(stop, Overwrite):
        stop = getattr(stop, "value", None)
    visible = last_plain_text(compacted)
    reason = None
    if stop in {"budget", "token_budget"}:
        reason = "budget"
    elif not state.get("pending_clarify") and (
        not visible or is_internal_message(visible)
    ):
        reason = "empty_reply"
    messages: list = compacted
    extra: dict = {}
    if looks_like_sent_claim(visible) and not email_sent_this_turn(state):
        print("[安全] 收尾拦截未发信却宣称已发送")
        patched = list(compacted)
        for index in range(len(patched) - 1, -1, -1):
            msg = patched[index]
            if not isinstance(msg, AIMessage) or getattr(msg, "tool_calls", None):
                continue
            patched[index] = AIMessage(
                content=correct_false_email_claim(str(msg.content or "")),
                id=msg.id,
            )
            break
        messages = patched
    if reason:
        text = format_fallback_reply(reason)
        print(f"[兜底] {reason}")
        messages = [*messages, AIMessage(content=text)]
        extra["fallback_reason"] = reason
    return {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
        "skill_call_count": Overwrite(0),
        "loaded_skills": Overwrite([]),
        "run_results": Overwrite({}),
        "last_tool": Overwrite(""),
        "pending_clarify": Overwrite(False),
        **extra,
    }
