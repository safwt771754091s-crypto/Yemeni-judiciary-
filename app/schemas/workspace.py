"""Schemas for role-portal workspaces and grounded legal drafting.

A *workspace item* is a unit of work a portal owns: a judge's hearing session, a
lawyer's contract or memo, a prosecutor's investigation record, a researcher's
note. One model serves them all so a portal only has to know its own ``kind``.

The *drafting* endpoint turns a short brief into a grounded Arabic document
(contract, defence, charge decision, memo) by retrieving the relevant Yemeni
statutes and generating from them alone.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class WorkspaceKind(str, Enum):
    session = "session"              # جلسة قضائية (القاضي)
    memo = "memo"                    # مذكرة (القاضي/المحامي/النيابة)
    contract = "contract"            # عقد (المحامي)
    investigation = "investigation"  # محضر تحقيق (النيابة)
    charge = "charge"                # قرار اتهام (النيابة)
    note = "note"                    # ملاحظة بحث (الباحث)


WORKSPACE_KIND_LABELS_AR: dict[str, str] = {
    "session": "جلسة",
    "memo": "مذكرة",
    "contract": "عقد",
    "investigation": "محضر تحقيق",
    "charge": "قرار اتهام",
    "note": "ملاحظة بحث",
}

#: The workspace kind a portal owns (a portal may own several).
PORTAL_WORKSPACE_KINDS: dict[str, tuple[str, ...]] = {
    "judge": ("session", "memo"),
    "lawyer": ("contract", "memo"),
    "prosecution": ("investigation", "charge"),
    "researcher": ("note",),
}


class WorkspaceStatus(str, Enum):
    draft = "draft"
    active = "active"
    scheduled = "scheduled"
    closed = "closed"
    archived = "archived"


class WorkspaceItemCreate(BaseModel):
    kind: WorkspaceKind
    title: str = Field(min_length=2, max_length=300)
    case_id: uuid.UUID | None = None
    case_type: str | None = Field(default=None, max_length=60)
    status: WorkspaceStatus = WorkspaceStatus.draft
    scheduled_at: datetime | None = Field(default=None, description="موعد الجلسة (للجلسات)")
    body: str | None = Field(default=None, description="النص/التفاصيل")
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkspaceItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    status: WorkspaceStatus | None = None
    scheduled_at: datetime | None = None
    body: str | None = None
    payload: dict[str, Any] | None = None


class WorkspaceItemOut(ORMModel):
    id: uuid.UUID
    kind: str
    kind_label_ar: str = ""
    title: str
    case_id: uuid.UUID | None = None
    case_type: str | None = None
    status: str
    scheduled_at: datetime | None = None
    body: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    owner_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkspaceItemList(BaseModel):
    items: list[WorkspaceItemOut]
    total: int


class DraftType(str, Enum):
    contract = "contract"            # صياغة عقد
    defence = "defence"              # دفع/مذكرة دفاع
    charge = "charge"                # قرار اتهام
    memo = "memo"                    # مذكرة قانونية
    judgment_note = "judgment_note"  # ملاحظة/حيثيات للقاضي


DRAFT_TYPE_LABELS_AR: dict[str, str] = {
    "contract": "عقد",
    "defence": "مذكرة دفاع",
    "charge": "قرار اتهام",
    "memo": "مذكرة قانونية",
    "judgment_note": "حيثيات حكم",
}


class DraftRequest(BaseModel):
    draft_type: DraftType
    brief: str = Field(
        min_length=5,
        max_length=8000,
        description="وصف موجز للوقائع والمطلوب صياغته",
    )
    case_type: str | None = Field(default=None, description="نوع الدعوى لتوجيه الاسترجاع")
    parties: dict[str, str] = Field(default_factory=dict, description="أطراف المستند")
    top_k: int = Field(default=8, ge=1, le=20)


class DraftCitation(BaseModel):
    article_label: str
    law_title: str
    law_number: str | None = None
    source_type: str | None = None
    quote: str | None = None
    relevance: str = ""
    similarity: float | None = None


class DraftResponse(BaseModel):
    draft_type: str
    draft_type_label_ar: str
    title: str
    markdown: str
    cited_articles: list[DraftCitation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    needs_review: bool = False
    grounding_score: float = Field(default=0.0, ge=0.0, le=1.0)
    model: str | None = None
    provider: str | None = None
