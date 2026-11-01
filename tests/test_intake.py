"""Intake flow tests: papers in, classified and structured case out.

These exercise the real extraction, classification and submission-assembly
paths. The pipeline is run for real against the in-memory vector store and the
deterministic mock provider, so a regression in how intake builds the request
shows up here rather than only in a live deployment.
"""
from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.schemas.intake import CaseClassifyRequest, CaseExtractRequest
from app.services.auth import auth_service
from app.services.case_details import case_details_extractor
from app.services.intake import intake_service

CRIMINAL_FILING = """المحكمة الابتدائية بأمانة العاصمة - الدائرة الجزائية
القضية رقم 2201/2026
الوقائع
أحالت النيابة العامة المتهم إلى المحكمة بتهمة الاعتداء على سلامة الجسم باستعمال
آلة حادة، وطلبت توقيع العقوبة المقررة قانوناً.
المدعي: النيابة العامة
المتهم: سامي عبده قائد
الطلبات
1. إدانة المتهم بجريمة الاعتداء على سلامة الجسم.
2. توقيع العقوبة المقررة قانوناً.
وفقاً للمادة 236 من قانون الجرائم والعقوبات اليمني.
"""

FAMILY_FILING = """المحكمة الابتدائية
القضية رقم 77/2026
الوقائع: تزوجت المدعية من المدعى عليه بعقد نكاح صحيح وامتنع عن أداء النفقة.
المدعي: فاطمة علي
المدعى عليه: محمد حسن
الطلبات
1. إلزام المدعى عليه بنفقة الزوجية وقدرها 50000 ريال شهرياً.
2. إلزامه بنفقة الأولاد.
الدفوع
- ينكر المدعى عليه الزوجية.
"""


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _auth_headers(client: httpx.AsyncClient) -> dict[str, str]:
    # The autouse clean_state fixture resets the user store for every test, so
    # the owner must be (re)seeded before logging in.
    await auth_service.ensure_owner()
    r = await client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": "Owner@Yemen2026"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestIntakeServiceAssembly:
    """The service turns extracted components into a valid submission."""

    def test_builds_submission_from_criminal_text(self):
        details = case_details_extractor.extract(CRIMINAL_FILING)
        classification = intake_service.classify(CRIMINAL_FILING, details=details)
        submission = intake_service.build_submission(
            details=details, classification=classification, evidence=[]
        )
        assert submission.case_type == "جنائي"
        assert submission.plaintiff.name == "النيابة العامة"
        assert submission.defendant.name == "سامي عبده قائد"
        assert submission.plaintiff_claims, "claims must be assembled"
        assert submission.plaintiff_claims[0].legal_basis

    def test_reviewer_override_wins_over_extraction(self):
        details = case_details_extractor.extract(CRIMINAL_FILING)
        classification = intake_service.classify(CRIMINAL_FILING, details=details)
        submission = intake_service.build_submission(
            details=details,
            classification=classification,
            evidence=[],
            defendant_name="اسم مصحَّح يدوياً",
            claim_value=123456.0,
        )
        assert submission.defendant.name == "اسم مصحَّح يدوياً"
        assert submission.claim_value == 123456.0

    def test_defence_types_are_classified(self):
        details = case_details_extractor.extract(
            "المدعى عليه: خالد\nالدفوع\n- الدفع بالتقادم.\n- الدفع بعدم اختصاص المحكمة."
        )
        classification = intake_service.classify("الدفوع", details=details)
        submission = intake_service.build_submission(
            details=details, classification=classification, evidence=[]
        )
        types = {r.response_type for r in submission.defendant_responses}
        assert "prescription" in types
        assert "jurisdiction" in types

    def test_gap_assessment_flags_missing_parties(self):
        details = case_details_extractor.extract("الوقائع: وقائع دون أطراف.")
        classification = intake_service.classify("الوقائع: وقائع دون أطراف.", details=details)
        gaps = intake_service.assess_gaps(details, classification)
        assert any("المدعي" in g for g in gaps)
        assert any("المدعى عليه" in g for g in gaps)

    def test_gap_assessment_flags_criminal_signal_in_civil_case(self):
        """A civil routing that hides a criminal signal must be surfaced."""
        details = case_details_extractor.extract(FAMILY_FILING)
        classification = intake_service.classify(FAMILY_FILING, details=details)
        # Force the mismatch the way a mislabelled file would.
        classification.case_type = "مدني"
        classification.criminal_flag = True
        gaps = intake_service.assess_gaps(details, classification)
        assert any("النيابة" in g for g in gaps)


class TestIntakeApi:
    async def test_health_and_lexicon_require_auth(self, client):
        assert (await client.get("/api/v1/cases/lexicon")).status_code == 401

    async def test_lexicon_lists_case_types_and_ocr(self, client):
        headers = await _auth_headers(client)
        r = await client.get("/api/v1/cases/lexicon", headers=headers)
        assert r.status_code == 200
        body = r.json()
        keys = {c["key"] for c in body["case_types"]}
        assert {"مدني", "جنائي", "أسري", "تجاري"} <= keys
        assert "ocr" in body
        assert ".pdf" in body["supported_formats"]

    async def test_classify_criminal(self, client):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/classify",
            headers=headers,
            json=CaseClassifyRequest(text=CRIMINAL_FILING).model_dump(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["case_type"] == "جنائي"
        assert body["criminal_flag"] is True
        assert "criminal_law" in body["source_types"]

    async def test_classify_keeps_declared_type_as_presumption(self, client):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/classify",
            headers=headers,
            json={"text": "أطلب إلزام المدعى عليه بأداء دين بموجب سند دين.", "declared_case_type": "تجاري"},
        )
        assert r.status_code == 200
        assert any("قرينة" in n for n in r.json()["notes"])

    async def test_extract_family_case(self, client):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/extract",
            headers=headers,
            json=CaseExtractRequest(text=FAMILY_FILING).model_dump(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["classification"]["case_type"] == "أسري"
        assert body["details"]["plaintiff_name"] == "فاطمة علي"
        assert body["details"]["claim_value"] == 50_000.0

    async def test_intake_from_text_runs_full_pipeline(self, client, seeded_store):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/intake",
            headers=headers,
            data={"text": FAMILY_FILING, "generate_judgment": "true"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] in ("judgment_ready", "needs_review")
        assert body["classification"]["case_type"] == "أسري"
        assert body["details"]["plaintiff_name"] == "فاطمة علي"
        assert body["pipeline"] is not None
        assert body["pipeline"]["retrieved_articles"], "statutes must be retrieved"
        assert body["judgment_id"]

    async def test_intake_without_judgment_stops_after_structuring(self, client, seeded_store):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/intake",
            headers=headers,
            data={"text": FAMILY_FILING, "generate_judgment": "false"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["pipeline"] is None
        assert body["judgment_id"] is None
        assert body["status"] == "structured"

    async def test_intake_rejects_empty_request(self, client):
        headers = await _auth_headers(client)
        r = await client.post("/api/v1/cases/intake", headers=headers, data={})
        assert r.status_code == 422

    async def test_intake_accepts_uploaded_text_file(self, client, seeded_store):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/intake",
            headers=headers,
            files={"files": ("دعوى.txt", FAMILY_FILING.encode("utf-8"), "text/plain")},
            data={"generate_judgment": "false"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["documents"]) == 1
        assert body["documents"][0]["filename"] == "دعوى.txt"
        assert body["details"]["plaintiff_name"] == "فاطمة علي"

    async def test_intake_override_fields_reach_the_pipeline(self, client, seeded_store):
        headers = await _auth_headers(client)
        r = await client.post(
            "/api/v1/cases/intake",
            headers=headers,
            data={
                "text": FAMILY_FILING,
                "generate_judgment": "false",
                "case_number": "9999/2026",
                "plaintiff_name": "مدعية مصححة",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["structured_case"]["case_number"] == "9999/2026"
        assert body["structured_case"]["plaintiff"]["name"] == "مدعية مصححة"
