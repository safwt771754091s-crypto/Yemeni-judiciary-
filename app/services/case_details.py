"""Deterministic extraction of case components (تفصيل الدعوى) from raw text.

A judge does not want to retype a filing into a form. Given the text of a
lawsuit - typed, OCR-ed, or pasted - this module recovers the structural parts:

* parties (المدعي، المدعى عليه) and their representatives
* court, circuit, case number, hearing date
* the subject matter (موضوع الدعوى) and the claim value (قيمة الدعوى)
* the claims (الطلبات) and the defences (الدفوع)
* the narrative of facts (الوقائع)
* the statutory articles each side relied on (الأسانيد القانونية)

Everything here is deterministic and traceable to a span of the source text.
The LLM is used afterwards only to *enrich* what these patterns found - to write
a clean claim description or split a run-on paragraph - and its output is always
cross-checked against this baseline. A pattern miss degrades to "not found",
which the reviewer sees; a hallucination would silently invent a party, which is
why the deterministic layer is the spine and the model is the flesh.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.legal_lexicon import (
    CASE_NUMBER_PATTERNS,
    CLAIM_MARKERS,
    COURT_PATTERNS,
    DEFENCE_MARKERS,
    DEFENDANT_PATTERNS,
    FACTS_MARKERS,
    LEGAL_BASIS_PATTERNS,
    PLAINTIFF_PATTERNS,
    SUBJECT_PATTERNS,
)
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic, to_western_digits

logger = get_logger(__name__)

#: Amounts in Yemeni filings, with the currency that usually follows.
_AMOUNT_RE = re.compile(r"([0-9\u0660-\u0669][0-9\u0660-\u0669,\.]{2,})")
_CURRENCY_WORDS: dict[str, str] = {
    "ريال": "YER",
    "دولار": "USD",
    "سعودي": "SAR",
    "درهم": "AED",
}
#: Words that show a figure is money rather than a date, year or case number.
_MONEY_MARKERS: tuple[str, ...] = (
    "ريال", "دولار", "درهم", "مبلغ", "قيمة", "ثمن", "دين", "أجر", "اجر", "نفقة",
    "تعويض", "مكافأة", "مكافاه", "أتعاب", "اتعاب", "مستحقات", "بدل", "إيجار", "ايجار",
    "قرض", "مهر", "صداق", "دية", "غرامة", "خسارة", "أرباح", "ارباح", "رأس المال",
)
#: Words that show a figure is a date, year or reference number, not money.
_REFERENCE_MARKERS: tuple[str, ...] = (
    "رقم", "بتاريخ", "سنة", "لعام", "لعام", "هجرية", "ميلادية", "المادة", "المواد",
    "صفحة", "هاتف", "بطاقة", "سجل", "هوية",
)
#: Multipliers used in Arabic legal prose ("خمسة ملايين ريال").
_MULTIPLIERS: dict[str, int] = {
    "ألف": 1_000, "الف": 1_000, "آلاف": 1_000, "الاف": 1_000,
    "مليون": 1_000_000, "ملايين": 1_000_000, "مليار": 1_000_000_000,
}
_ARABIC_NUMBER_WORDS: dict[str, int] = {
    "خمس": 5, "خمسه": 5, "أربع": 4, "اربع": 4, "أربعه": 4,
    "ثلاث": 3, "ثلاثه": 3, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
    "عشرين": 20, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60,
    "مائه": 100, "مئه": 100, "مائتين": 200, "مئتين": 200,
}
#: Numeric dates are matched first and separately: a written date alternative
#: ("بتاريخ ...") is prone to swallowing the following sentence, because Arabic
#: words are separated by spaces and any word may end in a date-like letter.
_NUMERIC_DATE_RE = re.compile(
    r"([0-9\u0660-\u0669]{1,4}\s*/\s*[0-9\u0660-\u0669]{1,2}\s*/\s*[0-9\u0660-\u0669]{2,4})"
)
_WRITTEN_DATE_RE = re.compile(
    r"(?:يوم|بتاريخ)\s+([0-9\u0660-\u0669]{1,2}\s+[\u0621-\u064A]{3,15}\s+"
    r"[0-9\u0660-\u0669]{2,4}\s*(?:هـ|هجرية|ميلادية|م))"
)
_REPRESENTATIVE_RE = re.compile(
    r"(?:وكيل|محامي|محامي|وكالة|المحامي)\s*(?:ال)?(?:مدعي|مدعى عليه|المدعي|المدعى عليه)?\s*[:：]?\s*([^\n،,]{3,80})"
)
_WITNESS_RE = re.compile(r"(?:شاهد|الشهود|شهادة)\s*[:：]?\s*([^\n]{3,120})")

#: Phrases that open a statutory-basis line ("وفقاً للمادة 146 ..."). They close
#: the الطلبات block: a citation is an authority, not a demand.
CITATION_MARKERS: tuple[str, ...] = (
    "وفقاً", "وفقا", "استناداً", "استنادا", "عملاً", "عملا", "بموجب المادة",
    "سنداً", "سندا", "طبقاً", "طبقا",
)


@dataclass(slots=True)
class ExtractedDetails:
    """Structured case components recovered from raw filing text."""

    court_name: str | None = None
    circuit: str | None = None
    case_number: str | None = None
    hearing_date: str | None = None
    judge_name: str | None = None
    subject_matter: str | None = None
    claim_value: float | None = None
    currency: str = "YER"
    facts_summary: str | None = None
    plaintiff_name: str | None = None
    plaintiff_representative: str | None = None
    defendant_name: str | None = None
    defendant_representative: str | None = None
    claims: list[str] = field(default_factory=list)
    defences: list[str] = field(default_factory=list)
    legal_basis: list[str] = field(default_factory=list)
    witnesses: list[str] = field(default_factory=list)
    confidence: float = 0.0
    fields_found: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {
            "court_name": self.court_name,
            "circuit": self.circuit,
            "case_number": self.case_number,
            "hearing_date": self.hearing_date,
            "judge_name": self.judge_name,
            "subject_matter": self.subject_matter,
            "claim_value": self.claim_value,
            "currency": self.currency,
            "facts_summary": self.facts_summary,
            "plaintiff_name": self.plaintiff_name,
            "plaintiff_representative": self.plaintiff_representative,
            "defendant_name": self.defendant_name,
            "defendant_representative": self.defendant_representative,
            "claims": list(self.claims),
            "defences": list(self.defences),
            "legal_basis": list(self.legal_basis),
            "witnesses": list(self.witnesses),
            "confidence": round(self.confidence, 3),
            "fields_found": list(self.fields_found),
            "notes": list(self.notes),
        }


class CaseDetailsExtractor:
    """Pattern-based extraction of the parts of a Yemeni case file."""

    def extract(self, text: str) -> ExtractedDetails:
        details = ExtractedDetails()
        if not text or not text.strip():
            details.notes.append("لا يوجد نص لتحليله.")
            return details

        working = text.replace("\u00a0", " ").replace("\r\n", "\n")
        western = to_western_digits(working)

        details.court_name = self._first(COURT_PATTERNS, western, clean=True)
        details.case_number = self._first(CASE_NUMBER_PATTERNS, western, clean=True)
        details.hearing_date = self._first_date(working)
        details.plaintiff_name = self._first(PLAINTIFF_PATTERNS, working, clean=True)
        details.defendant_name = self._first(DEFENDANT_PATTERNS, working, clean=True)
        details.subject_matter = self._first(SUBJECT_PATTERNS, working, clean=True)
        details.judge_name = self._first(
            (r"(?:القاضي|رئيس المحكمة|القاضى)\s*[:：]?\s*([^\n،,]{3,60})",), working, clean=True
        )
        details.circuit = self._first(
            (r"(?:الدائرة|دائرة)\s*[:：]?\s*([^\n،,]{2,60})",), working, clean=True
        )
        details.plaintiff_representative = self._representative(working, "مدعي")
        details.defendant_representative = self._representative(working, "مدعى عليه")

        amount, currency = self._amount(western)
        details.claim_value = amount
        if currency:
            details.currency = currency

        details.claims = self._section_items(working, CLAIM_MARKERS)
        details.defences = self._section_items(working, DEFENCE_MARKERS)
        details.facts_summary = self._facts(working)
        details.legal_basis = self._legal_basis(working)
        details.witnesses = self._witnesses(working)
        if not details.subject_matter:
            details.subject_matter = self._derive_subject(details)
            if details.subject_matter:
                details.notes.append(
                    "لم يُذكر «موضوع الدعوى» صريحاً؛ تم استنتاجه من أول طلب في الدعوى."
                )

        self._finalize(details)
        return details

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _first(
        patterns: tuple[str, ...] | list[str],
        text: str,
        *,
        clean: bool = False,
    ) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            value = next((g for g in match.groups() if g), None)
            if value:
                value = value.strip(" .:،,\u00a0")
                if clean:
                    value = re.sub(r"\s+", " ", value)
                if 1 < len(value) <= 200:
                    return value
        return None

    @staticmethod
    def _first_date(text: str) -> str | None:
        match = _NUMERIC_DATE_RE.search(text)
        if match:
            return to_western_digits(match.group(1).strip())
        written = _WRITTEN_DATE_RE.search(text)
        if written:
            return to_western_digits(re.sub(r"\s+", " ", written.group(1).strip()))
        return None

    @staticmethod
    def _representative(text: str, party: str) -> str | None:
        for match in _REPRESENTATIVE_RE.finditer(text):
            window = text[max(0, match.start() - 40) : match.end()]
            if party in window:
                value = match.group(1).strip(" .:،,")
                if 1 < len(value) <= 80:
                    return value
        return None

    @staticmethod
    def _amount(text: str) -> tuple[float | None, str | None]:
        """Find the claim value, preferring money the text actually labels.

        A filing is full of numbers that are not money - the case number, the
        year, the article numbers. Taking the largest figure would return the
        year "2026" as the claim value. Each candidate is therefore scored by
        the words around it: money words ("مبلغ", "قيمة", "ريال") raise it,
        reference words ("رقم", "المادة", "بتاريخ") disqualify it. Written-out
        amounts ("خمسة ملايين ريال") are handled separately and take precedence,
        since they are unambiguous.
        """
        currency: str | None = None
        for word, code in _CURRENCY_WORDS.items():
            if word in text:
                currency = code
                break

        written = CaseDetailsExtractor._written_amount(text)
        if written is not None:
            return written, currency

        best: float | None = None
        best_score = 0
        for match in _AMOUNT_RE.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            if value < 1000:
                continue
            # A figure followed by a multiplier word is scaled ("5 ملايين").
            tail = text[match.end() : match.end() + 14]
            for word, multiplier in _MULTIPLIERS.items():
                if word in tail:
                    value *= multiplier
                    break
            # Score the surrounding context. The two windows are deliberately
            # asymmetric: a reference word disqualifies a figure only when it is
            # glued to it ("رقم 1451", "المادة 146", "بتاريخ 12"), whereas a
            # money word counts from further away ("المطالبة بمبلغ 750000 ريال").
            # A single shared window would let the case number's own "رقم" cancel
            # the money word of a later amount.
            before = text[max(0, match.start() - 24) : match.start()]
            after = text[match.end() : match.end() + 28]
            score = 0
            if any(marker in before or marker in after for marker in _MONEY_MARKERS):
                score += 2
            if any(marker in before[-14:] for marker in _REFERENCE_MARKERS):
                score -= 3
            # A bare four-digit year is almost never a claim value.
            if 1900 <= value <= 2100 and len(raw) == 4 and score <= 0:
                continue
            if score > best_score or (score == best_score and best is not None and value > best):
                best, best_score = value, score
        return (best if best_score > 0 else None), currency

    @staticmethod
    def _written_amount(text: str) -> float | None:
        """Parse a written-out amount such as "خمسة ملايين ريال"."""
        best: float | None = None
        for word, multiplier in _MULTIPLIERS.items():
            pattern = re.compile(r"(?:^|[\s،,])([\u0621-\u064A]{3,12})\s+" + re.escape(word) + r"(?![\u0621-\u064A])")
            for match in pattern.finditer(text):
                base = _ARABIC_NUMBER_WORDS.get(normalize_arabic(match.group(1)).strip())
                if base:
                    value = float(base * multiplier)
                    best = value if best is None else max(best, value)
        return best

    @staticmethod
    def _section_items(text: str, markers: tuple[str, ...]) -> list[str]:
        """Collect the bullet/enumerated items following a section marker.

        Collection stops at the next section heading: in a Yemeni filing the
        الطلبات block is immediately followed by الدفوع or الأسانيد, and without
        this boundary the demands and the defences would merge into one list.
        """
        items: list[str] = []
        stop_markers = tuple(
            m
            for m in (*CLAIM_MARKERS, *DEFENCE_MARKERS, *FACTS_MARKERS, *CITATION_MARKERS)
            if m not in markers
        )
        for marker in markers:
            index = text.find(marker)
            if index == -1:
                continue
            tail = text[index + len(marker) : index + len(marker) + 3000]
            for line in tail.split("\n"):
                candidate = line.strip(" .-–—•\t")
                if any(candidate.startswith(sm) for sm in stop_markers):
                    break
                stripped = re.sub(r"^[0-9\u0660-\u0669]+\s*[\.\)\-–]\s*", "", candidate)
                stripped = re.sub(r"^[-–—•]\s*", "", stripped)
                if not stripped:
                    if items:
                        break
                    continue
                if 4 <= len(stripped) <= 400 and stripped not in items:
                    items.append(stripped)
                if len(items) >= 12:
                    break
            if items:
                break
        return items

    @staticmethod
    def _facts(text: str) -> str | None:
        """Take the paragraph after a facts marker, else the longest paragraph."""
        for marker in FACTS_MARKERS:
            index = text.find(marker)
            if index == -1:
                continue
            tail = text[index + len(marker) : index + len(marker) + 2500]
            body = tail.lstrip(" :：\n")
            paragraph = body.split("\n\n")[0].strip()
            if len(paragraph) >= 30:
                return re.sub(r"\s+", " ", paragraph)[:2000]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 60]
        if paragraphs:
            longest = max(paragraphs, key=len)
            return re.sub(r"\s+", " ", longest)[:2000]
        return None

    @staticmethod
    def _legal_basis(text: str) -> list[str]:
        found: list[str] = []
        for pattern in LEGAL_BASIS_PATTERNS:
            for match in re.finditer(pattern, text):
                for part in re.split(r"[،,\s]+(?:و)?", match.group(1)):
                    number = to_western_digits(part.strip())
                    if number.isdigit() and number not in found:
                        found.append(number)
        return found[:20]

    @staticmethod
    def _witnesses(text: str) -> list[str]:
        found: list[str] = []
        for match in _WITNESS_RE.finditer(text):
            value = match.group(1).strip(" .:،,")
            if 2 < len(value) <= 160 and value not in found:
                found.append(value)
        return found[:10]

    @staticmethod
    def _derive_subject(details: ExtractedDetails) -> str | None:
        """Derive a subject matter when the filing does not state one.

        The first demand in the الطلبات section names what the plaintiff wants
        ("إلزام المدعى عليه بأداء الثمن"), which is the subject matter for a
        Yemeni judgment preamble. Failing that, the first sentence of the facts
        is used. Both are truncated to a heading-sized phrase.
        """
        if details.claims:
            first = details.claims[0]
            for prefix in ("إلزام المدعى عليه بـ", "إلزام المدعى عليه ب", "الزام المدعى عليه ب"):
                if first.startswith(prefix):
                    first = first[len(prefix) :]
                    break
            return first.strip()[:160] or None
        if details.facts_summary:
            sentence = re.split(r"[.؛\n]", details.facts_summary, maxsplit=1)[0].strip()
            return sentence[:160] or None
        return None

    @staticmethod
    def _finalize(details: ExtractedDetails) -> None:
        """Record which fields were found and derive a confidence score."""
        checks = [
            ("court_name", details.court_name),
            ("case_number", details.case_number),
            ("subject_matter", details.subject_matter),
            ("plaintiff_name", details.plaintiff_name),
            ("defendant_name", details.defendant_name),
            ("facts_summary", details.facts_summary),
            ("claim_value", details.claim_value),
            ("claims", details.claims),
            ("defences", details.defences),
            ("legal_basis", details.legal_basis),
        ]
        found = [name for name, value in checks if value]
        details.fields_found = found
        details.confidence = round(len(found) / len(checks), 3)

        for name, value in checks:
            if not value and name in ("plaintiff_name", "defendant_name", "subject_matter"):
                details.notes.append(f"لم يتم التعرّف على «{name}» من النص؛ يلزم إدخاله يدوياً.")


case_details_extractor = CaseDetailsExtractor()
