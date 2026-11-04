"""Schemas for authentication, users, roles and platform sections."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.permissions import ROLE_LABELS_AR, Role
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class SignupRequest(BaseModel):
    """Self-service account creation.

    The role is limited to the professional portals; the service also rejects
    owner/admin/editor, so a client cannot escalate by sending a role directly.
    """

    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9._\-]+$")
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    role: Role | None = Field(default=None, description="دور البوابة المهنية (اختياري)")

    @field_validator("username")
    @classmethod
    def _normalise_username(cls, v: str) -> str:
        return v.strip()

    @field_validator("full_name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("الاسم الكامل مطلوب")
        return cleaned

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        # Deliberately permissive: one @, a dot in the domain, no spaces.
        if cleaned.count("@") != 1 or " " in cleaned:
            raise ValueError("البريد الإلكتروني غير صالح")
        local, _, domain = cleaned.partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("البريد الإلكتروني غير صالح")
        return cleaned

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        if v.isdigit() or v.isalpha():
            raise ValueError("كلمة المرور يجب أن تجمع بين الحروف والأرقام")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class SignupResponse(TokenResponse):
    """A signed-in session, so the new account lands straight in its portal."""

    role_portal: str | None = Field(default=None, description="البوابة المقترحة للدور")
    role_portal_page: str | None = Field(default=None, description="رابط البوابة المقترحة")


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: str | None = None
    full_name: str = ""
    role: Role
    role_label_ar: str = ""
    is_active: bool = True
    permissions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_role_label(self) -> UserOut:
        # Derive the Arabic role label from the role so every response path
        # (login, me, user list) carries it without each route repeating it.
        if not self.role_label_ar:
            self.role_label_ar = ROLE_LABELS_AR.get(self.role, self.role.value)
        return self


class UserWithPermissions(UserOut):
    """A user together with the permissions they effectively hold."""

    effective_permissions: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(default="", max_length=200)
    email: str | None = Field(default=None, max_length=200)
    role: Role = Role.VIEWER
    permissions: list[str] = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def _no_owner_creation(cls, v: Role) -> Role:
        if v is Role.OWNER:
            raise ValueError("لا يمكن إنشاء حساب مالك إضافي عبر الواجهة")
        return v


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    permissions: list[str] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)

    @field_validator("role")
    @classmethod
    def _no_owner_promotion(cls, v: Role | None) -> Role | None:
        if v is Role.OWNER:
            raise ValueError("لا يمكن ترقية حساب إلى مالك المنصة")
        return v


class SectionCreate(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    title_ar: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int = 0
    is_active: bool = True


class SectionUpdate(BaseModel):
    title_ar: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    is_active: bool | None = None


class SectionOut(ORMModel):
    id: uuid.UUID
    key: str
    title_ar: str
    description: str | None = None
    icon: str | None = None
    sort_order: int = 0
    is_active: bool = True


class PermissionCatalogue(BaseModel):
    permissions: list[str]
    roles: dict[str, list[str]]


TokenResponse.model_rebuild()
