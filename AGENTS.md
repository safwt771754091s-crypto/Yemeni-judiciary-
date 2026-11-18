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
- Never tail a log without a bound. `docker compose logs -f api` and
  `tail -f` never return; use `--tail=50` / `tail -n 50` instead. The docs in
  `README.md` follow this rule, and `tests/test_run_safety.py` fails the suite
  if a foreground server or a `logs -f` reappears in them.
- `pytest.ini` sets a 120s per-test timeout (`pytest-timeout`) and
  `tests/conftest.py` sets a 10s default socket timeout. Keep both. If you add a
  dependency that opens connections, give it an explicit timeout.
- A direct `asyncpg.connect(...)` must pass **both** `timeout` (establishing the
  connection) and `command_timeout` (each statement). `timeout` alone leaves a
  stuck `create index` or a lock-waiting `alter table` running forever, which is
  what killed the runs; the pool already sets both. `tests/test_run_safety.py`
  walks the AST of every `asyncpg.connect` call and fails if either is missing.
- The Makefile's `dev` and `run` targets start the server in the foreground and
  never return. Do not call them from an automated command; use `make serve` or
  `make deploy`, which background the server. `tests/test_run_safety.py` fails
  if either target loses its "never return" warning.
- The deploy scripts (`scripts/deploy_native.sh`, `scripts/serve_public.sh`) must
  wrap **every** external command in the `run`/`run_long` helpers, which apply
  `timeout`. A bare `apt-get`, `psql`, `createdb`, `pg_ctlcluster` or `docker`
  call can block on a network that never answers or a lock that never clears;
  that is what timed out the run stuck on "Apply schema to local pgvector
  database". `tests/test_run_safety.py` fails if a bare call returns.

## Dependabot auto-merge

Dependabot opens weekly pull requests (`.github/dependabot.yml`) and
`.github/workflows/dependabot-auto-merge.yml` squash-merges them **only after
the `CI` workflow completes successfully**. The workflow is triggered by
`workflow_run` (not `pull_request`) so it gets a writable token for
Dependabot-authored pull requests, and it merges only the Dependabot pull
request whose head commit CI just tested. `tests/test_dependabot_automerge.py`
pins the trigger, the success gate and the Dependabot-only filter.

## Database connections

`db_connect_timeout` in `app/core/config.py` (default 10s) bounds connection
establishment; `db_command_timeout` (default 60s) bounds query execution. Pass
`timeout=settings.db_connect_timeout` to any new `asyncpg` call. The app is
designed to start degraded when the database is unreachable — never make
startup depend on a successful connection.

## Account self-service (reset, verification, Google)

`app/services/account.py` owns password reset, email verification and Google
sign-in; `app/services/mailer.py` delivers the mail. The suite stays offline
because the default `MAIL_PROVIDER=console` logs the message instead of sending
it, and the reset/verify links are returned in the API response outside
production (`settings.is_production` false). `tests/test_account_selfservice.py`
consumes those links, so it needs no mail server and no Google credentials.

Rules to keep:

- A password-reset request must answer the same way whether or not the address
  is registered, so the endpoint cannot enumerate accounts.
- Reset and verification tokens are stored hashed in `account_tokens` and are
  single-use; never return a raw token except through the mail body.
- Google sign-in is enabled only when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
  are both set (`settings.google_enabled`); the login/signup pages hide the
  button otherwise. An account is matched by `google_sub` first and by a
  *verified* email second — an unverified claim that collides with a local
  account is refused, never linked.
- `REQUIRE_EMAIL_VERIFICATION` is off by default so the platform works with no
  mail server. Turning it on makes `AuthService.authenticate` reject an
  unconfirmed address with a 403.

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

## One canonical host

Three Vercel projects deploy this same repository, but only the official one
(`yemeni-judiciary.vercel.app`) has the database credentials. The other two
answer from the in-memory store and would lose every account on a cold start, so
`CANONICAL_HOST` names the one real host and `REDIRECT_HOSTS` lists the
duplicates; a request that arrives under a duplicate gets a 308 to the canonical
host, path and query preserved. `tests/test_canonical_host.py` covers it.

- To retire another duplicate, append its hostname to `REDIRECT_HOSTS` (in
  `.env`/the Vercel dashboard, or the default in `app/core/config.py`).
- The list is a comma-separated string, not a JSON array: pydantic-settings
  parses a `list` field as JSON and would reject `a,b`. Read it through
  `settings.redirect_host_names`.
- Clearing `CANONICAL_HOST` disables the redirect, which is what a self-hosted
  install or a local run wants. A host not listed in `REDIRECT_HOSTS` is always
  served locally, so Vercel preview URLs keep working.

## Neon tooling

The Neon CLI and the official Neon agent skills are installed for building on
Neon (see `.agents/skills/`, fetched from
`https://neon.com/.well-known/agent-skills/<skill>/SKILL.md`).

- CLI: `npm install -g neon@latest`; the binary lands in `~/.npm-global/bin/neon`
  (add that directory to `PATH`). The system npm prefix is not writable.
- Auth: `neon login` opens a browser and cannot complete in a headless sandbox.
  Use an API key instead — `NEON_API_KEY=... neon projects list`, or persist one
  with `neon profile create agent --api-key -`. The value must be a real Neon
  key; a placeholder is rejected with HTTP 401.
- The MCP server (`https://mcp.neon.tech/mcp`) is configured in the OpenHands
  account settings, not on disk, and only loads after the agent restarts.
