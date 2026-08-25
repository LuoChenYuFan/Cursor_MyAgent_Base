from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.types import Overwrite

from cursor_myagent_base.state import AgentState

DomainName = Literal["trip", "office"]
DomainRoute = Literal[
    "trip_agent",
    "office_agent",
    "chat_agent",
    "clarify_agent",
    "fallback_agent",
    "compact_skill",
]

DOMAIN_ORDER: tuple[str, ...] = ("trip", "office")

DOMAIN_SKILLS: dict[str, tuple[str, ...]] = {
    "trip": ("weather", "amap"),
    "office": ("email",),
}

DOMAIN_LABELS: dict[str, str] = {
    "trip": "行程",
    "office": "办公",
}

DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "trip": "查询天气、路线导航与行程规划",
    "office": "向通讯录联系人发送邮件",
}

DOMAIN_NODES: dict[str, str] = {
    "trip": "trip_agent",
    "office": "office_agent",
}

_SKILL_TO_DOMAIN: dict[str, str] = {
    skill: domain
    for domain, skills in DOMAIN_SKILLS.items()
    for skill in skills
}

_DOMAIN_ALIASES: dict[str, str] = {
    "trip": "trip",
    "travel": "trip",
    "行程": "trip",
    "出行": "trip",
    "office": "office",
    "email": "office",
    "mail": "office",
    "办公": "office",
    "发信": "office",
}


def skill_domain(skill_name: str) -> str | None:
    key = (skill_name or "").strip()
    return _SKILL_TO_DOMAIN.get(key) or _SKILL_TO_DOMAIN.get(key.casefold())


def allowed_skills(domain: str | None) -> frozenset[str]:
    if not domain:
        return frozenset()
    return frozenset(DOMAIN_SKILLS.get(domain.strip(), ()))


def domain_node(domain: str | None) -> str | None:
    if not domain:
        return None
    return DOMAIN_NODES.get(domain.strip())


def canonicalize_domain(raw: str) -> str | None:
    key = (raw or "").strip()
    if not key:
        return None
    if key in DOMAIN_SKILLS:
        return key
    return _DOMAIN_ALIASES.get(key) or _DOMAIN_ALIASES.get(key.casefold())


def normalize_domains(
    raw_domains: list[str] | None,
    *,
    skill_name: str | None = None,
    intent: str | None = None,
) -> list[str]:
    """去重并按 DOMAIN_ORDER 排序：行程领域始终先于办公领域。"""
    found: set[str] = set()
    for item in raw_domains or []:
        domain = canonicalize_domain(str(item))
        if domain:
            found.add(domain)
    mapped = skill_domain(skill_name or "")
    if mapped:
        found.add(mapped)
    ordered = [name for name in DOMAIN_ORDER if name in found]
    if ordered:
        return ordered
    return []


def format_domain_catalog() -> str:
    lines: list[str] = []
    for name in DOMAIN_ORDER:
        skills = "、".join(DOMAIN_SKILLS[name])
        lines.append(f"- {name}: {DOMAIN_DESCRIPTIONS[name]}。可用 Skill：{skills}")
    return "\n".join(lines)


HANDOVER_MARK = "领域已完成】"


def is_domain_handover(message) -> bool:
    if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
        return False
    content = message.content if isinstance(message.content, str) else ""
    return content.strip().startswith("【") and HANDOVER_MARK in content


def _last_plain_ai(messages) -> str:
    for message in reversed(list(messages or [])):
        if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
            continue
        content = message.content if isinstance(message.content, str) else ""
        if content.strip():
            return content.strip()
    return ""


def first_domain_route(state: AgentState) -> DomainRoute:
    if state.get("fallback_reason"):
        return "fallback_agent"
    if state.get("needs_clarify"):
        return "clarify_agent"
    if state.get("intent") != "skill":
        return "chat_agent"
    domains = [item for item in (state.get("domains") or []) if item in DOMAIN_SKILLS]
    node = domain_node(domains[0] if domains else None)
    if node in {"trip_agent", "office_agent"}:
        return node
    return "fallback_agent"


def advance_domain(state: AgentState) -> dict:
    """当前领域 Agent 跑完后前进到下一个领域；没有后续则去压缩收尾。"""
    domains = [item for item in (state.get("domains") or []) if item in DOMAIN_SKILLS]
    if state.get("pending_clarify"):
        update: dict = {"domain_index": len(domains), "current_domain": None}
        question = str(state.get("clarify_question") or "").strip()
        if question and not question.endswith(("？", "?")):
            question = f"{question}？"
        visible = _last_plain_ai(state.get("messages") or [])
        if question and question.rstrip("？?") not in visible:
            update["messages"] = [AIMessage(content=question)]
        return update

    prev_idx = int(state.get("domain_index") or 0)
    next_idx = prev_idx + 1
    if next_idx >= len(domains):
        return {"domain_index": next_idx, "current_domain": None}

    prev = domains[prev_idx] if 0 <= prev_idx < len(domains) else ""
    nxt = domains[next_idx]
    prev_label = DOMAIN_LABELS.get(prev, prev)
    next_label = DOMAIN_LABELS.get(nxt, nxt)
    return {
        "domain_index": next_idx,
        "current_domain": nxt,
        "skill_call_count": Overwrite(0),
        "skill_stop_reason": Overwrite(None),
        "skill_token_count": 0,
        "messages": [
            AIMessage(
                content=(
                    f"【{prev_label}领域已完成】请{next_label}领域继续处理用户请求中尚未完成的部分。"
                    "条件判断必须基于上面脚本返回的数字，禁止编造。"
                    "上一领域若拒绝过不属于它的 Skill，那是正常交接，不是系统故障；本领域仍须调用自己的 Skill。"
                )
            )
        ],
    }


def route_after_advance(state: AgentState) -> DomainRoute:
    domains = [item for item in (state.get("domains") or []) if item in DOMAIN_SKILLS]
    idx = int(state.get("domain_index") or 0)
    if idx < 0 or idx >= len(domains):
        return "compact_skill"
    node = domain_node(domains[idx])
    return node if node in {"trip_agent", "office_agent"} else "compact_skill"
