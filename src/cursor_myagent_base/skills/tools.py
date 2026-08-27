from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command, interrupt

from cursor_myagent_base.skills.errors import (
    CONTENT_TOO_LONG,
    DEFERRED_SKILL,
    EMAIL_CANCELLED,
    FORBIDDEN_RECIPIENT,
    FORBIDDEN_SKILL,
    INJECTION_BLOCKED,
    INVALID_MODE,
    MISSING_CITY,
    MISSING_DESTINATION,
    MISSING_ORIGIN,
    MISSING_TO,
    skill_error,
    skill_error_code,
)
from cursor_myagent_base.domains import DOMAIN_LABELS, allowed_skills, skill_domain
from cursor_myagent_base.skills.guard import (
    MAX_TOOL_CALLS,
    run_cache_key,
)
from cursor_myagent_base.skills.loader import (
    _normalize_arguments,
    load_skill_markdown,
    run_skill_script,
)
from cursor_myagent_base.skills.normalize import (
    as_question,
    canonical_mode,
    canonical_when,
    infer_city_from_place,
    infer_weather_city_from_text,
    is_city_clarify_question,
    last_human_content,
    resolve_run_city,
    normalize_text,
)
from cursor_myagent_base.skills.safety import (
    clip_or_reject_email_fields,
    is_confirm_resume,
    looks_like_injection,
    resolve_allowed_recipient,
    skip_email_confirm,
)

_NO_RETRY = "请用一句中文问句向用户反问缺的信息，然后结束本轮，不要猜测，不要用相同参数再次调用工具。"
_DEFERRED_HINT = (
    "这不是失败。请继续完成本领域步骤，不要再调用该 Skill，"
    "也不要告诉用户系统无法发信或无法使用该 Skill。"
)
_DUP_LOAD_TEXT = "Skill「{key}」的说明书已经加载过，请直接调用 run_skill，不要再次 load_skill。"


def _weather_city_from_state(state: dict) -> str:
    explicit = str(state.get("city") or "").strip()
    if explicit:
        return explicit
    user_text = last_human_content(state.get("messages") or [])
    return infer_weather_city_from_text(user_text) or (
        infer_city_from_place(user_text) if user_text and len(user_text) <= 12 else ""
    )


def _guard_email(to_value: str, subject_value: str, body_value: str) -> str | None:
    allowed = resolve_allowed_recipient(to_value)
    if allowed is None:
        print(f"[安全] 拒绝发信：收件人「{to_value}」不在通讯录白名单")
        return skill_error(
            FORBIDDEN_RECIPIENT,
            f"收件人「{to_value}」不在通讯录中，已拒绝发送。只能发给 contacts.json 里已登记的称呼或邮箱。",
        )
    too_long = clip_or_reject_email_fields(subject_value, body_value)
    if too_long:
        return skill_error(CONTENT_TOO_LONG, too_long)
    if looks_like_injection(to_value) or looks_like_injection(subject_value) or looks_like_injection(body_value):
        print("[安全] 拒绝发信：字段疑似提示词注入")
        return skill_error(INJECTION_BLOCKED, "发信参数疑似提示词注入，已拒绝发送。")
    return None


def _log_tool(count: int, tool_name: str, params: str, repeated: bool) -> None:
    line = f"[工具] #{count} {tool_name} {params} 重复={'是' if repeated else '否'}"
    if count >= MAX_TOOL_CALLS:
        line += f"  告警: 本轮工具调用已达上限 {MAX_TOOL_CALLS}"
    print(line)


def _finish_run(runtime: ToolRuntime, results: dict, cache_key: str, text: str) -> Command:
    if skill_error_code(text):
        text = _with_no_retry(text)
    results[cache_key] = text
    return Command(
        update={
            "messages": [_tool_message(runtime, "run_skill", text)],
            "skill_call_count": 1,
            "run_results": results,
            "last_tool": "run_skill",
        }
    )


def _with_no_retry(text: str) -> str:
    if _NO_RETRY in text:
        return text
    return f"{text}\n\n{_NO_RETRY}"


def _block_text(blocked: str) -> str:
    if skill_error_code(blocked) == DEFERRED_SKILL:
        if _DEFERRED_HINT in blocked:
            return blocked
        return f"{blocked}\n\n{_DEFERRED_HINT}"
    return _with_no_retry(blocked)


def _tool_message(runtime: ToolRuntime, name: str, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=runtime.tool_call_id or "",
        name=name,
    )


def _reject_outside_domain(state: dict, skill_name: str) -> str | None:
    domain = str(state.get("current_domain") or "").strip()
    allowed = allowed_skills(domain)
    key = normalize_text(skill_name or "")
    allowed_keys = {normalize_text(item) for item in allowed}
    if key and key in allowed_keys:
        return None
    owner = skill_domain(skill_name) or skill_domain(key)
    if owner and owner != domain:
        owner_label = DOMAIN_LABELS.get(owner, owner)
        current_label = DOMAIN_LABELS.get(domain, domain or "未指定")
        return skill_error(
            DEFERRED_SKILL,
            f"Skill「{skill_name}」属于{owner_label}领域，当前是{current_label}领域，不能在这里调用。"
            f"请完成本领域工作后结束；该 Skill 由后续{owner_label}领域处理。"
            "不要告诉用户系统无法发信，也不要问用户邮箱。",
        )
    available = "、".join(sorted(allowed)) or "无"
    label = domain or "未指定"
    return skill_error(
        FORBIDDEN_SKILL,
        f"当前领域「{label}」不能使用 Skill「{skill_name}」。本领域可用：{available}。",
    )


@tool
def ask_user(question: str, runtime: ToolRuntime) -> Command:
    """用户请求缺少关键信息、地点/收件人含糊时，向用户提一个简短问题。

    每次只问一件最影响执行的事。调用后结束本轮，等待用户回答，不要再 load_skill / run_skill。
    """
    state = runtime.state if isinstance(runtime.state, dict) else {}
    text = as_question(question) or "请再补充一下您的具体需求？"
    known_city = _weather_city_from_state(state)
    if known_city and is_city_clarify_question(text):
        count = int(state.get("skill_call_count") or 0) + 1
        _log_tool(count, "ask_user", f"skip-city-clarify city={known_city}", False)
        hint = (
            f"城市已确定为「{known_city}」。不要问用户哪座城市。"
            f"请立刻 run_skill(name=\"weather\", city=\"{known_city}\")。"
        )
        return Command(
            update={
                "messages": [_tool_message(runtime, "ask_user", hint)],
                "skill_call_count": 1,
                "city": known_city,
                "pending_clarify": False,
                "needs_clarify": False,
                "last_tool": "ask_user",
            }
        )
    count = int(state.get("skill_call_count") or 0) + 1
    _log_tool(count, "ask_user", f"question={text}", False)
    return Command(
        update={
            "messages": [_tool_message(runtime, "ask_user", f"请把下面这句话原样问用户，然后结束本轮：{text}")],
            "skill_call_count": 1,
            "pending_clarify": True,
            "clarify_question": text,
            "last_tool": "ask_user",
        }
    )


@tool
def load_skill(name: str, runtime: ToolRuntime) -> Command:
    """读取某个 Skill 的完整说明书（SKILL.md）。

    系统提示里只有 Skill 目录（名称 + 一句话简介）。若还不清楚参数、约束或调用方式，
    先调用本工具加载完整说明书，再执行 run_skill。
    """
    state = runtime.state if isinstance(runtime.state, dict) else {}
    key = normalize_text(name or "")
    blocked = _reject_outside_domain(state, key)
    loaded = list(state.get("loaded_skills") or [])
    count = int(state.get("skill_call_count") or 0) + 1
    repeated = key in loaded
    _log_tool(count, "load_skill", f"name={key}", repeated)
    if blocked:
        text = _block_text(blocked)
        return Command(
            update={
                "messages": [_tool_message(runtime, "load_skill", text)],
                "skill_call_count": 1,
                "last_tool": "load_skill",
            }
        )
    if repeated:
        text = _DUP_LOAD_TEXT.format(key=key)
    else:
        text = load_skill_markdown(key)
        if key:
            loaded.append(key)
        if skill_error_code(text):
            text = _with_no_retry(text)
    return Command(
        update={
            "messages": [_tool_message(runtime, "load_skill", text)],
            "skill_call_count": 1,
            "loaded_skills": loaded,
            "last_tool": "load_skill",
        }
    )


@tool
def run_skill(
    name: str,
    runtime: ToolRuntime,
    city: str = "",
    when: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    origin: str = "",
    destination: str = "",
    mode: str = "",
    waypoints: str = "",
    keywords: str = "",
    days: str = "",
    arguments_json: str = "",
) -> Command:
    """执行指定 Skill 的脚本并返回结果。

    天气查询时必须把城市放到 city 参数，例如：
    run_skill(name="weather", city="上海")
    查询明天天气时加上 when：
    run_skill(name="weather", city="上海", when="明天")

    发邮件时必须把收件人、主题、正文放到独立参数，例如：
    run_skill(name="email", to="someone@example.com", subject="主题", body="正文")
    to 也可以是 contacts.json 里的称呼。

    高德路线/行程规划时用 origin / destination / mode，例如：
    run_skill(name="amap", origin="北京南站", destination="故宫", mode="公交")
    前面若刚查过别的城市天气，amap 不要带那个 city。
    城市一日游：
    run_skill(name="amap", city="杭州", keywords="西湖")

    其他 Skill 的额外参数用 arguments_json，传入 JSON 字符串。
    """
    state = runtime.state if isinstance(runtime.state, dict) else {}
    skill_name = normalize_text(name or state.get("skill_name") or "")
    args: dict[str, Any] = _normalize_arguments(arguments_json)
    blocked = _reject_outside_domain(state, skill_name)
    when_raw = str(
        when or args.get("when") or args.get("date") or state.get("when") or ""
    ).strip()
    when_value = canonical_when(when_raw) if when_raw else ""
    to_value = str(
        to or args.get("to") or args.get("receiver") or args.get("email") or ""
    ).strip()
    subject_value = str(subject or args.get("subject") or args.get("title") or "").strip()
    body_value = str(body or args.get("body") or args.get("content") or "").strip()
    origin_value = str(
        origin
        or args.get("origin")
        or args.get("from")
        or args.get("start")
        or state.get("origin")
        or ""
    ).strip()
    destination_value = str(
        destination
        or args.get("destination")
        or args.get("dest")
        or args.get("end")
        or state.get("destination")
        or ""
    ).strip()
    mode_raw = str(mode or args.get("mode") or args.get("travel_mode") or "").strip()
    mode_value = canonical_mode(mode_raw) if mode_raw else "driving"
    waypoints_value = str(waypoints or args.get("waypoints") or args.get("via") or "").strip()
    keywords_value = str(
        keywords or args.get("keywords") or args.get("theme") or args.get("query") or ""
    ).strip()
    days_value = str(days or args.get("days") or "").strip()
    city_value = resolve_run_city(
        skill_name,
        city_param=str(city or args.get("city") or ""),
        origin=origin_value,
        destination=destination_value,
        state_city=str(state.get("city") or ""),
    )
    if skill_name == "weather" and not city_value:
        city_value = _weather_city_from_state(state)
    count = int(state.get("skill_call_count") or 0) + 1
    results = dict(state.get("run_results") or {})

    if blocked:
        cache_key = run_cache_key(skill_name, args)
        _log_tool(count, "run_skill", f"name={skill_name} domain-block", cache_key in results)
        text = results.get(cache_key) or _block_text(blocked)
        results[cache_key] = text
        return Command(
            update={
                "messages": [_tool_message(runtime, "run_skill", text)],
                "skill_call_count": 1,
                "run_results": results,
                "last_tool": "run_skill",
            }
        )

    if skill_name == "weather" and not city_value:
        cache_key = run_cache_key(skill_name, args)
        _log_tool(count, "run_skill", f"name={skill_name} city=", cache_key in results)
        text = results.get(cache_key) or _with_no_retry(
            skill_error(
                MISSING_CITY,
                "缺少城市名，无法查询天气。请反问用户：请问您要查哪座城市的天气？"
                "不要再次调用 load_skill，也不要猜测城市后重试 run_skill。",
            )
        )
        results[cache_key] = text
        return Command(
            update={
                "messages": [_tool_message(runtime, "run_skill", text)],
                "skill_call_count": 1,
                "run_results": results,
                "last_tool": "run_skill",
            }
        )

    if skill_name == "email" and not to_value:
        cache_key = run_cache_key(skill_name, args)
        _log_tool(count, "run_skill", f"name={skill_name} to=", cache_key in results)
        text = results.get(cache_key) or _with_no_retry(
            skill_error(
                MISSING_TO,
                "缺少收件人，无法发送邮件。请反问用户：请问这封邮件要发给谁？"
                "不要再次调用 load_skill，也不要猜测后重试 run_skill。",
            )
        )
        results[cache_key] = text
        return Command(
            update={
                "messages": [_tool_message(runtime, "run_skill", text)],
                "skill_call_count": 1,
                "run_results": results,
                "last_tool": "run_skill",
            }
        )

    if skill_name == "amap":
        missing_text = ""
        if mode_raw and not canonical_mode(mode_raw):
            missing_text = skill_error(
                INVALID_MODE,
                f"不支持的出行方式「{mode_raw}」。请使用驾车、步行、公交或骑行。",
            )
        elif origin_value and not destination_value and not city_value and not keywords_value:
            missing_text = skill_error(
                MISSING_DESTINATION,
                "缺少终点，无法规划路线。请反问用户：请问您要去哪里？"
                "不要再次调用 load_skill，也不要猜测后重试 run_skill。",
            )
        elif destination_value and not origin_value and not city_value and not keywords_value:
            missing_text = skill_error(
                MISSING_ORIGIN,
                "缺少起点，无法规划路线。请反问用户：请问您从哪里出发？"
                "不要再次调用 load_skill，也不要猜测后重试 run_skill。",
            )
        elif not origin_value and not destination_value and not city_value and not keywords_value:
            missing_text = skill_error(
                MISSING_ORIGIN,
                "缺少起点和终点（或城市）。请反问用户：请问从哪到哪，或要规划哪座城市的行程？"
                "不要再次调用 load_skill，也不要猜测后重试 run_skill。",
            )
        if missing_text:
            cache_key = run_cache_key(skill_name, args)
            _log_tool(count, "run_skill", f"name={skill_name}", cache_key in results)
            text = results.get(cache_key) or _with_no_retry(missing_text)
            results[cache_key] = text
            return Command(
                update={
                    "messages": [_tool_message(runtime, "run_skill", text)],
                    "skill_call_count": 1,
                    "run_results": results,
                    "last_tool": "run_skill",
                }
            )

    if city_value:
        args["city"] = city_value
    else:
        args.pop("city", None)
        args.pop("location", None)
    if when_value:
        args["when"] = when_value
    if to_value:
        args["to"] = to_value
    if subject_value:
        args["subject"] = subject_value
    if body_value:
        args["body"] = body_value
    if origin_value:
        args["origin"] = origin_value
    if destination_value:
        args["destination"] = destination_value
    if skill_name == "amap":
        args["mode"] = mode_value
    if waypoints_value:
        args["waypoints"] = waypoints_value
    if keywords_value:
        args["keywords"] = keywords_value
    if days_value:
        args["days"] = days_value
    if skill_name == "email":
        cfg = getattr(runtime, "config", None) or {}
        thread_id = str((cfg.get("configurable") or {}).get("thread_id") or "").strip()
        if thread_id:
            args["thread_id"] = thread_id
    cache_key = run_cache_key(skill_name, args)
    params = " ".join(
        [f"name={skill_name}"] + [f"{k}={v}" for k, v in sorted(args.items())]
    )
    repeated = cache_key in results
    _log_tool(count, "run_skill", params, repeated)
    if repeated:
        text = results[cache_key]
        return _finish_run(runtime, results, cache_key, text)

    if skill_name == "email":
        blocked = _guard_email(to_value, subject_value, body_value)
        if blocked:
            return _finish_run(runtime, results, cache_key, blocked)
        if not skip_email_confirm() and subject_value and body_value:
            allowed = resolve_allowed_recipient(to_value)
            display = allowed[1] if allowed else to_value
            decision = interrupt(
                {
                    "type": "confirm_email",
                    "to": display,
                    "email": allowed[0] if allowed else "",
                    "subject": subject_value,
                    "body": body_value,
                }
            )
            if not is_confirm_resume(decision):
                print("[安全] 用户未确认，已取消发送邮件")
                return _finish_run(
                    runtime,
                    results,
                    cache_key,
                    skill_error(EMAIL_CANCELLED, "用户未确认，已取消发送邮件。"),
                )

    text = run_skill_script(skill_name, args)
    return _finish_run(runtime, results, cache_key, text)
