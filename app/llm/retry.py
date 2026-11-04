"""Shared retry policy for provider calls (chat completions and embeddings).

A transient provider failure - a rate limit, a timeout, a 5xx - must not abort
a long-running job. Re-indexing the corpus is the clearest case: it issues one
embedding request per batch, so a free-tier limit of 100 requests/minute fails
the whole run halfway through unless the caller backs off and retries.
"""
from __future__ import annotations

import re

from app.core.config import settings

#: Provider errors that a retry can plausibly fix (rate limits, timeouts, 5xx).
RETRYABLE_RE = re.compile(
    r"\b(timeout|timed out|temporarily|overloaded|unavailable|too many requests|"
    r"rate.?limit|free.?limit|connection reset|retry)\b",
    re.IGNORECASE,
)
STATUS_RE = re.compile(r"(?:error\s+code|status(?:\s+code)?|http)[:\s]*(\d{3})", re.IGNORECASE)
#: HTTP statuses worth retrying: rate limits, request timeout, and any 5xx.
#: 402 is included because free-tier providers report a resetting quota as
#: "Payment Required" (e.g. "free limit reached, retry after 45s").
RETRYABLE_STATUSES = frozenset({402, 408, 409, 425, 429})
#: "Retry after 26s" / "retry_after: 26" / Google's "Please retry in 16.5s" -
#: honour the provider's own delay instead of guessing.
RETRY_AFTER_RE = re.compile(
    r"retry[_ ](?:after|in)[\"':\s]+(\d+(?:\.\d+)?)", re.IGNORECASE
)


def error_text(exc: Exception) -> str:
    """Message plus details: the status code and "retry after" hint live in details."""
    details = getattr(exc, "details", None)
    return f"{exc} {details}" if details else str(exc)


def is_retryable(exc: Exception) -> bool:
    """True for a transient failure.

    An explicit HTTP status decides it when present, so a client error (400)
    that happens to mention "rate limit" in its body is not retried. Only when
    no status is available do the keyword heuristics apply.
    """
    text = error_text(exc)
    match = STATUS_RE.search(text)
    if match:
        code = int(match.group(1))
        if 400 <= code < 500:
            return code in RETRYABLE_STATUSES
        if code >= 500:
            return True
    return bool(RETRYABLE_RE.search(text))


def retry_delay(exc: Exception, attempt: int) -> float:
    """Backoff for a transient failure, respecting a provider "retry after" hint."""
    match = RETRY_AFTER_RE.search(error_text(exc))
    if match:
        return min(float(match.group(1)) + 1.0, settings.llm_retry_max_delay_seconds)
    return min(
        settings.llm_retry_base_delay_seconds * (2**attempt),
        settings.llm_retry_max_delay_seconds,
    )
