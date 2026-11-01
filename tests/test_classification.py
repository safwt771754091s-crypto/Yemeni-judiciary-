"""Classification tests for the Yemeni court-branch router.

The classifier routes a case to the branch that will hear it and to the codes
that branch applies. Its two failure modes are both serious: routing a criminal
matter to a civil branch, and matching a legal term inside an unrelated word
(the classic Arabic substring bug - "سب" inside "مناسباً").
"""
from __future__ import annotations

import pytest

from app.core.legal_lexicon import (
    CASE_TYPES,
    case_type_label,
    is_criminal,
    resolve_case_type,
    source_types_for,
)
from app.services.classification import case_classifier, detect_criminal_signals


class TestRouting:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("قام المتهم بسرقة المسروقات وأحالت النيابة العامة القضية للمحكمة الجزائية ونطلب توقيع العقوبة.", "جنائي"),
            ("تزوجت المدعية من المدعى عليه بعقد نكاح صحيح وامتنع عن أداء النفقة، وأطلب الحضانة والطلاق.", "أسري"),
            ("أطلب إلزام المدعى عليه بأداء الثمن بموجب سند لأمر تجاري وأتعاب السمسرة.", "تجاري"),
            ("تم فصلي تعسفياً من العمل وأطلب مكافأة نهاية الخدمة والأجور المتأخرة.", "عمالي"),
            ("أطلب إلغاء القرار الإداري الصادر عن الجهة الإدارية بتعيين غيري في الوظيفة العامة.", "إداري"),
            ("أطلب تسجيل الملكية وإزالة التعدي على الأرض وبيان حدود العقار.", "عقاري"),
            ("أطلب تنفيذ السند التنفيذي وإجراء الحجز على أموال المدين.", "تنفيذ"),
            ("أطلب إلزام المدعى عليه بالتعويض عن الإخلال بعقد البيع وفسخ العقد.", "مدني"),
        ],
    )
    def test_routes_each_branch(self, text, expected):
        result = case_classifier.classify(text)
        assert result.case_type == expected, (
            f"«{text[:40]}…» routed to {result.case_type}, expected {expected}"
        )

    def test_criminal_signal_is_never_lost(self):
        """A civil-looking dispute that contains an offence stays criminal."""
        text = "أطلب إلزام المدعى عليه بالتعويض، وقد قام بالاعتداء علي بالضرب وإحداث عاهة."
        result = case_classifier.classify(text)
        assert result.criminal_flag is True

    def test_criminal_signals_detector_returns_terms(self):
        signals = detect_criminal_signals("أحالت النيابة العامة المتهم بجريمة القتل.")
        assert signals, "an explicit criminal filing must yield signals"
        assert all(isinstance(s, str) for s in signals)


class TestNoSubstringFalsePositives:
    """Boundary-aware matching: a term inside a longer word is not a match."""

    @pytest.mark.parametrize(
        "text",
        [
            "هذا الأمر مناسباً تماماً للطرفين.",
            "المبلغ المتبقي مناسباً لما اتفقنا عليه.",
        ],
    )
    def test_سب_inside_مناسبا_is_not_a_criminal_signal(self, text):
        assert detect_criminal_signals(text) == []

    def test_plain_contract_dispute_is_civil(self):
        result = case_classifier.classify(
            "أطلب إلزام المدعى عليه بأداء الدين المتبقي بموجب سند الدين الموقّع منه."
        )
        assert result.case_type == "مدني"
        assert result.criminal_flag is False


class TestDeclaredType:
    def test_declared_type_is_a_presumption_not_a_verdict(self):
        result = case_classifier.classify(
            "أطلب إلزام المدعى عليه بأداء الثمن بموجب عقد بيع.",
            declared_case_type="تجاري",
        )
        # The declared type must be recorded as a note, whatever the outcome.
        assert any("قرينة" in n for n in result.notes)

    def test_unknown_declared_type_is_ignored(self):
        result = case_classifier.classify(
            "أطلب إلزام المدعى عليه بالتعويض.", declared_case_type="نوع غير موجود"
        )
        assert result.case_type in {p.key for p in CASE_TYPES}


class TestLexiconIntegrity:
    def test_every_case_type_has_codes_and_label(self):
        for profile in CASE_TYPES:
            assert profile.label_ar, profile.key
            assert profile.source_types, f"{profile.key} has no governing codes"
            assert profile.default_court

    def test_criminal_codes(self):
        assert is_criminal("جنائي") is True
        assert is_criminal("مدني") is False
        assert "criminal_law" in (source_types_for("جنائي") or [])

    def test_resolve_accepts_labels_and_keys(self):
        assert resolve_case_type("مدني") == "مدني"
        assert resolve_case_type("غير معروف") is None

    def test_label_lookup(self):
        assert case_type_label("جنائي")
        assert case_type_label(None) is None or isinstance(case_type_label(None), str)
