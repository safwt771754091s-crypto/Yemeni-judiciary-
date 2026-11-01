"""Case detailing (تفصيل الدعوى) extraction tests.

Every assertion here is about *not inventing* things: a field the patterns do
not find must stay None, and a number that is a case number or an article must
never be reported as the claim value.
"""
from __future__ import annotations

import pytest

from app.services.case_details import case_details_extractor

CIVIL_FILING = """المحكمة الابتدائية بأمانة العاصمة - الدائرة التجارية الأولى
القاضي: عبدالله ناصر المقطري
القضية رقم 1451/2026
الوقائع
بتاريخ 12/03/2026 أبرم المدعي عقد بيع مع المدعى عليه بموجب عقد مكتوب، وقد التزم
المدعي بتسليم البضاعة إلا أن المدعى عليه امتنع عن سداد الثمن المتفق عليه وقدره
خمسة ملايين ريال يمني رغم مطالبته مراراً، مما ألحق بالمدعي أضراراً مادية ومعنوية.
المدعي: أحمد محمد صالح
وكيل المدعي: الأستاذ سامي عبدالرحمن
المدعى عليه: شركة الفجر للتجارة والاستيراد
وكيل المدعى عليه: الأستاذ هاني القباطي
الطلبات
1. إلزام المدعى عليه بأداء مبلغ 5000000 ريال قيمة الثمن المتأخر.
2. إلزام المدعى عليه بالتعويض عن الأضرار المادية والمعنوية.
3. تحميل المدعى عليه المصاريف وأتعاب المحاماة.
وفقاً للمادة 146 والمادة 189 من القانون المدني اليمني.
الدفوع
- ينكر المدعى عليه توقيع العقد المبرم بين الطرفين.
- الدفع بالتقادم.
"""


class TestStructuredExtraction:
    def test_extracts_all_core_fields(self):
        d = case_details_extractor.extract(CIVIL_FILING)
        assert d.court_name.startswith("المحكمة الابتدائية")
        assert d.circuit == "التجارية الأولى"
        assert d.case_number == "1451/2026"
        assert d.hearing_date == "12/03/2026"
        assert d.judge_name == "عبدالله ناصر المقطري"
        assert d.plaintiff_name == "أحمد محمد صالح"
        assert d.defendant_name == "شركة الفجر للتجارة والاستيراد"
        assert d.plaintiff_representative == "الأستاذ سامي عبدالرحمن"
        assert d.defendant_representative == "الأستاذ هاني القباطي"
        assert d.currency == "YER"

    def test_claim_value_is_money_not_a_year(self):
        """The claim value must be the amount, never the case-number year."""
        d = case_details_extractor.extract(CIVIL_FILING)
        assert d.claim_value == 5_000_000.0

    def test_claims_and_defences_are_separated(self):
        d = case_details_extractor.extract(CIVIL_FILING)
        assert len(d.claims) == 3
        assert len(d.defences) == 2
        assert "التقادم" in d.defences[1]
        # The statutory-citation line must not leak into the claims list.
        assert not any("القانون المدني" in c for c in d.claims)

    def test_legal_basis_found_through_preposition(self):
        """«وفقاً للمادة 146» must yield 146, despite the attached preposition."""
        d = case_details_extractor.extract(CIVIL_FILING)
        assert "146" in d.legal_basis
        assert "189" in d.legal_basis

    def test_subject_derived_when_absent(self):
        d = case_details_extractor.extract(CIVIL_FILING)
        assert d.subject_matter
        assert "الثمن" in d.subject_matter
        assert any("استنتاجه" in n for n in d.notes)

    def test_confidence_reflects_completeness(self):
        d = case_details_extractor.extract(CIVIL_FILING)
        assert d.confidence >= 0.9
        assert "plaintiff_name" in d.fields_found
        assert "defendant_name" in d.fields_found


class TestNoFabrication:
    def test_empty_text_yields_empty_details(self):
        d = case_details_extractor.extract("")
        assert d.plaintiff_name is None
        assert d.defendant_name is None
        assert d.claims == []
        assert d.confidence == 0.0
        assert d.notes

    def test_whitespace_only_text_is_reported(self):
        d = case_details_extractor.extract("   \n\t  ")
        assert d.confidence == 0.0

    def test_bare_case_number_is_not_the_claim_value(self):
        """A filing that only mentions the case number has no claim value."""
        d = case_details_extractor.extract("المحكمة الابتدائية\nالقضية رقم 1451/2026\nلا شيء آخر.")
        assert d.claim_value is None

    def test_missing_parties_are_not_invented(self):
        d = case_details_extractor.extract(
            "المحكمة الابتدائية\nالوقائع: لم يُحدد أطراف الدعوى في هذه الورقة."
        )
        assert d.plaintiff_name is None
        assert d.defendant_name is None
        assert any("plaintiff_name" in n for n in d.notes)


class TestAmounts:
    def test_written_amount(self):
        d = case_details_extractor.extract("المطلوب إلزام المدعى عليه بأداء مبلغ ثلاثة ملايين ريال.")
        assert d.claim_value == 3_000_000.0

    def test_numeric_amount_with_thousands_separator(self):
        d = case_details_extractor.extract("قيمة الدعوى مبلغ 2,500,000 ريال يمني.")
        assert d.claim_value == 2_500_000.0

    def test_article_number_is_not_an_amount(self):
        d = case_details_extractor.extract("وفقاً للمادة 146 من القانون المدني.")
        assert d.claim_value is None

    def test_usd_detected(self):
        d = case_details_extractor.extract("المطالبة بمبلغ 50000 دولار أمريكي.")
        assert d.claim_value == 50_000.0
        assert d.currency == "USD"


class TestRobustness:
    def test_arabic_indic_digits_are_normalized(self):
        d = case_details_extractor.extract("القضية رقم ١٢٣٤/٢٠٢٦\nالمطالبة بمبلغ ٧٥٠٠٠٠ ريال.")
        assert d.case_number == "1234/2026"
        assert d.claim_value == 750_000.0

    @pytest.mark.parametrize("filler", ["", "\n\n\n", "  "])
    def test_handles_odd_whitespace(self, filler):
        d = case_details_extractor.extract(f"{filler}المدعي: علي محمد{filler}")
        assert d.plaintiff_name == "علي محمد"
