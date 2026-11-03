"""Workspace service for the role portals.

Provides CRUD over the portal workspace items (sessions, memos, contracts,
investigation records, charge decisions, notes). Access rules:

- The kinds a caller may touch are decided by their permissions, not their role
  name, so a grant of ``sessions.manage`` is enough to own hearing sessions.
- Sessions, memos and prosecution records are shared across the portal (a
  chamber coordinates on them); contracts and notes stay private to the author.
"""
from __future__ import annotations

from typing import Any

from app.core.errors import ForbiddenError, ValidationError
from app.core.logging import get_logger
from app.db.repositories import case_repository
from app.db.workspace_repository import WorkspaceRepository, workspace_repository
from app.schemas.workspace import (
    PORTAL_WORKSPACE_KINDS,
    WORKSPACE_KIND_LABELS_AR,
    WorkspaceItemCreate,
    WorkspaceItemList,
    WorkspaceItemOut,
    WorkspaceItemUpdate,
)
from app.services.auth import auth_service

logger = get_logger(__name__)

#: The permission that unlocks each workspace kind.
KIND_PERMISSIONS: dict[str, str] = {
    "session": "sessions.manage",
    "memo": "memos.review",
    "contract": "contracts.draft",
    "investigation": "prosecution.manage",
    "charge": "prosecution.manage",
    "note": "research.read",
}

#: Kinds visible to the whole portal rather than only their author.
SHARED_KINDS: frozenset[str] = frozenset({"session", "memo", "investigation", "charge"})


class WorkspaceService:
    def __init__(self, *, repository: WorkspaceRepository | None = None) -> None:
        self.repository = repository or workspace_repository

    # -- reads -------------------------------------------------------------
    async def list_items(
        self,
        user: dict[str, Any],
        *,
        portal: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> WorkspaceItemList:
        kinds = self._allowed_kinds(user, portal=portal)
        if kind:
            if kind not in kinds:
                raise ForbiddenError("لا تملك صلاحية الوصول إلى هذا النوع من العناصر")
            kinds = [kind]
        if not kinds:
            return WorkspaceItemList(items=[], total=0)

        # A personal kind is filtered to its author; shared kinds are not.
        personal_only = all(k not in SHARED_KINDS for k in kinds)
        owner_id = str(user["id"]) if personal_only else None
        rows = await self.repository.list_items(
            kinds=kinds,
            owner_id=owner_id,
            status=status,
            case_id=case_id,
            limit=limit,
            offset=offset,
        )
        total = await self.repository.count(kinds=kinds)
        return WorkspaceItemList(
            items=[self._to_out(r) for r in rows],
            total=total,
        )

    async def get_item(self, user: dict[str, Any], item_id: str) -> WorkspaceItemOut:
        item = await self.repository.get(item_id)
        if item is None:
            from app.core.errors import NotFoundError

            raise NotFoundError("العنصر غير موجود")
        self._assert_access(user, item)
        return self._to_out(item)

    # -- writes ------------------------------------------------------------
    async def create_item(
        self, user: dict[str, Any], payload: WorkspaceItemCreate
    ) -> WorkspaceItemOut:
        kind = payload.kind.value
        self._require_kind(user, kind)
        if kind == "session" and payload.scheduled_at is None:
            raise ValidationError("الجلسة تتطلب موعداً (scheduled_at)")
        item = await self.repository.create(
            owner_id=str(user["id"]),
            kind=kind,
            title=payload.title,
            case_id=str(payload.case_id) if payload.case_id else None,
            case_type=payload.case_type,
            status=payload.status.value,
            scheduled_at=payload.scheduled_at,
            body=payload.body,
            payload=payload.payload,
        )
        await case_repository.audit(
            "workspace", "created", str(item["id"]), actor=user["username"],
            payload={"kind": kind, "title": payload.title},
        )
        return self._to_out(item)

    async def update_item(
        self, user: dict[str, Any], item_id: str, payload: WorkspaceItemUpdate
    ) -> WorkspaceItemOut:
        item = await self.repository.get(item_id)
        if item is None:
            from app.core.errors import NotFoundError

            raise NotFoundError("العنصر غير موجود")
        self._assert_access(user, item)
        changes = payload.model_dump(exclude_unset=True)
        if "status" in changes and changes["status"] is not None:
            changes["status"] = (
                changes["status"].value
                if hasattr(changes["status"], "value")
                else changes["status"]
            )
        updated = await self.repository.update(item_id, **changes)
        await case_repository.audit(
            "workspace", "updated", item_id, actor=user["username"], payload=changes
        )
        return self._to_out(updated)

    async def delete_item(self, user: dict[str, Any], item_id: str) -> None:
        item = await self.repository.get(item_id)
        if item is None:
            from app.core.errors import NotFoundError

            raise NotFoundError("العنصر غير موجود")
        self._assert_access(user, item, write=True)
        await self.repository.delete(item_id)
        await case_repository.audit(
            "workspace", "deleted", item_id, actor=user["username"]
        )

    # -- helpers -----------------------------------------------------------
    def _allowed_kinds(self, user: dict[str, Any], *, portal: str | None) -> list[str]:
        effective = set(auth_service.effective_permissions(user))
        allowed = [
            kind
            for kind, permission in KIND_PERMISSIONS.items()
            if permission in effective
        ]
        if portal and portal in PORTAL_WORKSPACE_KINDS:
            portal_kinds = set(PORTAL_WORKSPACE_KINDS[portal])
            allowed = [k for k in allowed if k in portal_kinds]
        return allowed

    def _require_kind(self, user: dict[str, Any], kind: str) -> None:
        permission = KIND_PERMISSIONS.get(kind)
        if permission is None:
            raise ForbiddenError("نوع العنصر غير معروف")
        auth_service.require_permission(user, permission)

    def _assert_access(
        self, user: dict[str, Any], item: dict[str, Any], *, write: bool = False
    ) -> None:
        kind = item["kind"]
        self._require_kind(user, kind)
        if kind in SHARED_KINDS:
            return
        # A personal item (contract, note) belongs to its author alone.
        if str(item["owner_id"]) != str(user["id"]):
            raise ForbiddenError("لا تملك صلاحية الوصول إلى هذا العنصر")

    @staticmethod
    def _to_out(row: dict[str, Any]) -> WorkspaceItemOut:
        return WorkspaceItemOut.model_validate(
            {
                **row,
                "kind_label_ar": WORKSPACE_KIND_LABELS_AR.get(row["kind"], row["kind"]),
            }
        )


workspace_service = WorkspaceService()
