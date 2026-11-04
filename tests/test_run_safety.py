"""Guards that keep a run from hanging.

A phase of an agent run timed out because a command never returned. The suite is
meant to be hermetic and fast, so two invariants are pinned here:

* pytest enforces a per-test timeout, so one blocked call fails the run instead
  of hanging it forever;
* every outbound connection is bounded, so an unreachable database raises
  instead of stalling until the OS gives up.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.db.pool import Database

ROOT = Path(__file__).resolve().parents[1]


def test_pytest_enforces_a_per_test_timeout() -> None:
    config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "timeout" in config, (
        "pytest.ini must set a timeout so a hanging test fails instead of "
        "blocking the whole run"
    )


def test_pytest_timeout_is_a_declared_dependency() -> None:
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "pytest-timeout" in requirements


def test_database_connect_timeout_is_configured() -> None:
    assert settings.db_connect_timeout > 0
    assert settings.db_connect_timeout <= 60


def test_pool_creation_passes_the_connect_timeout() -> None:
    """The pool must actually use the setting, not just declare it."""
    source = Path(Database.connect.__code__.co_filename).read_text(encoding="utf-8")
    assert "timeout=settings.db_connect_timeout" in source


def test_apply_sql_passes_the_connect_timeout() -> None:
    source = (ROOT / "scripts" / "apply_sql.py").read_text(encoding="utf-8")
    assert "timeout=settings.db_connect_timeout" in source


def test_docs_do_not_teach_a_blocking_command() -> None:
    """The README must not hand an agent a command that never returns.

    Runs timed out because a command blocked until the automation deadline. The
    usual culprit is a server started in the foreground, or a log tailed with
    ``-f``. The docs are what an agent follows, so they must stay safe.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uvicorn app.main:app" in readme, "the run section should still document the server"
    for line in readme.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "logs -f" not in stripped, f"README teaches a blocking log tail: {stripped}"
        if "uvicorn app.main:app" in stripped:
            assert stripped.startswith("nohup") or "&" in stripped or stripped.startswith("pkill"), (
                f"README starts the server in the foreground: {stripped}"
            )
