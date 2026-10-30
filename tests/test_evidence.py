"""Tests for the evidence-weighing layer under the Yemeni Law of Evidence."""
from __future__ import annotations

from app.services.evidence import PROOF_HIERARCHY, evidence_service


def test_baseline_ranks_written_evidence_above_digital():
    assessments = evidence_service.baseline(
        [
            {"title": "عقد رسمي", "evidence_type": "contract", "submitted_by": "plaintiff",
             "document_ref": "s://c.pdf"},
            {"title": "محادثات واتساب", "evidence_type": "digital", "submitted_by": "defendant",
             "document_ref": "s://chat.txt"},
        ]
    )
    contract, digital = assessments
    assert contract["weight"] > digital["weight"]
    assert contract["proof_category"] == "كتابة"
    assert digital["admissibility"] == "requires_verification"
    assert digital["requires_verification"] is True


def test_admission_is_decisive():
    assessments = evidence_service.baseline(
        [{"title": "إقرار قضائي", "evidence_type": "admission", "submitted_by": "defendant",
          "extracted_text": "أقر المدعى عليه بالدين"}]
    )
    assert assessments[0]["strength"] == "decisive"
    assert assessments[0]["weight"] == PROOF_HIERARCHY["إقرار"][1]


def test_evidence_without_any_document_reference_is_downgraded():
    assessments = evidence_service.baseline(
        [{"title": "دليل بلا مستند", "evidence_type": "document", "submitted_by": "plaintiff"}]
    )
    assert assessments[0]["weight"] <= 0.5
    assert assessments[0]["admissibility"] in {"conditional", "requires_verification"}


async def test_analyze_merges_llm_output_without_upgrading_admissibility(seeded_store):
    """The LLM cannot lift a 'requires_verification' item to 'admissible'."""
    evidence = [
        {
            "id": "e1",
            "title": "تسجيل صوتي",
            "evidence_type": "audio",
            "submitted_by": "plaintiff",
            "document_ref": "s://audio.mp3",
        }
    ]
    assessments, _warnings, _model = await evidence_service.analyze(
        case={"case_type": "مدني", "subject_matter": "نزاع", "plaintiff_claims": []},
        evidence=evidence,
        articles=[
            {
                "article_label": "المادة (38)",
                "law_title": "قانون الإثبات اليمني",
                "content": "الأدلة الرقمية تعد أدلة كتابية إذا أمكن التحقق من صحتها.",
                "similarity": 0.6,
                "article_id": "x",
            }
        ],
    )
    assert len(assessments) == 1
    assert assessments[0].admissibility.value == "requires_verification"
    assert assessments[0].weight <= 0.35


async def test_analyze_without_evidence_warns(seeded_store):
    assessments, warnings, _ = await evidence_service.analyze(
        case={"case_type": "مدني", "subject_matter": "نزاع", "plaintiff_claims": []},
        evidence=[],
        articles=[],
    )
    assert assessments == []
    assert any("لا توجد أدلة" in w for w in warnings)
