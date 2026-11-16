"""The app must find the database under any name a host injects.

NEON_DATABASE_URL is the platform's documented variable. A host may instead
inject the same string as DATABASE_URL (Vercel↔Neon integration, Heroku),
POSTGRES_URL or POSTGRES_PRISMA_URL, and SUPABASE_DB_URL remains a legacy
alias. Missing all of them silently downgrades the app to the per-instance
in-memory store, so accounts vanish on a cold start.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings

POSTGRES = "postgresql://user:pw@host.neon.tech/db"
ALIASES = [
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "SUPABASE_DB_URL",
]
ALL_NAMES = ["NEON_DATABASE_URL", *ALIASES]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_adopts_url_under_any_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, POSTGRES)
    settings = Settings()
    assert settings.database_url == POSTGRES
    assert settings.database_enabled


def test_neon_database_url_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEON_DATABASE_URL", POSTGRES)
    monkeypatch.setenv("DATABASE_URL", "postgresql://other:pw@other.host/db")
    assert Settings().database_url == POSTGRES


def test_legacy_supabase_db_url_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_DB_URL", POSTGRES)
    assert Settings().database_url == POSTGRES


def test_non_postgres_alias_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pw@host/db")
    assert not Settings().database_enabled


def test_no_url_keeps_in_memory_mode() -> None:
    assert not Settings().database_enabled
    assert Settings().database_url is None
