"""Role portal endpoints (بوابات الأدوار المتخصصة).

GET /portals/me            - the caller's own portal landing data
GET /portals/catalogue     - the five portals, their roles and tools
GET /portals/{portal}      - a named portal's landing data (403 if not permitted)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, PortalDep, require_permission
from app.core.errors import ForbiddenError
from app.core.permissions import PORTAL_KEYS
from app.services.portal import portals_catalogue

router = APIRouter(prefix="/portals", tags=["portals"])


@router.get(
    "/me",
    summary="بيانات بوابة المستخدم الحالي",
    description="يُرجع البوابة التي ينتمي إليها المستخدم وأدواته وإحصاءات المدونة.",
)
async def my_portal(user: CurrentUserDep, portals: PortalDep) -> dict:
    return await portals.describe(user)


@router.get(
    "/catalogue",
    summary="بوابات الأدوار المتخصصة وأدواتها",
)
async def catalogue() -> list[dict]:
    return portals_catalogue()


@router.get(
    "/{portal}",
    summary="بيانات بوابة محددة",
    dependencies=[Depends(require_permission("cases.read"))],
)
async def portal(portal: str, user: CurrentUserDep, portals: PortalDep) -> dict:
    if portal not in PORTAL_KEYS:
        raise ForbiddenError("البوابة المطلوبة غير معروفة")
    data = await portals.describe(user, portal=portal)
    # A portal the caller's role does not unlock is refused rather than shown empty.
    if not data["tools"]:
        raise ForbiddenError("لا تملك صلاحية الوصول إلى هذه البوابة")
    return data
