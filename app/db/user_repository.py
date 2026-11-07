"""Repository for platform users and sections.

Follows the same dual-mode contract as :mod:`app.db.repositories`: Postgres when
``SUPABASE_DB_URL`` is configured, otherwise an in-process store so the API and
the test-suite work with no database at all.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.permissions import Role
from app.core.security import hash_password
from app.db.pool import database
from app.db.repositories import _jsonable

logger = get_logger(__name__)

_USER_COLUMNS = (
    "id, username, email, email_verified, full_name, role, is_active, permissions, "
    "last_login_at, created_by, created_at, updated_at"
)


def _now() -> datetime:
    return datetime.now(UTC)


class UserRepository:
    """CRUD for platform users (accounts, roles, permissions)."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    # -- reads -------------------------------------------------------------
    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                f"select {_USER_COLUMNS}, password_hash from platform_users "
                "where lower(username) = lower($1)",
                username,
            )
        for user in self._users.values():
            if user["username"].lower() == username.lower():
                return dict(user)
        return None

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                f"select {_USER_COLUMNS}, password_hash from platform_users "
                "where lower(email) = lower($1)",
                email,
            )
        target = email.strip().lower()
        for user in self._users.values():
            if (user.get("email") or "").strip().lower() == target:
                return dict(user)
        return None

    async def get(self, user_id: str | uuid.UUID) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                f"select {_USER_COLUMNS}, password_hash from platform_users where id = $1",
                uuid.UUID(str(user_id)),
            )
        return dict(self._users[str(user_id)]) if str(user_id) in self._users else None

    async def get_by_google_sub(self, google_sub: str) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                f"select {_USER_COLUMNS}, password_hash from platform_users "
                "where google_sub = $1",
                google_sub,
            )
        for user in self._users.values():
            if user.get("google_sub") == google_sub:
                return dict(user)
        return None

    async def mark_email_verified(self, user_id: str | uuid.UUID) -> dict[str, Any] | None:
        """Flag an account's email as confirmed."""
        if self.persistent:
            row = await database.fetchrow(
                f"update platform_users set email_verified = true where id = $1 "
                f"returning {_USER_COLUMNS}",
                uuid.UUID(str(user_id)),
            )
            return dict(row) if row else None
        stored = self._users.get(str(user_id))
        if stored is None:
            return None
        stored["email_verified"] = True
        stored["updated_at"] = _now()
        return {k: v for k, v in stored.items() if k != "password_hash"}

    async def set_password(self, user_id: str | uuid.UUID, password: str) -> dict[str, Any] | None:
        """Replace a user's password hash (password reset, Google onboarding)."""
        new_hash = hash_password(password)
        if self.persistent:
            row = await database.fetchrow(
                f"update platform_users set password_hash = $2 where id = $1 "
                f"returning {_USER_COLUMNS}",
                uuid.UUID(str(user_id)),
                new_hash,
            )
            return dict(row) if row else None
        stored = self._users.get(str(user_id))
        if stored is None:
            return None
        stored["password_hash"] = new_hash
        stored["updated_at"] = _now()
        return {k: v for k, v in stored.items() if k != "password_hash"}

    async def link_google(self, user_id: str | uuid.UUID, google_sub: str) -> dict[str, Any] | None:
        """Attach a Google subject id to an existing account."""
        if self.persistent:
            row = await database.fetchrow(
                f"update platform_users set google_sub = $2 where id = $1 "
                f"returning {_USER_COLUMNS}",
                uuid.UUID(str(user_id)),
                google_sub,
            )
            return dict(row) if row else None
        stored = self._users.get(str(user_id))
        if stored is None:
            return None
        stored["google_sub"] = google_sub
        stored["updated_at"] = _now()
        return {k: v for k, v in stored.items() if k != "password_hash"}

    async def list_users(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if self.persistent:
            return await database.fetch(
                f"select {_USER_COLUMNS} from platform_users "
                "order by created_at asc limit $1 offset $2",
                limit,
                offset,
            )
        rows = sorted(self._users.values(), key=lambda u: u["created_at"])
        return [{k: v for k, v in row.items() if k != "password_hash"} for row in rows[offset : offset + limit]]

    async def count(self) -> int:
        if self.persistent:
            value = await database.fetchval("select count(*) from platform_users")
            return int(value or 0)
        return len(self._users)

    # -- writes ------------------------------------------------------------
    async def create(
        self,
        *,
        username: str,
        password: str,
        role: Role,
        full_name: str = "",
        email: str | None = None,
        permissions: list[str] | None = None,
        created_by: str | None = None,
        email_verified: bool = False,
    ) -> dict[str, Any]:
        if await self.get_by_username(username):
            raise ConflictError(f"اسم المستخدم «{username}» مستخدم بالفعل")
        if email and await self.get_by_email(email):
            raise ConflictError("هذا البريد الإلكتروني مسجَّل بالفعل")
        payload = {
            "id": uuid.uuid4(),
            "username": username,
            "email": email,
            "email_verified": bool(email_verified),
            "full_name": full_name,
            "role": Role(role).value,
            "is_active": True,
            "permissions": list(permissions or []),
            "password_hash": hash_password(password),
            "last_login_at": None,
            "created_by": uuid.UUID(created_by) if created_by else None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if self.persistent:
            row = await database.fetchrow(
                f"insert into platform_users "
                "(username, email, email_verified, full_name, password_hash, role, "
                "permissions, created_by) "
                "values ($1, $2, $3, $4, $5, $6::user_role, $7::jsonb, $8) "
                f"returning {_USER_COLUMNS}",
                username,
                email,
                payload["email_verified"],
                full_name,
                payload["password_hash"],
                payload["role"],
                _jsonable(payload["permissions"]),
                payload["created_by"],
            )
            return dict(row)
        self._users[str(payload["id"])] = payload
        return {k: v for k, v in payload.items() if k != "password_hash"}

    async def update(
        self,
        user_id: str | uuid.UUID,
        *,
        full_name: str | None = None,
        email: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
        permissions: list[str] | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        current = await self.get(user_id)
        if current is None:
            raise NotFoundError("المستخدم غير موجود")
        if Role(current["role"]) is Role.OWNER and role is not None and Role(role) is not Role.OWNER:
            raise ValidationError("لا يمكن تغيير دور مالك المنصة")

        if self.persistent:
            row = await database.fetchrow(
                f"update platform_users set "
                "full_name = coalesce($2, full_name), "
                "email = coalesce($3, email), "
                "role = coalesce($4::user_role, role), "
                "is_active = coalesce($5, is_active), "
                "permissions = coalesce($6::jsonb, permissions), "
                "password_hash = coalesce($7, password_hash) "
                f"where id = $1 returning {_USER_COLUMNS}",
                uuid.UUID(str(user_id)),
                full_name,
                email,
                Role(role).value if role else None,
                is_active,
                _jsonable(permissions) if permissions is not None else None,
                hash_password(password) if password else None,
            )
            return dict(row)

        stored = self._users[str(user_id)]
        if full_name is not None:
            stored["full_name"] = full_name
        if email is not None:
            stored["email"] = email
        if role is not None:
            stored["role"] = Role(role).value
        if is_active is not None:
            stored["is_active"] = is_active
        if permissions is not None:
            stored["permissions"] = list(permissions)
        if password:
            stored["password_hash"] = hash_password(password)
        stored["updated_at"] = _now()
        return {k: v for k, v in stored.items() if k != "password_hash"}

    async def delete(self, user_id: str | uuid.UUID) -> None:
        current = await self.get(user_id)
        if current is None:
            raise NotFoundError("المستخدم غير موجود")
        if Role(current["role"]) is Role.OWNER:
            raise ValidationError("لا يمكن حذف حساب مالك المنصة")
        if self.persistent:
            await database.execute("delete from platform_users where id = $1", uuid.UUID(str(user_id)))
        else:
            self._users.pop(str(user_id), None)

    async def touch_login(self, user_id: str | uuid.UUID) -> None:
        if self.persistent:
            await database.execute(
                "update platform_users set last_login_at = now() where id = $1",
                uuid.UUID(str(user_id)),
            )
        elif str(user_id) in self._users:
            self._users[str(user_id)]["last_login_at"] = _now()

    def reset(self) -> None:
        self._users.clear()


class SectionRepository:
    """CRUD for platform sections (أقسام المنصة)."""

    def __init__(self) -> None:
        self._sections: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    async def list_sections(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        if self.persistent:
            clause = "" if include_inactive else "where is_active = true"
            return await database.fetch(
                "select id, key, title_ar, description, icon, sort_order, is_active, "
                f"created_at, updated_at from platform_sections {clause} "
                "order by sort_order asc, title_ar asc"
            )
        rows = sorted(self._sections.values(), key=lambda s: (s["sort_order"], s["title_ar"]))
        if not include_inactive:
            rows = [row for row in rows if row["is_active"]]
        return [dict(row) for row in rows]

    async def get(self, section_id: str | uuid.UUID) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                "select id, key, title_ar, description, icon, sort_order, is_active, "
                "created_at, updated_at from platform_sections where id = $1",
                uuid.UUID(str(section_id)),
            )
        return dict(self._sections[str(section_id)]) if str(section_id) in self._sections else None

    async def get_by_key(self, key: str) -> dict[str, Any] | None:
        if self.persistent:
            return await database.fetchrow(
                "select id, key, title_ar, description, icon, sort_order, is_active, "
                "created_at, updated_at from platform_sections where key = $1",
                key,
            )
        for row in self._sections.values():
            if row["key"] == key:
                return dict(row)
        return None

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if await self.get_by_key(payload["key"]):
            raise ConflictError(f"المعرّف «{payload['key']}» مستخدم بالفعل")
        if self.persistent:
            row = await database.fetchrow(
                "insert into platform_sections "
                "(key, title_ar, description, icon, sort_order, is_active) "
                "values ($1, $2, $3, $4, $5, $6) "
                "returning id, key, title_ar, description, icon, sort_order, is_active, "
                "created_at, updated_at",
                payload["key"],
                payload["title_ar"],
                payload.get("description"),
                payload.get("icon"),
                payload.get("sort_order", 0),
                payload.get("is_active", True),
            )
            return dict(row)
        row = {
            "id": uuid.uuid4(),
            "key": payload["key"],
            "title_ar": payload["title_ar"],
            "description": payload.get("description"),
            "icon": payload.get("icon"),
            "sort_order": payload.get("sort_order", 0),
            "is_active": payload.get("is_active", True),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._sections[str(row["id"])] = row
        return dict(row)

    async def update(self, section_id: str | uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        if await self.get(section_id) is None:
            raise NotFoundError("القسم غير موجود")
        if self.persistent:
            row = await database.fetchrow(
                "update platform_sections set "
                "title_ar = coalesce($2, title_ar), "
                "description = coalesce($3, description), "
                "icon = coalesce($4, icon), "
                "sort_order = coalesce($5, sort_order), "
                "is_active = coalesce($6, is_active) "
                "where id = $1 "
                "returning id, key, title_ar, description, icon, sort_order, is_active, "
                "created_at, updated_at",
                uuid.UUID(str(section_id)),
                payload.get("title_ar"),
                payload.get("description"),
                payload.get("icon"),
                payload.get("sort_order"),
                payload.get("is_active"),
            )
            return dict(row)
        stored = self._sections[str(section_id)]
        for field in ("title_ar", "description", "icon", "sort_order", "is_active"):
            if payload.get(field) is not None:
                stored[field] = payload[field]
        stored["updated_at"] = _now()
        return dict(stored)

    async def delete(self, section_id: str | uuid.UUID) -> None:
        if await self.get(section_id) is None:
            raise NotFoundError("القسم غير موجود")
        if self.persistent:
            await database.execute(
                "delete from platform_sections where id = $1", uuid.UUID(str(section_id))
            )
        else:
            self._sections.pop(str(section_id), None)

    def reset(self) -> None:
        self._sections.clear()


class AccountTokenRepository:
    """One-time tokens for password reset and email verification.

    Same dual-mode contract as the other repositories: Postgres when
    ``SUPABASE_DB_URL`` is set, otherwise an in-process dict so the API and the
    test-suite work with no database.

    Tokens are stored hashed (SHA-256). A leaked database row therefore cannot
    be replayed as a working reset link; the raw token only ever lives in the
    email that was sent.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create(
        self,
        *,
        user_id: str | uuid.UUID,
        token: str,
        purpose: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        payload = {
            "id": uuid.uuid4(),
            "user_id": uuid.UUID(str(user_id)),
            "token_hash": self.hash_token(token),
            "purpose": purpose,
            "expires_at": expires_at,
            "used_at": None,
            "created_at": _now(),
        }
        if self.persistent:
            row = await database.fetchrow(
                "insert into account_tokens (user_id, token_hash, purpose, expires_at) "
                "values ($1, $2, $3, $4) "
                "returning id, user_id, purpose, expires_at, used_at, created_at",
                payload["user_id"],
                payload["token_hash"],
                payload["purpose"],
                payload["expires_at"],
            )
            return dict(row)
        self._tokens[str(payload["id"])] = payload
        return {k: v for k, v in payload.items() if k != "token_hash"}

    async def consume(self, token: str, purpose: str) -> dict[str, Any] | None:
        """Return the live token row and mark it used, or ``None``.

        A token is accepted only once, only for its purpose, and only before it
        expires; every failure returns ``None`` so the caller cannot tell a
        wrong token from an expired one.
        """
        digest = self.hash_token(token)
        now = _now()
        if self.persistent:
            row = await database.fetchrow(
                "update account_tokens set used_at = now() "
                "where token_hash = $1 and purpose = $2 "
                "and used_at is null and expires_at > now() "
                "returning id, user_id, purpose, expires_at, used_at, created_at",
                digest,
                purpose,
            )
            return dict(row) if row else None
        for stored in self._tokens.values():
            if (
                stored["token_hash"] == digest
                and stored["purpose"] == purpose
                and stored["used_at"] is None
                and stored["expires_at"] > now
            ):
                stored["used_at"] = now
                return {k: v for k, v in stored.items() if k != "token_hash"}
        return None

    async def invalidate(self, user_id: str | uuid.UUID, purpose: str) -> None:
        """Drop any outstanding token for a user and purpose (e.g. re-request)."""
        target = str(user_id)
        if self.persistent:
            await database.execute(
                "update account_tokens set used_at = now() "
                "where user_id = $1 and purpose = $2 and used_at is null",
                uuid.UUID(target),
                purpose,
            )
            return
        now = _now()
        for stored in self._tokens.values():
            if str(stored["user_id"]) == target and stored["purpose"] == purpose:
                stored["used_at"] = stored["used_at"] or now

    def reset(self) -> None:
        self._tokens.clear()


user_repository = UserRepository()
section_repository = SectionRepository()
account_token_repository = AccountTokenRepository()
