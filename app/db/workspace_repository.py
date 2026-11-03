"""Repository for role-portal workspace items.

A workspace item is any unit of work a portal owns: a hearing session, a memo, a
contract, an investigation record, a charge decision or a research note. One
dual-mode store (Postgres or in-process) serves them all.

Reads are scoped to the owning user for personal kinds, but sessions, memos and
prosecution records are shared with colleagues of the same portal so a chamber
can coordinate. The service layer decides the scope; this layer only stores.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.pool import database
from app.db.repositories import _jsonable

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class WorkspaceRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    async def create(
        self,
        *,
        owner_id: str,
        kind: str,
        title: str,
        case_id: str | None = None,
        case_type: str | None = None,
        status: str = "draft",
        scheduled_at: datetime | None = None,
        body: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.persistent:
            row = await database.fetchrow(
                "insert into portal_workspace_items "
                "(owner_id, kind, title, case_id, case_type, status, scheduled_at, body, payload) "
                "values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) returning *",
                uuid.UUID(str(owner_id)),
                kind,
                title,
                uuid.UUID(str(case_id)) if case_id else None,
                case_type,
                status,
                scheduled_at,
                body,
                _jsonable(payload or {}),
            )
            return dict(row)
        item_id = str(uuid.uuid4())
        record = {
            "id": item_id,
            "owner_id": str(owner_id),
            "kind": kind,
            "title": title,
            "case_id": str(case_id) if case_id else None,
            "case_type": case_type,
            "status": status,
            "scheduled_at": scheduled_at,
            "body": body,
            "payload": payload or {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._items[item_id] = record
        return dict(record)

    async def get(self, item_id: str) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow(
                "select * from portal_workspace_items where id = $1", uuid.UUID(str(item_id))
            )
            return dict(row) if row else None
        record = self._items.get(str(item_id))
        return dict(record) if record else None

    async def list_items(
        self,
        *,
        kinds: list[str] | None = None,
        owner_id: str | None = None,
        status: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if self.persistent:
            clauses: list[str] = []
            args: list[Any] = []
            if kinds:
                args.append(kinds)
                clauses.append(f"kind = any(${len(args)})")
            if owner_id:
                args.append(uuid.UUID(str(owner_id)))
                clauses.append(f"owner_id = ${len(args)}")
            if status:
                args.append(status)
                clauses.append(f"status = ${len(args)}")
            if case_id:
                args.append(uuid.UUID(str(case_id)))
                clauses.append(f"case_id = ${len(args)}")
            where = f"where {' and '.join(clauses)}" if clauses else ""
            args.extend([limit, offset])
            rows = await database.fetch(
                "select * from portal_workspace_items "
                f"{where} order by coalesce(scheduled_at, created_at) desc "
                f"limit ${len(args) - 1} offset ${len(args)}",
                *args,
            )
            return [dict(r) for r in rows]

        rows = list(self._items.values())
        if kinds:
            rows = [r for r in rows if r["kind"] in kinds]
        if owner_id:
            rows = [r for r in rows if r["owner_id"] == str(owner_id)]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if case_id:
            rows = [r for r in rows if r["case_id"] == str(case_id)]
        rows.sort(
            key=lambda r: r.get("scheduled_at") or r["created_at"], reverse=True
        )
        return [dict(r) for r in rows[offset : offset + limit]]

    async def count(self, *, kinds: list[str] | None = None) -> int:
        if self.persistent:
            if kinds:
                return int(
                    await database.fetchval(
                        "select count(*) from portal_workspace_items where kind = any($1)",
                        kinds,
                    )
                    or 0
                )
            return int(
                await database.fetchval("select count(*) from portal_workspace_items") or 0
            )
        rows = self._items.values()
        if kinds:
            return sum(1 for r in rows if r["kind"] in kinds)
        return len(list(rows))

    async def update(self, item_id: str, **changes: Any) -> dict[str, Any]:
        if await self.get(item_id) is None:
            raise NotFoundError("العنصر غير موجود")
        if self.persistent:
            row = await database.fetchrow(
                "update portal_workspace_items set "
                "title = coalesce($2, title), "
                "status = coalesce($3, status), "
                "scheduled_at = coalesce($4, scheduled_at), "
                "body = coalesce($5, body), "
                "payload = coalesce($6::jsonb, payload) "
                "where id = $1 returning *",
                uuid.UUID(str(item_id)),
                changes.get("title"),
                changes.get("status"),
                changes.get("scheduled_at"),
                changes.get("body"),
                _jsonable(changes["payload"]) if changes.get("payload") is not None else None,
            )
            return dict(row)
        stored = self._items[str(item_id)]
        for field in ("title", "status", "scheduled_at", "body", "payload"):
            if changes.get(field) is not None:
                stored[field] = changes[field]
        stored["updated_at"] = _now()
        return dict(stored)

    async def delete(self, item_id: str) -> None:
        if await self.get(item_id) is None:
            raise NotFoundError("العنصر غير موجود")
        if self.persistent:
            await database.execute(
                "delete from portal_workspace_items where id = $1", uuid.UUID(str(item_id))
            )
        else:
            self._items.pop(str(item_id), None)

    def reset(self) -> None:
        self._items.clear()


workspace_repository = WorkspaceRepository()
