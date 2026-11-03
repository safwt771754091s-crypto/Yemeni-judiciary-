"""Monetary amount parsing for Yemeni legal documents (استخلاص المبالغ المالية).

Yemeni filings state amounts in two ways that must agree:

* numerically  - «1,000,000 ريال يمني», «500000 دولار أمريكي»
* in words    - «مبلغ وقدره مليون ريال يمني», «خمسة ملايين وخمسمائة ألف ريال»

A judgment that awards one figure while the contract says another is a defect,
so both readings are parsed and cross-checked (``reconcile``). The parser is
deterministic and returns the source span for every value, which lets a
reviewer trace a figure back to the text instead of trusting it blindly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.text_utils import normalize_arabic, to_western_digits

# --------------------------------------------------------------------------- #
# Currencies
# --------------------------------------------------------------------------- #
#: Currency word (normalised) -> ISO code. Several surface forms map to the
#: same code because filings spell them inconsistently.
CURRENCY_WORDS: dict[str, str] = {
    "ريال": "YER",
    "ريال يمني": "YER",
    "يمني": "YER",
    "ريال يمنى": "YER",
    "دولار": "USD",
    "دولار امريكي": "USD",
    "امريكي": "USD",
    "دولار امريكى": "USD",
    "سعودي": "SAR",
    "ريال سعودي": "SAR",
    "درهم": "AED",
    "درهم اماراتي": "AED",
    "يورو": "EUR",
    "استرليني": "GBP",
    "جنيه": "EGP",
    "دينار": "KWD",
    "ريال قطري": "QAR",
    "ريال عماني": "OMR",
    "دينار كويتي": "KWD",
}

#: Display names, used when rendering the financial summary.
CURRENCY_LABELS_AR: dict[str, str] = {
    "YER": "ريال يمني",
    "USD": "دولار أمريكي",
    "SAR": "ريال سعودي",
    "AED": "درهم إماراتي",
    "EUR": "يورو",
    "GBP": "جنيه إسترليني",
    "EGP": "جنيه مصري",
    "KWD": "دينار كويتي",
    "QAR": "ريال قطري",
    "OMR": "ريال عماني",
}

DEFAULT_CURRENCY = "YER"

#: Longest phrases first so «ريال سعودي» is not read as plain «ريال».
_CURRENCY_PHRASES: list[tuple[str, str]] = sorted(
    ((normalize_arabic(k), v) for k, v in CURRENCY_WORDS.items()),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

# --------------------------------------------------------------------------- #
# Number words
# --------------------------------------------------------------------------- #
_ONES: dict[str, int] = {
    "صفر": 0,
    "واحد": 1, "واحده": 1, "احد": 1, "احدي": 1,
    "اثنان": 2, "اثنين": 2, "اثنتان": 2, "اثنتين": 2, "ثنتان": 2, "ثنتين": 2,
    "ثلاثه": 3, "ثلاث": 3, "اربع": 4, "اربعه": 4,
    "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6,
    "سبع": 7, "سبعه": 7, "ثمان": 8, "ثمانيه": 8, "ثماني": 8,
    "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
    "احد عشر": 11, "احدي عشر": 11, "اثنا عشر": 12, "اثني عشر": 12,
    "ثلاثه عشر": 13, "ثلاث عشر": 13, "اربعه عشر": 14, "اربع عشر": 14,
    "خمسه عشر": 15, "خمس عشر": 15, "سته عشر": 16, "ست عشر": 16,
    "سبعه عشر": 17, "سبع عشر": 17, "ثمانيه عشر": 18, "ثماني عشر": 18,
    "تسعه عشر": 19, "تسع عشر": 19,
}
_TENS: dict[str, int] = {
    "عشرون": 20, "عشرين": 20, "ثلاثون": 30, "ثلاثين": 30,
    "اربعون": 40, "اربعين": 40, "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60, "سبعون": 70, "سبعين": 70,
    "ثمانون": 80, "ثمانين": 80, "تسعون": 90, "تسعين": 90,
}
_HUNDREDS: dict[str, int] = {
    "مئه": 100, "مائه": 100, "ماءه": 100,
    "مئتان": 200, "مئتين": 200, "مائتان": 200, "مائتين": 200,
    "ثلاثمئه": 300, "ثلاثمائه": 300, "اربعمئه": 400, "اربعمائه": 400,
    "خمسمئه": 500, "خمسمائه": 500, "ستمئه": 600, "ستمائه": 600,
    "سبعمئه": 700, "سبعمائه": 700, "ثمانمئه": 800, "ثمانمائه": 800,
    "تسعمئه": 900, "تسعمائه": 900,
}
#: Multipliers ("مليون", "مليار"). Order matters: longest first at match time.
_MULTIPLIERS: dict[str, int] = {
    "الف": 1_000, "ألف": 1_000, "آلاف": 1_000, "الاف": 1_000, "الفا": 1_000,
    "مليون": 1_000_000, "ملايين": 1_000_000, "مليونا": 1_000_000,
    "مليار": 1_000_000_000, "مليارات": 1_000_000_000,
}
#: Fractions a filing may attach to a currency ("نصف مليون").
_FRACTIONS: dict[str, float] = {
    "نصف": 0.5, "ربع": 0.25, "ثلث": 1 / 3, "عشر": 0.1, "خمس": 0.2,
}


def _norm_keys(table: dict[str, Any]) -> dict[str, Any]:
    """Key a word table by its normalised form.

    Tokens are looked up *after* ``normalize_arabic`` (which maps «ة»->«ه» and
    «ئ»->«ي»), so a table keyed on the surface spelling («خمسمائة») would never
    match the normalised token («خمسمايه»). Normalising the keys keeps the two
    sides in the same space.
    """
    return {normalize_arabic(key): value for key, value in table.items()}


_ONES = _norm_keys(_ONES)
_TENS = _norm_keys(_TENS)
_HUNDREDS = _norm_keys(_HUNDREDS)
_MULTIPLIERS = _norm_keys(_MULTIPLIERS)
_FRACTIONS = _norm_keys(_FRACTIONS)

_NUMBER_WORD_TOKENS: frozenset[str] = frozenset(
    {*_ONES, *_TENS, *_HUNDREDS, *_MULTIPLIERS, *_FRACTIONS, "و", "فقط", "لا", "غير"}
)


def _canon_word(token: str) -> str | None:
    """Canonical number-word token, tolerating a glued conjunction («وخمسمائة»)."""
    if token in _NUMBER_WORD_TOKENS:
        return token
    if len(token) > 1 and token[0] == "و" and token[1:] in _NUMBER_WORD_TOKENS:
        return token[1:]
    return None
#: A percentage sign or word immediately after a figure marks a rate, not an amount.
_PERCENT_AFTER_RE = re.compile(r"^\s*(?:%|بالمئه|بالمائه|في المئه|في المائه)")
#: Bigger scale first so "مليون" wins over "الف" when both are present.
_SCALES: list[tuple[str, int]] = sorted(_MULTIPLIERS.items(), key=lambda p: -p[1])

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
_DIGITS = "0-9\u0660-\u0669"
#: A figure with optional thousand separators, e.g. 1,000,000 or ٣٥٠٠٠٠٠.
_NUMBER_RE = re.compile(rf"[{_DIGITS}][{_DIGITS},\.\u066B\u066C]*")
#: Arabic decimal separator (٫ U+066B) and thousands (٬ U+066C).
_ARABIC_DECIMAL = "\u066b"
_ARABIC_THOUSANDS = "\u066c"

#: Context words that mark a figure as money.
_MONEY_MARKERS: tuple[str, ...] = (
    "ريال", "دولار", "درهم", "يورو", "جنيه", "دينار", "مبلغ", "قدره", "قيمته",
    "قيمه", "ثمن", "دين", "مدين", "اجر", "اجره", "نفقه", "تعويض", "مكافاه",
    "اتعاب", "مستحقات", "بدل", "ايجار", "قرض", "مهر", "صداق", "ديه", "غرامه",
    "خساره", "ارباح", "راس المال", "رصيد", "سعر", "دفعه", "سلفه", "كفاله",
)
#: Context words that mark a figure as a reference (case/year/article/phone).
_REFERENCE_MARKERS: tuple[str, ...] = (
    "رقم", "بتاريخ", "سنه", "لعام", "هجريه", "ميلاديه", "الماده", "المواد",
    "صفحه", "هاتف", "بطاقه", "سجل", "هويه", "بند", "فقره", "ماده",
)


@dataclass(slots=True)
class MoneyValue:
    """A monetary amount recovered from text, with its provenance."""

    amount: float
    currency: str = DEFAULT_CURRENCY
    source: str = "numeric"  # "numeric" | "written"
    span: str = ""
    confidence: float = 0.5

    def model_dump(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "source": self.source,
            "span": self.span,
            "confidence": round(self.confidence, 3),
        }


@dataclass(slots=True)
class AmountExtraction:
    """All amounts found in a text, plus the reconciliation verdict."""

    values: list[MoneyValue] = field(default_factory=list)
    numeric: MoneyValue | None = None
    written: MoneyValue | None = None
    currency: str = DEFAULT_CURRENCY
    reconciled: bool | None = None  # True/False when both readings exist
    note: str = ""

    @property
    def primary(self) -> MoneyValue | None:
        """The figure a court would take as the claim value.

        A numeric figure is authoritative when the two readings disagree: it is
        what the instrument itself records, and a written-out amount is the one
        prone to transcription error.
        """
        return self.numeric or self.written

    def model_dump(self) -> dict[str, Any]:
        return {
            "values": [v.model_dump() for v in self.values],
            "numeric": self.numeric.model_dump() if self.numeric else None,
            "written": self.written.model_dump() if self.written else None,
            "currency": self.currency,
            "reconciled": self.reconciled,
            "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #
def find_currency(text: str) -> str:
    """Return the ISO code of the first currency named in `text`."""
    normalised = normalize_arabic(text)
    best_pos: int | None = None
    best_code = DEFAULT_CURRENCY
    for phrase, code in _CURRENCY_PHRASES:
        pos = normalised.find(phrase)
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos, best_code = pos, code
    return best_code


def currency_label(code: str | None) -> str:
    return CURRENCY_LABELS_AR.get((code or DEFAULT_CURRENCY).upper(), code or DEFAULT_CURRENCY)


def format_money(amount: float | None, currency: str | None = None) -> str:
    """Format an amount for display: «1,000,000 ريال يمني»."""
    if amount is None:
        return ""
    try:
        rendered = f"{float(amount):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(amount)
    return f"{rendered} {currency_label(currency)}"


def parse_number(text: str) -> float | None:
    """Parse a numeric literal tolerating Arabic digits and separators.

    Yemeni filings mix conventions: «1,000,000» (Anglo), «1.000.000» (European),
    «12,5» and «1.234,56». The rule is that the *last* separator present is the
    decimal one when it is followed by one or two digits; otherwise every
    separator is a thousands separator.
    """
    if not text:
        return None
    cleaned = to_western_digits(text).strip()
    cleaned = cleaned.replace(_ARABIC_DECIMAL, ".").replace(_ARABIC_THOUSANDS, ",")
    cleaned = cleaned.replace(" ", "")
    if cleaned.count(".") and cleaned.count(","):
        # Both present: whichever occurs last is the decimal separator.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "") if re.search(r",\d{3}(\D|$)", cleaned) else cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        # Several dots with no comma can only be thousands separators.
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _words_to_number(words: list[str]) -> float | None:
    """Convert a run of Arabic number words to a value.

    Handles additive forms («خمسمائة ألف» = 500 * 1000, «مليون ومائتان» =
    1,000,200) and the fraction words («نصف مليون» = 500,000). A larger scale is
    applied before a smaller one, so a run that names both ("خمسة ملايين
    وخمسمائة ألف") is read as millions plus hundreds of thousands, not the
    reverse.
    """
    if not words:
        return None
    total = 0.0
    current = 0.0
    seen = False
    pending_scales: list[tuple[float, int]] = []
    for raw in words:
        token = _canon_word(normalize_arabic(raw).strip())
        if token is None:
            break
        if token == "و":
            continue
        if token in _FRACTIONS and current == 0 and total == 0 and not pending_scales:
            current = _FRACTIONS[token]
            seen = True
            continue
        if token in _HUNDREDS:
            current += _HUNDREDS[token]
            seen = True
            continue
        if token in _TENS:
            current += _TENS[token]
            seen = True
            continue
        if token in _ONES:
            current += _ONES[token]
            seen = True
            continue
        if token in _MULTIPLIERS:
            pending_scales.append((current or 1.0, _MULTIPLIERS[token]))
            current = 0.0
            seen = True
            continue
        break
    # Apply the larger scales first: "5 ملايين" then "500 ألف".
    for factor, scale in sorted(pending_scales, key=lambda pair: -pair[1]):
        total += factor * scale
    result = total + current
    return result if seen and result > 0 else None


def parse_written_amount(text: str) -> MoneyValue | None:
    """Parse a written-out amount such as «مليون ريال» or «خمسة ملايين وخمسمائة ألف ريال».

    The run of number words is taken immediately before a currency word, so a
    stray number word elsewhere in the sentence cannot inflate the value.
    """
    normalised = normalize_arabic(text)
    if not normalised:
        return None
    tokens = normalised.split()
    best: MoneyValue | None = None
    for index, _token in enumerate(tokens):
        currency = _currency_at(tokens, index)
        if currency is None:
            continue
        # Walk backwards over number words (allowing the conjunction «و»).
        run: list[str] = []
        cursor = index - 1
        while cursor >= 0 and _canon_word(tokens[cursor]) is not None:
            run.insert(0, tokens[cursor])
            cursor -= 1
        value = _words_to_number(run)
        if value is None:
            continue
        span = " ".join(tokens[cursor + 1 : index + 1])
        candidate = MoneyValue(
            amount=value,
            currency=currency,
            source="written",
            span=span,
            confidence=0.85,
        )
        # Keep the largest written amount: it is the principal, not a payment.
        if best is None or candidate.amount > best.amount:
            best = candidate
    return best


def _currency_at(tokens: list[str], index: int) -> str | None:
    """Currency code when a currency phrase starts at `tokens[index]`."""
    for phrase, code in _CURRENCY_PHRASES:
        parts = phrase.split()
        if tokens[index : index + len(parts)] == parts:
            return code
    return None


def parse_numeric_amounts(text: str) -> list[MoneyValue]:
    """Find numeric amounts, scored by their surrounding money/reference context."""
    western = to_western_digits(text)
    results: list[MoneyValue] = []
    for match in _NUMBER_RE.finditer(western):
        raw = match.group(0)
        if not re.search(rf"[{_DIGITS}]", raw):
            continue
        value = parse_number(raw)
        if value is None:
            continue
        before = western[max(0, match.start() - 30) : match.start()]
        after = western[match.end() : match.end() + 34]
        norm_before = normalize_arabic(before)
        norm_after = normalize_arabic(after)

        currency = _currency_near(norm_after) or _currency_near(norm_before) or DEFAULT_CURRENCY

        # A figure immediately followed by a percent sign is a rate ("1%"),
        # never an amount. The sign is checked on the raw text because
        # normalisation strips it.
        raw_after = after.lstrip()
        if raw_after.startswith("%") or raw_after.startswith("٪") or _PERCENT_AFTER_RE.match(norm_after):
            continue

        score = 0
        window = f"{norm_before} {norm_after}"
        if any(marker in window for marker in _MONEY_MARKERS):
            score += 2
        if any(marker in norm_before[-16:] for marker in _REFERENCE_MARKERS):
            score -= 3
        # Scale a trailing multiplier ("5 ملايين").
        for word, multiplier in _SCALES:
            if norm_after.startswith(word) or f" {word}" in f" {norm_after}"[:14]:
                value *= multiplier
                break
        # A bare four-digit year is not a claim amount.
        digits = re.sub(r"\D", "", raw)
        if 1900 <= value <= 2100 and len(digits) == 4 and score <= 0:
            continue
        if score <= 0:
            continue
        results.append(
            MoneyValue(
                amount=value,
                currency=currency,
                source="numeric",
                span=western[match.start() : match.end() + 18].strip(),
                confidence=min(0.99, 0.55 + 0.15 * score),
            )
        )
    return results


def _currency_near(normalised: str) -> str | None:
    for phrase, code in _CURRENCY_PHRASES:
        if phrase in normalised:
            return code
    return None


def extract_amounts(text: str) -> AmountExtraction:
    """Extract every monetary reading from `text` and reconcile them."""
    result = AmountExtraction()
    if not text or not text.strip():
        return result

    numeric = parse_numeric_amounts(text)
    written = parse_written_amount(text)
    result.values = numeric
    if written:
        result.values.append(written)
    result.numeric = max(numeric, key=lambda v: (v.amount, v.confidence)) if numeric else None
    result.written = written

    currency = (
        (result.numeric.currency if result.numeric else None)
        or (written.currency if written else None)
        or find_currency(text)
    )
    result.currency = currency

    result.reconciled, note = reconcile(result.numeric, written)
    result.note = note
    return result


def reconcile(numeric: MoneyValue | None, written: MoneyValue | None) -> tuple[bool | None, str]:
    """Check that the numeric and written readings agree.

    Returns ``(verdict, note)``: ``None`` when only one reading exists, ``True``
    when both agree, ``False`` when they conflict. A conflict is a material
    finding for a commercial judgment, so it is surfaced rather than hidden.
    """
    if numeric is None or written is None:
        return None, ""
    # Tolerate rounding in the written form (e.g. «نحو مليون»).
    tolerance = max(1.0, 0.01 * max(numeric.amount, written.amount))
    if abs(numeric.amount - written.amount) <= tolerance:
        return True, "الرقم والمبلغ المكتوب متطابقان."
    return False, (
        f"تعارض بين المبلغ الرقمي ({format_money(numeric.amount, numeric.currency)}) "
        f"والمبلغ المكتوب ({format_money(written.amount, written.currency)}) — يلزم التحقق."
    )
