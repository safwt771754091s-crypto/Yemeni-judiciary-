#!/usr/bin/env python3
"""Link the platform to a PostgreSQL database (Neon by default).

The application already supports a real Postgres/pgvector backend: set
``SUPABASE_DB_URL`` and every repository (users, workspaces, sections, vector
store) switches from the in-process fallback to the database. On a serverless
host that fallback is per-instance, so accounts created there vanish on the
next cold start. This script provisions a durable database and applies the
schema so that stops happening.

Two modes
---------
``--dsn postgresql://...``
    Use a database you already have. Applies the schema and verifies it.

*(no --dsn)*
    Provision a Neon project through the Neon API (needs ``NEON_API_KEY``),
    then apply the schema and verify it. Reuses a project named
    ``--project-name`` when one already exists.

Either way it prints the exact value to set as ``SUPABASE_DB_URL`` in the
deployment environment (Vercel > Project > Settings > Environment Variables).

Usage
-----
    NEON_API_KEY=napi_... python scripts/setup_neon.py
    python scripts/setup_neon.py --dsn postgresql://user:pass@host/db
    python scripts/setup_neon.py --dsn "$SUPABASE_DB_URL" --verify-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
NEON_API = "https://console.neon.tech/api/v2"
DEFAULT_PROJECT = "legal-ai-yemen"


def _neon_request(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{NEON_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"Neon API {method} {path} failed: HTTP {exc.code} {detail}") from exc


def provision_neon(
    api_key: str,
    name: str,
    region: str | None,
    project_id: str | None = None,
    branch_id: str | None = None,
) -> str:
    """Return a connection string for a Neon project, creating it if needed.

    ``project_id`` (and optionally ``branch_id``) target a project that already
    exists, which is the usual case when the database was created by hand in the
    Neon console. Without them the project is looked up by name, then created.
    """
    if project_id:
        return _connection_uri(api_key, project_id, branch_id)

    projects = _neon_request("GET", "/projects", api_key).get("projects", [])
    existing = next((p for p in projects if p.get("name") == name), None)

    if existing:
        print(f"  reusing Neon project '{name}' ({existing['id']})")
        return _connection_uri(api_key, existing["id"], branch_id)

    payload: dict = {"project": {"name": name}}
    if region:
        payload["project"]["region_id"] = region
    created = _neon_request("POST", "/projects", api_key, payload)
    project_id = created["project"]["id"]
    print(f"  created Neon project '{name}' ({project_id})")
    uris = created.get("connection_uris") or []
    if not uris:
        raise SystemExit("Neon created the project but returned no connection URI.")
    return uris[0]["connection_uri"]


def _connection_uri(api_key: str, project_id: str, branch_id: str | None) -> str:
    """Fetch the connection string for a project, preferring the pooled endpoint.

    The pooler keeps a serverless host from exhausting Postgres connections, and
    asyncpg's protocol-level prepared statements work through PgBouncer, so the
    pooled URI is the right default for this deployment.
    """
    query = "database_name=neondb&role_name=neondb_owner&pooled=true"
    if branch_id:
        query += f"&branch_id={branch_id}"
    uri = _neon_request(
        "GET", f"/projects/{project_id}/connection_uri?{query}", api_key
    ).get("uri")
    if not uri:
        raise SystemExit(
            "Neon did not return a connection URI. Pass --dsn instead, or rotate "
            "the role password in the Neon console."
        )
    return uri


async def apply_schema(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30, command_timeout=60)
    try:
        for path in sorted(SQL_DIR.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
            print(f"  ✓ {path.name}")
    finally:
        await conn.close()


async def verify(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30, command_timeout=60)
    try:
        version = await conn.fetchval("select version()")
        has_vector = await conn.fetchval(
            "select count(*) from pg_extension where extname = 'vector'"
        )
        users = await conn.fetchval(
            "select count(*) from information_schema.tables "
            "where table_name = 'platform_users'"
        )
        count = await conn.fetchval("select count(*) from platform_users") if users else None
        print(f"  server:   {version.split(',')[0]}")
        print(f"  pgvector: {'installed' if has_vector else 'MISSING'}")
        print(f"  platform_users: {'present' if users else 'MISSING'}"
              f"{f' ({count} row(s))' if users else ''}")
        if not users:
            raise SystemExit("Schema incomplete: platform_users is missing.")
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Link the platform to a Postgres/Neon database")
    parser.add_argument("--dsn", help="existing postgresql:// connection string")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT, help="Neon project name")
    parser.add_argument(
        "--project-id",
        default=os.environ.get("NEON_PROJECT_ID") or None,
        help="Neon project id (e.g. rough-shape-45628230); skips the name lookup",
    )
    parser.add_argument(
        "--branch-id",
        default=os.environ.get("NEON_BRANCH_ID") or None,
        help="Neon branch id (e.g. br-lively-poetry-b745ltwg); defaults to the project's default branch",
    )
    parser.add_argument("--region", default=None, help="Neon region id, e.g. aws-us-east-1")
    parser.add_argument("--verify-only", action="store_true", help="skip applying the schema")
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        api_key = os.environ.get("NEON_API_KEY", "").strip()
        if not api_key:
            print(
                "No database configured.\n"
                "  - pass --dsn postgresql://... , or\n"
                "  - set NEON_API_KEY (a real key from https://console.neon.tech/app/settings/api-keys)\n"
                "    so a Neon project can be provisioned automatically.",
                file=sys.stderr,
            )
            return 2
        print("Provisioning Neon ...")
        dsn = provision_neon(
            api_key, args.project_name, args.region, args.project_id, args.branch_id
        )

    if not dsn.startswith(("postgres://", "postgresql://")):
        print(f"Not a Postgres DSN: {dsn[:24]}...", file=sys.stderr)
        return 2

    if not args.verify_only:
        print("Applying schema ...")
        asyncio.run(apply_schema(dsn))
    print("Verifying ...")
    asyncio.run(verify(dsn))

    print("\nDone. Set this in the deployment environment (Vercel > Settings > Environment Variables):\n")
    print(f"  SUPABASE_DB_URL={dsn}\n")
    print("Then redeploy. Accounts, workspaces and the vector store become durable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
