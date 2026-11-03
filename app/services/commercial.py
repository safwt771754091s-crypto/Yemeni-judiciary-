"""Commercial litigation & financial extraction engine.

Given a commercial case file, this service recovers the parts a court needs to
rule on money:

* the disputed financial lines (claimed / acknowledged / awarded),
* contractual terms, payment deadlines and penalty clauses
  (الشروط الجزائية والغرامة التأخيرية),
* the commercial instruments in evidence (فواتير، سندات لأمر، كمبيالات،
  حوالات بنكية، عقود مكتوبة).

The layer is deterministic: every figure is traceable to a span of the source
text. The LLM may later enrich the wording of the reasoning, but it never
invents an amount - the extraction here is the authority the draft is checked
against.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.core.money import DEFAULT_CURRENCY, extract_amounts, format_money
from app.core.text_utils import normalize_arabic
from app.schemas.commercial import (
    CommercialClause,
    CommercialEvidenceKind,
    CommercialExtraction,
    CommercialTerms,
    FinancialLine,
    FinancialSummary,
    PaymentMilestone,
    PenaltyClause,
)

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
#: A penalty clause, however the parties word it.
_PENALTY_MARKERS: tuple[str, ...] = (
    "شرط جزائي", "الشرط الجزائي", "شرط جزائى", "الشرط الجزائى",
    "غرامة تأخيرية", "غرامه تاخيريه", "غرامة تأخير", "غرامه تاخير",
    "غرامة التأخير", "غرامه التاخير", "تعويض اتفاقي", "التعويض الاتفاقي",
    "التعويض المتفق عليه", "تعويض مقطوع", "بدل إخلال", "بدل اخلال",
)
#: Percentage + period for a running penalty ("1% يومياً", "5% شهرياً").
_RATE_RE = re.compile(
    r"([0-9\u0660-\u0669]+(?:[.,][0-9\u0660-\u0669]+)?)\s*(?:%|بالمئة|بالمائة|في المئة|في المائة)"
    r"(?:\s*(يومي|يوميا|يوميًا|شهري|شهريا|شهريًا|سنوي|سنويا|سنويًا|لليوم|للشهر|للسنة))?"
)
#: A running penalty that names a period without a percentage ("غرامة تأخيرية يومية").
_PERIOD_WORDS: dict[str, str] = {
    "يومي": "يومي", "يوميا": "يومي", "يوميًا": "يومي", "لليوم": "يومي",
    "شهري": "شهري", "شهريا": "شهري", "شهريًا": "شهري", "للشهر": "شهري",
    "سنوي": "سنوي", "سنويا": "سنوي", "سنويًا": "سنوي", "للسنة": "سنوي",
}
#: Payment-term markers.
_PAYMENT_MARKERS: tuple[str, ...] = (
    "السداد", "سداد", "الدفع", "دفع", "يُسدَّد", "يسدد", "تُسدَّد", "تسدد",
    "أجل", "اجل", "تاريخ الاستحقاق", "تاريخ استحقاق", "يحل الأجل", "يحل اجل",
    "أقساط", "اقساط", "القسط", "قسط", "الاستحقاق", "استحقاق",
)
#: Delivery markers.
_DELIVERY_MARKERS: tuple[str, ...] = (
    "التسليم", "تسليم", "الشحن", "شحن", "التوريد", "توريد", "الاستلام", "استلام",
)
#: Interest / usury markers (relevant because Yemeni law caps/forbids riba).
_INTEREST_MARKERS: tuple[str, ...] = (
    "فائدة", "فائده", "عائد", "ربا", "نسبة ربح", "نسبه ربح", "فائدة تأخيرية",
)
#: A duration ("خلال ثلاثين يوماً", "مدة 60 يوما", "لمدة سنة").
_DURATION_RE = re.compile(
    r"(?:خلال|مدة|لمدة|مده|أجل|اجل|بمدة|بمده)\s+"
    r"(?:\(?\s*)?([0-9\u0660-\u0669]+|[\u0621-\u064A]{3,12})\s*"
    r"(يوم|يوما|يوماً|أيام|ايام|شهر|شهرا|شهراً|أشهر|اشهر|سنة|سنه|سنوات|أعوام|اعوام)"
)
_WRITTEN_DAYS: dict[str, int] = {
    "ثلاثين": 30, "ثلاثون": 30, "اربعين": 40, "خمسه عشر": 15, "خمسة عشر": 15,
    "ستين": 60, "تسعين": 90, "عشرين": 20, "عشره": 10, "عشرة": 10,
    "واحد": 1, "اثنين": 2, "ثلاثه": 3, "خمسه": 5, "سبعه": 7, "سبعه عشر": 17,
}
#: An explicit due date ("بتاريخ 2026/03/01", "حتى تاريخ ...").
_DUE_DATE_RE = re.compile(
    r"(?:بتاريخ|حتى|تاريخ|في)\s*[:：]?\s*"
    r"([0-9\u0660-\u0669]{1,4}\s*[/\-]\s*[0-9\u0660-\u0669]{1,2}\s*[/\-]\s*[0-9\u0660-\u0669]{2,4})"
)
#: Instalment markers.
_INSTALMENT_MARKERS: tuple[str, ...] = ("قسط", "اقساط", "أقساط", "على دفعات", "دفعات")

#: Evidence type -> commercial kind.
_TYPE_TO_KIND: dict[str, CommercialEvidenceKind] = {
    "contract": CommercialEvidenceKind.written_contract,
    "invoice": CommercialEvidenceKind.invoice,
    "promissory_note": CommercialEvidenceKind.promissory_note,
    "commercial_bill": CommercialEvidenceKind.commercial_bill,
    "cheque": CommercialEvidenceKind.cheque,
    "bank_transfer": CommercialEvidenceKind.bank_transfer,
    "delivery_note": CommercialEvidenceKind.delivery_note,
    "commercial_ledger": CommercialEvidenceKind.commercial_ledger,
    "letter_of_credit": CommercialEvidenceKind.letter_of_credit,
    "correspondence": CommercialEvidenceKind.correspondence,
}
#: Title keywords that identify a commercial instrument when the type is generic.
_TITLE_TO_KIND: tuple[tuple[tuple[str, ...], CommercialEvidenceKind], ...] = (
    (("كمبيالة", "كمبياله"), CommercialEvidenceKind.commercial_bill),
    (("سند لأمر", "سند لامر", "سند لامر", "سند لأمر"), CommercialEvidenceKind.promissory_note),
    (("شيك", "صك"), CommercialEvidenceKind.cheque),
    (("فاتورة", "فاتوره"), CommercialEvidenceKind.invoice),
    (("حوالة", "حواله", "تحويل بنكي", "إشعار تحويل", "اشعار تحويل"), CommercialEvidenceKind.bank_transfer),
    (("اعتماد مستندي", "اعتماد مستندى"), CommercialEvidenceKind.letter_of_credit),
    (("إذن تسليم", "اذن تسليم", "سند استلام", "مذكرة تسليم"), CommercialEvidenceKind.delivery_note),
    (("دفتر تجاري", "دفاتر تجارية"), CommercialEvidenceKind.commercial_ledger),
    (("عقد",), CommercialEvidenceKind.written_contract),
    (("مراسلات", "خطاب", "بريد"), CommercialEvidenceKind.correspondence),
)


#: A window must carry a penalty signal beyond the bare marker, or it is just a
#: mention (a denial, the case subject) rather than a penalty clause.
_PENALTY_SIGNALS: tuple[str, ...] = (
    "نسبه", "%", "بالمئه", "بالمائه", "يومي", "شهري", "سنوي", "يلتزم", "تلتزم",
    "يستحق", "تستحق", "مبلغ", "قدره", "يحق", "سقف", "لا يتجاوز", "يتعين", "يتعين",
)


def _marker_pattern(marker: str) -> re.Pattern[str]:
    """Compile a marker into a pattern that matches in the ORIGINAL text.

    ``normalize_arabic`` rewrites letters and drops punctuation, so a position
    found in the normalised string does not line up with the original text. The
    marker is instead compiled into character classes that tolerate the same
    variations in place, which keeps every match index valid for slicing.
    """
    classes = {
        "ا": "[اأإآٱ]", "ه": "[هة]", "ي": "[يىئ]", "و": "[وؤ]",
        "ء": "[ء]",
    }
    parts: list[str] = []
    for char in normalize_arabic(marker):
        if char == " ":
            parts.append(r"\s+")
        elif char in classes:
            parts.append(classes[char])
        else:
            parts.append(re.escape(char))
    # Allow tashkeel / tatweel between letters.
    body = r"[\u064B-\u0652\u0640]*".join(parts)
    return re.compile(body)


_PENALTY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _marker_pattern(m) for m in _PENALTY_MARKERS
)


class CommercialEngine:
    """Deterministic commercial case analysis."""

    # -- entry point -------------------------------------------------------
    def extract(
        self, *, case: dict[str, Any], evidence: list[dict[str, Any]] | None = None
    ) -> CommercialExtraction:
        evidence = evidence or []
        warnings: list[str] = []
        is_commercial = self._is_commercial(case, evidence)

        texts = self._case_texts(case, evidence)
        combined = "\n".join(texts)

        summary = self._financial_summary(case, combined, warnings)
        terms = self._terms(case, combined)
        summary.evidence_kinds = self._evidence_kinds(evidence)

        if is_commercial:
            if not summary.lines:
                warnings.append(
                    "دعوى تجارية دون بنود مالية صريحة؛ لم يُعثر على مبالغ في الطلبات أو المستندات."
                )
            if not terms.penalties and self._mentions(combined, _PENALTY_MARKERS):
                warnings.append(
                    "أُشير إلى شرط جزائي في النص دون تحديد نسبته أو مقداره؛ يلزم بيان الشرط."
                )
        return CommercialExtraction(
            is_commercial=is_commercial,
            financial_summary=summary,
            terms=terms,
            warnings=warnings,
        )

    # -- classification ----------------------------------------------------
    @staticmethod
    def _is_commercial(case: dict[str, Any], evidence: list[dict[str, Any]]) -> bool:
        case_type = str(case.get("case_type") or "").strip()
        if case_type in {"تجاري", "commercial"}:
            return True
        blob = normalize_arabic(
            " ".join(
                [
                    str(case.get("subject_matter") or ""),
                    str(case.get("facts_summary") or ""),
                ]
                + [str(e.get("title") or "") for e in evidence]
            )
        )
        indicators = (
            "سند لامر", "كمبياله", "شيك", "ورقه تجاريه", "تاجر", "الاعمال التجاريه",
            "شركه", "افلاس", "اعتماد مستندي", "فاتوره", "بضاعه", "استيراد", "تصدير",
            "وساطه تجاريه", "اسم تجاري", "علامه تجاريه", "مصرف", "بنك",
        )
        return any(word in blob for word in indicators)

    @staticmethod
    def _mentions(text: str, markers: tuple[str, ...]) -> bool:
        normalised = normalize_arabic(text)
        return any(normalize_arabic(m) in normalised for m in markers)

    # -- texts -------------------------------------------------------------
    @staticmethod
    def _case_texts(case: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
        texts: list[str] = [
            str(case.get("subject_matter") or ""),
            str(case.get("facts_summary") or ""),
        ]
        for claim in case.get("plaintiff_claims") or []:
            texts.append(f"{claim.get('title') or ''} {claim.get('description') or ''}")
        for response in case.get("defendant_responses") or []:
            texts.append(str(response.get("description") or ""))
        for item in evidence:
            texts.append(f"{item.get('title') or ''} {item.get('description') or ''}")
            if item.get("extracted_text"):
                texts.append(str(item["extracted_text"]))
        return [t for t in texts if t]

    # -- financial summary -------------------------------------------------
    def _financial_summary(
        self, case: dict[str, Any], combined: str, warnings: list[str]
    ) -> FinancialSummary:
        currency = str(case.get("currency") or DEFAULT_CURRENCY).upper()
        lines: list[FinancialLine] = []

        for claim in case.get("plaintiff_claims") or []:
            amount = claim.get("amount")
            line_currency = str(claim.get("currency") or currency).upper()
            if amount is None:
                # No structured amount: try to recover one from the claim text.
                found = extract_amounts(
                    f"{claim.get('title') or ''} {claim.get('description') or ''}"
                )
                if found.primary:
                    amount = found.primary.amount
                    line_currency = found.primary.currency
            if amount is None:
                continue
            lines.append(
                FinancialLine(
                    claim_ref=claim.get("title"),
                    subject=str(claim.get("title") or "بند مالي"),
                    claimed_amount=float(amount),
                    currency=line_currency,
                    basis="؛ ".join(claim.get("legal_basis") or []) or None,
                )
            )

        # Acknowledged amounts come from the defendant's admissions.
        acknowledged_by_ref: dict[str, float] = {}
        for response in case.get("defendant_responses") or []:
            if str(response.get("response_type") or "") != "admission":
                continue
            amount = response.get("amount")
            if amount is None:
                found = extract_amounts(str(response.get("description") or ""))
                amount = found.primary.amount if found.primary else None
            if amount is not None:
                acknowledged_by_ref[str(response.get("claim_ref") or "")] = float(amount)

        for line in lines:
            ref = str(line.claim_ref or "")
            if ref in acknowledged_by_ref:
                line.acknowledged_amount = acknowledged_by_ref[ref]

        # A penalty claim pleaded by rate has no lump sum; represent it as a line
        # so the financial summary shows the claim even before it is quantified.
        claim_subjects = {str(line.subject or "") for line in lines}
        for claim in case.get("plaintiff_claims") or []:
            if claim.get("amount") is not None:
                continue
            title = str(claim.get("title") or "")
            if title in claim_subjects:
                continue
            if any(k in normalize_arabic(title) for k in ("غرامه", "شرط جزايي", "تعويض", "فايده", "عايد")):
                lines.append(
                    FinancialLine(
                        claim_ref=title,
                        subject=title or "بند مالي",
                        claimed_amount=None,
                        currency=str(claim.get("currency") or currency).upper(),
                        basis="؛ ".join(claim.get("legal_basis") or []) or None,
                    )
                )

        if not lines:
            # Fall back to a claim value declared on the case itself.
            value = case.get("claim_value")
            if value:
                lines.append(
                    FinancialLine(
                        subject=str(case.get("subject_matter") or "قيمة الدعوى"),
                        claimed_amount=float(value),
                        currency=currency,
                        basis=None,
                    )
                )

        # Reconcile the numeric vs written reading of the principal.
        extraction = extract_amounts(combined)
        reconciled = extraction.reconciled
        note = extraction.note or None
        if reconciled is False:
            warnings.append(note)

        summary = FinancialSummary(
            currency=currency,
            lines=lines,
            total_claimed=round(sum(line.claimed_amount or 0.0 for line in lines), 2),
            total_acknowledged=round(sum(line.acknowledged_amount or 0.0 for line in lines), 2),
            total_awarded=0.0,
            reconciled=reconciled,
            reconciliation_note=note,
        )
        summary.narrative = self._summary_narrative(summary)
        return summary

    @staticmethod
    def _summary_narrative(summary: FinancialSummary) -> str:
        if not summary.lines:
            return "لا توجد بنود مالية محددة في الملف."
        parts = [
            f"إجمالي المطالب به {format_money(summary.total_claimed, summary.currency)}"
        ]
        if summary.total_acknowledged:
            parts.append(
                f"والمعترف به {format_money(summary.total_acknowledged, summary.currency)}"
            )
        if summary.total_awarded:
            parts.append(
                f"والمقضي به {format_money(summary.total_awarded, summary.currency)}"
            )
        return "، ".join(parts) + "."

    # -- terms -------------------------------------------------------------
    def _terms(self, case: dict[str, Any], combined: str) -> CommercialTerms:
        terms = CommercialTerms()
        currency = str(case.get("currency") or DEFAULT_CURRENCY).upper()

        terms.penalties = self._penalties(combined, currency)
        terms.milestones = self._milestones(combined, currency)
        terms.payment_deadline_days = self._payment_deadline_days(combined)
        terms.interest_rate_percent = self._interest_rate(combined)

        if terms.payment_deadline_days is not None:
            terms.clauses.append(
                CommercialClause(
                    clause_type="payment_term",
                    text=f"أجل السداد المتفق عليه: {terms.payment_deadline_days} يوماً.",
                )
            )
        if terms.interest_rate_percent is not None:
            terms.clauses.append(
                CommercialClause(
                    clause_type="interest",
                    text=f"نسبة الفائدة/العائد المذكورة: {terms.interest_rate_percent:g}%.",
                )
            )
        if self._mentions(combined, _DELIVERY_MARKERS):
            terms.clauses.append(
                CommercialClause(clause_type="delivery", text="يتضمن الملف شرطاً للتسليم/التوريد.")
            )
        if terms.penalties:
            terms.clauses.append(
                CommercialClause(
                    clause_type="penalty",
                    text="؛ ".join(p.description for p in terms.penalties),
                )
            )
        return terms

    def _penalties(self, text: str, currency: str) -> list[PenaltyClause]:
        penalties: list[PenaltyClause] = []
        seen: set[tuple] = set()
        for pattern in _PENALTY_PATTERNS:
            for match in pattern.finditer(text):
                window = self._sentence_around(text, match.start())
                # A bare mention (a denial, the case subject) is not a clause.
                norm_window = normalize_arabic(window)
                if not any(signal in norm_window for signal in _PENALTY_SIGNALS):
                    continue
                rate = _RATE_RE.search(window)
                rate_percent = float(rate.group(1).replace(",", ".")) if rate else None
                period = None
                if rate and rate.group(2):
                    period = _PERIOD_WORDS.get(normalize_arabic(rate.group(2)).strip())
                if period is None:
                    for word, label in _PERIOD_WORDS.items():
                        if word in normalize_arabic(window):
                            period = label
                            break
                capped = None
                if any(w in normalize_arabic(window) for w in ("سقف", "حد اقصي", "حد اقصى", "لا يتجاوز")):
                    capped = True
                # A running penalty is defined by its rate; only a fixed penalty
                # carries a lump amount. The window is the clause's own sentence,
                # so the principal of a neighbouring claim is never captured.
                amount = extract_amounts(window).primary if period is None else None
                description = self._clean(window)
                key = (period, rate_percent, round(amount.amount, 2) if amount else None, description)
                if key in seen:
                    continue
                seen.add(key)
                penalties.append(
                    PenaltyClause(
                        clause_type="delay_fine" if period else "penalty",
                        description=description,
                        rate_percent=rate_percent,
                        rate_period=period,
                        amount=amount.amount if amount else None,
                        currency=amount.currency if amount else currency,
                        capped=capped,
                        span=description[:200],
                    )
                )
                if len(penalties) >= 6:
                    return penalties
        return penalties

    def _milestones(self, text: str, currency: str) -> list[PaymentMilestone]:
        milestones: list[PaymentMilestone] = []
        for match in _DURATION_RE.finditer(text):
            raw_value, unit = match.group(1), match.group(2)
            days = self._duration_days(raw_value, unit)
            if days is None:
                continue
            window = self._sentence_around(text, match.start())
            amount = extract_amounts(window).primary
            milestones.append(
                PaymentMilestone(
                    description=self._clean(window),
                    amount=amount.amount if amount else None,
                    currency=amount.currency if amount else currency,
                    due_days=days,
                )
            )
            if len(milestones) >= 6:
                break
        # Instalments without a duration still describe a schedule.
        if self._mentions(text, _INSTALMENT_MARKERS) and not milestones:
            milestones.append(
                PaymentMilestone(description="سداد على أقساط/دفعات حسب ما ورد في الملف.")
            )
        return milestones

    @staticmethod
    def _sentence_around(text: str, index: int, *, before: int = 60, after: int = 220) -> str:
        """Return the sentence containing `index`.

        Descriptions must not bleed into the next claim, so the window stops at
        the nearest sentence boundary (a newline or a sentence-ending mark)
        rather than at a fixed character count.
        """
        start = index
        limit = max(0, index - before)
        while start > limit and text[start - 1] not in "\n.؛!؟":
            start -= 1
        end = index
        stop = min(len(text), index + after)
        while end < stop and text[end] not in "\n.؛!؟":
            end += 1
        return text[start : min(len(text), end + 1)].strip()

    @staticmethod
    def _duration_days(raw_value: str, unit: str) -> int | None:
        unit_norm = normalize_arabic(unit)
        value = None
        digits = re.sub(r"\D", "", raw_value)
        if digits:
            value = int(digits)
        else:
            value = _WRITTEN_DAYS.get(normalize_arabic(raw_value).strip())
        if value is None:
            return None
        if unit_norm in {"يوم", "يوما", "ايام", "أيام"}:
            return value
        if unit_norm in {"شهر", "شهرا", "اشهر", "أشهر"}:
            return value * 30
        if unit_norm in {"سنه", "سنة", "سنوات", "اعوام", "أعوام"}:
            return value * 365
        return None

    @staticmethod
    def _payment_deadline_days(text: str) -> int | None:
        for match in _DURATION_RE.finditer(text):
            window = normalize_arabic(text[max(0, match.start() - 30) : match.end() + 20])
            if any(normalize_arabic(m) in window for m in _PAYMENT_MARKERS):
                days = CommercialEngine._duration_days(match.group(1), match.group(2))
                if days:
                    return days
        return None

    @staticmethod
    def _interest_rate(text: str) -> float | None:
        normalised = normalize_arabic(text)
        for marker in _INTEREST_MARKERS:
            index = normalised.find(normalize_arabic(marker))
            if index == -1:
                continue
            window = text[max(0, index - 10) : index + 80]
            rate = _RATE_RE.search(window)
            if rate:
                return float(rate.group(1).replace(",", "."))
        return None

    # -- evidence ----------------------------------------------------------
    @staticmethod
    def _evidence_kinds(evidence: list[dict[str, Any]]) -> list[CommercialEvidenceKind]:
        kinds: list[CommercialEvidenceKind] = []
        for item in evidence:
            kind = _TYPE_TO_KIND.get(str(item.get("evidence_type") or ""))
            if kind is None:
                title = normalize_arabic(str(item.get("title") or ""))
                for keywords, candidate in _TITLE_TO_KIND:
                    if any(normalize_arabic(k) in title for k in keywords):
                        kind = candidate
                        break
            if kind is not None and kind not in kinds:
                kinds.append(kind)
        return kinds

    # -- award application -------------------------------------------------
    @staticmethod
    def apply_awards(
        summary: FinancialSummary, verdict_items: list[dict[str, Any]]
    ) -> FinancialSummary:
        """Fill the awarded column from the verdict, then recompute the totals.

        Only verdict items that carry an amount are considered; a bare ruling
        with no figure leaves the line unawarded, which is the honest reading.
        """
        awarded: list[float] = []
        for item in verdict_items or []:
            amount = item.get("amount")
            if amount is None:
                continue
            subject = normalize_arabic(str(item.get("subject") or ""))
            ref = normalize_arabic(str(item.get("claim_ref") or ""))
            target = None
            for line in summary.lines:
                line_ref = normalize_arabic(str(line.claim_ref or ""))
                line_subject = normalize_arabic(str(line.subject or ""))
                if (ref and ref == line_ref) or (subject and subject == line_subject):
                    target = line
                    break
            if target is None and len(summary.lines) == 1:
                target = summary.lines[0]
            if target is not None:
                target.awarded_amount = float(amount)
                if item.get("currency"):
                    target.currency = str(item["currency"]).upper()
            awarded.append(float(amount))
        summary.total_awarded = round(sum(awarded), 2)
        summary.narrative = CommercialEngine._summary_narrative(summary)
        return summary

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip(" .:،,\n\t")[:300]


commercial_engine = CommercialEngine()
