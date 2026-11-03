"""AI legal assistant (المساعد القانوني الذكي) — the "Harvey" layer.

Answers a legal question strictly from retrieved Yemeni statutes and keeps a
per-user conversation history. The design mirrors the judgment pipeline:

    question (+ thread context)
        |
        v
    hybrid retrieval over the Yemeni corpus      <-- RAG
        |
        v
    assistant chain grounded in the statutes
        |
        v
    citation validation, persist, return

A turn is only ever grounded in statutes the retriever actually returned; any
citation the model invents is stripped and the answer is flagged for review.
The assistant is deliberately available to every role (it is a reading aid) but
the *history* is private to the owning user.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.db.assistant_repository import AssistantRepository, assistant_repository
from app.llm.chains import build_assistant_chain
from app.llm.validator import validate_citations
from app.schemas.assistant import (
    AssistantAnswer,
    AssistantCitation,
    AssistantConversationDetail,
    AssistantConversationList,
    AssistantConversationOut,
    AssistantMessageOut,
)
from app.services.retrieval import retrieval_service

logger = get_logger(__name__)

#: How many prior turns are fed back as context. Enough to resolve a follow-up
#: ("وما الحكم إن كان العقد شفهياً؟") without letting an old thread dominate.
_HISTORY_TURNS = 6


class AssistantService:
    def __init__(self, *, repository: AssistantRepository | None = None) -> None:
        self.repository = repository or assistant_repository
        self.retrieval = retrieval_service
        self._chain = build_assistant_chain()

    # -- public API --------------------------------------------------------
    async def ask(
        self,
        *,
        user: dict[str, Any],
        question: str,
        conversation_id: str | None = None,
        case_id: str | None = None,
        case_type: str | None = None,
        top_k: int = 8,
        include_history: bool = True,
    ) -> AssistantAnswer:
        started = time.perf_counter()
        user_id = str(user["id"])

        conversation = await self._resolve_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            question=question,
            case_id=case_id,
            case_type=case_type,
        )
        conv_id = str(conversation["id"])

        history = (
            await self.repository.recent_history(conv_id, limit=_HISTORY_TURNS)
            if include_history
            else []
        )
        effective_type = case_type or conversation.get("case_type")

        # Retrieve against the question plus a short recap of the thread so a
        # follow-up resolves against the same legal subject.
        search_text = self._search_text(question, history)
        articles, _queries = await self.retrieval.retrieve_for_case(
            case={
                "case_type": effective_type,
                "subject_matter": question,
                "facts_summary": search_text,
            },
            top_k=top_k,
            case_type=effective_type,
        )
        article_dicts = [a.model_dump(mode="json") for a in articles]

        result = await self._chain.run(
            self._build_blocks(question=question, history=history, articles=article_dicts)
        )
        data = result.data or {}

        report = validate_citations(data.get("cited_articles") or [], article_dicts)
        citations = [AssistantCitation.model_validate(c) for c in report.verified]

        answer_text = str(data.get("answer") or "").strip()
        reasoning = [str(r) for r in (data.get("reasoning") or []) if str(r).strip()]
        gap = data.get("gap")
        needs_review = bool(report.unverified) or not articles or not answer_text
        if not answer_text:
            answer_text = (
                "تعذّر توليد إجابة موثقة عن هذا السؤال؛ يرجى إعادة صياغته أو مراجعة "
                "المدونة القانونية."
            )

        warnings = list(report.warnings)
        mismatch = await self.retrieval.embedding_mismatch_warning()
        if mismatch:
            warnings.append(mismatch)

        # Persist the turn (question then answer) so the thread is reconstructible.
        await self.repository.add_message(conv_id, role="user", content=question)
        stored = await self.repository.add_message(
            conv_id,
            role="assistant",
            content=answer_text,
            citations=[c.model_dump(mode="json") for c in citations],
            grounding_score=report.grounding_score,
            model=result.model,
            provider=result.provider,
            needs_review=needs_review,
        )
        await self.repository.touch_conversation(conv_id)

        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "assistant answered",
            extra={
                "conversation_id": conv_id,
                "articles": len(articles),
                "grounding_score": round(report.grounding_score, 3),
                "duration_ms": round(duration_ms, 1),
            },
        )

        return AssistantAnswer(
            conversation_id=conv_id,
            message_id=str(stored["id"]),
            question=question,
            answer=answer_text,
            reasoning=reasoning,
            cited_articles=citations,
            gap=gap if isinstance(gap, str) and gap.strip() else None,
            needs_review=needs_review,
            grounding_score=report.grounding_score,
            model=result.model,
            provider=result.provider,
            created_at=stored.get("created_at"),
        )

    async def list_conversations(
        self, *, user: dict[str, Any], limit: int = 30, offset: int = 0
    ) -> AssistantConversationList:
        user_id = str(user["id"])
        rows = await self.repository.list_conversations(
            user_id=user_id, limit=limit, offset=offset
        )
        return AssistantConversationList(
            items=[AssistantConversationOut.model_validate(r) for r in rows],
            total=await self.repository.count_conversations(user_id=user_id),
        )

    async def get_conversation(
        self, *, user: dict[str, Any], conversation_id: str
    ) -> AssistantConversationDetail:
        conversation = await self.repository.get_conversation(
            conversation_id, user_id=str(user["id"])
        )
        if conversation is None:
            from app.core.errors import NotFoundError

            raise NotFoundError("المحادثة غير موجودة")
        messages = await self.repository.list_messages(conversation_id)
        conversation = {
            **conversation,
            "message_count": len(messages),
        }
        return AssistantConversationDetail(
            conversation=AssistantConversationOut.model_validate(conversation),
            messages=[AssistantMessageOut.model_validate(m) for m in messages],
        )

    async def delete_conversation(self, *, user: dict[str, Any], conversation_id: str) -> None:
        await self.repository.delete_conversation(
            conversation_id, user_id=str(user["id"])
        )

    # -- helpers -----------------------------------------------------------
    async def _resolve_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        question: str,
        case_id: str | None,
        case_type: str | None,
    ) -> dict[str, Any]:
        if conversation_id:
            existing = await self.repository.get_conversation(
                conversation_id, user_id=user_id
            )
            if existing is None:
                from app.core.errors import NotFoundError

                raise NotFoundError("المحادثة غير موجودة")
            return existing
        return await self.repository.create_conversation(
            user_id=user_id,
            title=self._derive_title(question),
            case_id=case_id,
            case_type=case_type,
        )

    @staticmethod
    def _derive_title(question: str) -> str:
        """A short, readable thread title from the first question."""
        cleaned = " ".join(question.split())
        return cleaned[:80] if cleaned else "محادثة جديدة"

    @staticmethod
    def _search_text(question: str, history: list[dict[str, Any]]) -> str:
        """Question plus the last user turn, so a follow-up keeps its subject."""
        prior = [m["content"] for m in history if m.get("role") == "user"]
        if not prior:
            return question
        return f"{prior[-1]}\n{question}"

    @staticmethod
    def _build_blocks(
        *,
        question: str,
        history: list[dict[str, Any]],
        articles: list[dict[str, Any]],
    ) -> list[str]:
        from app.llm.prompts import build_retrieved_statutes_block

        blocks: list[str] = []
        if history:
            lines = ["«سياق المحادثة السابقة»:"]
            for message in history:
                speaker = "المستخدم" if message.get("role") == "user" else "المساعد"
                content = " ".join(str(message.get("content") or "").split())
                lines.append(f"- {speaker}: {content[:600]}")
            blocks.append("\n".join(lines))
        blocks.append(build_retrieved_statutes_block(articles))
        blocks.append(f"«سؤال المستخدم»:\n- السؤال: {question}")
        blocks.append(
            "«المطلوب»: أخرج إجابة موثقة بصيغة JSON مطابقة للمخطط الموصوف في تعليمات "
            "النظام، مع ذكر المواد المستشهد بها حرفياً."
        )
        return blocks


assistant_service = AssistantService()
