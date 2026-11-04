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
from app.core.legal_lexicon import profile_terms
from app.core.logging import get_logger
from app.core.text_utils import extract_article_numbers, normalize_arabic
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


def law_priority(source_types: list[str] | None) -> dict[str, int]:
    """Rank codes for a case type: earlier in ``DEFAULT_SOURCE_TYPES`` wins.

    Article numbers are reused across Yemeni codes (there is a «المادة 15» in
    almost every law), so a number cited by a party is ambiguous on its own.
    The ordering of the case type's corpus is the best available signal for
    which code a court would actually apply.
    """
    if not source_types:
        return {}
    return {source: index for index, source in enumerate(source_types)}


#: Vocabulary of a commercial transaction dispute. Articles that actually speak
#: to these matters are lifted above generic procedure ones, so a commercial
#: case is grounded in the التجارة / المعاملات provisions it turns on.
_COMMERCIAL_MARKERS: tuple[str, ...] = (
    "تاجر", "الاعمال التجاريه", "عمل تجاري", "الاوراق التجاريه", "سند لامر",
    "كمبياله", "شيك", "تظهير", "الوفاء", "حلول", "الاعتماد المستندي",
    "شركه", "الشركات", "افلاس", "التصفيه", "السمسره", "الوكاله التجاريه",
    "الفوائد", "الربا", "الشرط الجزايي", "الغرامه التاخيريه", "الفسخ",
    "الالتزامات", "العقد", "البيع", "الايجار", "الرهن التجاري", "الحيازه",
    "المطالبه بالدين", "الدين", "المدين", "الوفاء بالالتزام", "حسن النيه",
)
#: How much a commercial-relevant article is lifted. Small enough never to
#: outrank a pinned citation, large enough to reorder generic topical hits.
_COMMERCIAL_BOOST = 0.08


def commercial_relevance(article: Any, case_type: str | None) -> float:
    """Extra rank weight for an article in a commercial case.

    Weighting is by (a) the governing code and (b) whether the article text
    actually speaks to commercial transactions. A non-commercial case gets no
    boost, so this cannot perturb other case types.
    """
    if (case_type or "").strip() not in {"تجاري", "commercial"}:
        return 0.0
    boost = 0.0
    source = getattr(article, "source_type", None)
    source_value = getattr(source, "value", source)
    if source_value in {"commercial_law", "civil_law"}:
        boost += _COMMERCIAL_BOOST
    text = normalize_arabic(
        f"{getattr(article, 'heading', '') or ''} {getattr(article, 'content', '') or ''}"
    )
    if any(marker in text for marker in _COMMERCIAL_MARKERS):
        boost += _COMMERCIAL_BOOST
    return boost


#: Vocabulary of an administrative-law dispute: the constitutional and statutory
#: provisions about the civil service, the legality of administrative decisions
#: and access to a court. An administrative case turns on these, not on the
#: general civil procedure articles that dominate the corpus. Stored normalized
#: (no hamza/taa marbuta) because it is matched against normalized article text.
_ADMINISTRATIVE_MARKERS: tuple[str, ...] = (
    "الخدمه العامه", "الوظيفه العامه", "الموظفون", "موظف عام", "حق التقاضي",
    "اللجوي الي القضاي", "حق الدفاع", "الوحدات الاداريه", "المجالس المحليه",
    "القرارات واللوايح", "اللوايح", "مجلس الوزراي", "الوزير", "الوزراي",
    "المحكمه العليا", "استقلال القضاي", "السلطه القضاييه", "الرقابه", "المساواه",
    "السياسه العامه", "التعيين", "المصلحه العامه",
)
#: How much an administrative-relevant article is lifted. Small enough never to
#: outrank a pinned citation, large enough to reorder generic procedural hits.
_ADMINISTRATIVE_BOOST = 0.02


def administrative_relevance(article: Any, case_type: str | None) -> float:
    """Extra rank weight for an article in an administrative case.

    Weighting is by whether the article text actually speaks to the civil
    service, the legality of an administrative decision or access to a court.
    A non-administrative case gets no boost, so this cannot perturb other case
    types.
    """
    if (case_type or "").strip() not in {"إداري", "اداري", "administrative"}:
        return 0.0
    text = normalize_arabic(
        f"{getattr(article, 'heading', '') or ''} {getattr(article, 'content', '') or ''}"
    )
    if any(marker in text for marker in _ADMINISTRATIVE_MARKERS):
        return _ADMINISTRATIVE_BOOST
    return 0.0


# Structural labels that appear in the case digest handed to the expansion chain.
# A weak model echoes them back as "queries"; used alone they match nothing
# topical and only dilute the reciprocal-rank fusion, pushing the truly
# relevant articles down. They are dropped before they reach the store.
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "وقائع",
        "القضية",
        "النوع",
        "الموضوع",
        "الملخص",
        "المطالب",
        "الدفوع",
        "المادة",
        "القانون",
        "المحكمة",
        "المدعي",
        "المدعى",
        "عليه",
        "الطلبات",
        "الحكم",
        "الدعوى",
        "بيانات",
        "النصوص",
    }
)


def _sanitize_query(text: str) -> str:
    """Drop expansion output that is a bare structural label rather than a query."""
    cleaned = re.sub(r"\s+", " ", text or "").strip(" :،.-«»")
    tokens = cleaned.split()
    if not tokens or len(cleaned) < 3:
        return ""
    if all(token in _QUERY_STOPWORDS for token in tokens):
        return ""
    return cleaned


# A scanned or pasted filing opens with the court heading, the case number and
# the party labels. None of it is legal substance, but it is long enough to
# dominate the embedding of the base query, so it is removed before searching.
_CASE_NUMBER_RE = re.compile(r"\b\d{1,4}\s*/\s*\d{2,4}\b")


def _strip_boilerplate(text: str) -> str:
    """Remove case numbers, court headings and party labels from a search query."""
    cleaned = _CASE_NUMBER_RE.sub(" ", text)
    cleaned = cleaned.replace("القضية رقم", " ").replace("قضية رقم", " ")
    for label in ("المدعي", "المدعى عليه", "المتهم", "المجني عليه", "الطلبات", "النيابة العامة"):
        cleaned = cleaned.replace(label, " ")
    return re.sub(r"\s+", " ", cleaned).strip()


# Terms that describe the parties or the procedural posture rather than the
# legal subject matter. They are common in every filing of a given type, so
# including them in a search query adds no discrimination - it only flattens
# the embedding and pulls generic procedure articles above the governing one.
_PROCEDURAL_TERMS: frozenset[str] = frozenset(
    {
        "النيابة العامة",
        "المجني عليه",
        "نيابة",
        "نيابة عامة",
        "بلاغ",
        "شكوى جزائية",
        "محضر",
        "ضبط",
        "تحقيق",
        "التحريات",
        "أمن",
        "شرطة",
        "القبض",
        "التوقيف",
        "التلبس",
        "قاضي الجزاء",
        "الدعوى الجزائية",
        "قضية جنائية",
        "متهم",
        "إدانة",
        "براءة",
        "عقوبة",
        "حبس",
        "سجن",
        "غرامة",
        "جريمة",
        "فعل جنائي",
        "شكوى",
    }
)


class RetrievalService:
    def __init__(self) -> None:
        self.embedder = embedding_provider
        self._expansion_chain = build_query_expansion_chain()

    @property
    def store(self):
        """Resolve the active store lazily so config changes/tests take effect."""
        return vector_store_module.vector_store

    async def embedding_mismatch_warning(self) -> str | None:
        """Warn when the index was built with a different embedding model.

        Vectors from another model live in a different space, so cosine
        similarity is meaningless and the vector half of hybrid retrieval
        silently degrades to noise. Lexical search still works, which is why
        this is a warning rather than an error: the fix is to re-index.
        """
        try:
            stored = await self.store.embedding_models()
        except Exception:  # pragma: no cover - introspection must never break retrieval
            return None
        current = self.embedder.model
        stale = {m for m in stored if m != current}
        if not stale:
            return None
        return (
            f"الفهرس مبني بموديل متجهات مختلف ({', '.join(sorted(stale))}) بينما "
            f"الموديل الحالي ({current})؛ أعد فهرسة النصوص القانونية لضمان دقة البحث."
        )

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
        # Key on (document, article) rather than chunk id: dedup keeps one chunk
        # per article, and that survivor is what must rank first.
        pinned_keys = {
            (a.document_id, a.article_number or a.article_id) for a in pinned
        }
        for article in pinned:
            merged.setdefault(article.article_id, article)

        effective_case_type = case_type or case.get("case_type")

        def rank_score(article: RetrievedArticle) -> float:
            return (
                article.fused_score
                + commercial_relevance(article, effective_case_type)
                + administrative_relevance(article, effective_case_type)
            )

        ranked = sorted(
            merged.values(),
            key=lambda a: (rank_score(a), a.similarity),
            reverse=True,
        )
        deduped = self._deduplicate(ranked)
        results = sorted(
            deduped,
            key=lambda a: (
                (a.document_id, a.article_number or a.article_id) in pinned_keys,
                rank_score(a),
                a.similarity,
            ),
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
        queries: list[str] = []
        focused = self._focused_query(case)
        if focused:
            queries.append(focused)
        queries.append(base)
        try:
            result = await self._expansion_chain.run(
                [
                    self._case_digest(case),
                    "«المطلوب»: أخرج كائن JSON يحوي قائمة استعلامات البحث القانونية.",
                ]
            )
            for item in (result.data or {}).get("queries", []) or []:
                text = _sanitize_query(str(item))
                if text and text not in queries:
                    queries.append(text)
        except Exception as exc:
            logger.warning("query expansion failed", extra={"error": str(exc)})

        for article in extract_article_numbers(base)[:5]:
            queries.append(f"المادة {article}")
        for extra in self._commercial_queries(case):
            if extra not in queries:
                queries.append(extra)
        for extra in self._administrative_queries(case):
            if extra not in queries:
                queries.append(extra)
        return queries[:10]

    @staticmethod
    def _commercial_queries(case: dict[str, Any]) -> list[str]:
        """Statutory search terms a commercial dispute turns on.

        A commercial case is decided on the provisions about the commercial act,
        the debt and its settlement, breach of contract, the penalty clause and
        interest limits — not on generic civil procedure. These terms are added
        as focused queries so the governing commercial provisions are retrieved
        even when the pleading uses colloquial wording.
        """
        if str(case.get("case_type") or "").strip() not in {"تجاري", "commercial"}:
            return []
        return [
            "الأعمال التجارية التزامات التاجر",
            "الوفاء بالدين والمطالبة به",
            "الشرط الجزائي والتعويض عن الإخلال بالالتزام",
            "فسخ العقد والإخلال بالالتزام",
            "الأوراق التجارية سند لأمر كمبيالة",
            "حدود الفوائد والربا في المعاملات التجارية",
        ]

    @staticmethod
    def _administrative_queries(case: dict[str, Any]) -> list[str]:
        """Statutory search terms an administrative dispute turns on.

        An administrative case is decided on the constitutional and statutory
        provisions about the civil service, the legality of administrative
        decisions and access to a court — not on the generic civil procedure
        articles that dominate the corpus. Without these focused queries the
        formal constitutional provisions never surface for the case's own
        colloquial wording, so an administrative case retrieved no constitution
        articles at all.
        """
        if str(case.get("case_type") or "").strip() not in {"إداري", "اداري", "administrative"}:
            return []
        return [
            "حق التقاضي واللجوء إلى القضاء",
            "الخدمة العامة والوظيفة العامة",
            "القرارات واللوائح الإدارية",
            "الوحدات الإدارية والمجالس المحلية",
            "استقلال القضاء ورقابة المشروعية",
            "المساواة أمام القانون",
        ]

    # -- helpers -----------------------------------------------------------
    def _focused_query(self, case: dict[str, Any]) -> str:
        """Build a short, term-dense query from the lexicon and the case facts.

        A long free-text query averages its embedding across court headings,
        party names and dates, which dilutes the legal signal and buries the
        governing article. This query keeps only the legal vocabulary that the
        case facts actually contain. Multi-word phrases ("اعتداء على سلامة
        الجسم") and long single terms carry the specificity; short generic words
        ("متهم", "عقوبة") only blur the embedding, so they are dropped when
        enough specific terms are present.
        """
        corpus = " ".join(
            str(case.get(field) or "")
            for field in ("subject_matter", "facts_summary", "case_type")
        )
        for claim in case.get("plaintiff_claims") or []:
            corpus += " " + str(claim.get("title") or "") + " " + str(claim.get("description") or "")
        for response in case.get("defendant_responses") or []:
            corpus += " " + str(response.get("description") or "")

        present = [
            term
            for term in profile_terms(case.get("case_type"))
            if term in corpus and term not in _PROCEDURAL_TERMS
        ]
        specific = [term for term in present if " " in term or len(term) >= 8]
        return " ".join((specific or present)[:8])

    def _base_query(self, case: dict[str, Any]) -> str:
        parts = [case.get("subject_matter") or "", case.get("facts_summary") or ""]
        for claim in case.get("plaintiff_claims") or []:
            parts += [claim.get("title") or "", claim.get("description") or ""]
        for response in case.get("defendant_responses") or []:
            parts.append(response.get("description") or "")
        text = " ".join(p for p in parts if p)
        text = _strip_boilerplate(text)
        return text[:1500] or "قواعد الالتزام والتعويض"

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
        """Fetch cited articles by exact number, preferring the governing code.

        A semantic search for the string "المادة 146" is not reliable across a
        corpus this size, so cited articles are looked up by number. Numbers are
        reused across Yemeni codes, so when several codes share the number the
        case type's corpus order decides which one a court would apply; the
        other candidates are deliberately dropped rather than pinned, or they
        would rank level with the governing article and let the draft cite the
        wrong statute.
        """
        priority = law_priority(source_types)
        pinned: list[RetrievedArticle] = []
        seen: set[str] = set()
        for number in article_numbers:
            try:
                found = await self.store.find_by_article_number(
                    number, source_types=source_types, limit=8
                )
            except Exception:
                continue
            candidates = [a for a in found if a.article_id not in seen]
            if not candidates:
                continue
            best = min(
                enumerate(candidates),
                key=lambda pair: (
                    priority.get(pair[1].source_type.value, len(priority)),
                    pair[0],
                ),
            )[1]
            seen.add(best.article_id)
            pinned.append(best)
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
