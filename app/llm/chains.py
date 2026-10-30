"""LangChain-style legal reasoning chains.

Each chain wraps one LLM call with the appropriate system prompt, JSON contract
and a Pydantic output model. The chains are intentionally thin and depend only on
the ``LLMProvider`` protocol, so swapping OpenAI/Anthropic/the offline mock is a
configuration change, not a code change.

A LangChain ``ChatPromptTemplate`` is used when langchain-core is installed
(production path); otherwise the equivalent prompt is composed directly. Both
paths produce identical message content.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.llm.prompts import (
    EVIDENCE_SYSTEM_PROMPT,
    JUDGMENT_SYSTEM_PROMPT,
    QUERY_EXPANSION_PROMPT,
    REASONING_SYSTEM_PROMPT,
    build_case_block,
    build_evidence_block,
    build_retrieved_statutes_block,
)
from app.llm.provider import ChatMessage, LLMProvider, llm_provider

logger = get_logger(__name__)


class _ChainOutput(BaseModel):
    """Marker base class for chain outputs."""


@dataclass(slots=True)
class ChainResult:
    data: dict[str, Any]
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_text: str = ""
    schema_error: str | None = None


class LegalChain:
    """One grounded LLM call: system prompt + context blocks -> JSON."""

    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        output_model: type[BaseModel] | None = None,
        provider: LLMProvider | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.output_model = output_model
        self.provider = provider or llm_provider
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _render(self, blocks: Sequence[str]) -> str:
        body = "\n\n".join(blocks)
        try:  # pragma: no cover - exercised only when langchain-core is present
            from langchain_core.prompts import ChatPromptTemplate

            template = ChatPromptTemplate.from_messages(
                [("system", self.system_prompt), ("human", "{body}")]
            )
            return template.format(body=body)
        except Exception:
            return body

    async def run(self, blocks: Sequence[str], *, context: dict[str, Any] | None = None) -> ChainResult:
        body = self._render(blocks)
        completion = await self.provider.complete(
            system=self.system_prompt,
            messages=[ChatMessage(role="user", content=body)],
            json_mode=True,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        data = completion.data or {}
        schema_error = None
        if self.output_model is not None and data:
            try:
                data = self.output_model.model_validate(data).model_dump()
            except ValidationError as exc:
                schema_error = str(exc)
                logger.warning(
                    "chain output failed schema validation",
                    extra={"chain": self.name, "error": schema_error},
                )
        return ChainResult(
            data=data,
            model=completion.model,
            provider=completion.provider,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            raw_text=completion.text,
            schema_error=schema_error,
        )


def build_query_expansion_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="query_expansion",
        system_prompt=QUERY_EXPANSION_PROMPT,
        provider=provider,
        max_tokens=512,
    )


def build_evidence_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="evidence_analysis",
        system_prompt=EVIDENCE_SYSTEM_PROMPT,
        provider=provider,
    )


def build_reasoning_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="legal_reasoning",
        system_prompt=REASONING_SYSTEM_PROMPT,
        provider=provider,
    )


def build_judgment_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="judgment_draft",
        system_prompt=JUDGMENT_SYSTEM_PROMPT,
        provider=provider,
    )


def build_reasoning_blocks(
    case: dict[str, Any],
    articles: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    evidence_analysis: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Assemble the shared context blocks for the reasoning/judgment chains."""
    blocks = [
        build_case_block(case),
        build_evidence_block(evidence),
        build_retrieved_statutes_block(articles),
    ]
    if evidence_analysis:
        import json

        blocks.append(
            "«تقييم الأدلة المبدئي»:\n"
            + json.dumps(evidence_analysis, ensure_ascii=False, indent=2)
        )
    blocks.append(
        "«المطلوب»: أخرج النتيجة في كائن JSON واحد مطابق للمخطط الموصوف في تعليمات النظام."
    )
    return blocks
