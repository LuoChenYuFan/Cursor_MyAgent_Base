from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from cursor_myagent_base.config import require_postgres_uri

# 每一步完成后再写盘，宕机时才能从最后一个成功节点续跑（默认 async 可能丢掉最后一步）。
CHECKPOINT_DURABILITY = "sync"

_POOL_KWARGS = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}


@contextmanager
def postgres_checkpointer() -> Iterator[PostgresSaver]:
    """打开连接池、建表（如需要），在整个 CLI 生命周期内保持 checkpointer 可用。"""
    uri = require_postgres_uri()
    with ConnectionPool(conninfo=uri, min_size=1, max_size=8, kwargs=_POOL_KWARGS) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver


@asynccontextmanager
async def async_postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """FastAPI 用异步连接池，大模型等待时不占满工作线程。"""
    uri = require_postgres_uri()
    async with AsyncConnectionPool(
        conninfo=uri,
        min_size=1,
        max_size=8,
        kwargs=_POOL_KWARGS,
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
