from __future__ import annotations

from cursor_myagent_base.fallback import fallback_messages
from cursor_myagent_base.state import AgentState


def fallback_node(state: AgentState) -> dict:
    """系统兜底：固定回复，不调大模型、不调 Skill。"""
    reason = str(state.get("fallback_reason") or "unhandled").strip() or "unhandled"
    return fallback_messages(reason)
