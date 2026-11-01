"""Platform administration endpoints: sections, laws and articles.

Sections (أقسام المنصة)
    GET    /admin/sections
    POST   /admin/sections
    PATCH  /admin/sections/{id}
    DELETE /admin/sections/{id}

Laws (القوانين)
    GET    /admin/laws
    GET    /admin/laws/{id}
    PATCH  /admin/laws/{id}
    DELETE /admin/laws/{id}
    POST   /admin/laws/ingest

Articles (المواد)
    GET    /admin/laws/{id}/articles
    PATCH  /admin/articles/{id}
    DELETE /admin/articles/{id}

Every mutation is written to the audit log with the acting user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.deps import AdminDep, CurrentUserDep, require_permission
from app.schemas.auth import SectionCreate, SectionOut, SectionUpdate
from app.schemas.common import LegalSourceType
from app.schemas.legal import LegalDocumentUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


class ArticleUpdate(BaseModel):
    content_ar: str | None = Field(default=None, min_length=1)
    article_number: str | None = None
    article_label: str | None = None
    heading_ar: str | None = None


class IngestLawRequest(BaseModel):
    source_type: LegalSourceType
    title_ar: str = Field(min_length=2, max_length=500)
    text: str = Field(min_length=10)
    law_number: str | None = None
    promulgation_year: int | None = Field(default=None, ge=1900, le=2100)
    source_url: str | None = None
    section_id: str | None = None


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
@router.get("/sections", summary="قائمة أقسام المنصة")
async def list_sections(
    admin: AdminDep,
    user: CurrentUserDep,
    include_inactive: bool = Query(default=True),
) -> list[SectionOut]:
    rows = await admin.list_sections(user, include_inactive=include_inactive)
    return [SectionOut(**{k: r[k] for k in SectionOut.model_fields if k in r}) for r in rows]


@router.post(
    "/sections",
    response_model=SectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="إضافة قسم جديد",
)
async def create_section(payload: SectionCreate, admin: AdminDep, user: CurrentUserDep) -> SectionOut:
    section = await admin.create_section(user, payload.model_dump())
    return SectionOut(**{k: section[k] for k in SectionOut.model_fields if k in section})


@router.patch(
    "/sections/{section_id}",
    response_model=SectionOut,
    summary="تعديل قسم",
)
async def update_section(
    section_id: str, payload: SectionUpdate, admin: AdminDep, user: CurrentUserDep
) -> SectionOut:
    changes = payload.model_dump(exclude_unset=True)
    section = await admin.update_section(user, section_id, changes)
    return SectionOut(**{k: section[k] for k in SectionOut.model_fields if k in section})


@router.delete(
    "/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="حذف قسم",
)
async def delete_section(section_id: str, admin: AdminDep, user: CurrentUserDep) -> None:
    await admin.delete_section(user, section_id)


# --------------------------------------------------------------------------- #
# Laws
# --------------------------------------------------------------------------- #
@router.get("/laws", summary="قائمة القوانين في قاعدة البيانات")
async def list_laws(
    admin: AdminDep,
    user: CurrentUserDep,
    source_type: str | None = Query(default=None),
    section_id: str | None = Query(default=None),
) -> list[dict]:
    return await admin.list_documents(user, source_type=source_type, section_id=section_id)


@router.post(
    "/laws/ingest",
    status_code=status.HTTP_201_CREATED,
    summary="إدخال نص قانون كامل إلى قاعدة البيانات (فهرسة وتضمين)",
    dependencies=[Depends(require_permission("corpus.ingest"))],
)
async def ingest_law(payload: IngestLawRequest, admin: AdminDep, user: CurrentUserDep) -> dict:
    return await admin.ingest(user, **payload.model_dump())


@router.get("/laws/{document_id}", summary="بيانات قانون واحد")
async def get_law(document_id: str, admin: AdminDep, user: CurrentUserDep) -> dict:
    return await admin.get_document(user, document_id)


@router.patch("/laws/{document_id}", summary="تعديل بيانات قانون")
async def update_law(
    document_id: str, payload: LegalDocumentUpdate, admin: AdminDep, user: CurrentUserDep
) -> dict:
    return await admin.update_document(user, document_id, payload.model_dump(exclude_unset=True))


@router.delete(
    "/laws/{document_id}",
    summary="حذف قانون وكل مواده",
    dependencies=[Depends(require_permission("laws.delete"))],
)
async def delete_law(document_id: str, admin: AdminDep, user: CurrentUserDep) -> dict:
    removed = await admin.delete_document(user, document_id)
    return {"deleted": True, "articles_removed": removed}


# --------------------------------------------------------------------------- #
# Articles
# --------------------------------------------------------------------------- #
@router.get("/laws/{document_id}/articles", summary="مواد قانون معيّن")
async def list_articles(
    document_id: str,
    admin: AdminDep,
    user: CurrentUserDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
) -> list[dict]:
    return await admin.list_articles(
        user, document_id, limit=limit, offset=offset, search=search
    )


@router.patch("/articles/{article_id}", summary="تعديل نص مادة وإعادة تضمينها")
async def update_article(
    article_id: str, payload: ArticleUpdate, admin: AdminDep, user: CurrentUserDep
) -> dict:
    return await admin.update_article(user, article_id, payload.model_dump(exclude_unset=True))


@router.delete(
    "/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="حذف مادة",
)
async def delete_article(article_id: str, admin: AdminDep, user: CurrentUserDep) -> None:
    await admin.delete_article(user, article_id)
