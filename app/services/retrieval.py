"""Hybrid retrieval over Yemeni statutes (the R in RAG).

Two-stage retrieval:

1. Query planning - expand the case facts into several Arabic legal search
   queries (query expansion via the LLM, with a deterministic fallback) and
   detect article numbers mentioned by the parties so those articles are always
   pulled in.
2. Hybrid search - each query is embedded and run through the pgvector store,
   which fuses dense cosine similarity with Arabic full-text + trigram ranking
   (Reciprocal Rank Fusion). Results from all queries are merged and de-duplicated.

The output is a ranked, citation-ready corpus that becomes the ONLY citable
material for the reasoning and judgment chains.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.text_utils import extract_article_numbers
from app.db import vector_store as vector_store_module
from app.llm.chains import build_query_expansion_chain
from app.llm.embeddings import embedding_provider
from app.schemas.legal import RetrievalQuery, RetrievedArticle

logger = get_logger(__name__)

# Default corpus for a civil/commercial case: the codes a Yemeni civil court
# applies most often. Evidence law is always included because the reasoning
# layer weighs proof.
DEFAULT_SOURCE_TYPES: dict[str, list[str]] = {
    "مدني": ["civil_law", "evidence_law", "civil_procedure"],
    "تجاري": ["commercial_law", "civil_law", "evidence_law", "civil_procedure"],
    "عقاري": ["civil_law", "evidence_law", "civil_procedure"],
    "عمالي": ["labour_law", "civil_law", "evidence_law", "civil_procedure"],
    "أسري": ["family_law", "evidence_law", "civil_procedure"],
    "جزائي": ["criminal_law", "criminal_procedure", "evidence_law"],
    "جنائي": ["criminal_law", "criminal_procedure", "evidence_law"],
    "إداري": ["constitution", "civil_procedure", "civil_law"],
}


def sources_for_case_type(case_type: str | None) -> list[str] | None:
    if not case_type:
        return None
    return DEFAULT_SOURCE_TYPES.get(case_type.strip())


class RetrievalService:
    def __init__(self) -> None:
        self.embedder = embedding_provider
        self._expansion_chain = build_query_expansion_chain()

    @property
    def store(self):
        """Resolve the active store lazily so config changes/tests take effect."""
        return vector_store_module.vector_store

    # -- public API --------------------------------------------------------
    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedArticle]:
        embedding = await self.embedder.embed_query(query.query)
        return await self.store.search(
            query=query.query,
            query_embedding=embedding,
            top_k=query.top_k,
            source_types=[s.value for s in query.source_types] if query.source_types else None,
            law_title_contains=query.law_title_contains,
            document_id=str(query.document_id) if query.document_id else None,
            min_similarity=query.min_similarity,
        )

    async def retrieve_for_case(
        self,
        *,
        case: dict[str, Any],
        evidence: list[dict[str, Any]] | None = None,
        top_k: int | None = None,
        case_type: str | None = None,
    ) -> tuple[list[RetrievedArticle], list[str]]:
        """Plan queries from the case, run hybrid search, merge and rank."""
        started = time.perf_counter()
        top_k = top_k or settings.retrieval_top_k
        queries = await self.plan_queries(case)
        source_types = sources_for_case_type(case_type or case.get("case_type"))

        merged: dict[str, RetrievedArticle] = {}
        for query in queries:
            embedding = await self.embedder.embed_query(query)
            for article in await self.store.search(
                query=query,
                query_embedding=embedding,
                top_k=settings.retrieval_candidates,
                source_types=source_types,
                min_similarity=0.0,
            ):
                current = merged.get(article.article_id)
                if current is None or article.fused_score > current.fused_score:
                    merged[article.article_id] = article

        # Article numbers explicitly cited by the parties must always be present
        # and must rank ahead of merely topical hits: a judge cannot ignore the
        # provisions the parties actually relied on.
        explicit = self._explicit_article_numbers(case)
        pinned = await self._pin_articles(explicit, source_types)
        pinned_ids = {a.article_id for a in pinned}
        for article in pinned:
            merged.setdefault(article.article_id, article)

        ranked = sorted(merged.values(), key=lambda a: (a.fused_score, a.similarity), reverse=True)
        deduped = self._deduplicate(ranked)
        results = sorted(
            deduped,
            key=lambda a: (a.article_id in pinned_ids, a.fused_score, a.similarity),
            reverse=True,
        )[: max(top_k, len(pinned))]
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "retrieval complete",
            extra={
                "queries": len(queries),
                "candidates": len(merged),
                "returned": len(results),
                "duration_ms": round(duration_ms, 1),
            },
        )
        return results, queries

    async def plan_queries(self, case: dict[str, Any]) -> list[str]:
        """Expand the case into a handful of precise Arabic legal queries."""
        base = self._base_query(case)
        queries: list[str] = [base]
        try:
            result = await self._expansion_chain.run(
                [
                    self._case_digest(case),
                    "«المطلوب»: أخرج كائن JSON يحوي قائمة استعلامات البحث القانونية.",
                ]
            )
            for item in (result.data or {}).get("queries", []) or []:
                text = str(item).strip()
                if text and text not in queries:
                    queries.append(text)
        except Exception as exc:
            logger.warning("query expansion failed", extra={"error": str(exc)})

        for article in extract_article_numbers(base)[:5]:
            queries.append(f"المادة {article}")
        return queries[:8]

    # -- helpers -----------------------------------------------------------
    def _base_query(self, case: dict[str, Any]) -> str:
        parts = [case.get("subject_matter") or "", case.get("facts_summary") or ""]
        for claim in case.get("plaintiff_claims") or []:
            parts += [claim.get("title") or "", claim.get("description") or ""]
        for response in case.get("defendant_responses") or []:
            parts.append(response.get("description") or "")
        text = " ".join(p for p in parts if p)
        return re.sub(r"\s+", " ", text).strip()[:1500] or "قواعد الالتزام والتعويض"

    def _case_digest(self, case: dict[str, Any]) -> str:
        claims = "؛ ".join(
            f"{c.get('title')}: {c.get('description')}" for c in (case.get("plaintiff_claims") or [])
        )
        defences = "؛ ".join(
            f"{r.get('description')}" for r in (case.get("defendant_responses") or [])
        )
        return (
            f"«وقائع القضية»:\n"
            f"- النوع: {case.get('case_type')}\n"
            f"- الموضوع: {case.get('subject_matter')}\n"
            f"- الملخص: {case.get('facts_summary') or '—'}\n"
            f"- المطالب: {claims or '—'}\n"
            f"- الدفوع: {defences or '—'}"
        )

    def _explicit_article_numbers(self, case: dict[str, Any]) -> list[str]:
        text_parts = [case.get("subject_matter") or "", case.get("facts_summary") or ""]
        for claim in case.get("plaintiff_claims") or []:
            text_parts += list(claim.get("legal_basis") or [])
            text_parts.append(claim.get("description") or "")
        for response in case.get("defendant_responses") or []:
            text_parts += list(response.get("legal_basis") or [])
            text_parts.append(response.get("description") or "")
        found: list[str] = []
        for part in text_parts:
            for number in extract_article_numbers(part or ""):
                if number not in found:
                    found.append(number)
        return found[:12]

    async def _pin_articles(
        self, article_numbers: list[str], source_types: list[str] | None
    ) -> list[RetrievedArticle]:
        pinned: list[RetrievedArticle] = []
        for number in article_numbers:
            try:
                found = await self.store.search(
                    query=f"المادة {number}",
                    query_embedding=await self.embedder.embed_query(f"المادة {number}"),
                    top_k=2,
                    source_types=source_types,
                    min_similarity=0.0,
                )
            except Exception:
                continue
            for article in found:
                if article.article_number and article.article_number.strip() == number:
                    pinned.append(article)
                    break
        return pinned

    @staticmethod
    def _deduplicate(articles: list[RetrievedArticle]) -> list[RetrievedArticle]:
        """Collapse multiple chunks of the same article, keeping the best.

        The key must include the document: article numbers are reused across
        Yemeni codes (there is a "المادة 15" in almost every law), so keying on
        the number alone would silently drop a different statute's article.
        """
        best: dict[tuple[str, str], RetrievedArticle] = {}
        order: list[tuple[str, str]] = []
        for article in articles:
            key = (article.document_id, article.article_number or article.article_id)
            if key not in best:
                best[key] = article
                order.append(key)
        return [best[key] for key in order]


retrieval_service = RetrievalService()
