from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from typing_extensions import NotRequired

# 约束意图识别结果的类型 ，只能取skill或chat
Intent = Literal["skill", "chat"]
SkillStopReason = Literal["duplicate", "budget", "token_budget"]


def last_value(_left: Any, right: Any) -> Any:
    return right


def merge_unique(left: list[str] | None, right: list[str] | None) -> list[str]:
    merged: list[str] = []
    for item in [*(left or []), *(right or [])]:
        if item not in merged:
            merged.append(item)
    return merged


def merge_dicts(
    left: dict[str, str] | None,
    right: dict[str, str] | None,
) -> dict[str, str]:
    return {**(left or {}), **(right or {})}


# 约束技能执行节点的状态的类型
class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]  # 历史对话
    remaining_steps: NotRequired[RemainingSteps]  # 剩余步骤
    intent: Intent | None  # 意图
    skill_name: str | None  # 技能名称（提示，真正调度看 domains）
    domains: Annotated[list[str], last_value]  # 本轮要跑的领域，如 trip、office
    domain_index: Annotated[int, last_value]  # 当前执行到 domains 的第几个
    current_domain: Annotated[str | None, last_value]  # 当前专家 Agent 所属领域
    city: str | None  # 本轮天气城市（不用于路线）
    when: str | None  # 天气时段：today / tomorrow
    origin: str | None  # 本轮路线起点（与天气城市无关）
    destination: str | None  # 本轮路线终点（与天气城市无关）
    needs_clarify: Annotated[bool, last_value]  # 本轮先反问、暂不调 Skill
    clarify_question: Annotated[str | None, last_value]  # 要问用户的那一句
    pending_clarify: Annotated[bool, last_value]  # 领域专家已反问，后续领域先别跑
    fallback_reason: Annotated[str | None, last_value]  # 系统兜底原因：route_unknown / llm_error / budget 等
    reason: str | None  # 原因
    skill_call_count: Annotated[int, operator.add]  # 本轮已执行的工具次数
    loaded_skills: Annotated[list[str], merge_unique]  # 本轮已加载过的 Skill 名
    run_results: Annotated[dict[str, str], merge_dicts]  # 本轮 run_skill 缓存
    last_tool: Annotated[str, last_value]
    skill_token_count: int  # 本轮 Worker 估算 token
    skill_stop_reason: SkillStopReason | None
