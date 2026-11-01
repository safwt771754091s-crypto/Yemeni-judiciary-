"""Authentication and user-administration endpoints.

POST /auth/login       - exchange credentials for an access token
GET  /auth/me          - the current user with effective permissions
GET  /auth/permissions - the full permission catalogue and role defaults
GET  /auth/users       - list platform users (owner/admin)
POST /auth/users       - create a user (owner/admin)
PATCH /auth/users/{id} - update a user's role, status or permissions
DELETE /auth/users/{id} - delete a user
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.api.deps import AuthDep, CurrentUserDep, require_permission
from app.core.permissions import (
    ALL_PERMISSIONS,
    ROLE_LABELS_AR,
    ROLE_PERMISSIONS,
    Role,
)
from app.schemas.auth import (
    LoginRequest,
    PermissionCatalogue,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
    UserWithPermissions,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="تسجيل الدخول",
    description="يستبدل اسم المستخدم وكلمة المرور برمز دخول صالح لمدة محددة.",
)
async def login(payload: LoginRequest, auth: AuthDep) -> TokenResponse:
    user = await auth.authenticate(payload.username, payload.password)
    token, expires_in = auth.issue_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(**{k: user[k] for k in UserOut.model_fields if k in user}),
    )


@router.get(
    "/me",
    response_model=UserWithPermissions,
    summary="بيانات المستخدم الحالي وصلاحياته",
)
async def me(user: CurrentUserDep, auth: AuthDep) -> UserWithPermissions:
    data = {k: user[k] for k in UserOut.model_fields if k in user}
    data["effective_permissions"] = auth.effective_permissions(user)
    return UserWithPermissions(**data)


@router.get(
    "/permissions",
    response_model=PermissionCatalogue,
    summary="قائمة الصلاحيات المتاحة وأدوار النظام",
)
async def permission_catalogue() -> PermissionCatalogue:
    return PermissionCatalogue(
        permissions=sorted(ALL_PERMISSIONS),
        roles={
            role.value: sorted(perms)
            for role, perms in ROLE_PERMISSIONS.items()
            if role is not Role.OWNER
        },
    )


@router.get(
    "/roles",
    summary="أدوار المنصة وتسمياتها العربية",
)
async def list_roles() -> list[dict[str, Any]]:
    return [
        {
            "role": role.value,
            "label_ar": ROLE_LABELS_AR[role],
            "permissions": sorted(ROLE_PERMISSIONS[role]),
            "is_owner": role is Role.OWNER,
        }
        for role in Role
    ]


@router.get(
    "/users",
    response_model=list[UserWithPermissions],
    summary="قائمة مستخدمي المنصة",
    dependencies=[Depends(require_permission("users.read"))],
)
async def list_users(auth: AuthDep) -> list[UserWithPermissions]:
    users = await auth.list_users()
    return [UserWithPermissions(**{k: u[k] for k in UserWithPermissions.model_fields if k in u})
            for u in users]


@router.post(
    "/users",
    response_model=UserWithPermissions,
    status_code=status.HTTP_201_CREATED,
    summary="إنشاء مستخدم جديد",
    dependencies=[Depends(require_permission("users.write"))],
)
async def create_user(payload: UserCreate, user: CurrentUserDep, auth: AuthDep) -> UserWithPermissions:
    created = await auth.create_user(payload.model_dump(), actor=user)
    return UserWithPermissions(
        **{k: created[k] for k in UserWithPermissions.model_fields if k in created}
    )


@router.patch(
    "/users/{user_id}",
    response_model=UserWithPermissions,
    summary="تعديل مستخدم (الدور، الحالة، الصلاحيات)",
    dependencies=[Depends(require_permission("users.write"))],
)
async def update_user(
    user_id: str, payload: UserUpdate, user: CurrentUserDep, auth: AuthDep
) -> UserWithPermissions:
    changes = payload.model_dump(exclude_unset=True)
    updated = await auth.update_user(user_id, changes, actor=user)
    return UserWithPermissions(
        **{k: updated[k] for k in UserWithPermissions.model_fields if k in updated}
    )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="حذف مستخدم",
    dependencies=[Depends(require_permission("users.delete"))],
)
async def delete_user(user_id: str, user: CurrentUserDep, auth: AuthDep) -> None:
    await auth.delete_user(user_id, actor=user)
