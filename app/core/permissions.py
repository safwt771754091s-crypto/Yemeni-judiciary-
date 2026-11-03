"""Role and permission model for the platform.

Permissions are plain ``resource.action`` strings. A role maps to a default
permission set; individual users may be granted extra permissions on top. The
``owner`` role is special: it always holds every permission and cannot be
demoted, which keeps the platform owner in full control.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Platform roles, ordered from most to least privileged.

    The first five roles administer the platform itself. The remaining five are
    the *specialized legal portals*: each one models a real actor in the Yemeni
    judicial system and is scoped to what that actor may lawfully do. They are
    deliberately not ordered against one another — a judge is not "above" a
    lawyer — so privilege comparisons must go through :func:`has_permission`
    rather than comparing enum members.
    """

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"
    # -- specialized legal portals --
    JUDGE = "judge"
    LAWYER = "lawyer"
    PROSECUTOR = "prosecutor"
    RESEARCHER = "researcher"


ROLE_LABELS_AR: dict[Role, str] = {
    Role.OWNER: "مالك المنصة",
    Role.ADMIN: "مدير",
    Role.EDITOR: "محرر",
    Role.REVIEWER: "مراجع",
    Role.VIEWER: "مطّلع",
    Role.JUDGE: "قاضٍ",
    Role.LAWYER: "محامٍ",
    Role.PROSECUTOR: "عضو نيابة",
    Role.RESEARCHER: "باحث قانوني",
}

#: The five specialized portals and the landing page each role sees.
ROLE_PORTALS: dict[Role, dict[str, str]] = {
    Role.OWNER: {"portal": "admin", "page": "/ui/admin.html", "title_ar": "لوحة المشرف"},
    Role.ADMIN: {"portal": "admin", "page": "/ui/admin.html", "title_ar": "لوحة المشرف"},
    Role.EDITOR: {"portal": "admin", "page": "/ui/admin.html", "title_ar": "لوحة المشرف"},
    Role.REVIEWER: {"portal": "judge", "page": "/ui/judge.html", "title_ar": "بوابة القاضي"},
    Role.VIEWER: {"portal": "researcher", "page": "/ui/researcher.html", "title_ar": "بوابة الباحث"},
    Role.JUDGE: {"portal": "judge", "page": "/ui/judge.html", "title_ar": "بوابة القاضي"},
    Role.LAWYER: {"portal": "lawyer", "page": "/ui/lawyer.html", "title_ar": "بوابة المحامي"},
    Role.PROSECUTOR: {
        "portal": "prosecution",
        "page": "/ui/prosecution.html",
        "title_ar": "بوابة النيابة العامة",
    },
    Role.RESEARCHER: {
        "portal": "researcher",
        "page": "/ui/researcher.html",
        "title_ar": "بوابة الباحث والطالب",
    },
}

#: The portal keys, in navigation order.
PORTAL_KEYS: tuple[str, ...] = ("admin", "judge", "lawyer", "prosecution", "researcher")

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
        # Specialized portals (بوابات الأدوار المتخصصة)
        "sessions.manage",        # الجلسات: جدولة وإدارة (القاضي/النيابة)
        "memos.review",            # استعراض المذكرات وإعدادها (القاضي/المحامي/النيابة)
        "judgments.draft",         # توليد مسودات الأحكام (القاضي)
        "contracts.draft",         # صياغة العقود (المحامي)
        "claims.analyze",          # تفكيك المطالبات المالية (المحامي)
        "defences.draft",          # صياغة الدفوع القانونية (المحامي)
        "prosecution.manage",      # محاضر التحقيق وقرارات الاتهام (النيابة)
        "research.read",           # البحث في السوابق والمواد الدستورية
        # AI legal assistant (المساعد القانوني الذكي)
        "assistant.use",
    }
)

#: Everything a non-owner can be granted.
ALL_PERMISSIONS: frozenset[str] = PERMISSIONS

READ_ONLY: frozenset[str] = frozenset(
    {
        "laws.read",
        "articles.read",
        "sections.read",
        "cases.read",
        "judgments.read",
        "research.read",
    }
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
            "assistant.use",
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
            "assistant.use",
        }
    ),
    Role.REVIEWER: frozenset(
        {
            "sections.read",
            "laws.read",
            "articles.read",
            "cases.read",
            "judgments.read",
            "judgments.approve",
            "memos.review",
            "assistant.use",
        }
    ),
    Role.VIEWER: READ_ONLY,
    # -- specialized legal portals ----------------------------------------
    # A judge manages hearings, reviews memos, drafts grounded judgments and may
    # approve them; read access to the whole corpus, no write access to it.
    Role.JUDGE: frozenset(
        {
            "sections.read",
            "laws.read",
            "articles.read",
            "cases.read",
            "cases.write",
            "judgments.read",
            "judgments.approve",
            "judgments.draft",
            "sessions.manage",
            "memos.review",
            "research.read",
            "assistant.use",
        }
    ),
    # A lawyer drafts contracts, breaks down financial claims and drafts
    # defences; can open and read cases, but cannot approve judgments.
    Role.LAWYER: frozenset(
        {
            "sections.read",
            "laws.read",
            "articles.read",
            "cases.read",
            "cases.write",
            "judgments.read",
            "contracts.draft",
            "claims.analyze",
            "defences.draft",
            "memos.review",
            "research.read",
            "assistant.use",
        }
    ),
    # A prosecutor manages investigation records and charge decisions.
    Role.PROSECUTOR: frozenset(
        {
            "sections.read",
            "laws.read",
            "articles.read",
            "cases.read",
            "cases.write",
            "judgments.read",
            "prosecution.manage",
            "sessions.manage",
            "memos.review",
            "research.read",
            "assistant.use",
        }
    ),
    # A researcher / student searches precedents, the constitution and statutes.
    Role.RESEARCHER: frozenset(
        {
            "sections.read",
            "laws.read",
            "articles.read",
            "cases.read",
            "judgments.read",
            "research.read",
            "assistant.use",
        }
    ),
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
