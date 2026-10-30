"""Citation grounding validator.

This is the enforcement arm of the anti-hallucination contract: whatever the LLM
returns, every statute it cites must exist in the retrieved corpus, and every
verbatim quote must actually appear in the cited article. Anything that fails is
stripped from the draft and surfaced as a warning.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.text_utils import normalize_arabic, to_western_digits

_ARTICLE_NUM = re.compile(r"(\d{1,5})")


@dataclass(slots=True)
class GroundingReport:
    verified: list[dict[str, Any]] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    quote_mismatches: list[str] = field(default_factory=list)
    grounding_score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unverified


def _article_key(label: str | None) -> str | None:
    """Reduce 'المادة (1045)' / 'المادة ١٠٤٥' to a comparable digit string."""
    if not label:
        return None
    match = _ARTICLE_NUM.search(to_western_digits(label))
    return match.group(1) if match else None


def _quote_matches(quote: str, content: str, *, min_overlap: float = 0.55) -> bool:
    """Verify a quote really comes from the cited article (fuzzy, Arabic-aware)."""
    if not quote:
        return True
    q = normalize_arabic(quote)
    c = normalize_arabic(content)
    if not q:
        return True
    if q in c:
        return True
    q_terms = set(q.split())
    if not q_terms:
        return True
    c_terms = set(c.split())
    return len(q_terms & c_terms) / len(q_terms) >= min_overlap


def validate_citations(
    citations: Iterable[dict[str, Any]],
    retrieved: list[dict[str, Any]],
    *,
    min_similarity: float = 0.0,
) -> GroundingReport:
    """Keep only citations that resolve to a retrieved article with a real quote."""
    report = GroundingReport()
    index: dict[str, dict[str, Any]] = {}
    for article in retrieved:
        key = _article_key(article.get("article_label") or article.get("article_number"))
        if key:
            index[key] = article

    for citation in citations or []:
        label = citation.get("article_label") or citation.get("article_number") or ""
        key = _article_key(label)
        source = index.get(key) if key else None
        if source is None:
            report.unverified.append(str(label) or "(مادة بلا رقم)")
            continue

        similarity = float(source.get("similarity") or 0.0)
        if min_similarity and similarity < min_similarity:
            report.unverified.append(f"{label} (ملاءمة منخفضة {similarity:.2f})")
            continue

        quote = citation.get("quote") or ""
        if not _quote_matches(quote, str(source.get("content") or "")):
            report.quote_mismatches.append(str(label))
            # Keep the citation but drop the fabricated quote.
            citation = {**citation, "quote": None}
            report.warnings.append(
                f"الاقتباس المنسوب إلى {label} لا يطابق النص المسترجع وتم حذفه."
            )

        report.verified.append(
            {
                **citation,
                "article_label": source.get("article_label") or label,
                "law_title": source.get("law_title") or citation.get("law_title") or "",
                "law_number": source.get("law_number"),
                "source_type": source.get("source_type"),
                "article_id": source.get("article_id"),
                "similarity": similarity or None,
            }
        )

    total = len(report.verified) + len(report.unverified)
    report.grounding_score = (len(report.verified) / total) if total else 0.0
    if report.unverified:
        report.warnings.append(
            "تم حذف استشهادات لا سند لها في النصوص المسترجعة: " + ", ".join(report.unverified)
        )
    if not retrieved:
        report.warnings.append(
            "لم يتم العثور على نصوص قانونية مطابقة؛ المسودة غير مُسنَدة ويجب مراجعتها يدوياً."
        )
    return report


def strip_unverified_reasoning(draft: dict[str, Any], verified_labels: set[str]) -> dict[str, Any]:
    """Remove citations the model invented from the reasoning and verdict sections."""
    reasoning = draft.get("reasoning") or {}
    for step in reasoning.get("legal_analysis") or []:
        step["cited_articles"] = [
            label for label in (step.get("cited_articles") or []) if label in verified_labels
        ]
    reasoning["cited_articles"] = [
        c for c in (reasoning.get("cited_articles") or [])
        if (c.get("article_label") or "") in verified_labels
    ]
    draft["reasoning"] = reasoning

    verdict = draft.get("verdict") or {}
    for item in verdict.get("items") or []:
        item["legal_basis"] = [
            label for label in (item.get("legal_basis") or []) if label in verified_labels
        ]
    draft["verdict"] = verdict
    return draft
