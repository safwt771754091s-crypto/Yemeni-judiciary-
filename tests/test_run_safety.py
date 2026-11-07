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


def test_makefile_warns_about_foreground_servers() -> None:
    """Every ``make`` target that starts the server in the foreground must warn.

    An automation run was killed because it called such a target and blocked
    until the deadline: the server never exits, so the command never returns.
    Targets that background the server (``make serve``/``make deploy``) are
    safe; the rest must carry a "never return" warning on the target line so an
    agent reading ``make help`` or the Makefile is warned off.
    """
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets: list[tuple[str, list[str]]] = []
    for line in makefile.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():  # recipe line for the current target
            if targets:
                targets[-1][1].append(line.strip())
        elif ":" in line and "=" not in line.split(":", 1)[0]:  # target definition
            targets.append((line, []))

    def starts_server_in_foreground(header: str, recipe: list[str]) -> bool:
        for line in [header, *recipe]:
            if "uvicorn app.main:app" not in line:
                continue
            if line.startswith("nohup") or "&" in line or "scripts/" in line:
                continue
            return True
        return False

    foreground = [
        header for header, recipe in targets if starts_server_in_foreground(header, recipe)
    ]
    assert foreground, "expected the Makefile to still document the server"
    for header in foreground:
        assert "never return" in header, (
            "a Makefile target starts the server in the foreground without a "
            "'never return' warning on its line: " + header
        )
