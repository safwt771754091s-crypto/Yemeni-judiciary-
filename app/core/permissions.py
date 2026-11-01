"""Role and permission model for the platform.

Permissions are plain ``resource.action`` strings. A role maps to a default
permission set; individual users may be granted extra permissions on top. The
``owner`` role is special: it always holds every permission and cannot be
demoted, which keeps the platform owner in full control.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Platform roles, ordered from most to least privileged."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


ROLE_LABELS_AR: dict[Role, str] = {
    Role.OWNER: "مالك المنصة",
    Role.ADMIN: "مدير",
    Role.EDITOR: "محرر",
    Role.REVIEWER: "مراجع",
    Role.VIEWER: "مطّلع",
}

# Permission catalogue: resource.action
PERMISSIONS: frozenset[str] = frozenset(
    {
        # Platform administration (owner only by default)
        "platform.manage",
        # User administration
        "users.read",
        "users.write",
        "users.delete",
        # Sections (أقسام المنصة)
        "sections.read",
        "sections.write",
        "sections.delete",
        # Legal documents (laws)
        "laws.read",
        "laws.write",
        "laws.delete",
        # Legal articles
        "articles.read",
        "articles.write",
        "articles.delete",
        # Corpus ingestion
        "corpus.ingest",
        # Cases
        "cases.read",
        "cases.write",
        # Judgments
        "judgments.read",
        "judgments.approve",
    }
)

#: Everything a non-owner can be granted.
ALL_PERMISSIONS: frozenset[str] = PERMISSIONS

READ_ONLY: frozenset[str] = frozenset(
    {"laws.read", "articles.read", "sections.read", "cases.read", "judgments.read"}
)

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    # Owner is handled separately (always all) but listing it keeps the map total.
    Role.OWNER: ALL_PERMISSIONS,
    Role.ADMIN: frozenset(
        {
            "users.read",
            "users.write",
            "sections.read",
            "sections.write",
            "sections.delete",
            "laws.read",
            "laws.write",
            "laws.delete",
            "articles.read",
            "articles.write",
            "articles.delete",
            "corpus.ingest",
            "cases.read",
            "cases.write",
            "judgments.read",
            "judgments.approve",
        }
    ),
    Role.EDITOR: frozenset(
        {
            "sections.read",
            "laws.read",
            "laws.write",
            "articles.read",
            "articles.write",
            "articles.delete",
            "corpus.ingest",
            "cases.read",
            "judgments.read",
        }
    ),
    Role.REVIEWER: frozenset(
        {"sections.read", "laws.read", "articles.read", "cases.read", "judgments.read", "judgments.approve"}
    ),
    Role.VIEWER: READ_ONLY,
}


def permissions_for(role: Role | str, extra: list[str] | None = None) -> frozenset[str]:
    """Return the effective permission set for a role plus any extra grants."""
    role_value = Role(role) if not isinstance(role, Role) else role
    if role_value is Role.OWNER:
        return ALL_PERMISSIONS
    granted = set(ROLE_PERMISSIONS[role_value])
    if extra:
        granted.update(p for p in extra if p in PERMISSIONS)
    return frozenset(granted)


def has_permission(
    role: Role | str, permission: str, extra: list[str] | None = None
) -> bool:
    """True when the role (plus extra grants) satisfies ``permission``."""
    role_value = Role(role) if not isinstance(role, Role) else role
    if role_value is Role.OWNER:
        return True
    return permission in permissions_for(role_value, extra)


def is_owner(role: Role | str) -> bool:
    role_value = Role(role) if not isinstance(role, Role) else role
    return role_value is Role.OWNER
