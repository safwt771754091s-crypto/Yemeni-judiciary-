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

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import AuthDep, CurrentUserDep, require_permission
from app.api.pages import clear_session_cookie, set_session_cookie
from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissions import (
    ALL_PERMISSIONS,
    ROLE_LABELS_AR,
    ROLE_PERMISSIONS,
    ROLE_PORTALS,
    Role,
)
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    PermissionCatalogue,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
    UserWithPermissions,
    VerifyEmailRequest,
)
from app.services.account import account_service
from app.services.auth import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

logger = get_logger(__name__)

#: Session cookie value for the OAuth round-trip, checked on the callback so a
#: third party cannot feed an authorization code to the endpoint.
OAUTH_STATE_COOKIE = "legal_oauth_state"


def _request_base(request: Request) -> str:
    """The origin the caller reached us on, used to build links and redirects."""
    return str(request.base_url).rstrip("/")


@router.get(
    "/signup",
    summary="خيارات إنشاء الحساب الذاتي",
    description="يعرض ما إذا كان التسجيل الذاتي متاحاً والأدوار التي يمكن اختيارها.",
)
async def signup_options() -> dict[str, Any]:
    return {
        "enabled": settings.allow_self_signup,
        "default_role": settings.signup_default_role.value,
        "roles": [
            {
                "role": role.value,
                "label_ar": ROLE_LABELS_AR[role],
                "portal": ROLE_PORTALS.get(role, {}).get("portal"),
                "page": ROLE_PORTALS.get(role, {}).get("page"),
            }
            for role in settings.signup_roles
        ],
    }


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إنشاء حساب جديد (تسجيل ذاتي)",
    description=(
        "ينشئ حساباً جديداً ويعيد رمز دخول فوراً، على غرار منصات العمل القانوني. "
        "الأدوار المتاحة هي البوابات المهنية فقط، ولا يمكن إنشاء حساب مالك أو مدير."
    ),
)
async def signup(
    payload: SignupRequest, auth: AuthDep, request: Request, response: Response
) -> SignupResponse:
    created = await auth.register(payload.model_dump())
    # Confirm the address when one was given; a missing mail server must never
    # fail the signup, so delivery errors are logged, not raised.
    if created.get("email"):
        try:
            await account_service.send_verification(created, request_base=_request_base(request))
        except Exception as exc:  # signup must still succeed
            logger.warning("verification email not sent", extra={"error": str(exc)})
    token, expires_in = auth.issue_token(created)
    set_session_cookie(response, token)
    portal = ROLE_PORTALS.get(Role(created["role"]), {})
    return SignupResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(**{k: created[k] for k in UserOut.model_fields if k in created}),
        role_portal=portal.get("portal"),
        role_portal_page=portal.get("page"),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="تسجيل الدخول",
    description=(
        "يستبدل اسم المستخدم وكلمة المرور برمز دخول صالح لمدة محددة. "
        "يُضبط أيضاً ملف ارتباط (HttpOnly cookie) ليتمكن الخادم من حماية صفحات البوابات."
    ),
)
async def login(payload: LoginRequest, auth: AuthDep, response: Response) -> TokenResponse:
    user = await auth.authenticate(payload.username, payload.password)
    token, expires_in = auth.issue_token(user)
    set_session_cookie(response, token)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(**{k: user[k] for k in UserOut.model_fields if k in user}),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="تسجيل الخروج",
    description="يحذف ملف الارتباط (cookie) الخاص بالجلسة.",
)
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="طلب إعادة تعيين كلمة المرور",
    description=(
        "يرسل رابط إعادة تعيين إلى البريد المسجَّل. الرد واحد دائماً سواء كان "
        "البريد مسجَّلاً أم لا، حتى لا يمكن استخدام النقطة لمعرفة الحسابات الموجودة."
    ),
)
async def forgot_password(
    payload: ForgotPasswordRequest, request: Request
) -> MessageResponse:
    result = await account_service.request_password_reset(
        payload.email, request_base=_request_base(request)
    )
    return MessageResponse(
        message=result["message"],
        link=result.get("reset_link"),
        expires_in_minutes=result.get("expires_in_minutes"),
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="تعيين كلمة مرور جديدة برمز إعادة التعيين",
)
async def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    await account_service.reset_password(payload.token, payload.password)
    return MessageResponse(message="تم تعيين كلمة المرور بنجاح، يمكنك الدخول الآن.")


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    summary="تأكيد البريد الإلكتروني برمز التحقق",
)
async def verify_email(payload: VerifyEmailRequest) -> MessageResponse:
    await account_service.verify_email(payload.token)
    return MessageResponse(message="تم تأكيد بريدك الإلكتروني بنجاح.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="إعادة إرسال رسالة تأكيد البريد",
    description="يرسل رابط تأكيد جديداً إلى بريد المستخدم الحالي.",
)
async def resend_verification(user: CurrentUserDep, request: Request) -> MessageResponse:
    result = await account_service.send_verification(
        user, request_base=_request_base(request)
    )
    return MessageResponse(message=result.get("message", "تم إرسال رسالة التأكيد."), link=result.get("verify_link"))


@router.get(
    "/google",
    summary="عنوان تسجيل الدخول عبر جوجل",
    description="يعيد ما إذا كان تسجيل الدخول عبر جوجل مُفعَّلاً على هذه المنصة.",
)
async def google_start() -> dict[str, Any]:
    return {"enabled": account_service.google_enabled}


@router.get(
    "/google/authorize",
    summary="بدء تسجيل الدخول عبر جوجل",
)
async def google_authorize(request: Request) -> Any:
    if not account_service.google_enabled:
        return RedirectResponse("/ui/login.html?error=google_disabled", status_code=302)
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(
        account_service.google_authorize_url(
            state=state, request_base=_request_base(request)
        ),
        status_code=302,
    )
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )
    return response


@router.get(
    "/google/callback",
    summary="عودة جوجل بعد تسجيل الدخول",
)
async def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Any:
    if error or not code:
        return RedirectResponse("/ui/login.html?error=google_cancelled", status_code=302)
    expected = request.cookies.get(OAUTH_STATE_COOKIE, "")
    if not expected or not state or not secrets.compare_digest(expected, state):
        return RedirectResponse("/ui/login.html?error=google_state", status_code=302)

    claims = await account_service.exchange_google_code(
        code, request_base=_request_base(request)
    )
    user = await account_service.login_with_google(
        claims, request_base=_request_base(request)
    )
    token, _ = auth_service.issue_token(user)
    portal = ROLE_PORTALS.get(Role(user["role"]), {})
    target = portal.get("page") or "/ui/workspace.html"
    response = RedirectResponse(target, status_code=302)
    set_session_cookie(response, token)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


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
    response_model=None,
    summary="حذف مستخدم",
    dependencies=[Depends(require_permission("users.delete"))],
)
async def delete_user(user_id: str, user: CurrentUserDep, auth: AuthDep) -> None:
    await auth.delete_user(user_id, actor=user)
