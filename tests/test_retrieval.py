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


# --------------------------------------------------------------------------- #
# Governing-code resolution for a cited article number
# --------------------------------------------------------------------------- #
async def _seed_shared_number(governing_type: str, governing_title: str, law_no: str, year: int):
    """Seed the same article number in two codes, the *wrong* one first.

    Ingesting the non-governing code first is what reproduces the bug: the store
    returns candidates in insertion order, so without governing-code resolution
    the first candidate is the code the case type does not apply.
    """
    from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
    from app.services.ingestion import ingestion_service

    other = "evidence_law" if governing_type != "evidence_law" else "civil_law"
    other_title = "قرار جمهوري بالقانون رقم (21) لسنة 1992م بشأن الإثبات"
    for source_type, title, number, yr in (
        (other, other_title, "21", 1992),
        (governing_type, governing_title, law_no, year),
    ):
        await ingestion_service.ingest_text(
            IngestTextRequest(
                document=LegalDocumentCreate(
                    source_type=source_type,
                    title_ar=title,
                    law_number=number,
                    promulgation_year=yr,
                ),
                text="المادة (146) نص مشترك بين قانونين لأغراض اختبار حصر القانون الواجب التطبيق.",
            )
        )


async def test_cited_number_pins_the_governing_code_for_a_civil_case():
    """A civil case citing «المادة 146» must pin the Civil Code, not the Evidence Law.

    Regression: pinning fetched every code that shares the number and gave each
    the same fused score, so the first one in store order (the Evidence Law)
    ranked level with the governing article and the draft cited the wrong
    statute.
    """
    await _seed_shared_number(
        "civil_law",
        "القانون المدني (قانون المعاملات المدنية) رقم (14) لسنة 2002م",
        "14",
        2002,
    )
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
    governing = next(r for r in results if r.article_number == "146")
    assert governing.source_type.value == "civil_law", (
        "the article the parties cited must resolve to the code the case type applies"
    )


async def test_cited_number_pins_the_governing_code_for_a_criminal_case():
    """The governing code follows the case type, not a fixed preference."""
    await _seed_shared_number(
        "criminal_law",
        "قرار جمهوري بالقانون رقم (12) لسنة 1994م بشأن الجرائم والعقوبات",
        "12",
        1994,
    )
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "جزائي",
            "subject_matter": "اتهام بارتكاب جريمة",
            "facts_summary": "يستند الاتهام إلى المادة 146 من قانون العقوبات.",
            "plaintiff_claims": [
                {"title": "الاتهام", "description": "المادة 146", "legal_basis": ["المادة 146"]}
            ],
            "defendant_responses": [],
        },
        top_k=10,
        case_type="جزائي",
    )
    governing = next(r for r in results if r.article_number == "146")
    assert governing.source_type.value == "criminal_law"


def test_law_priority_orders_by_corpus_position():
    from app.services.retrieval import law_priority, sources_for_case_type

    priority = law_priority(sources_for_case_type("مدني"))
    assert priority["civil_law"] < priority["evidence_law"] < priority["civil_procedure"]
    assert law_priority(None) == {}


# --------------------------------------------------------------------------- #
# Corpus coverage: every code the case-type routing names must actually exist
# --------------------------------------------------------------------------- #
def _corpus_source_types() -> set[str]:
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "app/data/corpus/yemeni_laws.json").read_text(
            encoding="utf-8"
        )
    )
    return {entry["document"]["source_type"] for entry in payload["documents"]}


def _excerpt_source_types() -> set[str]:
    """Codes present in the fixture the offline suite actually seeds from."""
    import json
    from pathlib import Path

    payload = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "app/data/samples/yemeni_laws_excerpt.json"
        ).read_text(encoding="utf-8")
    )
    return {entry["document"]["source_type"] for entry in payload["documents"]}


def test_every_routed_source_type_exists_in_the_corpus():
    """A case type must never route to a code the corpus does not contain.

    Regression: «عمالي» routed to ``labour_law``, which the corpus did not ship,
    so a labour case retrieved nothing and the judgment silently came out with
    no legal basis. The routing table and the corpus must stay in step.
    """
    routed = {source for sources in DEFAULT_SOURCE_TYPES.values() for source in sources}
    missing = routed - _corpus_source_types()
    assert not missing, f"case types route to codes absent from the corpus: {sorted(missing)}"


def test_every_routed_source_type_exists_in_the_test_excerpt():
    """The fixture the suite seeds from must cover every routed code.

    The full-corpus guard above passed while the excerpt still omitted the
    constitution, so an administrative case (إداري) retrieved nothing in the
    offline suite even though the shipped corpus had the code. The fixture and
    the routing table must stay in step too.
    """
    routed = {source for sources in DEFAULT_SOURCE_TYPES.values() for source in sources}
    missing = routed - _excerpt_source_types()
    assert not missing, (
        f"case types route to codes absent from the test excerpt: {sorted(missing)}; "
        "add them to KEEP in scripts/make_test_excerpt.py and regenerate"
    )


async def test_labour_case_retrieves_the_labour_code(seeded_store):
    """The labour code must be retrievable once it is routed to."""
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "عمالي",
            "subject_matter": "فصل تعسفي وتعويض عن إنهاء عقد العمل",
            "facts_summary": "أُنهي عقد عمل العامل دون سبب مشروع ودون إشعار.",
            "plaintiff_claims": [
                {"title": "التعويض", "description": "التعويض عن الفصل التعسفي"}
            ],
            "defendant_responses": [],
        },
        top_k=8,
        case_type="عمالي",
    )
    assert results, "a labour case must retrieve at least one article"
    assert any(r.source_type.value == "labour_law" for r in results), (
        "the labour code must be present in the labour case results"
    )


async def test_administrative_case_retrieves_the_constitution(seeded_store):
    """The constitution must be retrievable for a case type that routes to it.

    Regression: «إداري» routed to ``constitution``, which the test excerpt
    omitted, so an administrative case retrieved nothing from the fixture even
    though the shipped corpus contained the code.
    """
    results, _ = await retrieval_service.retrieve_for_case(
        case={
            "case_type": "إداري",
            "subject_matter": "إلغاء قرار إداري صادر عن جهة إدارية",
            "facts_summary": "أصدرت الجهة الإدارية قراراً بإلغاء تعيين المدعي دون سبب مشروع.",
            "plaintiff_claims": [
                {"title": "إلغاء القرار", "description": "إلغاء القرار الإداري المخالف للقانون"}
            ],
            "defendant_responses": [],
        },
        top_k=8,
        case_type="إداري",
    )
    assert results, "an administrative case must retrieve at least one article"
    assert any(r.source_type.value == "constitution" for r in results), (
        "the constitution must be present in the administrative case results"
    )
