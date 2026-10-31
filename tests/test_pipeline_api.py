"""End-to-end pipeline and API tests."""
from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.schemas.case import CaseSubmissionRequest
from app.services.judgment import render_markdown
from app.services.pipeline import case_pipeline


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_full_pipeline_produces_grounded_judgment(seeded_store, sample_case_payload):
    request = CaseSubmissionRequest.model_validate(sample_case_payload)
    response = await case_pipeline.submit(request)

    assert response.case.id
    assert response.judgment_id
    assert response.judgment is not None
    assert response.retrieved_articles, "the engine must retrieve Yemeni statutes"
    assert response.judgment_markdown

    draft = response.judgment
    assert draft.verdict.items, "the verdict must contain at least one ruling"
    assert draft.reasoning.summary
    assert draft.preamble.court_name

    # Every citation in the draft must resolve to a retrieved statute.
    retrieved_labels = {
        a["article_label"] for a in response.retrieved_articles
    }
    for citation in draft.reasoning.cited_articles:
        assert citation.article_label in retrieved_labels, (
            f"citation {citation.article_label} was not in the retrieved corpus"
        )

    assert response.grounding_score is not None and response.grounding_score > 0
    assert len(response.evidence_analysis) == len(sample_case_payload["evidence"])


async def test_markdown_contains_all_four_judgment_sections(seeded_store, sample_case_payload):
    request = CaseSubmissionRequest.model_validate(sample_case_payload)
    response = await case_pipeline.submit(request)
    markdown = response.judgment_markdown or ""

    for heading in ("الديباجة", "الوقائع", "الأسباب والحيثيات", "المنطوق"):
        assert heading in markdown, f"missing section: {heading}"
    assert "مسودة آلية غير مُعتمدة" in markdown
    assert "باسم الشعب" in markdown


async def test_pipeline_without_corpus_warns_and_does_not_fabricate(sample_case_payload):
    """With an empty corpus the engine must warn, not invent articles."""
    request = CaseSubmissionRequest.model_validate(sample_case_payload)
    response = await case_pipeline.submit(request)

    assert response.retrieved_articles == []
    assert any("لم يتم العثور على نصوص" in w for w in response.warnings)
    assert response.grounding_score == 0.0
    # Even with no statutes the schema must stay valid.
    assert response.judgment is not None
    assert response.judgment.verdict.items


async def test_submit_endpoint_over_http(seeded_store, sample_case_payload, client):
    response = await client.post("/api/v1/cases/submit", json=sample_case_payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["judgment_id"]
    assert body["retrieved_articles"]
    assert body["judgment"]["verdict"]["items"]


async def test_submit_rejects_payload_without_claims(client, sample_case_payload):
    payload = {**sample_case_payload, "plaintiff_claims": []}
    response = await client.post("/api/v1/cases/submit", json=payload)
    assert response.status_code == 422


async def test_health_and_readiness(client):
    assert (await client.get("/api/v1/health")).status_code == 200
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["llm"]["provider"]
    assert body["embeddings"]["model"]


async def test_ingestion_and_stats_over_http(client):
    payload = {
        "document": {
            "source_type": "civil_law",
            "title_ar": "قانون اختباري",
            "law_number": "99",
            "promulgation_year": 2020,
        },
        "text": "المادة (1) نص تجريبي أول.\n\nالمادة (2) نص تجريبي ثانٍ.",
    }
    response = await client.post("/api/v1/ingestion/text", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["chunks_created"] >= 2
    assert body["articles_detected"] == 2

    stats = await client.get("/api/v1/ingestion/stats")
    assert stats.status_code == 200
    assert stats.json()["total_articles"] >= 2


async def test_retrieval_endpoint_over_http(seeded_store, client):
    response = await client.post(
        "/api/v1/retrieval/search",
        json={"query": "العقد شريعة المتعاقدين والالتزام بالتنفيذ", "top_k": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 1
    assert body["results"][0]["article_label"]


async def test_case_and_judgment_retrieval_endpoints(seeded_store, sample_case_payload, client):
    submit = await client.post("/api/v1/cases/submit", json=sample_case_payload)
    body = submit.json()
    case_id = body["case"]["id"]
    judgment_id = body["judgment_id"]

    case = await client.get(f"/api/v1/cases/{case_id}")
    assert case.status_code == 200
    assert case.json()["status"] == "judgment_ready"

    judgments = await client.get(f"/api/v1/cases/{case_id}/judgments")
    assert judgments.status_code == 200
    assert len(judgments.json()["items"]) == 1

    detail = await client.get(f"/api/v1/cases/{case_id}/judgments/{judgment_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["grounding_score"] >= 0
    assert "المنطوق" in detail_body["markdown_ar"]

    approval = await client.post(
        f"/api/v1/cases/{case_id}/judgments/{judgment_id}/approve?decision=approved"
    )
    assert approval.status_code == 200
    assert approval.json()["decision"] == "approved"


async def test_missing_case_returns_404(client):
    response = await client.get("/api/v1/cases/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_render_markdown_is_pure(seeded_store=None):
    """render_markdown must not require a live engine (schema-level smoke test)."""
    from app.schemas.judgment import JudgmentDraftEnvelope

    envelope = JudgmentDraftEnvelope.model_validate(
        {
            "draft": {
                "preamble": {"court_name": "المحكمة الابتدائية", "plaintiff": {"name": "أ"},
                             "defendant": {"name": "ب"}},
                "facts": {"narrative": "وقائع مختصرة"},
                "reasoning": {"summary": "أسباب مختصرة"},
                "verdict": {"summary": "منطوق", "items": [
                    {"decision": "قبول", "subject": "شكلاً", "detail": "قبول الدعوى شكلاً"}
                ]},
            },
            "confidence": 0.5,
            "grounding_score": 0.4,
            "model": "test",
        }
    )
    markdown = render_markdown(envelope)
    assert "الديباجة" in markdown and "المنطوق" in markdown
    assert "أ" in markdown and "ب" in markdown


async def test_party_names_come_from_the_case_file_not_the_model(seeded_store, sample_case_payload):
    """Regression: the model once returned the subject matter as the defendant.

    Party identity is a fact of record. It must be copied from the case file,
    never generated, exactly like the court name and the case number.
    """
    request = CaseSubmissionRequest.model_validate(sample_case_payload)
    response = await case_pipeline.submit(request)

    preamble = response.judgment.preamble
    assert preamble.plaintiff["name"] == sample_case_payload["plaintiff"]["name"]
    assert preamble.defendant["name"] == sample_case_payload["defendant"]["name"]
    assert preamble.defendant["name"] != sample_case_payload["subject_matter"]


async def test_api_response_exposes_citation_provenance(seeded_store, sample_case_payload):
    """Every retrieved article must carry its source and text quality.

    Regression: the response builder hand-picked fields and silently dropped
    source_url, text_quality and verbatim, hiding the provenance the whole
    anti-fabrication design depends on.
    """
    request = CaseSubmissionRequest.model_validate(sample_case_payload)
    response = await case_pipeline.submit(request)

    assert response.retrieved_articles
    for article in response.retrieved_articles:
        assert "text_quality" in article
        assert "source_url" in article
        assert "verbatim" in article
        assert article["content"], "the cited text itself must be returned"
