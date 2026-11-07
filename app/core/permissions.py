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

#: Page-level access control (Harvey-style): every gated page maps to the single
#: permission that unlocks it. The owner holds every permission, so the owner can
#: open every page; other roles open only the pages their role unlocks. The check
#: is enforced twice - server-side before the page is served (see app/api/pages.py)
#: and client-side before the shell renders - so a direct URL cannot bypass it.
PAGE_PERMISSIONS: dict[str, str] = {
    "/ui/admin.html": "portal.admin",
    "/ui/judge.html": "portal.judge",
    "/ui/lawyer.html": "portal.lawyer",
    "/ui/prosecution.html": "portal.prosecution",
    "/ui/researcher.html": "portal.researcher",
    "/ui/workspace.html": "portal.workspace",
    "/ui/assistant.html": "assistant.use",
    "/ui/intake.html": "cases.write",
}

#: Arabic titles for each gated page, for the admin permission matrix.
PAGE_LABELS_AR: dict[str, str] = {
    "/ui/admin.html": "لوحة تحكم المنصة",
    "/ui/judge.html": "بوابة القاضي",
    "/ui/lawyer.html": "بوابة المحامي",
    "/ui/prosecution.html": "بوابة النيابة العامة",
    "/ui/researcher.html": "بوابة الباحث والطالب",
    "/ui/workspace.html": "مساحة العمل القانونية",
    "/ui/assistant.html": "المساعد القانوني الذكي",
    "/ui/intake.html": "استلام القضية وتفصيلها",
}

#: Pages anyone may open, with no session required (marketing / onboarding).
PUBLIC_PAGES: frozenset[str] = frozenset(
    {
        "/ui/landing.html",
        "/ui/index.html",
        "/ui/signup.html",
        "/ui/install.html",
        "/ui/offline.html",
        "/ui/login.html",
        "/ui/forbidden.html",
    }
)


def page_permission(path: str) -> str | None:
    """The permission a page requires, or ``None`` when the page is public.

    Unknown paths (assets, scripts, stylesheets, the API) return ``None`` so the
    caller only ever gates the pages listed in :data:`PAGE_PERMISSIONS`.
    """
    clean = path.rstrip("/") or "/"
    return PAGE_PERMISSIONS.get(clean)


def can_open_page(user: dict, path: str) -> bool:
    """Whether ``user`` may open ``path``; public pages are open to everyone."""
    required = page_permission(path)
    if required is None:
        return True
    return has_permission(user["role"], required, user.get("permissions") or [])

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
        # Backup and restore (النسخة الاحتياطية)
        "backup.manage",
        # Page-level access (Harvey-style): each gated page needs its own grant.
        # portal.admin is owner-only by default; the rest mirror the role portals.
        "portal.admin",
        "portal.judge",
        "portal.lawyer",
        "portal.prosecution",
        "portal.researcher",
        "portal.workspace",
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
        "portal.researcher",
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
            "backup.manage",
            "portal.admin",
            "portal.workspace",
            "portal.researcher",
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
            "cases.write",
            "judgments.read",
            "assistant.use",
            "portal.admin",
            "portal.workspace",
            "portal.researcher",
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
            "portal.judge",
            "portal.workspace",
            "portal.researcher",
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
            "portal.judge",
            "portal.workspace",
            "portal.researcher",
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
            "portal.lawyer",
            "portal.workspace",
            "portal.researcher",
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
            "portal.prosecution",
            "portal.workspace",
            "portal.researcher",
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
            "portal.researcher",
            "portal.workspace",
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
