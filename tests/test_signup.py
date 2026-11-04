"""Tests for self-service account creation (POST /auth/signup).

Exercises the real service path against the in-memory user repository: role
clamping, uniqueness of username and email, password policy, the returned
session and the role-based portal redirect.
"""
from __future__ import annotations

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _payload(**overrides) -> dict:
    base = {
        "username": "new.lawyer",
        "password": "Trial12345",
        "full_name": "أحمد محمد صالح",
        "email": "new.lawyer@example.com",
        "role": "lawyer",
    }
    base.update(overrides)
    return base


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_signup_creates_account_and_returns_session(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/signup", json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["access_token"]
    assert body["user"]["username"] == "new.lawyer"
    assert body["user"]["role"] == "lawyer"
    assert body["user"]["role_label_ar"] == "محامٍ"
    assert body["role_portal"] == "lawyer"
    assert body["role_portal_page"] == "/ui/lawyer.html"

    # The issued token authenticates immediately.
    me = await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "new.lawyer"


async def test_signup_defaults_to_viewer_when_role_omitted(client: httpx.AsyncClient) -> None:
    payload = _payload(role=None)
    payload.pop("role")
    response = await client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["user"]["role"] == "viewer"


@pytest.mark.parametrize("role", ["owner", "admin", "editor"])
async def test_signup_rejects_privileged_roles(client: httpx.AsyncClient, role: str) -> None:
    response = await client.post("/api/v1/auth/signup", json=_payload(username=f"x.{role}", role=role))
    assert response.status_code == 403, response.text


async def test_signup_rejects_duplicate_username(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/auth/signup", json=_payload())).status_code == 201
    again = await client.post(
        "/api/v1/auth/signup", json=_payload(email="other@example.com")
    )
    assert again.status_code == 409
    assert "اسم المستخدم" in again.json()["error"]["message"]


async def test_signup_rejects_duplicate_email(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/auth/signup", json=_payload())).status_code == 201
    again = await client.post(
        "/api/v1/auth/signup", json=_payload(username="someone.else")
    )
    assert again.status_code == 409
    assert "البريد" in again.json()["error"]["message"]


@pytest.mark.parametrize(
    "bad",
    [
        {"password": "short1"},          # too short
        {"password": "12345678"},        # digits only
        {"password": "abcdefgh"},        # letters only
        {"email": "not-an-email"},       # malformed email
        {"full_name": " "},              # empty name
        {"username": "ab"},              # too short
        {"username": "bad name!"},       # illegal characters
    ],
)
async def test_signup_validates_input(client: httpx.AsyncClient, bad: dict) -> None:
    response = await client.post("/api/v1/auth/signup", json=_payload(**bad))
    assert response.status_code == 422, response.text


async def test_signup_options_lists_portal_roles(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/signup")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    roles = {r["role"] for r in body["roles"]}
    assert roles == {"viewer", "researcher", "lawyer", "judge", "prosecutor"}
    # No privileged role is ever offered.
    assert roles.isdisjoint({"owner", "admin", "editor"})


async def test_signup_can_be_disabled(client: httpx.AsyncClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_self_signup", False)
    response = await client.post("/api/v1/auth/signup", json=_payload())
    assert response.status_code == 403
    assert "التسجيل الذاتي" in response.json()["error"]["message"]
