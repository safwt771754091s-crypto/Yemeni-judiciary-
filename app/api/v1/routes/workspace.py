"""Role-portal workspace and grounded drafting endpoints.

Workspace items (sessions, memos, contracts, investigation records, charges,
notes) are the day-to-day units of work each portal owns. The drafting endpoint
produces a grounded Arabic legal document from a short brief.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDep, DraftingDep, WorkspaceDep
from app.schemas.workspace import (
    DraftRequest,
    DraftResponse,
    WorkspaceItemCreate,
    WorkspaceItemList,
    WorkspaceItemOut,
    WorkspaceItemUpdate,
)

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get(
    "",
    response_model=WorkspaceItemList,
    summary="عناصر العمل المتاحة للمستخدم حسب صلاحياته",
)
async def list_items(
    user: CurrentUserDep,
    workspace: WorkspaceDep,
    portal: str | None = Query(default=None, description="تصفية حسب البوابة"),
    kind: str | None = Query(default=None, description="session|memo|contract|investigation|charge|note"),
    status_filter: str | None = Query(default=None, alias="status"),
    case_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> WorkspaceItemList:
    return await workspace.list_items(
        user,
        portal=portal,
        kind=kind,
        status=status_filter,
        case_id=str(case_id) if case_id else None,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=WorkspaceItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="إنشاء عنصر عمل جديد",
)
async def create_item(
    payload: WorkspaceItemCreate, user: CurrentUserDep, workspace: WorkspaceDep
) -> WorkspaceItemOut:
    return await workspace.create_item(user, payload)


@router.get("/{item_id}", response_model=WorkspaceItemOut, summary="تفاصيل عنصر عمل")
async def get_item(
    item_id: uuid.UUID, user: CurrentUserDep, workspace: WorkspaceDep
) -> WorkspaceItemOut:
    return await workspace.get_item(user, str(item_id))


@router.patch("/{item_id}", response_model=WorkspaceItemOut, summary="تحديث عنصر عمل")
async def update_item(
    item_id: uuid.UUID,
    payload: WorkspaceItemUpdate,
    user: CurrentUserDep,
    workspace: WorkspaceDep,
) -> WorkspaceItemOut:
    return await workspace.update_item(user, str(item_id), payload)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="حذف عنصر عمل",
)
async def delete_item(
    item_id: uuid.UUID, user: CurrentUserDep, workspace: WorkspaceDep
) -> None:
    await workspace.delete_item(user, str(item_id))


# --------------------------------------------------------------------------- #
# Grounded drafting
# --------------------------------------------------------------------------- #
@router.post(
    "/draft",
    response_model=DraftResponse,
    summary="صياغة مستند قانوني مسند إلى النصوص اليمنية",
    description=(
        "يصوغ عقداً أو مذكرة دفاع أو قرار اتهام أو مذكرة قانونية أو حيثيات حكم، "
        "مسنداً حصراً إلى النصوص القانونية المسترجعة. يتطلب صلاحية مطابقة لنوع "
        "الصياغة (contracts.draft / defences.draft / prosecution.manage / memos.review / "
        "judgments.draft)."
    ),
)
async def draft(
    payload: DraftRequest, user: CurrentUserDep, drafting: DraftingDep
) -> DraftResponse:
    return await drafting.draft(user=user, request=payload)
