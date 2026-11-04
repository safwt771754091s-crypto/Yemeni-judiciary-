"""Backup and restore endpoints (النسخة الاحتياطية).

GET  /backup/summary   - what a snapshot would contain (no payload)
GET  /backup/export    - download a full JSON snapshot
POST /backup/restore   - restore a snapshot (optionally replace existing data)

All endpoints require the ``backup.manage`` permission (owner, admin), because a
snapshot carries every case, user and conversation on the platform.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import BackupDep, CurrentUserDep, require_permission
from app.schemas.backup import BackupSummary, RestoreResult

router = APIRouter(prefix="/backup", tags=["backup"])


class RestoreRequest(BaseModel):
    """Body for POST /backup/restore — the snapshot plus its options."""

    snapshot: dict[str, Any] = Field(description="محتوى النسخة الاحتياطية (JSON)")
    replace: bool = Field(
        default=False,
        description="حذف البيانات الحالية قبل الاستعادة (استبدال كامل)",
    )
    dry_run: bool = Field(
        default=False,
        description="التحقق فقط دون كتابة أي بيانات",
    )
    include_credentials: bool = Field(
        default=True,
        description="استعادة بصمات كلمات المرور (تُعطَّل حسابات بلا كلمة مرور)",
    )


@router.get(
    "/summary",
    response_model=BackupSummary,
    summary="ملخص النسخة الاحتياطية الحالية",
    dependencies=[Depends(require_permission("backup.manage"))],
)
async def summary(backup: BackupDep, user: CurrentUserDep) -> BackupSummary:
    return await backup.summary()


@router.get(
    "/export",
    summary="تنزيل نسخة احتياطية كاملة (JSON)",
    dependencies=[Depends(require_permission("backup.manage"))],
)
async def export(
    backup: BackupDep,
    user: CurrentUserDep,
    include_credentials: bool = Query(
        default=True,
        description="تضمين بصمات كلمات المرور المُعمّاة (PBKDF2) لاستعادة الحسابات",
    ),
) -> Response:
    snapshot = await backup.export(include_credentials=include_credentials)
    body = backup.export_bytes(snapshot)
    stamp = snapshot.created_at.strftime("%Y%m%d-%H%M%S")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="yemen-legal-backup-{stamp}.json"'
        },
    )


@router.post(
    "/restore",
    response_model=RestoreResult,
    status_code=status.HTTP_200_OK,
    summary="استعادة نسخة احتياطية",
    dependencies=[Depends(require_permission("backup.manage"))],
)
async def restore(payload: RestoreRequest, backup: BackupDep, user: CurrentUserDep) -> RestoreResult:
    return await backup.restore(
        payload.snapshot,
        replace=payload.replace,
        dry_run=payload.dry_run,
        include_credentials=payload.include_credentials,
    )
