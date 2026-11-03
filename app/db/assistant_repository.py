"""Repository for assistant conversations and messages.

Same dual-mode contract as the rest of the data layer: Postgres when
``SUPABASE_DB_URL`` is configured, otherwise an in-process store so the
assistant works (and is testable) with no database at all.

Every read is scoped to the owning user, so one user can never see another
user's threads regardless of role.
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


class AssistantRepository:
    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    # -- conversations -----------------------------------------------------
    async def create_conversation(
        self,
        *,
        user_id: str,
        title: str = "محادثة جديدة",
        case_id: str | None = None,
        case_type: str | None = None,
    ) -> dict[str, Any]:
        if self.persistent:
            row = await database.fetchrow(
                "insert into assistant_conversations (user_id, title, case_id, case_type) "
                "values ($1, $2, $3, $4) returning *",
                uuid.UUID(str(user_id)),
                title,
                uuid.UUID(str(case_id)) if case_id else None,
                case_type,
            )
            return dict(row)
        conversation_id = str(uuid.uuid4())
        record = {
            "id": conversation_id,
            "user_id": str(user_id),
            "title": title,
            "case_id": str(case_id) if case_id else None,
            "case_type": case_type,
            "is_archived": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._conversations[conversation_id] = record
        self._messages[conversation_id] = []
        return dict(record)

    async def get_conversation(
        self, conversation_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow(
                "select * from assistant_conversations where id = $1 and user_id = $2",
                uuid.UUID(str(conversation_id)),
                uuid.UUID(str(user_id)),
            )
            return dict(row) if row else None
        record = self._conversations.get(str(conversation_id))
        if record and record["user_id"] == str(user_id):
            return dict(record)
        return None

    async def list_conversations(
        self, *, user_id: str, limit: int = 30, offset: int = 0, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        if self.persistent:
            clause = "" if include_archived else "and is_archived = false"
            rows = await database.fetch(
                "select c.*, (select count(*) from assistant_messages m "
                "  where m.conversation_id = c.id) as message_count "
                f"from assistant_conversations c where c.user_id = $1 {clause} "
                "order by c.updated_at desc limit $2 offset $3",
                uuid.UUID(str(user_id)),
                limit,
                offset,
            )
            return [dict(r) for r in rows]
        rows = [c for c in self._conversations.values() if c["user_id"] == str(user_id)]
        if not include_archived:
            rows = [c for c in rows if not c["is_archived"]]
        rows.sort(key=lambda c: c["updated_at"], reverse=True)
        page = rows[offset : offset + limit]
        return [{**c, "message_count": len(self._messages.get(c["id"], []))} for c in page]

    async def count_conversations(self, *, user_id: str) -> int:
        if self.persistent:
            value = await database.fetchval(
                "select count(*) from assistant_conversations where user_id = $1",
                uuid.UUID(str(user_id)),
            )
            return int(value or 0)
        return sum(1 for c in self._conversations.values() if c["user_id"] == str(user_id))

    async def touch_conversation(
        self, conversation_id: str, *, title: str | None = None
    ) -> None:
        if self.persistent:
            await database.execute(
                "update assistant_conversations set updated_at = now(), "
                "title = coalesce($2, title) where id = $1",
                uuid.UUID(str(conversation_id)),
                title,
            )
        elif conversation_id in self._conversations:
            self._conversations[conversation_id]["updated_at"] = _now()
            if title is not None:
                self._conversations[conversation_id]["title"] = title

    async def delete_conversation(self, conversation_id: str, *, user_id: str) -> None:
        if await self.get_conversation(conversation_id, user_id=user_id) is None:
            raise NotFoundError("المحادثة غير موجودة")
        if self.persistent:
            await database.execute(
                "delete from assistant_conversations where id = $1 and user_id = $2",
                uuid.UUID(str(conversation_id)),
                uuid.UUID(str(user_id)),
            )
        else:
            self._conversations.pop(str(conversation_id), None)
            self._messages.pop(str(conversation_id), None)

    # -- messages ----------------------------------------------------------
    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        grounding_score: float | None = None,
        model: str | None = None,
        provider: str | None = None,
        needs_review: bool = False,
    ) -> dict[str, Any]:
        if self.persistent:
            row = await database.fetchrow(
                "insert into assistant_messages "
                "(conversation_id, role, content, citations, grounding_score, model, "
                " provider, needs_review) "
                "values ($1, $2, $3, $4::jsonb, $5, $6, $7, $8) returning *",
                uuid.UUID(str(conversation_id)),
                role,
                content,
                _jsonable(citations or []),
                grounding_score,
                model,
                provider,
                needs_review,
            )
            return dict(row)
        message_id = str(uuid.uuid4())
        record = {
            "id": message_id,
            "conversation_id": str(conversation_id),
            "role": role,
            "content": content,
            "citations": citations or [],
            "grounding_score": grounding_score,
            "model": model,
            "provider": provider,
            "needs_review": needs_review,
            "created_at": _now(),
        }
        self._messages.setdefault(str(conversation_id), []).append(record)
        return dict(record)

    async def list_messages(
        self, conversation_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select * from assistant_messages where conversation_id = $1 "
                "order by created_at asc limit $2 offset $3",
                uuid.UUID(str(conversation_id)),
                limit,
                offset,
            )
            return [dict(r) for r in rows]
        rows = list(self._messages.get(str(conversation_id), []))
        return rows[offset : offset + limit]

    async def recent_history(
        self, conversation_id: str, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        """The most recent turns, oldest first, for the reasoning context."""
        messages = await self.list_messages(conversation_id, limit=1000)
        return messages[-limit:]

    def reset(self) -> None:
        self._conversations.clear()
        self._messages.clear()


assistant_repository = AssistantRepository()
