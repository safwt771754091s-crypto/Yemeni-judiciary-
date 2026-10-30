"""n8n integration: inbound shared-secret check and outbound HMAC signing."""
from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.services import n8n as n8n_module
from app.services.n8n import N8NClient


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_verify_secret_compares_the_shared_secret_verbatim():
    client = N8NClient()
    client.secret = "s3cret"

    assert client.verify_secret("s3cret") is True
    assert client.verify_secret("wrong") is False
    assert client.verify_secret(None) is False
    # The raw secret must never be accepted as an HMAC signature of itself.
    assert client.verify(b"s3cret", "s3cret") is False


def test_verify_secret_is_permissive_when_unconfigured():
    client = N8NClient()
    client.secret = None

    assert client.verify_secret(None) is True
    assert client.verify_secret("anything") is True


def test_outbound_signature_round_trips():
    client = N8NClient()
    client.secret = "s3cret"
    body = b'{"event":"judgment.created"}'

    signature = client.sign(body)
    assert signature and client.verify(body, signature) is True
    assert client.verify(body, "deadbeef") is False
    assert client.verify(b"tampered", signature) is False


async def test_submit_enforces_n8n_secret_when_configured(
    client, sample_case_payload, monkeypatch
):
    monkeypatch.setattr(n8n_module.n8n_client, "secret", "s3cret")

    accepted = await client.post(
        "/api/v1/cases/submit",
        json=sample_case_payload,
        headers={"X-N8N-Secret": "s3cret"},
    )
    assert accepted.status_code == 201, accepted.text

    rejected = await client.post(
        "/api/v1/cases/submit",
        json=sample_case_payload,
        headers={"X-N8N-Secret": "not-the-secret"},
    )
    assert rejected.status_code == 401
