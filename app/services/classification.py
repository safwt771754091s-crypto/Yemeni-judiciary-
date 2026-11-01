"""Case classification (تصنيف الدعوى) into a Yemeni court branch.

The classifier answers two questions the rest of the pipeline depends on:

1. Which branch of the Yemeni judiciary does this case belong to - مدني، تجاري،
   جنائي، أسري، عمالي، إداري، عقاري، تنفيذ؟
2. Which statutes must be searched to decide it?

Design
------
A deterministic weighted-lexicon model produces the primary answer. It is
auditable (every point is traced to a term that appeared in the text), stable
(identical input yields identical output), and works with no network. An
optional LLM pass may then *refine* the answer, but it is constrained to the
candidate set the lexicon produced: the model can pick a runner-up it can see,
it cannot invent a case type or override a decisive criminal signal. That
constraint is what keeps classification trustworthy in a legal setting - the
final say stays with a deterministic rule that a judge can inspect.

Criminal cases are treated conservatively in one specific way: a strong criminal
signal is recorded as a flag even when another branch scores higher, because a
civil court that fails to notice a criminal element is a serious error, whereas
the reverse merely routes the file to a judge who can transfer it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.legal_lexicon import (
    CASE_TYPE_BY_KEY,
    CASE_TYPES,
    CaseTypeProfile,
    CaseTypeScore,
    ClassificationSignal,
    resolve_case_type,
)
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic

logger = get_logger(__name__)

#: Relative strength of a matched term.
STRONG_WEIGHT = 3
WEAK_WEIGHT = 1
COUNTER_WEIGHT = -2

#: A term repeated many times should not dominate; cap its contribution.
MAX_TERM_HITS = 3


@dataclass(slots=True)
class ClassificationResult:
    case_type: str
    label_ar: str
    confidence: float
    source_types: list[str]
    scores: list[CaseTypeScore]
    signals: list[ClassificationSignal]
    criminal_flag: bool
    requires_review: bool
    method: str
    notes: list[str]

    def model_dump(self) -> dict[str, Any]:
        return {
            "case_type": self.case_type,
            "label_ar": self.label_ar,
            "confidence": round(self.confidence, 4),
            "source_types": list(self.source_types),
            "criminal_flag": self.criminal_flag,
            "requires_review": self.requires_review,
            "method": self.method,
            "notes": list(self.notes),
            "signals": [
                {"term": s.term, "weight": s.weight} for s in self.signals[:25]
            ],
            "scores": [
                {
                    "case_type": s.case_type,
                    "label_ar": s.label_ar,
                    "score": round(s.score, 3),
                    "matched": [sig.term for sig in s.signals[:8]],
                }
                for s in self.scores
            ],
        }


class CaseClassifier:
    """Deterministic classifier over the Yemeni legal lexicon."""

    def __init__(self) -> None:
        # Pre-normalise every keyword once so matching is a single pass, and
        # compile a boundary-aware pattern per term. Matching on raw substrings
        # is unsafe in Arabic: "سب" (insult, a criminal term) occurs inside
        # "مناسباً" (suitable), which would route a routine civil claim to the
        # criminal branch. The pattern therefore anchors each term between
        # non-letter characters, while still allowing the definite article,
        # since Arabic attaches it to the front of a noun ("مدين" -> "المدين").
        self._profiles: list[tuple[CaseTypeProfile, list[tuple[re.Pattern[str], int, str]]]] = []
        for profile in CASE_TYPES:
            terms: list[tuple[re.Pattern[str], int, str]] = []
            for term in profile.strong:
                terms.append((self._pattern(term), STRONG_WEIGHT, term))
            for term in profile.weak:
                terms.append((self._pattern(term), WEAK_WEIGHT, term))
            for term in profile.counter:
                terms.append((self._pattern(term), COUNTER_WEIGHT, term))
            self._profiles.append((profile, terms))

    @staticmethod
    def _pattern(term: str) -> re.Pattern[str]:
        """Build a boundary-aware pattern for an Arabic legal term.

        Two features of Arabic legal prose have to be handled or the term simply
        never matches:

        * The definite article attaches to *every* word, not just the first, so
          the term "قرار إداري" must match "القرار الإداري". The article is
          therefore optional on each word.
        * Prepositions and conjunctions are written glued to the following word,
          often in front of the article: "بالاعتداء", "للمدعي". A short
          proclitic run is allowed before the first word.

        The lookarounds keep matching word-bounded, so the term "سب" does not
        match inside "مناسباً".
        """
        normalized = CaseClassifier._normalize_for_match(term)
        words = [w for w in normalized.split(" ") if w]
        if not words:
            return re.compile(r"(?!x)x")  # never matches
        body = r"\s+".join(r"(?:ال)?" + re.escape(w) for w in words)
        prefix = r"(?:[وفبلك]{0,2})?(?:ال)?"
        return re.compile(
            r"(?<![\u0621-\u064Aa-z0-9])" + prefix + body + r"(?![\u0621-\u064Aa-z0-9])"
        )

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Normalise Arabic for keyword matching, collapsing tanween.

        Arabic marks the accusative with a final alef ("إدارياً" is written
        "إداريا" once diacritics are dropped), so a keyword such as "قرار إداري"
        would never match "قراراً إدارياً" without this step. Dropping a
        word-final alef from words of four letters or more folds the two forms
        together. The same transform is applied to keywords and to the text, so
        matching stays symmetric.
        """
        normalized = normalize_arabic(text)
        words = []
        for word in normalized.split(" "):
            if len(word) >= 4 and word.endswith("ا"):
                word = word[:-1]
            words.append(word)
        return " ".join(words)

    # -- public API --------------------------------------------------------
    def classify(
        self,
        text: str,
        *,
        hints: dict[str, Any] | None = None,
        declared_case_type: str | None = None,
    ) -> ClassificationResult:
        """Classify a case from its free text plus optional structured hints.

        ``hints`` may carry already-known values (a subject matter, a claim
        description, a crime named in a police report). ``declared_case_type``
        is a type the filer stated; it is treated as strong evidence, not as
        the answer, so a mislabelled filing is still routed correctly.
        """
        hints = hints or {}
        corpus = self._build_corpus(text, hints)
        normalized = self._normalize_for_match(corpus)
        notes: list[str] = []

        raw_scores = self._score(normalized)
        declared_key = resolve_case_type(declared_case_type)
        if declared_key:
            # A declared type is evidence, not a verdict: it is worth roughly
            # one strong term, so a filing that plainly describes a crime is
            # still classified as criminal despite a "مدني" label.
            for score in raw_scores:
                if score.case_type == declared_key:
                    score.score += STRONG_WEIGHT
                    score.signals.insert(0, ClassificationSignal(term="نوع مصرَّح به", weight=STRONG_WEIGHT))
            notes.append(f"تم احتساب النوع المصرَّح به «{declared_case_type}» كقرينة.")

        scores = sorted(raw_scores, key=lambda s: s.score, reverse=True)
        best = scores[0] if scores else None
        confidence = self._confidence(scores)

        criminal_flag = any(
            s.case_type == "جنائي" and s.score >= STRONG_WEIGHT for s in scores
        )
        if criminal_flag and best is not None and best.case_type != "جنائي":
            notes.append(
                "ظهرت مؤشرات جنائية في نص القضية؛ يلزم عرض الملف على النيابة العامة "
                "أو إحالته إلى الدائرة الجزائية قبل الفصل مدنياً."
            )

        if best is None or best.score <= 0:
            result = ClassificationResult(
                case_type="مدني",
                label_ar=CASE_TYPE_BY_KEY["مدني"].label_ar,
                confidence=0.0,
                source_types=list(CASE_TYPE_BY_KEY["مدني"].source_types),
                scores=scores,
                signals=[],
                criminal_flag=False,
                requires_review=True,
                method="lexicon",
                notes=[
                    *notes,
                    "لم تتوفر مؤشرات كافية لتصنيف الدعوى؛ تم افتراض النوع «مدني» "
                    "ويلزم مراجعته بشرياً.",
                ],
            )
            return result

        requires_review = confidence < settings.classification_min_confidence
        if requires_review:
            notes.append(
                f"درجة الثقة في التصنيف منخفضة ({confidence:.2f})؛ يُوصى بمراجعة النوع يدوياً."
            )

        profile = CASE_TYPE_BY_KEY[best.case_type]
        return ClassificationResult(
            case_type=profile.key,
            label_ar=profile.label_ar,
            confidence=confidence,
            source_types=list(profile.source_types),
            scores=scores,
            signals=best.signals,
            criminal_flag=criminal_flag,
            requires_review=requires_review,
            method="lexicon",
            notes=notes,
        )

    def apply_llm_refinement(
        self,
        result: ClassificationResult,
        *,
        suggested: str | None,
        reason: str | None = None,
    ) -> ClassificationResult:
        """Adopt an LLM suggestion only when it is a plausible alternative.

        The suggestion must name one of the top candidates the lexicon already
        produced, and it may not override a decisive criminal signal. Anything
        else is rejected and recorded as a note.
        """
        suggested_key = resolve_case_type(suggested)
        if not suggested_key:
            return result
        if suggested_key == result.case_type:
            return result

        candidates = {s.case_type for s in result.scores[:3] if s.score > 0}
        if suggested_key not in candidates:
            result.notes.append(
                f"اقترح النموذج تصنيفاً ({suggested_key}) لا تسنده مؤشرات النص؛ تم تجاهله."
            )
            return result
        if result.criminal_flag and suggested_key != "جنائي":
            result.notes.append(
                "تم تجاهل اقتراح النموذج بتصنيف غير جنائي لوجود مؤشرات جنائية قوية."
            )
            return result

        profile = CASE_TYPE_BY_KEY[suggested_key]
        result.case_type = profile.key
        result.label_ar = profile.label_ar
        result.source_types = list(profile.source_types)
        result.method = "lexicon+llm"
        result.requires_review = False
        note = f"تم اعتماد تصنيف النموذج «{profile.label_ar}» ضمن المرشحين المرجّحين."
        if reason:
            note += f" السبب: {reason}"
        result.notes.append(note)
        return result

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _build_corpus(text: str, hints: dict[str, Any]) -> str:
        parts = [text or ""]
        for key in ("subject_matter", "facts_summary", "court_name", "declared_case_type"):
            value = hints.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        for key in ("plaintiff_claims", "defendant_responses", "titles"):
            value = hints.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.extend(str(v) for v in item.values() if isinstance(v, str))
        return "\n".join(parts)

    def _score(self, normalized_text: str) -> list[CaseTypeScore]:
        scores: list[CaseTypeScore] = []
        for profile, terms in self._profiles:
            total = 0.0
            signals: list[ClassificationSignal] = []
            for pattern, weight, display in terms:
                hits = len(pattern.findall(normalized_text))
                if not hits:
                    continue
                applied = min(hits, MAX_TERM_HITS)
                contribution = weight * applied
                total += contribution
                signals.append(ClassificationSignal(term=display, weight=contribution))
            # Counters are recorded but a profile is never pushed below zero:
            # "no evidence against" is not the same as "evidence for".
            total = max(total, 0.0)
            signals.sort(key=lambda s: s.weight, reverse=True)
            scores.append(
                CaseTypeScore(
                    case_type=profile.key,
                    label_ar=profile.label_ar,
                    score=total,
                    signals=signals,
                    criminal=profile.criminal,
                )
            )
        return scores

    @staticmethod
    def _confidence(scores: list[CaseTypeScore]) -> float:
        """Blend the winner's absolute evidence with its lead over the runner-up."""
        if not scores:
            return 0.0
        ordered = sorted(scores, key=lambda s: s.score, reverse=True)
        best = ordered[0]
        if best.score <= 0:
            return 0.0
        runner_up = ordered[1].score if len(ordered) > 1 else 0.0
        # Absolute evidence saturates around 15 points (five strong terms).
        absolute = min(best.score / 15.0, 1.0)
        margin = (best.score - runner_up) / best.score
        return round(0.6 * absolute + 0.4 * margin, 4)

    def profile_for(self, case_type: str | None) -> CaseTypeProfile | None:
        key = resolve_case_type(case_type)
        return CASE_TYPE_BY_KEY[key] if key else None


case_classifier = CaseClassifier()


def detect_criminal_signals(text: str) -> list[str]:
    """Return the criminal terms present in a text, for warning banners."""
    profile = CASE_TYPE_BY_KEY["جنائي"]
    normalized = CaseClassifier._normalize_for_match(text or "")
    found = []
    for term in profile.strong:
        if CaseClassifier._pattern(term).search(normalized):
            found.append(term)
    return found


__all__ = [
    "CaseClassifier",
    "ClassificationResult",
    "case_classifier",
    "detect_criminal_signals",
]
