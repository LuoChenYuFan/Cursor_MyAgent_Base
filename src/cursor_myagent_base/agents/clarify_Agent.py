from __future__ import annotations

from langchain_core.messages import AIMessage

from cursor_myagent_base.skills.normalize import as_question
from cursor_myagent_base.state import AgentState

_FALLBACK = "您的需求还缺一些关键信息。请补充要查的城市、路线从哪到哪，或邮件发给谁？"


def clarify_node(state: AgentState) -> dict:
    """只反问，不调 Skill。用户补全后下一轮再走领域专家。"""
    question = as_question(str(state.get("clarify_question") or "")) or _FALLBACK
    print(f"[反问] {question}")
    return {
        "messages": [AIMessage(content=question)],
        "pending_clarify": True,
        "needs_clarify": False,
        "clarify_question": question,
    }
