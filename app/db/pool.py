"""asyncpg connection pool management."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.errors import DatabaseUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Thin async wrapper around an asyncpg pool.

    The pool is created lazily and the app stays usable (degraded, in-memory)
    when SUPABASE_DB_URL is not configured - important for local development and
    for the test-suite.
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return settings.database_enabled

    @property
    def connected(self) -> bool:
        return self._pool is not None and not self._pool._closed

    async def connect(self) -> None:
        if not self.enabled or self._pool is not None:
            return
        async with self._lock:
            if self._pool is not None:
                return
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=settings.supabase_db_url,
                    min_size=settings.db_min_pool,
                    max_size=settings.db_max_pool,
                    command_timeout=settings.db_command_timeout,
                    timeout=settings.db_connect_timeout,
                    init=self._init_connection,
                )
                logger.info("database pool created", extra={"max_size": settings.db_max_pool})
            except Exception as exc:
                logger.error("database connection failed", extra={"error": str(exc)})
                raise DatabaseUnavailableError(
                    "تعذّر الاتصال بقاعدة البيانات", details=str(exc)
                ) from exc

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await conn.set_type_codec(
            "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("database pool closed")

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        if not self.enabled:
            raise DatabaseUnavailableError("قاعدة البيانات غير مُهيأة (SUPABASE_DB_URL مفقود)")
        if self._pool is None:
            await self.connect()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.acquire() as conn, conn.transaction():
            yield conn

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def healthy(self) -> bool:
        if not self.enabled:
            return False
        try:
            async with self.acquire() as conn:
                return await conn.fetchval("select 1") == 1
        except Exception:
            return False


database = Database()
