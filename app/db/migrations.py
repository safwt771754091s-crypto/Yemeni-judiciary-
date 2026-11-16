"""Apply the SQL schema at startup so a deploy cannot run ahead of its schema.

The ``sql/*.sql`` files are the single source of truth for the database shape.
They are idempotent, but they were only ever applied by hand, so a deploy that
shipped code reading a new column could run against a database that never
received the migration (for example ``platform_users.email_verified`` from
``007_account_selfservice.sql``). Every login and signup then failed with
``UndefinedColumnError`` while the API itself looked healthy.

Applying the files on startup, tracked in a ``schema_migrations`` ledger, keeps
the schema in step with the code with no manual step. A failure is logged and
skipped rather than fatal: the app must still start (degraded) when the database
is unreachable, and a single bad migration must not take the whole platform down.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.db.pool import database

logger = get_logger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

_LEDGER = (
    "create table if not exists schema_migrations ("
    "filename text primary key, applied_at timestamptz not null default now())"
)


async def apply_migrations() -> int:
    """Apply every ``sql/*.sql`` file not yet recorded in the ledger.

    Returns the number of files applied. A no-op when no database is configured,
    so the offline test-suite and the in-memory mode are unaffected.
    """
    if not settings.database_enabled or not settings.auto_migrate:
        return 0
    if not SQL_DIR.is_dir():
        return 0

    async with database.acquire() as conn:
        await conn.execute(_LEDGER)

    applied = 0
    for path in sorted(SQL_DIR.glob("*.sql")):
        async with database.acquire() as conn:
            done = await conn.fetchval(
                "select 1 from schema_migrations where filename = $1", path.name
            )
            if done:
                continue
            try:
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    "insert into schema_migrations (filename) values ($1) "
                    "on conflict (filename) do nothing",
                    path.name,
                )
            except Exception as exc:
                # A broken migration is recorded as an error but not as applied,
                # so a later start retries it once the database is reachable.
                logger.error(
                    "migration failed", extra={"file": path.name, "error": str(exc)}
                )
                continue
            applied += 1
            logger.info("migration applied", extra={"file": path.name})
    return applied
