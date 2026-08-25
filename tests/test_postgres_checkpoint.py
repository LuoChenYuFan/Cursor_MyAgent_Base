"""验证：关掉连接后再打开，同一 thread_id 仍能从 PostgreSQL 读回 State。"""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from cursor_myagent_base.checkpointer import CHECKPOINT_DURABILITY, postgres_checkpointer
from cursor_myagent_base.graph import build_graph
from cursor_myagent_base.resume import is_run_pending, resume_pending
from cursor_myagent_base.state import AgentState


def _mini_graph(checkpointer):
    def remember(state: AgentState) -> dict:
        return {"reason": "checkpoint-ok"}

    graph = StateGraph(AgentState)
    graph.add_node("remember", remember)
    graph.add_edge(START, "remember")
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=checkpointer)


def test_state_survives_reconnect() -> None:
    thread_id = f"test-checkpoint-{uuid.uuid4().hex[:8]}"
    marker = f"persist-{thread_id}"

    with postgres_checkpointer() as checkpointer:
        graph = _mini_graph(checkpointer)
        graph.invoke(
            {"messages": [HumanMessage(content=marker)]},
            {"configurable": {"thread_id": thread_id}},
        )

    with postgres_checkpointer() as checkpointer:
        graph = _mini_graph(checkpointer)
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        values = snapshot.values or {}
        messages = list(values.get("messages") or [])
        assert messages, "重连后 messages 为空，checkpoint 没有写进 PostgreSQL"
        texts = [getattr(item, "content", "") for item in messages]
        assert marker in texts, f"重连后找不到原消息：{texts}"
        assert values.get("reason") == "checkpoint-ok"
        print(f"通过：thread_id={thread_id} 在关闭连接后仍能读回 messages 和 reason")


def test_build_graph_accepts_checkpointer() -> None:
    with postgres_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        assert graph is not None
        print("通过：主图 compile(checkpointer=PostgresSaver) 成功")


def _two_step_graph(checkpointer):
    def step_a(state: AgentState) -> dict:
        return {"reason": "after-a", "city": "上海"}

    def step_b(state: AgentState) -> dict:
        return {"reason": "after-b"}

    graph = StateGraph(AgentState)
    graph.add_node("step_a", step_a)
    graph.add_node("step_b", step_b)
    graph.add_edge(START, "step_a")
    graph.add_edge("step_a", "step_b")
    graph.add_edge("step_b", END)
    return graph.compile(checkpointer=checkpointer)


def test_resume_continues_from_breakpoint() -> None:
    """模拟宕机：第一步跑完后停下，重连后只续跑第二步，不从头再来。"""
    thread_id = f"test-resume-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    with postgres_checkpointer() as checkpointer:
        graph = _two_step_graph(checkpointer)
        graph.invoke(
            {"messages": [HumanMessage(content="resume-me")]},
            config,
            interrupt_before=["step_b"],
            durability=CHECKPOINT_DURABILITY,
        )
        snapshot = graph.get_state(config)
        assert is_run_pending(snapshot), f"应停在 step_b 之前，实际 next={snapshot.next}"
        assert snapshot.next == ("step_b",)
        assert (snapshot.values or {}).get("reason") == "after-a"
        assert (snapshot.values or {}).get("city") == "上海"

    with postgres_checkpointer() as checkpointer:
        graph = _two_step_graph(checkpointer)
        result = resume_pending(graph, thread_id)
        assert result is not None, "续跑没有返回结果"
        assert result.get("reason") == "after-b"
        assert result.get("city") == "上海"
        snapshot = graph.get_state(config)
        assert not is_run_pending(snapshot), f"续跑后应结束，实际 next={snapshot.next}"
        print(f"通过：thread_id={thread_id} 从 step_b 断点续跑，未重跑 step_a")


if __name__ == "__main__":
    test_state_survives_reconnect()
    test_build_graph_accepts_checkpointer()
    test_resume_continues_from_breakpoint()
    print("全部通过")
