from langchain_core.messages import AIMessage, HumanMessage

from cursor_myagent_base.domains import (
    allowed_skills,
    first_domain_route,
    is_domain_handover,
    normalize_domains,
    route_after_advance,
    skill_domain,
)
from cursor_myagent_base.skills.budget import tokens_used_this_turn
from cursor_myagent_base.skills.errors import skill_error_code
from cursor_myagent_base.skills.loader import format_catalog
from cursor_myagent_base.skills.tools import _reject_outside_domain


def test_skill_belongs_to_domain() -> None:
    assert skill_domain("weather") == "trip"
    assert skill_domain("amap") == "trip"
    assert skill_domain("email") == "office"
    assert skill_domain("unknown") is None
    print("通过：Skill 能映射到领域")


def test_normalize_domains_orders_trip_before_office() -> None:
    assert normalize_domains(["office", "trip"]) == ["trip", "office"]
    assert normalize_domains(["office"], skill_name="weather") == ["trip", "office"]
    assert normalize_domains([], skill_name="email") == ["office"]
    assert normalize_domains([], skill_name="amap") == ["trip"]
    assert normalize_domains([], intent="chat") == []
    assert normalize_domains([], intent="skill") == []
    print("通过：跨领域时行程在办公前面")


def test_allowed_skills_are_isolated() -> None:
    assert allowed_skills("trip") == frozenset({"weather", "amap"})
    assert allowed_skills("office") == frozenset({"email"})
    assert allowed_skills(None) == frozenset()
    assert "email" not in allowed_skills("trip")
    print("通过：领域 Skill 白名单隔离")


def test_first_route_and_advance() -> None:
    assert first_domain_route({"intent": "chat", "domains": []}) == "chat_agent"
    assert first_domain_route({"intent": "skill", "domains": ["office"]}) == "office_agent"
    assert first_domain_route({"intent": "skill", "domains": ["trip", "office"]}) == "trip_agent"
    assert first_domain_route(
        {"intent": "skill", "domains": ["trip"], "needs_clarify": True}
    ) == "clarify_agent"
    assert first_domain_route({"intent": "skill", "domains": []}) == "fallback_agent"
    assert first_domain_route(
        {"intent": "skill", "domains": ["trip"], "fallback_reason": "llm_error"}
    ) == "fallback_agent"
    assert route_after_advance({"domains": ["trip"], "domain_index": 1}) == "compact_skill"
    assert route_after_advance({"domains": ["trip", "office"], "domain_index": 1}) == "office_agent"
    print("通过：意图后进入对应领域，跑完后能交接或收尾")


def test_catalog_can_filter_by_domain() -> None:
    trip = format_catalog(allowed_skills("trip"))
    assert "weather" in trip
    assert "amap" in trip
    assert "email:" not in trip
    office = format_catalog(allowed_skills("office"))
    assert "email" in office
    assert "weather:" not in office
    print("通过：领域专家只能看见自己的 Skill 目录")


def test_tools_reject_skill_outside_domain() -> None:
    deferred = _reject_outside_domain({"current_domain": "trip"}, "email")
    assert deferred is not None
    assert skill_error_code(deferred) == "DEFERRED_SKILL"
    assert "无法发信" in deferred
    assert _reject_outside_domain({"current_domain": "trip"}, "weather") is None
    assert skill_error_code(_reject_outside_domain({"current_domain": "office"}, "amap")) == "DEFERRED_SKILL"
    assert _reject_outside_domain({"current_domain": "office"}, "email") is None
    print("通过：跨领域 Skill 会延期给对的专家，本领域 Skill 仍可用")


def test_office_token_budget_starts_after_handover() -> None:
    handover = AIMessage(content="【行程领域已完成】请办公领域继续处理用户请求中尚未完成的部分。")
    assert is_domain_handover(handover)
    padded = "路线结果。" * 4000
    messages = [
        HumanMessage(content="查北京天气再发信"),
        AIMessage(content=padded),
        handover,
        AIMessage(content="准备发信"),
    ]
    used = tokens_used_this_turn(messages)
    whole = tokens_used_this_turn(messages[:2])
    assert used < whole
    print("通过：交接后办公领域不把行程的 token 算进自己的预算")


if __name__ == "__main__":
    test_skill_belongs_to_domain()
    test_normalize_domains_orders_trip_before_office()
    test_allowed_skills_are_isolated()
    test_first_route_and_advance()
    test_catalog_can_filter_by_domain()
    test_tools_reject_skill_outside_domain()
    test_office_token_budget_starts_after_handover()
    print("全部通过")
