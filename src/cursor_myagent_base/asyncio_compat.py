from __future__ import annotations

import asyncio
import selectors
import sys


def new_selector_event_loop() -> asyncio.AbstractEventLoop:
    """Windows 上给 psycopg 异步连接用 SelectorEventLoop（不能用默认 Proactor）。"""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def use_selector_event_loop_policy() -> None:
    """anyio / TestClient 通过 new_event_loop() 建环时，避免 Windows ProactorEventLoop。"""
    if sys.platform != "win32":
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
