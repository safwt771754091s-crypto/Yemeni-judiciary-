#!/usr/bin/env python3
"""Apply the SQL schema to Supabase/PostgreSQL.

Usage
-----
    python scripts/apply_sql.py                 # applies sql/001 then sql/002
    python scripts/apply_sql.py --file sql/002_functions.sql
    python scripts/apply_sql.py --print         # just print the SQL

Requires SUPABASE_DB_URL. Uses asyncpg directly (no psql client needed).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger("apply_sql")
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


async def apply(files: list[Path]) -> int:
    if not settings.supabase_db_url:
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(settings.supabase_db_url)
    try:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            logger.info("applying", extra={"file": path.name, "bytes": len(sql)})
            await conn.execute(sql)
            print(f"  ✓ {path.name} applied")
    finally:
        await conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SQL schema files")
    parser.add_argument("--file", type=Path, action="append", default=None)
    parser.add_argument("--print", dest="print_only", action="store_true")
    args = parser.parse_args()

    files = args.file or [SQL_DIR / "001_init_pgvector.sql", SQL_DIR / "002_functions.sql"]
    files = [f if f.is_absolute() else (SQL_DIR.parent / f) for f in files]

    if args.print_only:
        for path in files:
            print(f"-- ==== {path.name} ====")
            print(path.read_text(encoding="utf-8"))
        return 0

    configure_logging()
    return asyncio.run(apply(files))


if __name__ == "__main__":
    raise SystemExit(main())
