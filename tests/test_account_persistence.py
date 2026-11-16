"""Guards for account persistence and the schema migrations they depend on.

A deploy shipped code that read ``platform_users.email_verified`` while the
database had never received ``007_account_selfservice.sql``. Every login and
signup then failed with ``UndefinedColumnError``, so the platform looked like it
was "forgetting" accounts and sessions. These tests pin the two fixes:

* startup applies ``sql/*.sql`` through a tracked ledger, so the schema can no
  longer drift behind the code;
* the browser keeps the session token across a tab close (localStorage), not in
  sessionStorage, which is discarded when the tab ends.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.migrations import _LEDGER, SQL_DIR, apply_migrations

ROOT = Path(__file__).resolve().parents[1]


def test_every_sql_file_is_present_and_readable() -> None:
    files = sorted(SQL_DIR.glob("*.sql"))
    assert files, "the sql/ directory is the schema source of truth"
    for path in files:
        assert path.read_text(encoding="utf-8").strip(), f"{path.name} is empty"


def test_account_selfservice_migration_is_shipped() -> None:
    """The migration the login query depends on must exist."""
    sql = (SQL_DIR / "007_account_selfservice.sql").read_text(encoding="utf-8")
    assert "email_verified" in sql
    assert "account_tokens" in sql


def test_user_columns_match_the_login_query() -> None:
    """Every column the login SELECT reads must be created by a migration.

    This is the exact drift that broke login: the query selected
    ``email_verified`` but no migration on the deployed database had added it.
    """
    from app.db.user_repository import _USER_COLUMNS

    columns = {c.strip() for c in _USER_COLUMNS.split(",")}
    corpus = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(SQL_DIR.glob("*.sql"))
    )
    missing = sorted(c for c in columns if c not in corpus)
    assert not missing, f"columns read by the login query but never created: {missing}"


def test_migrations_run_at_startup() -> None:
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "apply_migrations()" in main_src, (
        "startup must apply migrations so a deploy cannot run ahead of its schema"
    )


def test_auto_migrate_is_on_by_default() -> None:
    assert settings.auto_migrate is True


def test_migration_ledger_is_idempotent_by_name() -> None:
    assert "primary key" in _LEDGER.lower()
    assert "schema_migrations" in _LEDGER


def test_apply_migrations_is_a_noop_without_a_database() -> None:
    """The offline suite and the in-memory mode must never touch sql/."""
    assert settings.database_enabled is False
    assert asyncio.run(apply_migrations()) == 0


@pytest.mark.parametrize("page", ["portal.js", "admin.html"])
def test_session_token_survives_a_tab_close(page: str) -> None:
    """The token store must be localStorage, not sessionStorage.

    sessionStorage is cleared when the tab or the installed PWA closes, which is
    what made a signed-in user appear logged out after reopening the platform.
    """
    source = (ROOT / "app" / "static" / page).read_text(encoding="utf-8")
    assert "localStorage" in source, f"{page} must persist the token in localStorage"
    # A read from sessionStorage is allowed only as the one-time migration path.
    if "sessionStorage.getItem" in source:
        assert "localStorage.setItem" in source
