"""Provider behaviour the pipeline depends on: retries, and offline grounding."""
from __future__ import annotations

import pytest

from app.core.errors import LLMError
from app.llm import prompts
from app.llm.chains import LegalChain, _is_retryable, _retry_delay
from app.llm.provider import ChatMessage, Completion, MockLLMProvider


class _FlakyProvider:
    """Fails ``failures`` times with a retryable error, then succeeds."""

    name = "flaky"
    model = "flaky"

    def __init__(self, failures: int = 2, details: str = "Error code: 429") -> None:
        self.failures = failures
        self.details = details
        self.calls = 0

    async def complete(self, **_kwargs) -> Completion:
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError("rate limited", details=self.details)
        return Completion(text='{"ok": true}', data={"ok": True}, model=self.model, provider=self.name)


@pytest.mark.asyncio
async def test_retries_a_transient_failure_then_succeeds():
    provider = _FlakyProvider(failures=2)
    chain = LegalChain(name="t", system_prompt="s", provider=provider)
    result = await chain.run(["body"])
    assert provider.calls == 3
    assert result.data == {"ok": True}


@pytest.mark.asyncio
async def test_does_not_retry_a_client_error():
    """A 400 will fail again; retrying only wastes time and tokens."""
    provider = _FlakyProvider(failures=99, details="Error code: 400 - invalid schema")
    chain = LegalChain(name="t", system_prompt="s", provider=provider)
    with pytest.raises(LLMError):
        await chain.run(["body"])
    assert provider.calls == 1


def test_status_code_is_read_from_details_not_the_message():
    """The provider status lives in ``details``; the message is a generic Arabic string."""
    real = LLMError(
        "فشل استدعاء نموذج الذكاء الاصطناعي",
        details="Error code: 402 - Free limit reached. Retry after 26s",
    )
    assert _is_retryable(real)
    assert _retry_delay(real, 0) == 27.0
    assert not _is_retryable(LLMError("x", details="Error code: 400"))


def test_anthropic_provider_omits_temperature_when_the_sdk_rejects_it():
    """anthropic>=2 removed ``temperature`` from messages.create.

    The provider probes the installed SDK's signature; when the parameter is
    gone the call must not carry it, or every request dies with a TypeError
    before it reaches the network.
    """
    import inspect

    from app.llm.provider import _accepts

    class _Old:
        async def create(self, *, model, system, messages, temperature, max_tokens): ...

    class _New:
        async def create(self, *, model, system, messages, max_tokens, output_config=None): ...

    assert _accepts(_Old().create, "temperature") is True
    assert _accepts(_New().create, "temperature") is False
    # Guard the premise: the SDK really does hide the parameter from callers.
    assert "temperature" in inspect.signature(_Old.create).parameters


@pytest.mark.asyncio
async def test_mock_judgment_is_grounded_in_the_rendered_case_block():
    """The offline provider must echo case facts and cite only retrieved statutes.

    The preamble, the narrative and every verdict item's legal basis have to be
    recoverable from the prompt the chain builds, so the demo and CI path never
    drifts from the real grounding contract.
    """
    case = {
        "case_number": "123/2026",
        "plaintiff_name": "أحمد",
        "defendant_name": "محمد",
        "court_name": "المحكمة الابتدائية",
        "subject_matter": "مطالبة بدين",
        "case_type": "مدني",
        "claim_value": 500000,
        "currency": "YER",
        "facts_summary": "امتنع المدعى عليه عن السداد",
    }
    articles = [
        {
            "article_label": "المادة (146)",
            "law_title": "القانون المدني",
            "content": "يجب الوفاء بالالتزامات.",
        }
    ]
    body = prompts.build_case_block(case) + "\n\n" + prompts.build_retrieved_statutes_block(
        articles
    )
    completion = await MockLLMProvider().complete(
        system=prompts.JUDGMENT_SYSTEM_PROMPT,
        messages=[ChatMessage(role="user", content=body)],
    )
    data = completion.data
    assert data is not None
    assert data["preamble"]["plaintiff"]["name"] == "أحمد"
    assert data["preamble"]["defendant"]["name"] == "محمد"
    assert data["preamble"]["case_number"] == "123/2026"
    assert data["preamble"]["subject_matter"] == "مطالبة بدين"
    assert "أحمد" in data["facts"]["narrative"]
    assert "امتنع المدعى عليه عن السداد" in data["facts"]["narrative"]
    for item in data["verdict"]["items"]:
        assert item["legal_basis"], item
        assert all(basis == "المادة (146)" for basis in item["legal_basis"]), item


@pytest.mark.asyncio
async def test_embedding_provider_retries_a_rate_limited_batch(monkeypatch):
    """Re-indexing must survive a free-tier 429 instead of dying mid-corpus.

    A partial corpus is worse than a failed run: half the rows would carry
    vectors from a different model, and cosine search would silently degrade.
    """
    from app.llm import embeddings as embeddings_module

    class _RateLimited(Exception):
        pass

    class _Resp:
        def __init__(self, n: int) -> None:
            self.data = [type("D", (), {"embedding": [0.1] * 4})() for _ in range(n)]

    class _Embeddings:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _RateLimited("Error code: 429 - quota exceeded, please retry in 1s")
            return _Resp(2)

    class _Client:
        def __init__(self) -> None:
            self.embeddings = _Embeddings()

    provider = embeddings_module.OpenAIEmbeddingProvider.__new__(
        embeddings_module.OpenAIEmbeddingProvider
    )
    provider.model = "test-embedding"
    provider.dim = 4
    provider._client = _Client()
    monkeypatch.setattr(embeddings_module.settings, "llm_retry_base_delay_seconds", 0.01)

    vectors = await provider.embed_documents(["نص أول", "نص ثان"])
    assert provider._client.embeddings.calls == 2
    assert len(vectors) == 2


def test_chain_injects_output_schema_into_the_request():
    """The model must be told the exact JSON keys, in English.

    A hosted model that follows the Arabic instructions literally returns
    Arabic keys ("الديباجة") that the English schema cannot read; the draft
    then silently degrades to empty fallbacks with a grounding score of zero.
    """
    from app.llm.chains import LegalChain
    from app.llm.outputs import QueryExpansionOutput

    chain = LegalChain(
        name="t", system_prompt="s", output_model=QueryExpansionOutput
    )
    block = chain._schema_block()
    assert "queries" in block
    assert "أسماء المفاتيح" in block or "الإلزامي" in block


@pytest.mark.asyncio
async def test_chain_sends_the_schema_block_to_the_provider():
    """The schema must reach the provider payload, not just exist as a method."""
    from app.llm.chains import LegalChain
    from app.llm.outputs import QueryExpansionOutput

    seen: dict[str, str] = {}

    class _Recorder:
        name = "recorder"
        model = "recorder"

        async def complete(self, *, system, messages, **_kwargs):
            seen["prompt"] = "\n".join(m.content for m in messages)
            return Completion(text='{"queries": ["x"]}', data={"queries": ["x"]}, model=self.model, provider=self.name)

    chain = LegalChain(
        name="t", system_prompt="s", output_model=QueryExpansionOutput, provider=_Recorder()
    )
    await chain.run(["body"])
    assert "queries" in seen["prompt"]


# ---------------------------------------------------------------------------
# Offline fallback: the engine must keep drafting with no internet.
# ---------------------------------------------------------------------------
class _UnreachableProvider:
    """A hosted provider that cannot be reached (no internet)."""

    name = "hosted"
    model = "hosted-model"

    def __init__(self, details: str = "Connection error.") -> None:
        self.details = details
        self.calls = 0

    async def complete(self, **_kwargs) -> Completion:
        self.calls += 1
        raise LLMError("فشل استدعاء نموذج الذكاء الاصطناعي", details=self.details)


@pytest.mark.asyncio
async def test_offline_fallback_answers_when_the_hosted_model_is_unreachable():
    """No internet -> the deterministic reasoner answers instead of a 502."""
    from app.llm.provider import FallbackLLMProvider

    primary = _UnreachableProvider("Connection error: getaddrinfo failed")
    provider = FallbackLLMProvider(primary)
    completion = await provider.complete(
        system=prompts.JUDGMENT_SYSTEM_PROMPT,
        messages=[ChatMessage(role="user", content="«الوقائع»: لا شيء")],
    )
    assert primary.calls == 1
    assert completion.data is not None
    assert completion.provider == "hosted+offline"


@pytest.mark.asyncio
async def test_offline_fallback_does_not_mask_a_model_error():
    """A reachable provider rejecting the request is a real fault, not offline."""
    from app.llm.provider import FallbackLLMProvider

    primary = _UnreachableProvider("Error code: 401 - invalid api key")
    provider = FallbackLLMProvider(primary)
    with pytest.raises(LLMError):
        await provider.complete(
            system="s", messages=[ChatMessage(role="user", content="x")]
        )


def test_offline_failure_detection():
    from app.llm.provider import is_offline_failure

    assert is_offline_failure(LLMError("x", details="Connection error."))
    assert is_offline_failure(LLMError("x", details="Name or service not known"))
    assert is_offline_failure(LLMError("x", details="Request timed out"))
    assert not is_offline_failure(LLMError("x", details="Error code: 400 - bad request"))
    assert not is_offline_failure(LLMError("x", details="Error code: 429 - rate limited"))


@pytest.mark.asyncio
async def test_judgment_chain_still_drafts_offline(seeded_store):
    """End to end: an unreachable hosted model still yields a grounded draft."""
    from app.llm.provider import FallbackLLMProvider
    from app.services import judgment as judgment_module

    chain = judgment_module.build_judgment_chain(provider=FallbackLLMProvider(_UnreachableProvider()))
    service = judgment_module.JudgmentService()
    service._chain = chain

    case = {
        "case_number": "1445/2026",
        "case_type": "مدني",
        "court_name": "المحكمة الابتدائية - صنعاء",
        "subject_matter": "مطالبة بتعويض",
        "plaintiff_name": "أحمد",
        "defendant_name": "محمد",
        "facts_summary": "أخل المدعى عليه بالالتزام.",
    }
    articles = [
        {
            "article_label": "المادة (146)",
            "law_title": "القانون المدني",
            "law_number": "14/2002",
            "content": "يجب الوفاء بالالتزامات.",
            "source_type": "law",
            "article_number": "146",
        }
    ]
    envelope, meta = await service.generate(
        case=case, evidence=[], articles=articles, evidence_analysis=[]
    )
    assert envelope.draft.verdict.items, "the offline path must still produce rulings"
    assert envelope.draft.preamble.case_number == "1445/2026"
    assert envelope.grounding_score is not None
    assert meta["provider"].endswith("+offline")
