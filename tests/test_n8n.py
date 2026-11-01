"""n8n integration: token auth and signed outbound delivery.

The receiver is a real HTTP server on localhost, not a mock: the test exercises
the actual httpx call, the HMAC signing and the header contract end to end.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from app.core.config import settings
from app.services.n8n import N8NClient

SECRET = "n8n-test-secret"


class _Receiver(BaseHTTPRequestHandler):
    """Records each POST body and the signature header it arrived with."""

    received: ClassVar[list[tuple[dict, str]]] = []
    status = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).received.append(
            (json.loads(body.decode("utf-8")), self.headers.get("X-Legal-Signature", ""))
        )
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *_args) -> None:  # keep the test output clean
        pass


@pytest.fixture
def receiver():
    _Receiver.received = []
    _Receiver.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Receiver
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def configured_n8n(monkeypatch):
    monkeypatch.setattr(settings, "n8n_enabled", True)
    monkeypatch.setattr(settings, "n8n_hmac_secret", SECRET)
    return SECRET


# --------------------------------------------------------------------------- #
# Inbound auth
# --------------------------------------------------------------------------- #
def test_verify_token_accepts_the_configured_secret(configured_n8n):
    client = N8NClient()
    assert client.verify_token(SECRET)


def test_verify_token_rejects_a_wrong_secret(configured_n8n):
    client = N8NClient()
    assert not client.verify_token("wrong")
    assert not client.verify_token(None)
    assert not client.verify_token("")


def test_inbound_auth_is_open_when_no_secret_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "n8n_hmac_secret", None)
    assert N8NClient().verify_token(None)


def test_token_check_does_not_hash_the_value(configured_n8n):
    """Regression: inbound used HMAC(secret, secret) vs secret, which never matches.

    n8n echoes the shared secret verbatim, so the check must be a plain
    constant-time comparison of the token, not a signature over it.
    """
    client = N8NClient()
    hashed = hmac.new(SECRET.encode(), SECRET.encode(), hashlib.sha256).hexdigest()
    assert client.verify_token(SECRET)
    assert not client.verify_token(hashed)


# --------------------------------------------------------------------------- #
# Outbound delivery
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_emit_judgment_delivers_a_signed_payload(receiver, configured_n8n):
    server, handler = receiver
    settings.n8n_webhook_url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    client = N8NClient()

    ok = await client.emit_judgment(
        "case-1",
        {
            "grounding_score": 0.83,
            "warnings": ["تنبيه"],
            "citations": [{"article_label": "المادة (243)"}],
            "draft": {"verdict": {"summary": "حكمت المحكمة"}},
        },
    )

    assert ok
    assert len(handler.received) == 1
    payload, signature = handler.received[0]
    assert payload["event"] == "judgment.created"
    assert payload["data"]["case_id"] == "case-1"
    assert payload["data"]["verdict"] == {"summary": "حكمت المحكمة"}

    # The signature must verify over the exact body that was sent.
    body = json.dumps(
        {"event": payload["event"], "sent_at": payload["sent_at"], "data": payload["data"]},
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert signature == expected
    assert client.verify_signature(body, signature)


@pytest.mark.asyncio
async def test_emit_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "n8n_enabled", False)
    assert await N8NClient().emit_judgment("case-1", {}) is False


@pytest.mark.asyncio
async def test_a_failing_webhook_does_not_raise(receiver, configured_n8n):
    """Delivery is best-effort: a broken n8n must not fail the API request."""
    server, handler = receiver
    handler.status = 500
    settings.n8n_webhook_url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    assert await N8NClient().emit_judgment("case-1", {}) is False
