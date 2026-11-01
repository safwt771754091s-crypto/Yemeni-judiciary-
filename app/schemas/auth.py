"""Schemas for authentication, users, roles and platform sections."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import Role
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: str | None = None
    full_name: str = ""
    role: Role
    is_active: bool = True
    permissions: list[str] = Field(default_factory=list)


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
