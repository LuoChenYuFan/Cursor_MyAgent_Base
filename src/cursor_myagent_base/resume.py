from __future__ import annotations

from typing import Any

from langgraph.types import Command, StateSnapshot

from cursor_myagent_base.checkpointer import CHECKPOINT_DURABILITY
from cursor_myagent_base.skills.guard import GRAPH_RECURSION_LIMIT


def thread_config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": GRAPH_RECURSION_LIMIT,
    }


def get_snapshot(graph, thread_id: str) -> StateSnapshot:
    return graph.get_state(thread_config(thread_id), subgraphs=True)


def is_run_pending(snapshot: StateSnapshot) -> bool:
    """图还没走到 END：父图或子图 next 非空，表示上次 invoke 中断。"""
    if snapshot.next:
        return True
    for task in snapshot.tasks or ():
        nested = getattr(task, "state", None)
        if nested is not None and getattr(nested, "next", None):
            return True
    return False


def _iter_interrupts(snapshot: StateSnapshot | None):
    if snapshot is None:
        return
    for item in snapshot.interrupts or ():
        yield item
    for task in snapshot.tasks or ():
        for item in task.interrupts or ():
            yield item
        nested = getattr(task, "state", None)
        if nested is not None and hasattr(nested, "interrupts"):
            yield from _iter_interrupts(nested)


def first_email_confirm(snapshot: StateSnapshot) -> dict | None:
    for item in _iter_interrupts(snapshot):
        value = getattr(item, "value", None)
        if isinstance(value, dict) and value.get("type") == "confirm_email":
            return value
    return None


def resume_with(graph, thread_id: str, value: object) -> dict[str, Any]:
    return graph.invoke(
        Command(resume=value),
        thread_config(thread_id),
        durability=CHECKPOINT_DURABILITY,
    )


def resume_pending(graph, thread_id: str) -> dict[str, Any] | None:
    """从最后一个 checkpoint 接着跑。若正等用户确认发信，则不自动续跑。"""
    snapshot = get_snapshot(graph, thread_id)
    if first_email_confirm(snapshot):
        return None
    if not is_run_pending(snapshot):
        return None
    return graph.invoke(
        None,
        thread_config(thread_id),
        durability=CHECKPOINT_DURABILITY,
    )


async def aget_snapshot(graph, thread_id: str) -> StateSnapshot:
    return await graph.aget_state(thread_config(thread_id), subgraphs=True)


async def aresume_with(graph, thread_id: str, value: object) -> dict[str, Any]:
    return await graph.ainvoke(
        Command(resume=value),
        thread_config(thread_id),
        durability=CHECKPOINT_DURABILITY,
    )


async def aresume_pending(graph, thread_id: str) -> dict[str, Any] | None:
    snapshot = await aget_snapshot(graph, thread_id)
    if first_email_confirm(snapshot):
        return None
    if not is_run_pending(snapshot):
        return None
    return await graph.ainvoke(
        None,
        thread_config(thread_id),
        durability=CHECKPOINT_DURABILITY,
    )
