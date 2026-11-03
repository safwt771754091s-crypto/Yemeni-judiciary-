"""Commercial litigation & financial extraction engine tests.

These exercise the deterministic money parser and the commercial engine
directly, plus one end-to-end pass through the pipeline that proves a commercial
case gets a financial summary in the response and the markdown draft.
"""
from __future__ import annotations

import pytest

from app.core.money import extract_amounts, format_money, parse_number
from app.schemas.case import CaseSubmissionRequest
from app.services.commercial import commercial_engine
from app.services.pipeline import case_pipeline


# --------------------------------------------------------------------------- #
# Money parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,000,000", 1_000_000.0),
        ("1.000.000", 1_000_000.0),
        ("1,234,567.89", 1_234_567.89),
        ("1.234,56", 1234.56),
        ("12,5", 12.5),
        ("٢٬٥٠٠٬٠٠٠", 2_500_000.0),
    ],
)
def test_parse_number_handles_mixed_separators(text, expected):
    assert parse_number(text) == pytest.approx(expected)


def test_written_amount_with_millions_and_hundred_thousands():
    """«خمسة ملايين وخمسمائة ألف» must read as 5.5m, not 500,005,000.

    The scale words are applied largest-first; applying «ألف» before «ملايين»
    (the natural reading order) would multiply the wrong way.
    """
    result = extract_amounts("مبلغ وقدره خمسة ملايين وخمسمائة ألف ريال يمني")
    assert result.primary is not None
    assert result.primary.amount == pytest.approx(5_500_000.0)


def test_written_amount_with_fraction_word():
    result = extract_amounts("التعويض نصف مليون ريال")
    assert result.primary is not None
    assert result.primary.amount == pytest.approx(500_000.0)


def test_article_number_is_not_money():
    """«المادة 146» must never be read as an amount of 146."""
    result = extract_amounts("وفقاً للمادة 146 من القانون المدني")
    assert result.numeric is None
    assert result.written is None
    assert result.primary is None


def test_percent_figure_is_not_an_amount():
    """«1%» is a rate, not one riyal."""
    result = extract_amounts("غرامة تأخيرية بنسبة 1% يومياً")
    assert result.primary is None


def test_numeric_and_written_conflict_is_flagged():
    result = extract_amounts(
        "مبلغ وقدره مليون ريال يمني وبالمقابل 1,500,000 ريال يمني"
    )
    assert result.reconciled is False
    assert result.note


def test_numeric_and_written_agreement():
    result = extract_amounts("مبلغ وقدره مليون ريال ورقم 1,000,000 ريال")
    assert result.reconciled is True


def test_format_money_uses_arabic_currency_label():
    assert "ريال يمني" in format_money(1_000_000, "YER")
    assert "دولار" in format_money(500, "USD")


# --------------------------------------------------------------------------- #
# Commercial engine
# --------------------------------------------------------------------------- #
@pytest.fixture
def commercial_case() -> dict:
    return {
        "case_type": "تجاري",
        "currency": "YER",
        "subject_matter": "المطالبة بقيمة بضاعة موردة وغرامة تأخيرية",
        "facts_summary": (
            "ورد المدعي للمدعى عليه بضاعة بموجب عقد مكتوب وكمبيالة، "
            "وتأخر المدعى عليه عن السداد."
        ),
        "plaintiff_claims": [
            {
                "title": "قيمة البضاعة",
                "description": "مبلغ وقدره خمسة ملايين ريال يمني",
                "amount": 5_000_000,
                "currency": "YER",
                "legal_basis": ["المادة 146"],
            },
            {
                "title": "غرامة تأخيرية",
                "description": "غرامة تأخيرية بنسبة 1% يومياً وفق الشرط الجزائي",
                "amount": None,
                "legal_basis": [],
            },
        ],
        "defendant_responses": [
            {
                "claim_ref": "قيمة البضاعة",
                "response_type": "admission",
                "description": "يعترف بقيمة البضاعة وقدرها خمسة ملايين ريال",
            },
            {
                "claim_ref": "غرامة تأخيرية",
                "response_type": "denial",
                "description": "ينكر صحة الشرط الجزائي",
            },
        ],
    }


@pytest.fixture
def commercial_evidence() -> list[dict]:
    return [
        {
            "title": "عقد التوريد المكتوب",
            "evidence_type": "contract",
            "extracted_text": "يلتزم المشتري بالسداد خلال ثلاثين يوماً من تاريخ التسليم.",
        },
        {"title": "كمبيالة رقم 12", "evidence_type": "commercial_bill"},
        {
            "title": "فاتورة رقم 55",
            "evidence_type": "invoice",
            "extracted_text": "فاتورة بضاعة بمبلغ 5,000,000 ريال يمني",
        },
        {"title": "حوالة بنكية", "evidence_type": "bank_transfer"},
    ]


def test_commercial_case_is_detected(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    assert result.is_commercial is True


def test_financial_lines_keep_claimed_and_acknowledged_distinct(
    commercial_case, commercial_evidence
):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    summary = result.financial_summary
    principal = next(line for line in summary.lines if line.subject == "قيمة البضاعة")
    assert principal.claimed_amount == pytest.approx(5_000_000.0)
    assert principal.acknowledged_amount == pytest.approx(5_000_000.0)
    assert summary.total_claimed == pytest.approx(5_000_000.0)
    assert summary.total_acknowledged == pytest.approx(5_000_000.0)


def test_rate_only_penalty_claim_is_still_a_financial_line(
    commercial_case, commercial_evidence
):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    penalty_line = next(
        (line for line in result.financial_summary.lines if "غرامة" in line.subject), None
    )
    assert penalty_line is not None
    assert penalty_line.claimed_amount is None, "a rate has no lump sum to claim"


def test_penalty_clause_rate_and_period_extracted(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    penalties = result.terms.penalties
    assert penalties
    running = next(p for p in penalties if p.rate_percent is not None)
    assert running.rate_percent == pytest.approx(1.0)
    assert running.rate_period == "يومي"
    # A running penalty must not carry the principal as its own amount.
    assert running.amount is None


def test_payment_deadline_extracted(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    assert result.terms.payment_deadline_days == 30


def test_commercial_evidence_kinds_classified(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    kinds = {k.value for k in result.financial_summary.evidence_kinds}
    assert {"written_contract", "commercial_bill", "invoice", "bank_transfer"} <= kinds


def test_non_commercial_case_is_not_flagged():
    """A civil case with civil evidence must not be treated as commercial."""
    case = {
        "case_type": "مدني",
        "subject_matter": "المطالبة بتعويض عن الإخلال بعقد بيع سيارة",
        "facts_summary": "أقام المدعي دعوى تعويض.",
        "plaintiff_claims": [
            {"title": "تعويض", "description": "مبلغ 3,500,000 ريال", "amount": 3_500_000}
        ],
        "defendant_responses": [],
    }
    evidence = [
        {"title": "عقد بيع السيارة", "evidence_type": "contract"},
        {"title": "شهادة الشهود", "evidence_type": "witness"},
    ]
    result = commercial_engine.extract(case=case, evidence=evidence)
    assert result.is_commercial is False


def test_commercial_paper_flags_a_case_as_commercial():
    """A case carrying commercial paper is commercial however it is labelled."""
    case = {
        "case_type": "مدني",
        "subject_matter": "المطالبة بقيمة كمبيالة",
        "facts_summary": "أقام المدعي الدعوى بموجب كمبيالة مستحقة الدفع.",
        "plaintiff_claims": [{"title": "قيمة الكمبيالة", "amount": 1_000_000}],
        "defendant_responses": [],
    }
    result = commercial_engine.extract(case=case, evidence=[])
    assert result.is_commercial is True


def test_awards_come_from_the_verdict_not_the_claim(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    summary = commercial_engine.apply_awards(
        result.financial_summary,
        [{"subject": "قيمة البضاعة", "amount": 4_000_000, "currency": "YER"}],
    )
    principal = next(line for line in summary.lines if line.subject == "قيمة البضاعة")
    assert principal.claimed_amount == pytest.approx(5_000_000.0)
    assert principal.awarded_amount == pytest.approx(4_000_000.0)
    assert summary.total_awarded == pytest.approx(4_000_000.0)


def test_verdict_without_amount_leaves_line_unawarded(commercial_case, commercial_evidence):
    result = commercial_engine.extract(case=commercial_case, evidence=commercial_evidence)
    summary = commercial_engine.apply_awards(
        result.financial_summary, [{"subject": "قيمة البضاعة", "amount": None}]
    )
    principal = next(line for line in summary.lines if line.subject == "قيمة البضاعة")
    assert principal.awarded_amount is None
    assert summary.total_awarded == 0.0


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #
async def test_pipeline_returns_commercial_summary(seeded_store, commercial_case, commercial_evidence):
    payload = dict(commercial_case)
    payload.update(
        {
            "case_number": "ت-200/2026",
            "court_name": "المحكمة التجارية - صنعاء",
            "judge_name": "القاضي/ عمر",
            "plaintiff": {"name": "شركة النور التجارية", "role": "plaintiff"},
            "defendant": {"name": "مؤسسة الأمانة", "role": "defendant"},
            "evidence": commercial_evidence,
            "generate_judgment": True,
            "max_retrieved_articles": 10,
        }
    )
    response = await case_pipeline.submit(CaseSubmissionRequest.model_validate(payload))

    assert response.commercial is not None
    assert response.commercial.is_commercial is True
    assert response.commercial.financial_summary.lines
    assert response.commercial.financial_summary.total_claimed == pytest.approx(5_000_000.0)

    # The awarded figure comes from the verdict, so it is present offline too.
    assert response.commercial.financial_summary.total_awarded > 0
    assert response.judgment is not None
    assert response.judgment.financial_summary is not None

    markdown = response.judgment_markdown or ""
    assert "الملخص المالي التجاري" in markdown
    assert "المطالب به" in markdown and "المقضي به" in markdown


async def test_commercial_intake_overrides_extraction(seeded_store, commercial_case):
    payload = dict(commercial_case)
    payload.update(
        {
            "case_number": "ت-201/2026",
            "court_name": "المحكمة التجارية - عدن",
            "judge_name": "القاضي/ سالم",
            "plaintiff": {"name": "شركة أ", "role": "plaintiff"},
            "defendant": {"name": "شركة ب", "role": "defendant"},
            "evidence": [],
            "generate_judgment": False,
            "commercial": {"payment_deadline_days": 45},
        }
    )
    response = await case_pipeline.submit(CaseSubmissionRequest.model_validate(payload))
    assert response.commercial is not None
    assert response.commercial.is_commercial is True
    assert response.commercial.terms.payment_deadline_days == 45
