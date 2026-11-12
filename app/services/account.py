"""Account self-service: password reset, email verification, Google sign-in.

Kept separate from :mod:`app.services.auth` (which owns login and RBAC) so the
identity primitives stay small. Everything here is designed to work offline:

* mail is delivered by :mod:`app.services.mailer`, whose default ``console``
  provider logs the message instead of sending it;
* Google sign-in is only enabled when ``GOOGLE_CLIENT_ID``/``SECRET`` are set,
  and the token exchange is a bounded HTTP call.

Security notes that matter for a legal platform:

* a password-reset request always answers the same way, whether or not the
  address is registered, so the endpoint cannot be used to enumerate accounts;
* reset and verification tokens are stored hashed and are single-use;
* a Google account is matched by its stable ``sub`` first and by a *verified*
  email second, so an attacker cannot take over an account by registering an
  unverified Google address that collides with a local one.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    random_token,
    validate_password_strength,
)
from app.db.user_repository import (
    AccountTokenRepository,
    UserRepository,
    account_token_repository,
    user_repository,
)
from app.services.mailer import Mailer, mailer

logger = get_logger(__name__)

PURPOSE_RESET = "password_reset"
PURPOSE_VERIFY = "email_verify"

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

_USERNAME_SAFE = re.compile(r"[^a-z0-9._-]+")


class AccountService:
    def __init__(
        self,
        users: UserRepository | None = None,
        tokens: AccountTokenRepository | None = None,
        mail: Mailer | None = None,
    ) -> None:
        self.users = users or user_repository
        self.tokens = tokens or account_token_repository
        self.mail = mail or mailer

    # -- helpers -----------------------------------------------------------
    def _base_url(self, request_base: str | None) -> str:
        base = settings.app_base_url or request_base or "http://localhost:8000"
        return base.rstrip("/")

    @staticmethod
    def _normalise_email(email: str) -> str:
        return email.strip().lower()

    async def find_by_login(self, identifier: str) -> dict[str, Any] | None:
        """Resolve a login identifier that may be a username or an email."""
        user = await self.users.get_by_username(identifier)
        if user is None and settings.allow_email_login and "@" in identifier:
            user = await self.users.get_by_email(identifier)
        return user

    # -- password reset ----------------------------------------------------
    async def request_password_reset(
        self, email: str, *, request_base: str | None = None
    ) -> dict[str, Any]:
        """Send a reset link. Never reveals whether the address is registered."""
        normalised = self._normalise_email(email)
        user = await self.users.get_by_email(normalised)
        result: dict[str, Any] = {
            "sent": True,
            "message": "إذا كان البريد مسجَّلاً فستصلك رسالة لإعادة تعيين كلمة المرور.",
        }
        if user is None:
            logger.info("password reset requested for unknown email")
            return result

        await self.tokens.invalidate(user["id"], PURPOSE_RESET)
        token = random_token()
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.password_reset_expire_minutes
        )
        await self.tokens.create(
            user_id=user["id"], token=token, purpose=PURPOSE_RESET, expires_at=expires_at
        )
        link = f"{self._base_url(request_base)}/ui/reset.html?token={token}"
        await self.mail.send(
            to=normalised,
            subject="إعادة تعيين كلمة المرور — المنصة القانونية الذكية",
            text=(
                "لقد طلبت إعادة تعيين كلمة المرور لحسابك.\n\n"
                f"اضغط على الرابط التالي لتعيين كلمة مرور جديدة:\n{link}\n\n"
                f"الرابط صالح لمدة {settings.password_reset_expire_minutes} دقيقة، "
                "ويُستخدم مرة واحدة فقط.\n"
                "إذا لم تطلب ذلك، فتجاهل هذه الرسالة — لن يتغير شيء في حسابك."
            ),
        )
        result["expires_in_minutes"] = settings.password_reset_expire_minutes
        if not settings.is_production and settings.mail_provider == "console":
            # With no mail server configured the link would be invisible to a
            # tester; expose it only outside production.
            result["reset_link"] = link
        return result

    async def reset_password(self, token: str, new_password: str) -> dict[str, Any]:
        validate_password_strength(new_password)
        record = await self.tokens.consume(token, PURPOSE_RESET)
        if record is None:
            raise ValidationError("رابط إعادة التعيين غير صالح أو منتهي الصلاحية")
        user = await self.users.get(record["user_id"])
        if user is None:
            raise NotFoundError("الحساب غير موجود")
        await self.users.set_password(user["id"], new_password)
        logger.info("password reset completed", extra={"username": user["username"]})
        return {"reset": True, "username": user["username"]}

    # -- email verification ------------------------------------------------
    async def send_verification(
        self, user: dict[str, Any], *, request_base: str | None = None
    ) -> dict[str, Any]:
        """Email a confirmation link to a newly created account."""
        if not user.get("email"):
            raise ValidationError("لا يوجد بريد إلكتروني لهذا الحساب")
        if user.get("email_verified"):
            return {"sent": False, "message": "البريد الإلكتروني مؤكَّد مسبقاً."}

        await self.tokens.invalidate(user["id"], PURPOSE_VERIFY)
        token = random_token()
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.email_verify_expire_minutes
        )
        await self.tokens.create(
            user_id=user["id"], token=token, purpose=PURPOSE_VERIFY, expires_at=expires_at
        )
        link = f"{self._base_url(request_base)}/ui/verify.html?token={token}"
        await self.mail.send(
            to=user["email"],
            subject="تأكيد البريد الإلكتروني — المنصة القانونية الذكية",
            text=(
                f"مرحباً {user.get('full_name') or user['username']}،\n\n"
                "لتأكيد بريدك الإلكتروني وتفعيل حسابك، اضغط على الرابط التالي:\n"
                f"{link}\n\n"
                f"الرابط صالح لمدة {settings.email_verify_expire_minutes // 60} ساعة.\n"
                "إذا لم تنشئ هذا الحساب، فتجاهل هذه الرسالة."
            ),
        )
        result: dict[str, Any] = {"sent": True}
        if not settings.is_production and settings.mail_provider == "console":
            result["verify_link"] = link
        return result

    async def verify_email(self, token: str) -> dict[str, Any]:
        record = await self.tokens.consume(token, PURPOSE_VERIFY)
        if record is None:
            raise ValidationError("رابط التأكيد غير صالح أو منتهي الصلاحية")
        user = await self.users.mark_email_verified(record["user_id"])
        if user is None:
            raise NotFoundError("الحساب غير موجود")
        logger.info("email verified", extra={"username": user["username"]})
        return {"verified": True, "username": user["username"]}

    def enforce_verification(self, user: dict[str, Any]) -> None:
        """Block login for an unverified account when verification is required."""
        if not settings.require_email_verification:
            return
        if user.get("email") and not user.get("email_verified"):
            raise ForbiddenError(
                "يجب تأكيد بريدك الإلكتروني قبل الدخول. تحقّق من صندوق الوارد."
            )

    # -- Google sign-in ----------------------------------------------------
    @property
    def google_enabled(self) -> bool:
        return settings.google_enabled

    def google_authorize_url(self, *, state: str, request_base: str | None = None) -> str:
        if not self.google_enabled:
            raise ValidationError("تسجيل الدخول عبر جوجل غير مُفعَّل على هذه المنصة")
        redirect_uri = settings.google_redirect_uri or (
            f"{self._base_url(request_base)}/api/v1/auth/google/callback"
        )
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_google_code(
        self, code: str, *, request_base: str | None = None
    ) -> dict[str, Any]:
        """Swap an authorization code for the Google profile claims."""
        if not self.google_enabled:
            raise ValidationError("تسجيل الدخول عبر جوجل غير مُفعَّل على هذه المنصة")
        redirect_uri = settings.google_redirect_uri or (
            f"{self._base_url(request_base)}/api/v1/auth/google/callback"
        )
        async with httpx.AsyncClient(timeout=settings.mail_timeout_seconds) as client:
            token_response = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_response.status_code >= 400:
                logger.warning(
                    "google token exchange failed",
                    extra={"status": token_response.status_code},
                )
                raise UnauthorizedError("تعذّر التحقق من حساب جوجل")
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise UnauthorizedError("لم يُعِد جوجل رمز وصول صالح")
            profile_response = await client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if profile_response.status_code >= 400:
            raise UnauthorizedError("تعذّر جلب بيانات حساب جوجل")
        return profile_response.json()

    async def login_with_google(
        self, claims: dict[str, Any], *, request_base: str | None = None
    ) -> dict[str, Any]:
        """Find or create the local account behind a Google profile."""
        google_sub = str(claims.get("sub") or "").strip()
        email = (claims.get("email") or "").strip().lower() or None
        email_verified = bool(claims.get("email_verified"))
        if not google_sub:
            raise UnauthorizedError("بيانات حساب جوجل غير مكتملة")

        user = await self.users.get_by_google_sub(google_sub)
        if user is not None:
            return user

        # Link to an existing local account only when Google vouches for the
        # address; an unverified claim must never attach to someone's account.
        if email and email_verified:
            existing = await self.users.get_by_email(email)
            if existing is not None:
                await self.users.link_google(existing["id"], google_sub)
                return await self.users.get(existing["id"]) or existing

        if email is None:
            raise UnauthorizedError("حساب جوجل بلا بريد إلكتروني لا يمكن ربطه")

        # An unverified claim that collides with a local account is a takeover
        # attempt: refuse it rather than create a duplicate-email account.
        if await self.users.get_by_email(email) is not None:
            raise UnauthorizedError(
                "يوجد حساب بهذا البريد. سجّل الدخول بكلمة المرور ثم اربط حساب جوجل."
            )

        username = await self._unique_username(
            claims.get("email", "").split("@")[0] or "google.user"
        )
        created = await self.users.create(
            username=username,
            # Google authenticated the account, so a random local password is
            # never used to log in; it only satisfies the NOT NULL column.
            password=random_token(24),
            role=settings.signup_default_role,
            full_name=claims.get("name") or username,
            email=email,
            email_verified=True,
        )
        await self.users.link_google(created["id"], google_sub)
        logger.info("account created via google", extra={"username": username})
        return await self.users.get(created["id"]) or created

    async def _unique_username(self, seed: str) -> str:
        base = _USERNAME_SAFE.sub(".", seed.strip().lower()).strip(".") or "google.user"
        candidate = base
        suffix = 1
        while await self.users.get_by_username(candidate) is not None:
            suffix += 1
            candidate = f"{base}{suffix}"
        return candidate


account_service = AccountService()
