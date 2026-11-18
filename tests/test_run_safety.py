"""Guards that keep a run from hanging.

A phase of an agent run timed out because a command never returned. The suite is
meant to be hermetic and fast, so two invariants are pinned here:

* pytest enforces a per-test timeout, so one blocked call fails the run instead
  of hanging it forever;
* every outbound connection is bounded, so an unreachable database raises
  instead of stalling until the OS gives up.
"""
from __future__ import annotations

import ast
import re
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
    assert "command_timeout=settings.db_command_timeout" in source


def _asyncpg_connect_calls(source: str) -> list[ast.Call]:
    """Every ``asyncpg.connect(...)`` call in a module, found structurally.

    A substring check cannot tell a connect timeout from a statement timeout, and
    that is exactly how an unbounded ``apply_sql.py`` slipped through: the guard
    matched ``timeout=settings.db_connect_timeout`` and never noticed the missing
    ``command_timeout``. Walking the AST makes the check see the real call.
    """
    tree = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncpg"
        ):
            calls.append(node)
    return calls


def test_every_direct_asyncpg_connect_is_bounded() -> None:
    """A direct connect must set both the connect and the statement timeout.

    ``timeout`` only bounds establishing the connection. Without
    ``command_timeout`` a single stuck statement - a ``create index`` over
    pgvector, an ``alter table`` waiting on a lock - runs until the automation
    itself is killed. That is what timed out this repository's runs.
    """
    checked = 0
    for folder in ("app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            calls = _asyncpg_connect_calls(path.read_text(encoding="utf-8"))
            for call in calls:
                checked += 1
                kwargs = {kw.arg for kw in call.keywords}
                assert "timeout" in kwargs, f"{path}: asyncpg.connect without a connect timeout"
                assert "command_timeout" in kwargs, (
                    f"{path}: asyncpg.connect without command_timeout - a stuck "
                    f"statement would hang the run"
                )
    assert checked > 0, "expected to find at least one direct asyncpg.connect call"


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


#: External commands a deploy script runs that can block forever on a bad network
#: or a stuck daemon. ``timeout`` around each one turns a hang into a fast failure.
BLOCKING_COMMANDS = ("apt-get", "psql", "createdb", "pg_ctlcluster", "docker")


def _shell_scripts() -> list[Path]:
    return sorted((ROOT / "scripts").glob("*.sh"))


def _invocations(line: str) -> list[str]:
    """Split a shell line into the command segments that could block.

    Leading keywords (``if``, ``!``, ``then``, ``do``) and the separators between
    commands are stripped, so ``if ! run sudo ... ; then`` is recognised as the
    wrapped call it is rather than as a bare ``sudo``.
    """
    segments: list[str] = []
    for part in line.replace(";", "&&").split("&&"):
        part = part.replace("||", "&&")
        segments.extend(piece.strip() for piece in part.split("&&") if piece.strip())
    cleaned: list[str] = []
    for segment in segments:
        for keyword in ("if ", "then ", "do ", "while ", "! "):
            while segment.startswith(keyword):
                segment = segment[len(keyword):].lstrip()
        if segment:
            cleaned.append(segment)
    return cleaned


def _is_bounded(segment: str) -> bool:
    """True when the segment runs its command through the ``timeout`` helpers.

    ``run``/``run_long``/``timeout`` are matched as whole words, so they count
    whether they lead the command (``run sudo ...``), follow an assignment
    (``PGPASSWORD=x run psql ...``) or sit inside a command substitution
    (``$(run docker port ...)``).
    """
    return re.search(r"(?<![\w-])(run|run_long|timeout)(?![\w-])", segment) is not None


def test_deploy_scripts_bound_every_blocking_command() -> None:
    """A deploy script must not run an unbounded external command.

    An automation run timed out while a deploy script sat in a step that never
    returned. ``apt-get``, ``psql`` or ``docker`` can each block on a network that
    never answers or a lock that never clears, so every call must go through the
    ``run``/``run_long`` helpers, which wrap it in ``timeout``.
    """
    scripts = _shell_scripts()
    assert scripts, "expected deploy scripts under scripts/"
    for path in scripts:
        source = path.read_text(encoding="utf-8")
        assert "run() { timeout" in source, f"{path}: no bounded-command helper"
        for number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for segment in _invocations(stripped):
                for command in BLOCKING_COMMANDS:
                    if command not in segment:
                        continue
                    # Safe when wrapped, mentioned in a message, or an availability check.
                    if _is_bounded(segment):
                        continue
                    if "have " in segment or "command -v" in segment:
                        continue
                    if "die " in segment or "log " in segment or "printf" in segment:
                        continue
                    raise AssertionError(
                        f"{path}:{number}: unbounded '{command}' call would hang a run: "
                        f"{segment}"
                    )


def test_deploy_scripts_declare_a_timeout_bound() -> None:
    """The bound must be a named, overridable value, not a magic number inline."""
    for path in _shell_scripts():
        source = path.read_text(encoding="utf-8")
        assert "CMD_TIMEOUT=" in source, f"{path}: no short-command timeout declared"
        assert "LONG_TIMEOUT=" in source, f"{path}: no long-command timeout declared"
