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

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.llm.outputs import (
    AssistantOutput,
    ClassificationOutput,
    DraftOutput,
    EvidenceAnalysisOutput,
    QueryExpansionOutput,
    ReasoningOutput,
)
from app.llm.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    CLASSIFICATION_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    EVIDENCE_SYSTEM_PROMPT,
    JUDGMENT_SYSTEM_PROMPT,
    QUERY_EXPANSION_PROMPT,
    REASONING_SYSTEM_PROMPT,
    build_case_block,
    build_evidence_block,
    build_retrieved_statutes_block,
)
from app.llm.provider import ChatMessage, Completion, LLMProvider, llm_provider
from app.llm.retry import error_text, is_retryable, retry_delay
from app.schemas.judgment import JudgmentDraft

logger = get_logger(__name__)


class _ChainOutput(BaseModel):
    """Marker base class for chain outputs."""


# The retry policy is shared with the embedding provider; these aliases keep the
# existing chain call sites (and tests) working.
_error_text = error_text
_is_retryable = is_retryable
_retry_delay = retry_delay


def _resolve_ref(root: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Follow a Pydantic ``$ref`` into ``$defs`` so nested shapes can be rendered."""
    ref = spec.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return root.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
    return spec


def _type_hint(root: dict[str, Any], spec: dict[str, Any]) -> str:
    spec = _resolve_ref(root, spec)
    if "anyOf" in spec:
        options = [_type_hint(root, s) for s in spec["anyOf"] if s.get("type") != "null"]
        return " | ".join(dict.fromkeys(options)) or "أي"
    kind = spec.get("type")
    if kind == "array":
        return f"قائمة[{_type_hint(root, spec.get('items', {}))}]"
    if kind == "object":
        return "كائن"
    return {"string": "نص", "number": "رقم", "integer": "عدد", "boolean": "منطقي"}.get(
        kind or "", "أي"
    )


def _render_properties(
    root: dict[str, Any], node: dict[str, Any], lines: list[str], indent: int, depth: int
) -> None:
    if depth > 3:
        return
    required = set(node.get("required", []))
    pad = "  " * indent
    for name, raw in node.get("properties", {}).items():
        spec = _resolve_ref(root, raw)
        marker = "مطلوب" if name in required else "اختياري"
        description = spec.get("description") or ""
        lines.append(f"{pad}- {name} ({marker}, {_type_hint(root, spec)}): {description}")
        nested = _resolve_ref(root, spec.get("items", {})) if spec.get("type") == "array" else spec
        if nested.get("type") == "object" and nested.get("properties"):
            _render_properties(root, nested, lines, indent + 1, depth + 1)


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

    def _schema_block(self) -> str:
        """Render the expected JSON shape so the model cannot guess the keys.

        Models follow a concrete skeleton far more reliably than the prose
        instruction "match the schema", and the English key names stay stable
        for the validator. Nested objects are rendered up to a shallow depth;
        the top-level keys are what a wrong-language response breaks.
        """
        schema = self.output_model.model_json_schema()
        lines = [
            "«مخطط المخرجات الإلزامي»: أخرج كائن JSON واحداً بهذه المفاتيح بأسمائها",
            "الإنجليزية تماماً كما هي — لا تترجم أسماء المفاتيح إلى العربية:",
        ]
        _render_properties(schema, schema, lines, 0, 0)
        return "\n".join(lines)

    async def run(self, blocks: Sequence[str], *, context: dict[str, Any] | None = None) -> ChainResult:
        blocks = list(blocks)
        if self.output_model is not None:
            blocks.append(self._schema_block())
        body = self._render(blocks)
        completion = await self._complete_with_retry(body)
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

    async def _complete_with_retry(self, body: str) -> Completion:
        """Call the provider, retrying transient failures with exponential backoff.

        A rate-limited or briefly unavailable provider must not lose a drafted
        judgment: the pipeline runs four sequential calls per case, so one 429
        would otherwise discard the whole run.
        """
        last: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                return await self.provider.complete(
                    system=self.system_prompt,
                    messages=[ChatMessage(role="user", content=body)],
                    json_mode=True,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
            except LLMError as exc:
                last = exc
                if attempt >= settings.llm_max_retries or not _is_retryable(exc):
                    raise
                delay = _retry_delay(exc, attempt)
                logger.warning(
                    "transient LLM failure, retrying",
                    extra={"chain": self.name, "attempt": attempt + 1, "delay_s": delay},
                )
                await asyncio.sleep(delay)
        raise last if last else LLMError("فشل استدعاء النموذج دون تفاصيل")


def build_query_expansion_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="query_expansion",
        system_prompt=QUERY_EXPANSION_PROMPT,
        provider=provider,
        output_model=QueryExpansionOutput,
        max_tokens=512,
    )


def build_classification_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="case_classification",
        system_prompt=CLASSIFICATION_PROMPT,
        provider=provider,
        output_model=ClassificationOutput,
        max_tokens=512,
    )


def build_evidence_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="evidence_analysis",
        system_prompt=EVIDENCE_SYSTEM_PROMPT,
        provider=provider,
        output_model=EvidenceAnalysisOutput,
    )


def build_reasoning_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="legal_reasoning",
        system_prompt=REASONING_SYSTEM_PROMPT,
        provider=provider,
        output_model=ReasoningOutput,
    )


def build_judgment_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="judgment_draft",
        system_prompt=JUDGMENT_SYSTEM_PROMPT,
        provider=provider,
        output_model=JudgmentDraft,
    )


def build_assistant_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="legal_assistant",
        system_prompt=ASSISTANT_SYSTEM_PROMPT,
        provider=provider,
        output_model=AssistantOutput,
    )


def build_draft_chain(provider: LLMProvider | None = None) -> LegalChain:
    return LegalChain(
        name="legal_draft",
        system_prompt=DRAFT_SYSTEM_PROMPT,
        provider=provider,
        output_model=DraftOutput,
        max_tokens=3072,
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
