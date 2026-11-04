# AGENTS.md

Operational notes for any agent working in this repository.

## The test suite is offline and must stay fast

`tests/conftest.py` forces `SUPABASE_DB_URL=""`, `EMBEDDING_PROVIDER=hash`,
`LLM_PROVIDER=mock` and `N8N_ENABLED=false` before the app is imported, so the
suite needs no database, no network and no secrets. It runs in about 15 seconds.

The suite seeds from `app/data/samples/yemeni_laws_excerpt.json`, a trimmed copy
of the full corpus. The excerpt must contain every code the case-type routing in
`app/services/retrieval.py` names, or that case type retrieves nothing in the
tests. Add the code to `KEEP` in `scripts/make_test_excerpt.py`, run
`python scripts/make_test_excerpt.py`, and the guard test in
`tests/test_retrieval.py` will keep the two in step.

```bash
.venv/bin/python -m pytest -q      # ~15s, no network
.venv/bin/ruff check app tests scripts
```

Run these two commands after any change. Do not add tests that call a real
database or a hosted LLM.

## Never run a command that can block forever

An automation run failed because a single command ran for ~12 minutes without
output and was killed. Every command you run must be able to finish on its own:

- Prefer the bounded test command above over ad-hoc scripts.
- If you must run something that touches the network or a database, bound it
  (`timeout 120 <cmd>`, or a client-level timeout) so it fails instead of
  hanging.
- Never start the dev server in the foreground of a command you need to return
  from: it never exits, so the command hangs until it is killed. Start it in the
  background with its output redirected
  (`... > /tmp/server.log 2>&1 &`), poll it with `curl`, and stop it by PID.
- `pytest.ini` sets a 120s per-test timeout (`pytest-timeout`) and
  `tests/conftest.py` sets a 10s default socket timeout. Keep both. If you add a
  dependency that opens connections, give it an explicit timeout.

## Database connections

`db_connect_timeout` in `app/core/config.py` (default 10s) bounds connection
establishment; `db_command_timeout` (default 60s) bounds query execution. Pass
`timeout=settings.db_connect_timeout` to any new `asyncpg` call. The app is
designed to start degraded when the database is unreachable — never make
startup depend on a successful connection.

## Layout

- `app/core` — config, Arabic text normalisation, money parsing, security.
- `app/schemas` — Pydantic request/response and draft-judgment models.
- `app/services` — ingestion, retrieval, evidence, commercial, judgment, pipeline.
- `app/llm` — provider, prompts, chains, grounding validator.
- `app/static` — the frontend. `landing.html` (public intro, served at `/`),
  `index.html` (judgment generator), `workspace.html` + `workspace.js` +
  `workspace.css` (the chat-first Harvey-style workspace), `install.html`
  (Android install guide), plus the shared `portal.*` runtime and `sw.js`.
- `tests` — offline suite; `tests/conftest.py` holds the shared fixtures.

### Frontend pages are asserted by tests

`tests/test_pwa_install.py` requires every page in its `INSTALL_PAGES` list to
load `/ui/install.js` and to render an `id="installBtn"` (in markup or from the
page's shell script). `tests/test_portals_assistant.py` asserts the static
assets are served and that `/` returns the landing page. When you add a page,
add it to `INSTALL_PAGES` and to the static-asset list, and regenerate the
manifest screenshots if the layout changes.
