"""Role portal service (بوابات الأدوار المتخصصة).

Each specialized actor in the Yemeni judicial system gets a portal with the
tools and figures relevant to their work. This service assembles the landing
data for a portal: the actor's own permission set, the tools their role unlocks,
the corpus size, and a role-appropriate slice of recent activity.

The data is derived from the *effective permissions* of the caller, never from a
hard-coded role, so an owner who grants a user an extra permission immediately
sees the matching tool.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.permissions import (
    PORTAL_KEYS,
    ROLE_LABELS_AR,
    ROLE_PERMISSIONS,
    ROLE_PORTALS,
    Role,
)
from app.db.repositories import case_repository
from app.db.vector_store import vector_store
from app.services.auth import auth_service

logger = get_logger(__name__)

#: The tool a portal exposes, gated by the permission that unlocks it.
PORTAL_TOOLS: dict[str, list[dict[str, str]]] = {
    "admin": [
        {"key": "users", "title_ar": "إدارة المستخدمين والصلاحيات", "permission": "users.write", "page": "/ui/admin.html"},
        {"key": "corpus", "title_ar": "إدارة القوانين والمواد", "permission": "laws.write", "page": "/ui/admin.html"},
        {"key": "sections", "title_ar": "إدارة أقسام المنصة", "permission": "sections.write", "page": "/ui/admin.html"},
        {"key": "audit", "title_ar": "مراقبة الطلبات وسجل التدقيق", "permission": "platform.manage", "page": "/ui/admin.html"},
    ],
    "judge": [
        {"key": "sessions", "title_ar": "إدارة الجلسات", "permission": "sessions.manage", "page": "/ui/judge.html"},
        {"key": "memos", "title_ar": "استعراض المذكرات", "permission": "memos.review", "page": "/ui/judge.html"},
        {"key": "draft", "title_ar": "توليد مسودات الأحكام المسندة", "permission": "judgments.draft", "page": "/ui/judge.html"},
        {"key": "approve", "title_ar": "اعتماد المسودات", "permission": "judgments.approve", "page": "/ui/judge.html"},
    ],
    "lawyer": [
        {"key": "contracts", "title_ar": "صياغة العقود", "permission": "contracts.draft", "page": "/ui/lawyer.html"},
        {"key": "claims", "title_ar": "تفكيك المطالبات المالية", "permission": "claims.analyze", "page": "/ui/lawyer.html"},
        {"key": "defences", "title_ar": "صياغة الدفوع القانونية", "permission": "defences.draft", "page": "/ui/lawyer.html"},
        {"key": "memos", "title_ar": "مذكرات الدعوى", "permission": "memos.review", "page": "/ui/lawyer.html"},
    ],
    "prosecution": [
        {"key": "records", "title_ar": "محاضر التحقيق", "permission": "prosecution.manage", "page": "/ui/prosecution.html"},
        {"key": "charges", "title_ar": "قرارات الاتهام", "permission": "prosecution.manage", "page": "/ui/prosecution.html"},
        {"key": "sessions", "title_ar": "جدولة الجلسات الجزائية", "permission": "sessions.manage", "page": "/ui/prosecution.html"},
        {"key": "memos", "title_ar": "المذكرات الجوابية", "permission": "memos.review", "page": "/ui/prosecution.html"},
    ],
    "researcher": [
        {"key": "search", "title_ar": "البحث في المواد القانونية", "permission": "articles.read", "page": "/ui/researcher.html"},
        {"key": "precedent", "title_ar": "السوابق والمبادئ القضائية", "permission": "research.read", "page": "/ui/researcher.html"},
        {"key": "constitution", "title_ar": "المواد الدستورية", "permission": "research.read", "page": "/ui/researcher.html"},
        {"key": "assistant", "title_ar": "المساعد القانوني الذكي", "permission": "assistant.use", "page": "/ui/researcher.html"},
    ],
}


class PortalService:
    async def describe(
        self, user: dict[str, Any], *, portal: str | None = None
    ) -> dict[str, Any]:
        """The landing data for a portal, defaulting to the caller's own."""
        role = Role(user["role"])
        effective = set(auth_service.effective_permissions(user))
        home = ROLE_PORTALS[role]
        requested = portal or home["portal"]
        if requested not in PORTAL_KEYS:
            requested = home["portal"]

        tools = [
            tool for tool in PORTAL_TOOLS.get(requested, [])
            if tool["permission"] in effective or role is Role.OWNER
        ]
        corpus = await self._corpus_summary()
        recent = await self._recent_cases()

        return {
            "user": {
                "id": str(user["id"]),
                "username": user["username"],
                "full_name": user.get("full_name") or "",
                "role": role.value,
                "role_label_ar": ROLE_LABELS_AR[role],
                "permissions": sorted(effective),
            },
            "portal": requested,
            "portal_title_ar": self._portal_title(requested),
            "home_portal": home["portal"],
            "home_page": home["page"],
            "portals": self._available_portals(effective, role),
            "tools": tools,
            "stats": {**corpus, "recent_cases": len(recent)},
            "recent_cases": recent,
        }

    # -- helpers -----------------------------------------------------------
    def _available_portals(
        self, effective: set[str], role: Role
    ) -> list[dict[str, Any]]:
        """The portals the caller may open, judged by their permissions."""
        available: list[dict[str, Any]] = []
        for key in PORTAL_KEYS:
            tools = PORTAL_TOOLS.get(key, [])
            allowed = role is Role.OWNER or any(t["permission"] in effective for t in tools)
            available.append(
                {
                    "portal": key,
                    "title_ar": self._portal_title(key),
                    "allowed": allowed,
                }
            )
        return available

    @staticmethod
    def _portal_title(portal: str) -> str:
        return {
            "admin": "لوحة المشرف",
            "judge": "بوابة القاضي",
            "lawyer": "بوابة المحامي",
            "prosecution": "بوابة النيابة العامة",
            "researcher": "بوابة الباحث والطالب",
        }.get(portal, "بوابة")

    async def _corpus_summary(self) -> dict[str, Any]:
        try:
            stats = await vector_store.corpus_stats()
        except Exception:  # pragma: no cover - introspection must never break the portal
            stats = []
        total_articles = sum(int(s.get("articles") or 0) for s in stats)
        return {
            "documents": len(stats),
            "articles": total_articles,
        }

    async def _recent_cases(self, limit: int = 5) -> list[dict[str, Any]]:
        try:
            cases = await case_repository.list_cases(limit=limit)
        except Exception:  # pragma: no cover - defensive
            return []
        return [
            {
                "id": str(c.get("id")),
                "case_number": c.get("case_number"),
                "case_type": c.get("case_type"),
                "subject_matter": c.get("subject_matter"),
                "status": c.get("status"),
                "created_at": c.get("created_at"),
            }
            for c in cases
        ]


def role_catalogue() -> list[dict[str, Any]]:
    """All roles with their Arabic labels, portals and default permissions."""
    return [
        {
            "role": role.value,
            "label_ar": ROLE_LABELS_AR[role],
            "portal": ROLE_PORTALS[role]["portal"],
            "page": ROLE_PORTALS[role]["page"],
            "permissions": sorted(ROLE_PERMISSIONS[role]),
            "is_owner": role is Role.OWNER,
        }
        for role in Role
    ]


def portals_catalogue() -> list[dict[str, Any]]:
    """The five portals and the roles that land on each."""
    result = []
    for key in PORTAL_KEYS:
        roles = [r.value for r, info in ROLE_PORTALS.items() if info["portal"] == key]
        result.append(
            {
                "portal": key,
                "title_ar": PortalService._portal_title(key),
                "roles": roles,
                "tools": PORTAL_TOOLS.get(key, []),
            }
        )
    return result


portal_service = PortalService()
