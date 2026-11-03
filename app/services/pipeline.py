"""End-to-end legal decision pipeline.

     submit case
         |
         v
   persist case + evidence            (case_repository)
         |
         v
   plan queries -> hybrid retrieval    (retrieval_service)   <-- RAG
         |
         v
   weigh evidence under Yemeni law     (evidence_service)
         |
         v
   draft judgment grounded in statutes (judgment_service)
         |
         v
   validate citations, render markdown, persist judgment

Every stage is failure-tolerant: if the LLM is unavailable the deterministic
rule layers still produce a reviewable draft, and the caller always receives the
retrieved statutes and a grounding score.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.db.repositories import case_repository
from app.schemas.case import (
    CaseSubmissionRequest,
    CaseSubmissionResponse,
    EvidenceAssessment,
)
from app.services.commercial import commercial_engine
from app.services.evidence import evidence_service
from app.services.judgment import judgment_service, render_markdown
from app.services.retrieval import retrieval_service

logger = get_logger(__name__)


class CasePipeline:
    def __init__(self) -> None:
        self.cases = case_repository
        self.retrieval = retrieval_service
        self.evidence = evidence_service
        self.judgments = judgment_service
        self.commercial = commercial_engine

    async def submit(self, request: CaseSubmissionRequest) -> CaseSubmissionResponse:
        started = time.perf_counter()
        warnings: list[str] = []

        # 1. persist the case file -----------------------------------------
        case = await self.cases.create_case(
            {
                "case_number": request.case_number,
                "case_type": request.case_type,
                "court_name": request.court_name,
                "circuit": request.circuit,
                "judge_name": request.judge_name,
                "subject_matter": request.subject_matter,
                "claim_value": request.claim_value,
                "currency": request.currency,
                "facts_summary": request.facts_summary,
                "plaintiff_name": request.plaintiff.name,
                "plaintiff_details": request.plaintiff.model_dump(mode="json"),
                "defendant_name": request.defendant.name,
                "defendant_details": request.defendant.model_dump(mode="json"),
                "plaintiff_claims": [c.model_dump(mode="json") for c in request.plaintiff_claims],
                "defendant_responses": [
                    r.model_dump(mode="json") for r in request.defendant_responses
                ],
                "extra": request.extra,
            }
        )
        case_id = str(case["id"])

        stored_evidence: list[dict[str, Any]] = []
        if request.evidence:
            stored_evidence = await self.cases.add_evidence(
                case_id, [item.model_dump(mode="json") for item in request.evidence]
            )

        # 2. RAG retrieval --------------------------------------------------
        articles, _queries = await self.retrieval.retrieve_for_case(
            case=case,
            evidence=stored_evidence,
            top_k=request.max_retrieved_articles,
            case_type=request.case_type,
        )
        article_dicts = [a.model_dump(mode="json") for a in articles]
        mismatch = await self.retrieval.embedding_mismatch_warning()
        if mismatch:
            warnings.append(mismatch)
        if not articles:
            warnings.append(
                "لم يتم العثور على نصوص قانونية يمنية مطابقة في قاعدة البيانات. "
                "المسودة غير مُسنَدة إلى نصوص؛ يجب إدخال التشريعات إلى قاعدة البيانات "
                "(POST /api/v1/ingestion/text) قبل الاعتماد على النتيجة."
            )

        # 3. evidence analysis ----------------------------------------------
        evidence_analysis, evidence_warnings, _ = await self.evidence.analyze(
            case=case, evidence=stored_evidence, articles=article_dicts
        )
        warnings.extend(evidence_warnings)
        if evidence_analysis:
            await self.cases.update_evidence_assessment(
                case_id, [a.model_dump(mode="json") for a in evidence_analysis]
            )

        # 3b. commercial financial extraction --------------------------------
        commercial = self.commercial.extract(case=case, evidence=stored_evidence)
        if request.commercial is not None:
            self._merge_commercial_intake(commercial, request.commercial)
        commercial.financial_summary = self.commercial.apply_awards(
            commercial.financial_summary, []
        )
        warnings.extend(commercial.warnings)

        judgment_id = None
        envelope = None
        markdown = None
        model = None
        grounding = None

        # 4. judgment drafting ----------------------------------------------
        if request.generate_judgment:
            envelope, meta = await self.judgments.generate(
                case=case,
                evidence=stored_evidence,
                articles=article_dicts,
                evidence_analysis=[a.model_dump(mode="json") for a in evidence_analysis],
                commercial=commercial if commercial.is_commercial else None,
            )
            warnings.extend(envelope.warnings)
            model = envelope.model
            grounding = envelope.grounding_score
            # The awarded column comes from the verdict the engine produced, not
            # from the model's prose: only a ruling that carries a figure counts.
            commercial.financial_summary = self.commercial.apply_awards(
                commercial.financial_summary,
                [item.model_dump(mode="json") for item in envelope.draft.verdict.items],
            )
            if envelope.draft.financial_summary is not None:
                envelope.draft.financial_summary = commercial.financial_summary
            markdown = render_markdown(envelope, case)

            record = await self.cases.save_judgment(
                case_id,
                {
                    "status": "ai_generated",
                    "language": request.language,
                    "model": envelope.model,
                    "preamble": envelope.draft.preamble.model_dump(mode="json"),
                    "facts": envelope.draft.facts.model_dump(mode="json"),
                    "reasoning": envelope.draft.reasoning.model_dump(mode="json"),
                    "verdict": envelope.draft.verdict.model_dump(mode="json"),
                    "citations": list(envelope.citations),
                    "evidence_analysis": [
                        a.model_dump(mode="json") for a in envelope.draft.reasoning.evidence_analysis
                    ],
                    "markdown_ar": markdown,
                    "confidence": envelope.confidence,
                    "grounding_score": envelope.grounding_score,
                    "warnings": warnings,
                    "raw_llm": meta,
                },
            )
            judgment_id = record["id"]
            # Refresh so the response reflects the final persisted status
            # rather than the state captured when the case was created.
            case = await self.cases.update_case_status(case_id, "judgment_ready") or case

        await self.cases.audit(
            "case",
            "submitted",
            case_id,
            payload={
                "retrieved_articles": len(articles),
                "evidence": len(stored_evidence),
                "judgment_id": judgment_id,
            },
        )

        pipeline_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "case pipeline finished",
            extra={
                "case_id": case_id,
                "articles": len(articles),
                "evidence": len(stored_evidence),
                "duration_ms": round(pipeline_ms, 1),
            },
        )

        # De-duplicate warnings while preserving order.
        seen: set[str] = set()
        unique_warnings = [w for w in warnings if not (w in seen or seen.add(w))]

        return CaseSubmissionResponse(
            case=case,
            judgment_id=judgment_id,
            judgment=envelope.draft if envelope else None,
            judgment_markdown=markdown,
            retrieved_articles=[
                {
                    "article_label": a.article_label,
                    "law_title": a.law_title,
                    "law_number": a.law_number,
                    "source_type": a.source_type.value,
                    "article_number": a.article_number,
                    "content": a.content,
                    "text_quality": a.text_quality,
                    "source_url": a.source_url,
                    "verbatim": a.verbatim,
                    "similarity": round(a.similarity, 4),
                    "fused_score": round(a.fused_score, 6),
                }
                for a in articles
            ],
            evidence_analysis=[
                EvidenceAssessment.model_validate(a) for a in evidence_analysis
            ],
            commercial=commercial,
            warnings=unique_warnings,
            pipeline_ms=round(pipeline_ms, 2),
            model=model,
            grounding_score=grounding,
        )

    @staticmethod
    def _merge_commercial_intake(commercial, intake) -> None:
        """Let explicitly supplied commercial details override the extractor.

        A client that already holds the contract terms should not have to trust
        a parser, so anything supplied in the request takes precedence.
        """
        commercial.is_commercial = True
        terms = commercial.terms
        if intake.penalties:
            terms.penalties = list(intake.penalties)
        if intake.milestones:
            terms.milestones = list(intake.milestones)
        if intake.payment_deadline_days is not None:
            terms.payment_deadline_days = intake.payment_deadline_days
        if intake.interest_rate_percent is not None:
            terms.interest_rate_percent = intake.interest_rate_percent
        if intake.clauses:
            known = {c.clause_type for c in terms.clauses}
            terms.clauses.extend(c for c in intake.clauses if c.clause_type not in known)


case_pipeline = CasePipeline()
