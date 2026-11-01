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


async def test_cited_article_is_looked_up_by_number_not_similarity(seeded_store):
    """A cited article must be fetched exactly, whatever the embedder scores.

    Regression: pinning used to run a semantic search for the literal string
    "المادة 146", which the hash embedder cannot rank reliably over a full
    corpus, so a party's own cited provision could silently go missing.
    """
    store = seeded_store.store
    found = await store.find_by_article_number("146", source_types=["civil_law"])
    assert found, "article 146 must exist in the civil corpus"
    assert all(a.article_number == "146" for a in found)
    assert all(a.source_type.value == "civil_law" for a in found)


async def test_cited_article_pinning_survives_duplicate_numbers(seeded_store):
    """The same number exists in several codes and each must be retrievable.

    Article 1 opens nearly every Yemeni code, so keying a lookup on the number
    alone would drop a statute.
    """
    store = seeded_store.store
    found = await store.find_by_article_number("1", source_types=None, limit=20)
    documents = {a.document_id for a in found}
    assert len(documents) >= 2, "article 1 exists in more than one Yemeni code"


async def test_find_by_article_number_normalises_arabic_digits(seeded_store):
    """Sources print numbers in Arabic-Indic digits; citations use Western."""
    store = seeded_store.store
    assert await store.find_by_article_number("١٤٦", source_types=["civil_law"])
    assert await store.find_by_article_number("146", source_types=["civil_law"])


# --------------------------------------------------------------------------- #
# Query planning: the plan must not dilute the legal signal
# --------------------------------------------------------------------------- #
async def test_plan_queries_drops_bare_structural_labels():
    """A weak expansion model echoes digest labels back as "queries".

    Used alone they match nothing topical and dilute reciprocal-rank fusion,
    pushing the governing article below generic procedure ones.
    """
    case = {
        "case_type": "جنائي",
        "subject_matter": "اعتداء على سلامة الجسم وإحداث عاهة مستديمة",
        "facts_summary": "أحالت النيابة العامة المتهم إلى المحكمة.",
        "plaintiff_claims": [],
        "defendant_responses": [],
    }
    queries = await retrieval_service.plan_queries(case)
    for label in ("وقائع", "القضية", "النوع", "الموضوع"):
        assert label not in queries, f"structural label {label!r} must not be a query"


async def test_plan_queries_leads_with_a_focused_legal_query():
    """The first query must be the term-dense one, not the diluted base text."""
    case = {
        "case_type": "جنائي",
        "subject_matter": "إدانة المتهم",
        "facts_summary": (
            "المحكمة الابتدائية بأمانة العاصمة - الدائرة الجزائية، القضية رقم 6644/2026. "
            "أحالت النيابة العامة المتهم بتهمة الاعتداء على سلامة الجسم وإحداث عاهة مستديمة."
        ),
        "plaintiff_claims": [],
        "defendant_responses": [],
    }
    queries = await retrieval_service.plan_queries(case)
    focused = queries[0]
    # The governing offence must survive, and the case number must not.
    assert "اعتداء على سلامة الجسم" in focused
    assert "عاهة مستديمة" in focused
    assert "6644" not in focused
    assert "النيابة العامة" not in focused
    assert "المحكمة" not in focused


async def test_focused_query_keeps_specific_terms_and_drops_procedural_ones():
    case = {
        "case_type": "جنائي",
        "subject_matter": "اعتداء على سلامة الجسم",
        "facts_summary": "إحداث عاهة مستديمة بالمجني عليه باستعمال آلة حادة.",
        "plaintiff_claims": [],
        "defendant_responses": [],
    }
    focused = retrieval_service._focused_query(case)
    assert "عاهة مستديمة" in focused
    assert "النيابة العامة" not in focused
    assert "المجني عليه" not in focused


async def test_strip_boilerplate_removes_identifiers_not_substance():
    from app.services.retrieval import _strip_boilerplate

    cleaned = _strip_boilerplate(
        "المحكمة الابتدائية القضية رقم 9911/2026 المتهم سامي عبده اعتدى على سلامة الجسم"
    )
    assert "9911/2026" not in cleaned
    assert "المتهم" not in cleaned
    assert "سلامة الجسم" in cleaned
