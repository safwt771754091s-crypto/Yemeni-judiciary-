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


# A DSN pasted from a dashboard carries stray quotes or a newline, and some
# consoles wrap it in `psql '...'`. Accepting those made the app report
# `configured: true` while every connection failed, because `configured` is
# what tells an operator the store is durable. Reject them instead.


@pytest.mark.parametrize(
    "raw",
    [
        f'"{POSTGRES}"',
        f"'{POSTGRES}'",
        f"  {POSTGRES}  ",
        f"{POSTGRES}\n",
        POSTGRES.replace("postgresql://", "postgres://"),
    ],
)
def test_stray_quoting_and_whitespace_are_cleaned(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("NEON_DATABASE_URL", raw)
    settings = Settings()
    assert settings.database_url == POSTGRES
    assert settings.database_enabled


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://",
        "postgresql:///neondb",
        "psql 'postgresql://user:pw@host/db'",
        "postgresql://user:pa[ss@host/db",
        "https://example.com",
        "",
    ],
)
def test_unusable_values_are_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("NEON_DATABASE_URL", raw)
    assert not Settings().database_enabled


def test_unusable_value_falls_through_to_the_next_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://")
    monkeypatch.setenv("DATABASE_URL", POSTGRES)
    assert Settings().database_url == POSTGRES
