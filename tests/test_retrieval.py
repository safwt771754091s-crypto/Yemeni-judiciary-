"""Tests for the retrieval (RAG) layer."""
from __future__ import annotations

from app.services.retrieval import DEFAULT_SOURCE_TYPES, retrieval_service, sources_for_case_type


async def test_ingestion_then_retrieval_finds_relevant_article(seeded_store):
    results, queries = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "مدني",
            "subject_matter": "المطالبة بتنفيذ عقد بيع وإلزام المدين بالثمن",
            "facts_summary": "تأخر المدعى عليه في سداد ثمن المبيع رغم تسليم البضاعة.",
            "plaintiff_claims": [{"title": "إلزام بالثمن", "description": "إلزام المدعى عليه بأداء الثمن"}],
            "defendant_responses": [],
        },
        top_k=8,
        case_type="مدني",
    )
    assert results, "retrieval must return at least one article"
    labels = " ".join(r.article_label for r in results)
    assert "المادة" in labels
    assert queries, "the query planner must produce at least one query"
    # Every returned article must come from the civil/evidence/procedure corpus.
    for article in results:
        assert article.source_type.value in DEFAULT_SOURCE_TYPES["مدني"]


async def test_explicitly_cited_articles_are_always_pinned(seeded_store):
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "مدني",
            "subject_matter": "نزاع حول عقد",
            "facts_summary": "يستند المدعي إلى المادة 146 من القانون المدني.",
            "plaintiff_claims": [
                {"title": "الطلب", "description": "وفقاً للمادة 146", "legal_basis": ["المادة 146"]}
            ],
            "defendant_responses": [],
        },
        top_k=10,
        case_type="مدني",
    )
    numbers = {r.article_number for r in results}
    assert "146" in numbers, "an article the party cites must be pinned into the corpus"


async def test_cited_article_resolves_to_its_governing_code(seeded_store):
    """A cited number must ground in the code the court applies for the case type.

    Article numbers repeat across Yemeni codes («المادة 15» exists in the Civil
    Procedure, Evidence and Commercial codes). A party citing «المادة 191» in a
    civil case must get the Civil Code's article, never the Procedure Code's.
    """
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "مدني",
            "subject_matter": "تعويض عن الإخلال بعقد",
            "facts_summary": "يستند المدعي إلى المادة 191 من القانون المدني.",
            "plaintiff_claims": [
                {"title": "تعويض", "description": "وفقاً للمادة 191", "legal_basis": ["المادة 191"]}
            ],
            "defendant_responses": [],
        },
        top_k=10,
        case_type="مدني",
    )
    cited = [r for r in results if r.article_number == "191"]
    assert cited, "the cited article must be present"
    assert all(r.source_type.value == "civil_law" for r in cited), (
        "المادة 191 was resolved to the wrong statute: "
        + ", ".join(f"{r.source_type.value}" for r in cited)
    )


async def test_pinned_article_prefers_the_priority_code(seeded_store):
    """When several codes share a number, the case type's corpus order decides."""
    # «المادة 15» exists in the Evidence and Civil Procedure codes, both in the
    # مدني corpus; the number alone cannot disambiguate it.
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "مدني",
            "subject_matter": "نزاع إثبات",
            "facts_summary": "يتمسك المدعى عليه بالمادة 15.",
            "plaintiff_claims": [],
            "defendant_responses": [
                {"description": "دفع", "legal_basis": ["المادة 15"]}
            ],
        },
        top_k=10,
        case_type="مدني",
    )
    fifteen = [r for r in results if r.article_number == "15"]
    assert fifteen, "the cited article must be pinned"
    assert all(r.source_type.value in DEFAULT_SOURCE_TYPES["مدني"] for r in fifteen)


async def test_find_by_article_number_matches_arabic_indic_digits(seeded_store):
    from app.db import vector_store as vector_store_module

    store = vector_store_module.vector_store
    for label in ("191", "١٩١", "المادة (191)"):
        found = await store.find_by_article_number(label, source_types=["civil_law"])
        assert found, f"exact lookup failed for {label!r}"
        assert all(a.article_number == "191" for a in found)
        assert all(a.source_type.value == "civil_law" for a in found)


async def test_case_type_selects_appropriate_corpus():
    assert sources_for_case_type("تجاري") and "commercial_law" in sources_for_case_type("تجاري")
    assert "evidence_law" in sources_for_case_type("مدني")
    assert sources_for_case_type(None) is None


async def test_retrieval_returns_empty_when_corpus_is_empty():
    results, _ = await retrieval_service.retrieve_for_case(
        case={"case_type": "مدني", "subject_matter": "أي موضوع", "plaintiff_claims": [], "defendant_responses": []},
        top_k=5,
    )
    assert results == []
