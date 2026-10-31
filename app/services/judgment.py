"""Judgment drafting and Markdown rendering.

Two responsibilities:

* ``JudgmentService`` - drives the judgment chain, validates the result against
  the ``JudgmentDraft`` schema, runs the citation validator, and assembles a
  ``JudgmentDraftEnvelope`` carrying confidence + grounding metadata.
* ``render_markdown`` - renders the structured draft as a formal Yemeni court
  judgment (مسودة حكم) suitable for printing or review by a judge.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.core.errors import GroundingError
from app.core.logging import get_logger
from app.llm.chains import build_judgment_chain, build_reasoning_blocks
from app.llm.validator import GroundingReport, strip_unverified_reasoning, validate_citations
from app.schemas.judgment import JudgmentDraft, JudgmentDraftEnvelope

logger = get_logger(__name__)


class JudgmentService:
    def __init__(self) -> None:
        self._chain = build_judgment_chain()

    async def generate(
        self,
        *,
        case: dict[str, Any],
        evidence: list[dict[str, Any]],
        articles: list[dict[str, Any]],
        evidence_analysis: list[dict[str, Any]],
    ) -> tuple[JudgmentDraftEnvelope, dict[str, Any]]:
        blocks = build_reasoning_blocks(
            case, articles, evidence, evidence_analysis=evidence_analysis
        )
        result = await self._chain.run(blocks)

        draft_payload = result.data or {}
        warnings: list[str] = []
        if result.schema_error:
            warnings.append("مخرجات النموذج لم تطابق المخطط المتوقع وتم تصحيحها آلياً.")

        draft = self._coerce_draft(draft_payload, case)
        report = validate_citations(
            (draft.reasoning.cited_articles and [c.model_dump() for c in draft.reasoning.cited_articles]) or [],
            articles,
            min_similarity=0.0,
        )

        # Remove invented citations from the body of the draft.
        verified_labels = {str(c.get("article_label")) for c in report.verified}
        cleaned = strip_unverified_reasoning(draft.model_dump(), verified_labels)
        try:
            draft = JudgmentDraft.model_validate(cleaned)
        except ValidationError as exc:  # pragma: no cover - defensive
            logger.warning("draft cleanup produced invalid model", extra={"error": str(exc)})

        grounding = self._grounding_score(report, articles)
        warnings.extend(report.warnings)

        envelope = JudgmentDraftEnvelope(
            draft=draft,
            citations=list(report.verified),
            confidence=self._confidence(draft, evidence_analysis, articles),
            grounding_score=grounding,
            warnings=warnings,
            model=result.model,
            unverified_citations=report.unverified,
        )
        meta = {
            "model": result.model,
            "provider": result.provider,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "schema_error": result.schema_error,
        }
        return envelope, meta

    # -- helpers -----------------------------------------------------------
    def _coerce_draft(self, payload: dict[str, Any], case: dict[str, Any]) -> JudgmentDraft:
        """Validate the LLM output, filling mandatory fields from the case file."""
        payload = dict(payload or {})
        preamble = payload.get("preamble") if isinstance(payload.get("preamble"), dict) else {}
        # Identity fields are authoritative facts from the case file. The model
        # must never be trusted to name the court, the parties or the case
        # number - a loose generation could otherwise attribute the draft to the
        # wrong court or party.
        for key, value in (
            ("court_name", case.get("court_name") or settings.default_court_name),
            ("case_number", case.get("case_number")),
            ("judge_name", case.get("judge_name")),
            ("circuit", case.get("circuit")),
            ("jurisdiction", case.get("jurisdiction") or settings.jurisdiction),
            ("subject_matter", case.get("subject_matter")),
        ):
            preamble[key] = value
        # Party names are authoritative facts from the case file, exactly like
        # the court and the case number. A model that "recalls" a name - or
        # derives one from the subject matter - must never override the record.
        for role, name_key, details_key in (
            ("plaintiff", "plaintiff_name", "plaintiff_details"),
            ("defendant", "defendant_name", "defendant_details"),
        ):
            party = preamble.get(role)
            party = dict(party) if isinstance(party, dict) else {}
            details = case.get(details_key) or {}
            party["name"] = case.get(name_key) or details.get("name")
            for extra in ("address", "representative", "role"):
                if not party.get(extra) and details.get(extra):
                    party[extra] = details[extra]
            preamble[role] = party
        payload["preamble"] = preamble

        facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
        facts.setdefault("narrative", case.get("facts_summary") or case.get("subject_matter") or "")
        # The claims/defences of record come from the case file, not the model.
        if not facts.get("plaintiff_claims"):
            facts["plaintiff_claims"] = [
                {
                    "title": c.get("title") or "",
                    "description": c.get("description") or "",
                    "amount": c.get("amount"),
                    "currency": c.get("currency"),
                    "supported_by": [],
                }
                for c in (case.get("plaintiff_claims") or [])
            ]
        if not facts.get("defendant_responses"):
            facts["defendant_responses"] = [
                {
                    "claim_ref": r.get("claim_ref"),
                    "response_type": r.get("response_type") or "denial",
                    "description": r.get("description") or "",
                    "legal_basis": list(r.get("legal_basis") or []),
                }
                for r in (case.get("defendant_responses") or [])
            ]
        payload["facts"] = facts

        reasoning = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else {}
        reasoning.setdefault("summary", "وحيث إن المحكمة استعرضت عناصر الدعوى وأدلتها.")
        payload["reasoning"] = reasoning

        verdict = payload.get("verdict") if isinstance(payload.get("verdict"), dict) else {}
        if not verdict.get("items"):
            verdict["items"] = [
                {
                    "decision": "قبول",
                    "subject": "قبول الدعوى شكلاً",
                    "detail": "قبول الدعوى شكلاً لاستيفائها أوضاعها الشكلية.",
                    "legal_basis": [],
                }
            ]
        verdict.setdefault("summary", "حكمت المحكمة وفقاً لما بيّنته الأسباب.")
        payload["verdict"] = verdict

        try:
            return JudgmentDraft.model_validate(payload)
        except ValidationError as exc:
            raise GroundingError(
                "تعذّر التحقق من بنية مسودة الحكم المُولَّدة",
                details=exc.errors()[:5],
            ) from exc

    @staticmethod
    def _grounding_score(report: GroundingReport, articles: list[dict[str, Any]]) -> float:
        if not articles:
            return 0.0
        coverage = min(1.0, len(report.verified) / max(1, min(4, len(articles))))
        return round(0.6 * report.grounding_score + 0.4 * coverage, 3)

    @staticmethod
    def _confidence(
        draft: JudgmentDraft, evidence_analysis: list[dict[str, Any]], articles: list[dict[str, Any]]
    ) -> float:
        if evidence_analysis:
            ev = sum(float(e.get("weight") or 0.0) for e in evidence_analysis) / len(evidence_analysis)
        else:
            ev = 0.0
        corpus = min(1.0, len(articles) / 6)
        steps = min(1.0, len(draft.reasoning.legal_analysis) / 4)
        return round(max(0.0, min(1.0, 0.4 * ev + 0.35 * corpus + 0.25 * steps)), 3)


judgment_service = JudgmentService()


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def _money(amount: Any, currency: str | None) -> str:
    if amount is None:
        return ""
    try:
        return f"{float(amount):,.2f} {currency or 'ريال يمني'}"
    except (TypeError, ValueError):
        return str(amount)


def render_markdown(envelope: JudgmentDraftEnvelope, case: dict[str, Any] | None = None) -> str:
    """Render the structured draft as a formal Yemeni judgment (مسودة حكم)."""
    d = envelope.draft
    p, f, r, v = d.preamble, d.facts, d.reasoning, d.verdict
    lines: list[str] = []

    lines.append(f"# {p.court_name}")
    if p.circuit:
        lines.append(f"**{p.circuit}**")
    lines.append("")
    lines.append("## مسودة حكم قضائي")
    lines.append("")
    lines.append("> ⚠️ **مسودة آلية غير مُعتمدة.** هذه المسودة مُولَّدة بمساعدة الذكاء الاصطناعي "
                 "ولا تُعدّ حكماً قضائياً إلا بعد مراجعتها واعتمادها من القاضي المختص.")
    lines.append("")

    # الديباجة
    lines.append("### أولاً: الديباجة")
    lines.append("")
    lines.append(f"باسم الشعب، {p.jurisdiction or settings.jurisdiction}.")
    meta_bits = []
    if p.case_number:
        meta_bits.append(f"رقم القضية: {p.case_number}")
    if p.subject_matter:
        meta_bits.append(f"موضوع الدعوى: {p.subject_matter}")
    if p.judgment_date:
        meta_bits.append(f"تاريخ الجلسة: {p.judgment_date}")
    if meta_bits:
        lines.append(" — ".join(meta_bits) + ".")
    if p.judge_name:
        lines.append(f"برئاسة القاضي: {p.judge_name}.")
    plaintiff = p.plaintiff.get("name") if isinstance(p.plaintiff, dict) else p.plaintiff
    defendant = p.defendant.get("name") if isinstance(p.defendant, dict) else p.defendant
    lines.append(f"المدعي: {plaintiff or '—'}.")
    lines.append(f"المدعى عليه: {defendant or '—'}.")
    if p.attendance:
        lines.append(p.attendance)
    lines.append("")

    # الوقائع
    lines.append("### ثانياً: الوقائع")
    lines.append("")
    if f.narrative:
        lines.append(f.narrative)
        lines.append("")
    if f.plaintiff_claims:
        lines.append("**دعوى المدعي:**")
        lines.append("")
        for index, claim in enumerate(f.plaintiff_claims, start=1):
            amount = f" ({_money(claim.amount, claim.currency)})" if claim.amount else ""
            lines.append(f"{index}. {claim.title}: {claim.description}{amount}")
        lines.append("")
    if f.defendant_responses:
        lines.append("**ردود المدعى عليه:**")
        lines.append("")
        for index, response in enumerate(f.defendant_responses, start=1):
            lines.append(f"{index}. [{response.response_type}] {response.description}")
        lines.append("")
    if f.undisputed_points:
        lines.append("**الوقائع غير المتنازع عليها:** " + "؛ ".join(f.undisputed_points) + ".")
        lines.append("")
    if f.disputed_points:
        lines.append("**نقاط الخلاف:** " + "؛ ".join(f.disputed_points) + ".")
        lines.append("")

    # الأسباب والحيثيات
    lines.append("### ثالثاً: الأسباب والحيثيات")
    lines.append("")
    if r.summary:
        lines.append(r.summary)
        lines.append("")
    if r.burden_of_proof:
        lines.append(f"**توزيع عبء الإثبات:** {r.burden_of_proof}")
        lines.append("")
    for step in r.legal_analysis:
        lines.append(f"**{step.step}) {step.question}**")
        lines.append("")
        lines.append(step.analysis)
        lines.append("")
        lines.append(f"*النتيجة:* {step.conclusion}")
        if step.cited_articles:
            lines.append(f"*السند:* {', '.join(step.cited_articles)}")
        lines.append("")
    if r.evidence_analysis:
        lines.append("**تقييم الأدلة:**")
        lines.append("")
        lines.append("| الدليل | مقدمه | التصنيف | الحجية | الوزن | التقييم |")
        lines.append("|---|---|---|---|---|---|")
        for item in r.evidence_analysis:
            lines.append(
                f"| {item.evidence_title} | {item.submitted_by} | {item.proof_category or '—'} "
                f"| {item.admissibility} | {item.weight:.2f} | {item.assessment} |"
            )
        lines.append("")
    if r.defendant_defences_assessment:
        lines.append("**درء الدفوع:**")
        lines.append("")
        for defence in r.defendant_defences_assessment:
            lines.append(f"- {defence}")
        lines.append("")
    if r.cited_articles:
        lines.append("**النصوص القانونية المستند إليها:**")
        lines.append("")
        for citation in r.cited_articles:
            law = citation.law_title
            if citation.law_number:
                law = f"{law} رقم ({citation.law_number})"
            lines.append(f"- **{citation.article_label}** من {law} — {citation.relevance}")
            if citation.quote:
                lines.append(f"  > «{citation.quote.strip()[:600]}»")
        lines.append("")

    # المنطوق
    lines.append("### رابعاً: المنطوق")
    lines.append("")
    if v.summary:
        lines.append(v.summary)
        lines.append("")
    for index, item in enumerate(v.items, start=1):
        amount = f" — {_money(item.amount, item.currency)}" if item.amount else ""
        lines.append(f"**{'أولاً' if index == 1 else 'ثانياً' if index == 2 else 'ثالثاً' if index == 3 else f'{index})'}** "
                     f"{item.decision} {item.subject}: {item.detail}{amount}")
        if item.legal_basis:
            lines.append(f"  *(السند: {', '.join(item.legal_basis)})*")
        lines.append("")
    if v.costs:
        lines.append(f"**المصاريف:** {v.costs}")
        lines.append("")
    if v.appeal_deadline:
        lines.append(f"**ميعاد الطعن:** {v.appeal_deadline}")
        lines.append("")

    # Footer / provenance
    lines.append("---")
    lines.append("")
    lines.append("**بيانات إسناد المسودة (Grounding)**")
    lines.append("")
    lines.append(f"- درجة الإسناد إلى النصوص المسترجعة: {envelope.grounding_score:.2f}")
    lines.append(f"- درجة الثقة التقديرية: {envelope.confidence:.2f}")
    lines.append(f"- النموذج المستخدم: {envelope.model or '—'}")
    lines.append(f"- عدد المواد المستشهد بها: {len(envelope.citations)}")
    if envelope.unverified_citations:
        lines.append(
            "- استشهادات محذوفة (لا سند لها في النصوص المسترجعة): "
            + ", ".join(envelope.unverified_citations)
        )
    if envelope.warnings:
        lines.append("")
        lines.append("**تحذيرات:**")
        lines.append("")
        for warning in envelope.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("*تنبيه: لا يُعتد بهذه المسودة إلا بعد مراجعتها واعتمادها من القاضي المختص.*")
    return "\n".join(lines)
