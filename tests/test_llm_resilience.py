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
