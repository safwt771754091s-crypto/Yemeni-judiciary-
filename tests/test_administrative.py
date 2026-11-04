"""Administrative-case retrieval tests.

An administrative case (إداري) is decided on the constitution and the civil
procedure code. The commercial profile has always carried a relevance signal
for its governing provisions (``commercial_relevance`` plus a set of statutory
queries); the administrative profile carried neither, so an administrative case
planned only its generic queries and the formal constitutional provisions never
surfaced — the case retrieved generic civil-procedure articles and no
constitutional provision at all.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.case import CaseSubmissionRequest
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.ingestion import ingestion_service
from app.services.pipeline import case_pipeline
from app.services.retrieval import administrative_relevance, retrieval_service

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "app/data/corpus/yemeni_laws.json"

ADMIN_CASE = {
    "case_type": "إداري",
    "subject_matter": "إلغاء قرار إداري صادر عن جهة إدارية",
    "facts_summary": "أصدرت الجهة الإدارية قراراً بإلغاء تعيين المدعي دون سبب مشروع.",
    "plaintiff_claims": [
        {"title": "إلغاء القرار", "description": "إلغاء القرار الإداري المخالف للقانون"}
    ],
    "defendant_responses": [],
}


async def _seed_routed_codes() -> None:
    """Seed the two codes an administrative case routes to and turns on.

    The full corpus is not needed: the defect is that the constitution is
    crowded out by the civil procedure code, and a two-code corpus reproduces
    that in a fraction of the time.
    """
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    for entry in payload["documents"]:
        if entry["document"]["source_type"] in {"constitution", "civil_procedure"}:
            await ingestion_service.ingest_text(
                IngestTextRequest(
                    document=LegalDocumentCreate(**entry["document"]),
                    text=entry["text"],
                )
            )


async def test_administrative_case_retrieves_constitution_articles():
    """The constitution must reach the top of an administrative case.

    Regression: an administrative case routed to the constitution but carried no
    relevance signal for it, so the generic civil-procedure articles outranked
    every constitutional provision and the case retrieved none of the law it is
    decided on.
    """
    await _seed_routed_codes()
    results, _ = await retrieval_service.retrieve_for_case(
        case=ADMIN_CASE, top_k=8, case_type="إداري"
    )
    assert results, "an administrative case must retrieve at least one article"

    constitution = [r for r in results if r.source_type.value == "constitution"]
    assert len(constitution) >= 3, (
        "constitutional provisions must make up a real share of an administrative "
        f"case's results; got {[r.source_type.value for r in results]}"
    )
    assert any(r.source_type.value == "constitution" for r in results[:3]), (
        "a constitutional provision must rank among the top three results, not be "
        "buried beneath generic civil-procedure articles"
    )
    # The civil-service provision is what a قرار تعيين/ترقية dispute turns on.
    assert "28" in {r.article_number for r in constitution}, (
        "the civil-service provision (المادة 28) must be retrieved for a case "
        "about an administrative appointment decision"
    )


async def test_administrative_plan_queries_cover_the_governing_provisions():
    """The query planner must add the administrative statutory queries.

    Without them the case's colloquial wording never surfaces the formal
    constitutional provisions.
    """
    queries = await retrieval_service.plan_queries(ADMIN_CASE)
    joined = " | ".join(queries)
    for term in ("حق التقاضي", "الوظيفة العامة", "اللوائح الإدارية"):
        assert any(term in query for query in queries), (
            f"the administrative plan must query for {term!r}; got {joined}"
        )


class _FixedExpansion:
    """Expansion chain stub that returns a caller-supplied query list."""

    def __init__(self, queries: list[str]) -> None:
        self._queries = queries

    async def run(self, _messages: object) -> SimpleNamespace:
        return SimpleNamespace(data={"queries": list(self._queries)})


async def test_administrative_queries_survive_a_full_model_budget(monkeypatch):
    """The model must not be able to crowd the statutory queries out of the plan.

    The expansion prompt asks for up to six queries and the plan is capped at
    ten. Appended after the model's output, a full budget pushed the statutory
    queries past the cap, so an administrative case kept none of them and fell
    back to the generic civil-procedure articles this module exists to outrank.
    """
    monkeypatch.setattr(
        retrieval_service,
        "_expansion_chain",
        _FixedExpansion([f"الالتزامات والعقود والتعويض {i}" for i in range(6)]),
    )

    queries = await retrieval_service.plan_queries(ADMIN_CASE)
    for term in ("حق التقاضي", "الوظيفة العامة", "اللوائح الإدارية"):
        assert any(term in query for query in queries), (
            f"a full model budget must not drop {term!r}; got {' | '.join(queries)}"
        )


async def test_administrative_case_retrieves_constitution_despite_a_full_budget(monkeypatch):
    """A full model budget must not undo the retrieval fix end to end.

    The civil law is seeded alongside the two routed codes because it is the
    code the degraded plan falls back to; without it in the corpus the assertion
    below cannot tell the two plans apart.
    """
    monkeypatch.setattr(
        retrieval_service,
        "_expansion_chain",
        _FixedExpansion([f"الالتزامات والعقود والتعويض {i}" for i in range(6)]),
    )
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    for entry in payload["documents"]:
        if entry["document"]["source_type"] in {"constitution", "civil_procedure", "civil_law"}:
            await ingestion_service.ingest_text(
                IngestTextRequest(
                    document=LegalDocumentCreate(**entry["document"]),
                    text=entry["text"],
                )
            )

    results, _ = await retrieval_service.retrieve_for_case(
        case=ADMIN_CASE, top_k=8, case_type="إداري"
    )
    types = [r.source_type.value for r in results]
    assert len([t for t in types[:3] if t == "constitution"]) >= 2, (
        "the constitution must not merely appear in an administrative case's "
        "results but dominate its top three; a full model budget left only the "
        f"least specific statutory queries in the plan and produced {types}"
    )


def test_administrative_relevance_only_boosts_administrative_cases():
    """The administrative signal must be inert for every other case type."""
    article = SimpleNamespace(
        heading=None, content="الخدمة العامة تكليف وشرف للقائمين بها ويستهدف الموظفون المصلحة العامة"
    )
    assert administrative_relevance(article, "إداري") > 0.0
    for case_type in ("تجاري", "مدني", "جنائي", None, ""):
        assert administrative_relevance(article, case_type) == 0.0


def test_administrative_relevance_ignores_unrelated_articles():
    """An article with no administrative vocabulary must not be lifted."""
    article = SimpleNamespace(
        heading=None, content="يلتزم البائع بتسليم المبيع للمشتري في الأجل المتفق عليه"
    )
    assert administrative_relevance(article, "إداري") == 0.0


@pytest.mark.asyncio
async def test_administrative_case_grounds_its_draft_in_the_constitution():
    """End to end: the draft's citations must include a constitutional provision.

    This is the user-visible form of the defect — an administrative judgment that
    cited only the civil procedure code and never the constitution.
    """
    await _seed_routed_codes()
    request = CaseSubmissionRequest(
        case_number="1446/2026",
        case_type="إداري",
        court_name="محكمة القضاء الإداري - صنعاء",
        judge_name="القاضي/ عبدالله الشرعبي",
        subject_matter="إلغاء قرار إداري بإلغاء تعيين المدعي في الوظيفة العامة",
        facts_summary=ADMIN_CASE["facts_summary"],
        plaintiff={"name": "أحمد عبدالله ناصر", "role": "plaintiff", "address": "صنعاء"},
        defendant={"name": "وزارة الخدمة المدنية", "role": "defendant", "address": "صنعاء"},
        plaintiff_claims=[
            {
                "title": "إلغاء القرار الإداري",
                "description": "إلغاء قرار الجهة الإدارية بإلغاء تعيين المدعي في الوظيفة العامة",
            }
        ],
        evidence=[],
    )
    response = await case_pipeline.submit(request)

    assert response.retrieved_articles, "the engine must retrieve Yemeni statutes"
    assert any(
        a["source_type"] == "constitution" for a in response.retrieved_articles
    ), (
        "an administrative case must retrieve constitutional provisions; got "
        f"{sorted({a['source_type'] for a in response.retrieved_articles})}"
    )
    assert any(
        c.law_title and "دستور" in c.law_title
        for c in response.judgment.reasoning.cited_articles
    ), (
        "the administrative draft must cite a constitutional provision in its "
        "الأسباب والحيثيات; cited "
        f"{[c.law_title for c in response.judgment.reasoning.cited_articles]}"
    )

