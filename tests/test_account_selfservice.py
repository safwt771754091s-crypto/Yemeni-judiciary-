"""Tests for account self-service: password reset, email verification, Google.

Everything runs offline. The ``console`` mail provider is the default, so no
mail server is needed; the reset/verify links it would have sent are returned in
the response outside production, which is exactly what these tests consume.
"""
from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.services.account import account_service
from app.services.auth import auth_service


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signup(**overrides) -> dict:
    base = {
        "username": "reset.user",
        "password": "Trial12345",
        "full_name": "سالم علي محمد",
        "email": "reset.user@example.com",
        "role": "lawyer",
    }
    base.update(overrides)
    return base


async def _create_account(client: httpx.AsyncClient, **overrides) -> dict:
    response = await client.post("/api/v1/auth/signup", json=_signup(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
async def test_forgot_password_returns_a_link_outside_production(
    client: httpx.AsyncClient,
) -> None:
    await _create_account(client)
    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "reset.user@example.com"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["link"], "the console provider should expose the link in dev"
    assert "/ui/reset.html?token=" in body["link"]
    assert body["expires_in_minutes"] > 0


async def test_forgot_password_does_not_reveal_unknown_email(
    client: httpx.AsyncClient,
) -> None:
    """An unknown address gets the same shape of answer as a known one."""
    unknown = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 200
    body = unknown.json()
    assert body["ok"] is True
    assert body["link"] is None, "no link must be produced for an unknown address"


async def test_reset_password_with_a_valid_token(client: httpx.AsyncClient) -> None:
    await _create_account(client)
    forgot = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "reset.user@example.com"}
    )
    token = forgot.json()["link"].split("token=", 1)[1]

    reset = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": "NewPass123"}
    )
    assert reset.status_code == 200, reset.text

    # The old password no longer works and the new one does.
    old = await client.post(
        "/api/v1/auth/login", json={"username": "reset.user", "password": "Trial12345"}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login", json={"username": "reset.user", "password": "NewPass123"}
    )
    assert new.status_code == 200


async def test_reset_token_is_single_use(client: httpx.AsyncClient) -> None:
    await _create_account(client)
    forgot = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "reset.user@example.com"}
    )
    token = forgot.json()["link"].split("token=", 1)[1]

    first = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": "NewPass123"}
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": "ThirdPass1"}
    )
    assert second.status_code == 422


async def test_reset_password_rejects_a_bad_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-real-token-value", "password": "NewPass123"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("weak", ["short1", "12345678", "abcdefgh"])
async def test_reset_password_enforces_the_policy(
    client: httpx.AsyncClient, weak: str
) -> None:
    response = await client.post(
        "/api/v1/auth/reset-password", json={"token": "x" * 20, "password": weak}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Email verification
# --------------------------------------------------------------------------- #
async def test_signup_sends_a_verification_link(client: httpx.AsyncClient) -> None:
    await _create_account(client)
    # The signup response itself carries no link, but a token now exists.
    response = await client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": "Bearer "
                 + (await client.post(
                     "/api/v1/auth/login",
                     json={"username": "reset.user", "password": "Trial12345"},
                 )).json()["access_token"]},
    )
    assert response.status_code == 200, response.text
    assert "/ui/verify.html?token=" in response.json()["link"]


async def test_verify_email_marks_the_account(client: httpx.AsyncClient) -> None:
    await _create_account(client)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "reset.user", "password": "Trial12345"}
    )
    token = login.json()["access_token"]
    resend = await client.post(
        "/api/v1/auth/resend-verification", headers={"Authorization": f"Bearer {token}"}
    )
    verify_token = resend.json()["link"].split("token=", 1)[1]

    verified = await client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verified.status_code == 200, verified.text

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email_verified"] is True


async def test_verify_email_rejects_a_bad_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/verify-email", json={"token": "not-a-real-token-value"}
    )
    assert response.status_code == 422


async def test_verification_required_blocks_login_until_confirmed(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.core.config import settings

    await _create_account(client)
    monkeypatch.setattr(settings, "require_email_verification", True)

    blocked = await client.post(
        "/api/v1/auth/login", json={"username": "reset.user", "password": "Trial12345"}
    )
    assert blocked.status_code == 403
    assert "تأكيد" in blocked.json()["error"]["message"]

    # Confirming the address lifts the block.
    token, _ = auth_service.issue_token(
        await account_service.users.get_by_username("reset.user")
    )
    resend = await client.post(
        "/api/v1/auth/resend-verification", headers={"Authorization": f"Bearer {token}"}
    )
    await client.post(
        "/api/v1/auth/verify-email",
        json={"token": resend.json()["link"].split("token=", 1)[1]},
    )
    allowed = await client.post(
        "/api/v1/auth/login", json={"username": "reset.user", "password": "Trial12345"}
    )
    assert allowed.status_code == 200


# --------------------------------------------------------------------------- #
# Email login
# --------------------------------------------------------------------------- #
async def test_login_with_email_address(client: httpx.AsyncClient) -> None:
    await _create_account(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "reset.user@example.com", "password": "Trial12345"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "reset.user"


async def test_email_login_can_be_disabled(client: httpx.AsyncClient, monkeypatch) -> None:
    from app.core.config import settings

    await _create_account(client)
    monkeypatch.setattr(settings, "allow_email_login", False)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "reset.user@example.com", "password": "Trial12345"},
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Google sign-in
# --------------------------------------------------------------------------- #
async def test_google_reports_disabled_when_unconfigured(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/google")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


async def test_google_authorize_redirects_when_disabled(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/google/authorize", follow_redirects=False)
    assert response.status_code == 302
    assert "google_disabled" in response.headers["location"]


async def test_google_authorize_builds_the_consent_url(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    response = await client.get("/api/v1/auth/google/authorize", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid.apps.googleusercontent.com" in location
    assert "state=" in location
    # The state is also stored in an HttpOnly cookie for the callback check.
    assert "legal_oauth_state" in response.headers.get("set-cookie", "")


async def test_google_callback_rejects_a_missing_state(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/google/callback?code=abc", follow_redirects=False
    )
    assert response.status_code == 302
    assert "google_state" in response.headers["location"]


async def test_google_login_links_an_existing_verified_email(
    client: httpx.AsyncClient,
) -> None:
    """A verified Google address attaches to the existing local account."""
    created = await _create_account(client)
    user = await account_service.login_with_google(
        {"sub": "google-123", "email": "reset.user@example.com", "email_verified": True,
         "name": "سالم علي"}
    )
    assert str(user["id"]) == created["user"]["id"]

    # A second sign-in with the same subject resolves to the same account.
    again = await account_service.login_with_google(
        {"sub": "google-123", "email": "reset.user@example.com", "email_verified": True}
    )
    assert str(again["id"]) == created["user"]["id"]


async def test_google_login_does_not_take_over_on_unverified_email(
    client: httpx.AsyncClient,
) -> None:
    """An unverified Google claim must not attach to a local account."""
    from app.core.errors import UnauthorizedError

    created = await _create_account(client)
    with pytest.raises(UnauthorizedError):
        await account_service.login_with_google(
            {"sub": "google-999", "email": "reset.user@example.com", "email_verified": False}
        )
    # The local account is untouched.
    still = await account_service.users.get_by_email("reset.user@example.com")
    assert str(still["id"]) == created["user"]["id"]


async def test_google_login_creates_a_new_account(client: httpx.AsyncClient) -> None:
    user = await account_service.login_with_google(
        {"sub": "google-new", "email": "brand.new@example.com", "email_verified": True,
         "name": "New Google User"}
    )
    assert user["email"] == "brand.new@example.com"
    assert user["email_verified"] is True
    assert user["role"] == "viewer"
    # A second account with a colliding seed gets a unique username.
    other = await account_service.login_with_google(
        {"sub": "google-new2", "email": "brand.new@example.com".replace("brand", "brand2"),
         "email_verified": True}
    )
    assert other["username"] != user["username"]


# --------------------------------------------------------------------------- #
# New pages are public and served
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("page", ["/ui/forgot.html", "/ui/reset.html", "/ui/verify.html"])
async def test_self_service_pages_are_public_and_served(
    client: httpx.AsyncClient, page: str
) -> None:
    response = await client.get(page, follow_redirects=False)
    assert response.status_code == 200, page
    assert "text/html" in response.headers["content-type"]
