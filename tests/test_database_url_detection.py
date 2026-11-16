"""The app must find the database under any name a host injects.

SUPABASE_DB_URL is the documented variable, but Vercel's Neon integration and
Heroku-style hosts expose the same connection string as DATABASE_URL /
POSTGRES_URL / NEON_DATABASE_URL. Missing that fallback silently downgrades the
app to the per-instance in-memory store, so accounts vanish on a cold start.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings

POSTGRES = "postgresql://user:pw@host.neon.tech/db"
ALIASES = [
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "NEON_DATABASE_URL",
]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["SUPABASE_DB_URL", *ALIASES]:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", ALIASES)
def test_adopts_alias_url(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, POSTGRES)
    settings = Settings()
    assert settings.supabase_db_url == POSTGRES
    assert settings.database_enabled


def test_supabase_db_url_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_DB_URL", POSTGRES)
    monkeypatch.setenv("DATABASE_URL", "postgresql://other:pw@other.host/db")
    assert Settings().supabase_db_url == POSTGRES


def test_non_postgres_alias_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pw@host/db")
    assert not Settings().database_enabled


def test_no_url_keeps_in_memory_mode() -> None:
    assert not Settings().database_enabled
