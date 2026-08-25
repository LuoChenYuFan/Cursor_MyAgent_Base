from __future__ import annotations

from langgraph.errors import GraphInterrupt, GraphRecursionError

from cursor_myagent_base.checkpointer import postgres_checkpointer
from cursor_myagent_base.config import get_agent_thread_id
from cursor_myagent_base.domains import format_domain_catalog
from cursor_myagent_base.graph import build_graph
from cursor_myagent_base.skills.loader import format_catalog
from cursor_myagent_base.skills.safety import classify_email_confirm
from cursor_myagent_base.turns import (
    confirm_email,
    continue_pending,
    inspect_session,
    last_ai_reply,
    run_new_turn,
)


class EmailConfirmAborted(Exception):
    """在发信确认处停掉进程：不写入取消，保留 interrupt 等下次启动。"""


def _print_session_status(graph, thread_id: str) -> None:
    info = inspect_session(graph, thread_id)
    if info["status"] == "needs_confirmation":
        print(f"会话 thread_id={thread_id}")
        print("检测到待确认发信，需你确认后才会真正发送。\n")
        return
    if info["status"] == "pending_run":
        nxt = ", ".join(info["next"]) or "—"
        pending = info["last_user"] or "（上一轮请求）"
        print(f"会话 thread_id={thread_id}")
        print(f"检测到未完成任务，将从节点 [{nxt}] 断点续跑。")
        print(f"上次请求：{pending}\n")
        return
    if info["status"] == "idle":
        print(f"会话 thread_id={thread_id}（新会话，checkpoint 写入 PostgreSQL）")
        return
    print(f"已从 PostgreSQL 恢复会话 thread_id={thread_id}，当前 {info['message_count']} 条消息")
    reply = info.get("reply") or ""
    if reply and reply != "(没有生成回复)":
        print(f"上次助手回复：{reply}\n")


def _print_turn(result: dict) -> None:
    values = result.get("values") or result
    intent = values.get("intent") or "chat"
    skill_name = values.get("skill_name") or "—"
    domains = " > ".join(values.get("domains") or []) or "—"
    current_domain = values.get("current_domain") or "—"
    city = values.get("city") or "—"
    origin = values.get("origin") or "—"
    destination = values.get("destination") or "—"
    when = values.get("when") or "—"
    reason = values.get("reason") or "—"
    clarify = "是" if values.get("needs_clarify") or values.get("pending_clarify") else "否"
    fallback = values.get("fallback_reason") or "—"
    print(
        f"[路由] intent={intent}  domains={domains}  current={current_domain}  "
        f"skill={skill_name}  city={city}  origin={origin}  dest={destination}  "
        f"when={when}  反问={clarify}  兜底={fallback}  reason={reason}"
    )
    token_count = values.get("skill_token_count") or 0
    if token_count:
        print(f"[约束] 本轮估算 token={token_count}")
    stop_reason = values.get("skill_stop_reason")
    if stop_reason is not None and type(stop_reason).__name__ == "Overwrite":
        stop_reason = getattr(stop_reason, "value", None)
    if stop_reason:
        print(f"[约束] 本轮硬停止原因={stop_reason}")
    messages = list(values.get("messages") or [])
    print(f"助手: {last_ai_reply(messages)}\n")


def _prompt_email_confirm(payload: dict) -> bool:
    print("[安全] 发信前确认（防止提示词注入改收件人）")
    print(f"  收件人：{payload.get('to') or '—'}")
    if payload.get("email"):
        print(f"  邮箱：{payload.get('email')}")
    print(f"  主题：{payload.get('subject') or '—'}")
    body = str(payload.get("body") or "")
    preview = body if len(body) <= 300 else body[:300] + "…"
    print(f"  正文：{preview}")
    print("输入「确认」发送，输入「取消」放弃。关掉窗口或 Ctrl+C 会保留这次确认，下次启动再问。")
    while True:
        try:
            answer = input("确认发信: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            print("\n已中断。没有写入取消，发信仍待确认。")
            raise EmailConfirmAborted from exc
        decision = classify_email_confirm(answer)
        if decision == "approve":
            return True
        if decision == "reject":
            return False
        print("请输入「确认」发送，或「取消」放弃。")


def _drain_confirms(graph, thread_id: str, result: dict) -> dict:
    while result.get("status") == "needs_confirmation" and result.get("confirm"):
        approved = _prompt_email_confirm(result["confirm"])
        result = confirm_email(graph, thread_id, approved)
    return result


def _invoke_new_turn(graph, thread_id: str, user_text: str) -> dict:
    result = run_new_turn(graph, thread_id, user_text)
    return _drain_confirms(graph, thread_id, result)


def _try_resume_pending(graph, thread_id: str) -> None:
    info = inspect_session(graph, thread_id)
    if info["status"] == "needs_confirmation":
        try:
            result = _drain_confirms(graph, thread_id, info)
        except GraphRecursionError:
            print("助手: 工具调用次数过多，已停止。请换一种问法。\n")
            return
        except EmailConfirmAborted:
            raise
        except KeyboardInterrupt:
            print("\n发信确认被中断。下次启动将再次等待确认。")
            raise
        if result.get("status") == "ok":
            print("发信确认流程结束。")
            _print_turn(result)
        return
    if info["status"] != "pending_run":
        return
    try:
        result = continue_pending(graph, thread_id)
        result = _drain_confirms(graph, thread_id, result)
    except GraphInterrupt:
        result = _drain_confirms(graph, thread_id, inspect_session(graph, thread_id))
    except EmailConfirmAborted:
        raise
    except GraphRecursionError:
        print("助手: 工具调用次数过多，已停止。请换一种问法。\n")
        return
    except KeyboardInterrupt:
        print("\n续跑被中断。下次启动将再次从断点继续。")
        raise
    if result is not None:
        print("断点续跑完成。")
        _print_turn(result)


def run_cli() -> None:
    print("多 Agent 助手已启动（意图识别 → 行程/办公领域专家 / 聊天）")
    print("领域目录：")
    print(format_domain_catalog())
    print("Skills 目录（仅名称和简介，完整说明书按需加载）：")
    print(format_catalog())
    print("\n输入问题开始对话，输入 quit 退出。\n")
    thread_id = get_agent_thread_id()
    try:
        with postgres_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            _print_session_status(graph, thread_id)
            try:
                _try_resume_pending(graph, thread_id)
            except EmailConfirmAborted:
                print("已退出。发信仍待确认，下次启动会再次询问。")
                return
            except KeyboardInterrupt:
                return
            while True:
                try:
                    user_text = input("你: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n已退出。未完成的任务下次启动会从断点继续。")
                    break
                if not user_text:
                    continue
                if user_text.lower() in {"quit", "exit", "q"}:
                    print("已退出。未完成的任务下次启动会从断点继续。")
                    break
                try:
                    result = _invoke_new_turn(graph, thread_id, user_text)
                except GraphRecursionError:
                    print("助手: 工具调用次数过多，已停止。请换一种问法。\n")
                    continue
                except EmailConfirmAborted:
                    print("已退出。发信仍待确认，下次启动会再次询问。")
                    break
                except KeyboardInterrupt:
                    print("\n任务未完成（例如邮件还没发）。请重新运行同一条启动命令，不要再输入一遍，系统会从断点继续。")
                    break
                _print_turn(result)
    except RuntimeError as exc:
        print(exc)


def main() -> None:
    run_cli()
