"""Authentication and authorization service.

Owns the platform's identity concerns: seeding the owner account, issuing and
verifying access tokens, and answering permission questions for a user.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.permissions import Role, has_permission, permissions_for
from app.core.security import decode_token, encode_token, verify_password
from app.db.user_repository import UserRepository, user_repository
from app.services.account import account_service

logger = get_logger(__name__)

#: Default sections created on first startup so the platform is usable at once.
DEFAULT_SECTIONS: list[dict[str, Any]] = [
    {
        "key": "civil",
        "title_ar": "القانون المدني",
        "description": "قانون المعاملات المدنية وما يتصل به من أحكام الالتزامات والعقود.",
        "icon": "scale",
        "sort_order": 10,
    },
    {
        "key": "commercial",
        "title_ar": "القانون التجاري",
        "description": "الأعمال التجارية، الشركات، الأوراق التجارية والإفلاس.",
        "icon": "briefcase",
        "sort_order": 20,
    },
    {
        "key": "criminal",
        "title_ar": "القانون الجزائي",
        "description": "قانون الجرائم والعقوبات وقانون الإجراءات الجزائية.",
        "icon": "gavel",
        "sort_order": 30,
    },
    {
        "key": "evidence",
        "title_ar": "قانون الإثبات",
        "description": "قواعد الإثبات وأدلة الإثبات أمام المحاكم اليمنية.",
        "icon": "shield-check",
        "sort_order": 40,
    },
    {
        "key": "procedure",
        "title_ar": "قانون المرافعات",
        "description": "قانون المرافعات والتنفيذ المدني وإجراءات التقاضي.",
        "icon": "file-text",
        "sort_order": 50,
    },
    {
        "key": "family",
        "title_ar": "قانون الأحوال الشخصية",
        "description": "الزواج والطلاق والنفقات والمواريث والأحوال الشخصية.",
        "icon": "users",
        "sort_order": 60,
    },
    {
        "key": "constitution",
        "title_ar": "الدستور",
        "description": "دستور الجمهورية اليمنية والحقوق والواجبات الأساسية.",
        "icon": "book",
        "sort_order": 70,
    },
]


class AuthService:
    def __init__(self, users: UserRepository | None = None) -> None:
        self.users = users or user_repository

    # -- bootstrap ---------------------------------------------------------
    async def ensure_owner(self) -> dict[str, Any] | None:
        """Create the owner account when the platform has no users yet.

        Returns the created user, or ``None`` when an owner already exists.
        """
        existing = await self.users.get_by_username(settings.owner_username)
        if existing is not None:
            return None
        owner = await self.users.create(
            username=settings.owner_username,
            password=settings.owner_password,
            role=Role.OWNER,
            full_name=settings.owner_full_name,
            email=settings.owner_email,
        )
        logger.info("seeded platform owner account", extra={"username": settings.owner_username})
        return owner

    # -- login -------------------------------------------------------------
    async def authenticate(self, username: str, password: str) -> dict[str, Any]:
        """Verify credentials and return the user row.

        ``username`` may be a username or, when ``ALLOW_EMAIL_LOGIN`` is on, an
        email address.
        """
        user = await account_service.find_by_login(username)
        if user is None or not verify_password(password, user.get("password_hash", "")):
            # Same message for unknown user and wrong password: no enumeration.
            raise UnauthorizedError("اسم المستخدم أو كلمة المرور غير صحيحة")
        if not user.get("is_active", True):
            raise ForbiddenError("هذا الحساب معطّل، يرجى مراجعة مالك المنصة")
        account_service.enforce_verification(user)
        await self.users.touch_login(user["id"])
        return user

    # -- self-service signup -----------------------------------------------
    async def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an account from a public signup request.

        Deliberately separate from :meth:`create_user`: signup has no actor, so
        it can never inherit an admin's authority. The role is clamped to the
        professional portals and the account always starts active as a viewer
        or the chosen portal role - never owner, admin or editor.
        """
        if not settings.allow_self_signup:
            raise ForbiddenError("التسجيل الذاتي غير مُفعَّل على هذه المنصة")

        username = str(payload["username"]).strip()
        email = payload.get("email")
        requested = payload.get("role")
        role = Role(requested) if requested else settings.signup_default_role
        if role not in settings.signup_roles:
            raise ForbiddenError("لا يمكن التسجيل بهذا الدور")

        if await self.users.get_by_username(username) is not None:
            raise ConflictError(f"اسم المستخدم «{username}» مستخدم بالفعل")
        if email and await self.users.get_by_email(email) is not None:
            raise ConflictError("هذا البريد الإلكتروني مسجَّل بالفعل")

        created = await self.users.create(
            username=username,
            password=payload["password"],
            role=role,
            full_name=payload.get("full_name", ""),
            email=email,
        )
        logger.info(
            "self-service signup",
            extra={"username": username, "role": role.value},
        )
        return created

    def issue_token(self, user: dict[str, Any]) -> tuple[str, int]:
        """Return ``(token, expires_in_seconds)`` for a user row."""
        expires_in = settings.access_token_expire_minutes * 60
        token = encode_token(
            {
                "sub": str(user["id"]),
                "username": user["username"],
                "role": Role(user["role"]).value,
            },
            settings.auth_secret,
            expires_in=expires_in,
        )
        return token, expires_in

    async def user_from_token(self, token: str) -> dict[str, Any]:
        """Resolve a bearer token to the current (active) user."""
        claims = decode_token(token, settings.auth_secret)
        user = await self.users.get(claims.get("sub", ""))
        if user is None:
            raise UnauthorizedError("الحساب المرتبط برمز الدخول غير موجود")
        if not user.get("is_active", True):
            raise ForbiddenError("هذا الحساب معطّل")
        return user

    # -- authorization -----------------------------------------------------
    @staticmethod
    def effective_permissions(user: dict[str, Any]) -> list[str]:
        return sorted(permissions_for(user["role"], user.get("permissions") or []))

    @staticmethod
    def require_permission(user: dict[str, Any], permission: str) -> None:
        if not has_permission(user["role"], permission, user.get("permissions") or []):
            raise ForbiddenError(
                f"لا تملك الصلاحية المطلوبة ({permission}) لتنفيذ هذه العملية"
            )

    # -- user administration ----------------------------------------------
    async def list_users(self) -> list[dict[str, Any]]:
        users = await self.users.list_users()
        for user in users:
            user["effective_permissions"] = self.effective_permissions(user)
        return users

    async def create_user(self, payload: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
        self.require_permission(actor, "users.write")
        if Role(payload["role"]) is Role.OWNER:
            raise ValidationError("لا يمكن إنشاء حساب مالك إضافي")
        created = await self.users.create(
            username=payload["username"],
            password=payload["password"],
            role=payload["role"],
            full_name=payload.get("full_name", ""),
            email=payload.get("email"),
            permissions=payload.get("permissions") or [],
            created_by=str(actor["id"]),
        )
        created["effective_permissions"] = self.effective_permissions(created)
        return created

    async def update_user(
        self, user_id: str, payload: dict[str, Any], *, actor: dict[str, Any]
    ) -> dict[str, Any]:
        self.require_permission(actor, "users.write")
        target = await self.users.get(user_id)
        if target is None:
            raise NotFoundError("المستخدم غير موجود")
        if Role(target["role"]) is Role.OWNER:
            raise ValidationError("لا يمكن تعديل حساب مالك المنصة")
        updated = await self.users.update(user_id, **payload)
        updated["effective_permissions"] = self.effective_permissions(updated)
        return updated

    async def delete_user(self, user_id: str, *, actor: dict[str, Any]) -> None:
        self.require_permission(actor, "users.delete")
        if str(actor["id"]) == str(user_id):
            raise ValidationError("لا يمكنك حذف حسابك الخاص")
        await self.users.delete(user_id)


auth_service = AuthService()
