from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from cursor_myagent_base.skills.loader import format_catalog
from cursor_myagent_base.domains import is_domain_handover

MAX_INPUT_TOKENS = 8000
MAX_TURN_TOKENS = 20000
MAX_TOOL_RESULT_TOKENS = 2000
_WORKER_SYSTEM_RESERVE = 1500
TOKEN_STOP_REPLY = "本轮 token 预算已用尽，已停止，避免继续消耗。请换一种问法。"


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is not None:
        return len(encoding.encode(text))
    return max(1, (len(text) + 1) // 2)


def _message_text(message: BaseMessage) -> str:
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


def extract_usage_tokens(message: BaseMessage) -> int:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        total = int(usage.get("total_tokens") or 0)
        if total:
            return total
        return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    meta = getattr(message, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    if isinstance(token_usage, dict):
        total = int(token_usage.get("total_tokens") or token_usage.get("total") or 0)
        if total:
            return total
        return int(token_usage.get("prompt_tokens") or 0) + int(
            token_usage.get("completion_tokens") or 0
        )
    return 0


def estimate_content_tokens(message: BaseMessage) -> int:
    extra = ""
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        extra = json.dumps(tool_calls, ensure_ascii=False)
    role = getattr(message, "type", "") or ""
    return count_tokens(f"{role}:{_message_text(message)}\n{extra}")


def estimate_message_tokens(message: BaseMessage) -> int:
    billed = extract_usage_tokens(message)
    if billed:
        return billed
    return estimate_content_tokens(message)


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    return sum(estimate_message_tokens(item) for item in messages)


def estimate_messages_content_tokens(messages: list[BaseMessage]) -> int:
    return sum(estimate_content_tokens(item) for item in messages)


def worker_overhead_tokens() -> int:
    return _WORKER_SYSTEM_RESERVE + count_tokens(format_catalog())


def truncate_text(text: str, max_tokens: int) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    encoding = _encoding()
    if encoding is not None:
        pieces = encoding.encode(text)
        clipped = encoding.decode(pieces[: max(1, max_tokens - 8)])
        return clipped + "\n…(已按 token 预算截断)"
    approx_chars = max(16, max_tokens * 2)
    return text[:approx_chars] + "\n…(已按 token 预算截断)"


def _copy_message(message: BaseMessage, **updates: Any) -> BaseMessage:
    copier = getattr(message, "model_copy", None)
    if callable(copier):
        return copier(update=updates)
    return message


def fit_messages_to_input_budget(
    messages: list[BaseMessage],
    *,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    overhead_tokens: int = 0,
) -> list[BaseMessage]:
    budget = max(256, max_input_tokens - overhead_tokens)
    fitted: list[BaseMessage] = []
    for message in messages:
        content = _message_text(message)
        if isinstance(message, ToolMessage) and count_tokens(content) > MAX_TOOL_RESULT_TOKENS:
            fitted.append(
                _copy_message(message, content=truncate_text(content, MAX_TOOL_RESULT_TOKENS))
            )
        else:
            fitted.append(message)
    if estimate_messages_content_tokens(fitted) <= budget:
        return fitted

    last_human = 0
    for index, message in enumerate(fitted):
        if isinstance(message, HumanMessage):
            last_human = index
    current_turn = fitted[last_human:]
    prefix = fitted[:last_human]
    kept = list(current_turn)
    while prefix and estimate_messages_content_tokens(prefix + kept) > budget:
        prefix.pop(0)
    # 只压缩工具原文，不删除 ToolMessage，避免留下未配对的 tool_calls
    if estimate_messages_content_tokens(prefix + kept) > budget:
        squeezed: list[BaseMessage] = []
        for message in kept:
            content = _message_text(message)
            if isinstance(message, ToolMessage) and count_tokens(content) > 80:
                squeezed.append(_copy_message(message, content=truncate_text(content, 80)))
            else:
                squeezed.append(message)
        kept = squeezed
    return prefix + kept


def tokens_used_this_turn(messages: list[BaseMessage]) -> int:
    last_human = 0
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_human = index
            start = index
        elif is_domain_handover(message) and index >= last_human:
            start = index
    return worker_overhead_tokens() + estimate_messages_tokens(messages[start:])
