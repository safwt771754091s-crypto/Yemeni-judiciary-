"""Grounded legal drafting (الصياغة القانونية المسندة).

Turns a short brief into a full Arabic legal document — a contract, a defence
memorandum, a charge decision, a legal memo or judgment reasoning — by
retrieving the relevant Yemeni statutes and generating from them alone.

Each draft type is gated by the permission the actor's role must hold, so a
lawyer can draft a contract but not a charge decision, and a prosecutor can
draft a charge decision but not a contract. The retrieved statutes are the only
citable corpus; any citation the model invents is stripped and the document is
flagged for review.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.llm.chains import build_draft_chain
from app.llm.validator import validate_citations
from app.schemas.workspace import (
    DRAFT_TYPE_LABELS_AR,
    DraftCitation,
    DraftRequest,
    DraftResponse,
)
from app.services.auth import auth_service
from app.services.retrieval import retrieval_service

logger = get_logger(__name__)

#: The permission that unlocks each draft type.
DRAFT_PERMISSIONS: dict[str, str] = {
    "contract": "contracts.draft",
    "defence": "defences.draft",
    "charge": "prosecution.manage",
    "memo": "memos.review",
    "judgment_note": "judgments.draft",
}


class DraftingService:
    def __init__(self) -> None:
        self.retrieval = retrieval_service
        self._chain = build_draft_chain()

    async def draft(self, *, user: dict[str, Any], request: DraftRequest) -> DraftResponse:
        permission = DRAFT_PERMISSIONS.get(request.draft_type.value)
        if permission is None:
            raise ForbiddenError("نوع الصياغة غير مدعوم")
        auth_service.require_permission(user, permission)

        started = time.perf_counter()
        articles, _queries = await self.retrieval.retrieve_for_case(
            case={
                "case_type": request.case_type,
                "subject_matter": request.brief,
                "facts_summary": request.brief,
            },
            top_k=request.top_k,
            case_type=request.case_type,
        )
        article_dicts = [a.model_dump(mode="json") for a in articles]

        result = await self._chain.run(
            self._build_blocks(request=request, articles=article_dicts)
        )
        data = result.data or {}

        report = validate_citations(data.get("cited_articles") or [], article_dicts)
        citations = [DraftCitation.model_validate(c) for c in report.verified]

        markdown = str(data.get("markdown") or "").strip()
        notes = [str(n) for n in (data.get("notes") or []) if str(n).strip()]
        needs_review = bool(report.unverified) or not articles or not markdown
        if not markdown:
            markdown = (
                "تعذّر توليد المسودة؛ يرجى إعادة صياغة الموجز أو إثراء المدونة "
                "بالنصوص القانونية ذات الصلة."
            )
        if not articles:
            notes.append(
                "لم يتم العثور على نصوص قانونية مطابقة؛ المسودة غير مسندة ويجب مراجعتها."
            )

        mismatch = await self.retrieval.embedding_mismatch_warning()
        if mismatch:
            notes.append(mismatch)

        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "legal draft generated",
            extra={
                "draft_type": request.draft_type.value,
                "articles": len(articles),
                "grounding_score": round(report.grounding_score, 3),
                "duration_ms": round(duration_ms, 1),
            },
        )

        return DraftResponse(
            draft_type=request.draft_type.value,
            draft_type_label_ar=DRAFT_TYPE_LABELS_AR[request.draft_type.value],
            title=str(data.get("title") or DRAFT_TYPE_LABELS_AR[request.draft_type.value]),
            markdown=markdown,
            cited_articles=citations,
            notes=notes,
            needs_review=needs_review,
            grounding_score=report.grounding_score,
            model=result.model,
            provider=result.provider,
        )

    @staticmethod
    def _build_blocks(*, request: DraftRequest, articles: list[dict[str, Any]]) -> list[str]:
        from app.llm.prompts import build_retrieved_statutes_block

        lines = [
            "«طلب الصياغة»:",
            f"- نوع المستند المطلوب: {request.draft_type.value}",
            f"- نوع الدعوى: {request.case_type or 'غير محدد'}",
        ]
        if request.parties:
            parties = "، ".join(f"{k}: {v}" for k, v in request.parties.items())
            lines.append(f"- الأطراف: {parties}")
        blocks = [
            build_retrieved_statutes_block(articles),
            "\n".join(lines),
            f"«موجز الصياغة»:\n{request.brief}",
            (
                "«المطلوب»: أخرج المستند كاملاً بصيغة JSON مطابقة للمخطط الموصوف في "
                "تعليمات النظام (title, markdown, cited_articles, notes)."
            ),
        ]
        return blocks


drafting_service = DraftingService()
