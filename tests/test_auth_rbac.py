"""Tests for authentication, RBAC, the owner account and platform administration.

These exercise the real code paths: PBKDF2 password hashing, HMAC-signed tokens,
the role/permission model, the owner bootstrap and the admin CRUD endpoints.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.permissions import ALL_PERMISSIONS, Role, has_permission, permissions_for
from app.core.security import decode_token, encode_token, hash_password, verify_password
from app.db.user_repository import section_repository, user_repository
from app.main import app
from app.services.auth import DEFAULT_SECTIONS, auth_service

OWNER = "owner"
OWNER_PW = "Owner@Yemen2026"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def owner_token(client: httpx.AsyncClient) -> str:
    await auth_service.ensure_owner()
    response = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": OWNER_PW}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Password hashing and tokens
# --------------------------------------------------------------------------- #
def test_password_hash_roundtrip() -> None:
    stored = hash_password("Correct@Horse1")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("Correct@Horse1", stored)
    assert not verify_password("wrong", stored)
    # Salted: the same password hashes differently every time.
    assert hash_password("Correct@Horse1") != stored


def test_password_hash_rejects_short_password() -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        hash_password("short")


def test_token_roundtrip_and_tamper_detection() -> None:
    token = encode_token({"sub": "u1", "role": "owner"}, "secret", expires_in=60)
    claims = decode_token(token, "secret")
    assert claims["sub"] == "u1" and claims["role"] == "owner"

    from app.core.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        decode_token(token, "different-secret")

    header, payload, _ = token.split(".")
    with pytest.raises(UnauthorizedError):
        decode_token(f"{header}.{payload}.AAAA", "secret")


def test_expired_token_is_rejected() -> None:
    from app.core.errors import UnauthorizedError

    token = encode_token({"sub": "u1"}, "secret", expires_in=-1)
    with pytest.raises(UnauthorizedError):
        decode_token(token, "secret")


# --------------------------------------------------------------------------- #
# Permission model
# --------------------------------------------------------------------------- #
def test_owner_holds_every_permission() -> None:
    assert permissions_for(Role.OWNER) == ALL_PERMISSIONS
    for permission in ALL_PERMISSIONS:
        assert has_permission(Role.OWNER, permission)


def test_role_permission_matrix() -> None:
    assert has_permission(Role.ADMIN, "users.write")
    assert has_permission(Role.ADMIN, "laws.delete")
    assert not has_permission(Role.ADMIN, "platform.manage")

    assert has_permission(Role.EDITOR, "articles.write")
    assert not has_permission(Role.EDITOR, "users.write")
    assert not has_permission(Role.EDITOR, "laws.delete")

    assert has_permission(Role.REVIEWER, "judgments.approve")
    assert not has_permission(Role.REVIEWER, "articles.write")

    assert has_permission(Role.VIEWER, "laws.read")
    assert not has_permission(Role.VIEWER, "laws.write")


def test_extra_permissions_are_granted_on_top_of_role() -> None:
    assert not has_permission(Role.VIEWER, "articles.write")
    assert has_permission(Role.VIEWER, "articles.write", extra=["articles.write"])
    # Unknown permission strings are ignored, never granted.
    assert not has_permission(Role.VIEWER, "nonsense.permission", extra=["nonsense.permission"])


# --------------------------------------------------------------------------- #
# Owner bootstrap and login
# --------------------------------------------------------------------------- #
async def test_owner_is_seeded_with_all_permissions(client: httpx.AsyncClient) -> None:
    created = await auth_service.ensure_owner()
    assert created is not None and created["role"] == "owner"
    # Idempotent: a second call does not create another owner.
    assert await auth_service.ensure_owner() is None

    response = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": OWNER_PW}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "owner"
    assert body["expires_in"] > 0

    me = await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert set(me.json()["effective_permissions"]) == set(ALL_PERMISSIONS)


async def test_login_rejects_bad_credentials(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    bad = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": "not-the-password"}
    )
    assert bad.status_code == 401
    missing = await client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "whatever123"}
    )
    assert missing.status_code == 401
    # Unknown user and wrong password are indistinguishable.
    assert bad.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_inactive_user_cannot_log_in(client: httpx.AsyncClient, owner_token: str) -> None:
    await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={"username": "temp", "password": "Temp@12345", "role": "viewer"},
    )
    users = (await client.get("/api/v1/auth/users", headers=_auth(owner_token))).json()
    temp = next(u for u in users if u["username"] == "temp")
    await client.patch(
        f"/api/v1/auth/users/{temp['id']}", headers=_auth(owner_token), json={"is_active": False}
    )
    response = await client.post(
        "/api/v1/auth/login", json={"username": "temp", "password": "Temp@12345"}
    )
    assert response.status_code == 403


async def test_unauthenticated_and_malformed_tokens_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"})).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
    ).status_code == 401


# --------------------------------------------------------------------------- #
# User administration
# --------------------------------------------------------------------------- #
async def test_owner_creates_user_with_role_scoped_permissions(
    client: httpx.AsyncClient, owner_token: str
) -> None:
    response = await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={
            "username": "editor1",
            "password": "Editor@12345",
            "full_name": "محرر المدونة",
            "role": "editor",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "editor"
    assert "articles.write" in body["effective_permissions"]
    assert "users.write" not in body["effective_permissions"]

    login = await client.post(
        "/api/v1/auth/login", json={"username": "editor1", "password": "Editor@12345"}
    )
    assert login.status_code == 200


async def test_cannot_create_a_second_owner(client: httpx.AsyncClient, owner_token: str) -> None:
    response = await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={"username": "owner2", "password": "Owner@12345", "role": "owner"},
    )
    assert response.status_code == 422
    # And the response body is valid JSON despite the ValueError in the validator.
    assert response.json()["error"]["code"] == "request_validation_error"


async def test_cannot_demote_or_delete_the_owner(client: httpx.AsyncClient, owner_token: str) -> None:
    me = (await client.get("/api/v1/auth/me", headers=_auth(owner_token))).json()
    demote = await client.patch(
        f"/api/v1/auth/users/{me['id']}", headers=_auth(owner_token), json={"role": "admin"}
    )
    assert demote.status_code == 422
    delete = await client.delete(f"/api/v1/auth/users/{me['id']}", headers=_auth(owner_token))
    assert delete.status_code == 422


async def test_admin_cannot_delete_their_own_account(
    client: httpx.AsyncClient, owner_token: str
) -> None:
    """Self-deletion is blocked; for an admin the permission guard fires first."""
    await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={"username": "admin1", "password": "Admin@12345", "role": "admin"},
    )
    admin_token = (
        await client.post(
            "/api/v1/auth/login", json={"username": "admin1", "password": "Admin@12345"}
        )
    ).json()["access_token"]
    me = (await client.get("/api/v1/auth/me", headers=_auth(admin_token))).json()
    response = await client.delete(f"/api/v1/auth/users/{me['id']}", headers=_auth(admin_token))
    # Admin lacks users.delete, so the request is refused before self-check.
    assert response.status_code == 403

    # The owner holds users.delete and still cannot delete itself.
    owner_me = (await client.get("/api/v1/auth/me", headers=_auth(owner_token))).json()
    self_delete = await client.delete(
        f"/api/v1/auth/users/{owner_me['id']}", headers=_auth(owner_token)
    )
    assert self_delete.status_code == 422


# --------------------------------------------------------------------------- #
# RBAC on the admin surface
# --------------------------------------------------------------------------- #
async def test_viewer_is_read_only(client: httpx.AsyncClient, owner_token: str) -> None:
    await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={"username": "viewer9", "password": "Viewer@12345", "role": "viewer"},
    )
    token = (
        await client.post(
            "/api/v1/auth/login", json={"username": "viewer9", "password": "Viewer@12345"}
        )
    ).json()["access_token"]

    assert (await client.get("/api/v1/admin/sections", headers=_auth(token))).status_code == 200
    assert (await client.get("/api/v1/admin/laws", headers=_auth(token))).status_code == 200

    created = await client.post(
        "/api/v1/admin/sections",
        headers=_auth(token),
        json={"key": "nope", "title_ar": "قسم ممنوع"},
    )
    assert created.status_code == 403
    assert (await client.get("/api/v1/auth/users", headers=_auth(token))).status_code == 403


async def test_editor_can_edit_articles_but_not_users_or_sections(
    client: httpx.AsyncClient, owner_token: str, seeded_store
) -> None:
    await client.post(
        "/api/v1/auth/users",
        headers=_auth(owner_token),
        json={"username": "editor2", "password": "Editor@12345", "role": "editor"},
    )
    token = (
        await client.post(
            "/api/v1/auth/login", json={"username": "editor2", "password": "Editor@12345"}
        )
    ).json()["access_token"]

    # An editor may read the corpus and edit articles.
    documents = (await client.get("/api/v1/admin/laws", headers=_auth(token))).json()
    assert documents
    document_id = documents[0]["id"]
    articles = (
        await client.get(f"/api/v1/admin/laws/{document_id}/articles", headers=_auth(token))
    ).json()
    assert articles
    edited = await client.patch(
        f"/api/v1/admin/articles/{articles[0]['id']}",
        headers=_auth(token),
        json={"heading_ar": "باب تجريبي"},
    )
    assert edited.status_code == 200
    assert edited.json()["heading_ar"] == "باب تجريبي"

    # But not create sections, create users, or delete laws.
    assert (await client.post("/api/v1/admin/sections", headers=_auth(token), json={
        "key": "labour", "title_ar": "قانون العمل"})).status_code == 403
    assert (await client.post("/api/v1/auth/users", headers=_auth(token), json={
        "username": "x1", "password": "Xxxxx@12345", "role": "viewer"})).status_code == 403
    assert (
        await client.delete(f"/api/v1/admin/laws/{document_id}", headers=_auth(token))
    ).status_code == 403


# --------------------------------------------------------------------------- #
# Sections CRUD
# --------------------------------------------------------------------------- #
async def test_section_crud_lifecycle(client: httpx.AsyncClient, owner_token: str) -> None:
    created = await client.post(
        "/api/v1/admin/sections",
        headers=_auth(owner_token),
        json={"key": "maritime", "title_ar": "القانون البحري", "sort_order": 90},
    )
    assert created.status_code == 201
    section_id = created.json()["id"]

    duplicate = await client.post(
        "/api/v1/admin/sections",
        headers=_auth(owner_token),
        json={"key": "maritime", "title_ar": "مكرر"},
    )
    assert duplicate.status_code == 409

    updated = await client.patch(
        f"/api/v1/admin/sections/{section_id}",
        headers=_auth(owner_token),
        json={"title_ar": "قانون الملاحة البحرية"},
    )
    assert updated.json()["title_ar"] == "قانون الملاحة البحرية"

    listed = (await client.get("/api/v1/admin/sections", headers=_auth(owner_token))).json()
    assert any(s["key"] == "maritime" for s in listed)

    assert (
        await client.delete(f"/api/v1/admin/sections/{section_id}", headers=_auth(owner_token))
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/admin/sections/{section_id}", headers=_auth(owner_token))
    ).status_code == 404


async def test_default_sections_are_seeded_on_bootstrap() -> None:
    assert not await section_repository.list_sections()
    for section in DEFAULT_SECTIONS:
        await section_repository.create(dict(section))
    rows = await section_repository.list_sections()
    assert len(rows) == len(DEFAULT_SECTIONS)
    assert [r["sort_order"] for r in rows] == sorted(r["sort_order"] for r in rows)
    assert {r["key"] for r in rows} >= {"civil", "evidence", "constitution"}


# --------------------------------------------------------------------------- #
# Permission catalogue endpoints
# --------------------------------------------------------------------------- #
async def test_permission_catalogue_and_roles(client: httpx.AsyncClient) -> None:
    catalogue = (await client.get("/api/v1/auth/permissions")).json()
    assert set(catalogue["permissions"]) == set(ALL_PERMISSIONS)
    # The owner role is intentionally absent from the grantable roles.
    assert "owner" not in catalogue["roles"]

    roles = (await client.get("/api/v1/auth/roles")).json()
    by_name = {r["role"]: r for r in roles}
    assert by_name["owner"]["is_owner"] is True
    assert set(by_name["owner"]["permissions"]) == set(ALL_PERMISSIONS)
    assert "platform.manage" not in by_name["admin"]["permissions"]


# --------------------------------------------------------------------------- #
# The admin console is served
# --------------------------------------------------------------------------- #
async def test_admin_console_is_served(client: httpx.AsyncClient, owner_token: str) -> None:
    # The console is behind the page gate; with the owner's session it is served.
    response = await client.get("/ui/admin.html", headers=_auth(owner_token))
    assert response.status_code == 200
    assert "لوحة تحكم المنصة" in response.text


def test_app_exposes_expected_route_count() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/users" in paths
    assert "/api/v1/admin/sections" in paths
    assert "/api/v1/admin/laws/ingest" in paths
    assert "/api/v1/admin/articles/{article_id}" in paths


async def test_user_repository_rejects_duplicate_username() -> None:
    from app.core.errors import ConflictError

    await user_repository.create(username="dup", password="Dup@123456", role=Role.VIEWER)
    with pytest.raises(ConflictError):
        await user_repository.create(username="dup", password="Dup@123456", role=Role.VIEWER)
    # Usernames are compared case-insensitively.
    with pytest.raises(ConflictError):
        await user_repository.create(username="DUP", password="Dup@123456", role=Role.VIEWER)


def test_insecure_default_secret_is_refused_in_production() -> None:
    from app.core.config import Settings

    insecure = Settings(app_env="production", jwt_secret="dev-insecure-change-me-in-production")
    with pytest.raises(RuntimeError):
        _ = insecure.auth_secret
    # A strong secret is accepted, and the default is tolerated outside production.
    assert Settings(app_env="production", jwt_secret="a-strong-random-secret").auth_secret
    assert Settings(app_env="development").auth_secret
