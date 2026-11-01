"""A transient provider failure must not discard a drafted judgment."""
from __future__ import annotations

import pytest

from app.core.errors import LLMError
from app.llm.chains import LegalChain, _is_retryable, _retry_delay
from app.llm.provider import Completion


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
