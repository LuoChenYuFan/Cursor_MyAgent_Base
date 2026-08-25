from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from cursor_myagent_base.agents.chat_Agent import chat_node
from cursor_myagent_base.agents.clarify_Agent import clarify_node
from cursor_myagent_base.agents.fallback_Agent import fallback_node
from cursor_myagent_base.agents.intent_Agent import intent_node, route_after_intent
from cursor_myagent_base.agents.office_Agent import build_office_agent
from cursor_myagent_base.agents.trip_Agent import build_trip_agent
from cursor_myagent_base.domains import advance_domain, route_after_advance
from cursor_myagent_base.skills.guard import SKILL_RECURSION_LIMIT, compact_skill_node
from cursor_myagent_base.state import AgentState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(AgentState)
    graph.add_node("intent", intent_node)
    graph.add_node(
        "trip_agent",
        build_trip_agent().with_config({"recursion_limit": SKILL_RECURSION_LIMIT}),
    )
    graph.add_node(
        "office_agent",
        build_office_agent().with_config({"recursion_limit": SKILL_RECURSION_LIMIT}),
    )
    graph.add_node("advance_domain", advance_domain)
    graph.add_node("compact_skill", compact_skill_node)
    graph.add_node("chat_agent", chat_node)
    graph.add_node("clarify_agent", clarify_node)
    graph.add_node("fallback_agent", fallback_node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "trip_agent": "trip_agent",
            "office_agent": "office_agent",
            "chat_agent": "chat_agent",
            "clarify_agent": "clarify_agent",
            "fallback_agent": "fallback_agent",
        },
    )
    graph.add_edge("trip_agent", "advance_domain")
    graph.add_edge("office_agent", "advance_domain")
    graph.add_conditional_edges(
        "advance_domain",
        route_after_advance,
        {
            "trip_agent": "trip_agent",
            "office_agent": "office_agent",
            "compact_skill": "compact_skill",
        },
    )
    graph.add_edge("compact_skill", END)
    graph.add_edge("chat_agent", END)
    graph.add_edge("clarify_agent", END)
    graph.add_edge("fallback_agent", END)
    return graph.compile(checkpointer=checkpointer)
