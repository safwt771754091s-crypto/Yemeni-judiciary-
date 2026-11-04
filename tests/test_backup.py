"""Tests for platform backup and restore (النسخة الاحتياطية).

They exercise the real export/restore code paths against the in-memory stores,
including a round trip that proves data survives export then import.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.permissions import Role
from app.db.user_repository import user_repository
from app.main import app
from app.services.auth import auth_service
from app.services.backup import backup_service

OWNER = "owner"
OWNER_PW = "Owner@Yemen2026"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _owner_token(client: httpx.AsyncClient) -> str:
    await auth_service.ensure_owner()
    response = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": OWNER_PW}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
async def test_backup_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/backup/summary")).status_code == 401
    assert (await client.get("/api/v1/backup/export")).status_code == 401


async def test_backup_forbidden_for_low_privilege(client: httpx.AsyncClient) -> None:
    await user_repository.create(username="viewer_b", password="Secret@12345", role=Role.VIEWER)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "viewer_b", "password": "Secret@12345"}
    )
    token = login.json()["access_token"]
    assert (await client.get("/api/v1/backup/summary", headers=_auth(token))).status_code == 403


async def test_backup_allowed_for_owner(client: httpx.AsyncClient) -> None:
    token = await _owner_token(client)
    response = await client.get("/api/v1/backup/summary", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "yemen-legal-backup"
    assert body["counts"]["users"] >= 1


# --------------------------------------------------------------------------- #
# Export / restore round trip
# --------------------------------------------------------------------------- #
async def test_export_contains_users_without_plaintext_passwords(
    client: httpx.AsyncClient,
) -> None:
    token = await _owner_token(client)
    response = await client.get("/api/v1/backup/export", headers=_auth(token))
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers.get("content-disposition", "")
    snapshot = response.json()
    assert snapshot["format"] == "yemen-legal-backup"
    assert snapshot["includes_credentials"] is True
    users = snapshot["data"]["users"]
    assert any(u["username"] == OWNER for u in users)
    owner = next(u for u in users if u["username"] == OWNER)
    # A hash is present, but never the plaintext password.
    assert owner["password_hash"].startswith("pbkdf2_sha256$")
    assert "Owner@Yemen2026" not in response.text


async def test_export_without_credentials_omits_hashes(client: httpx.AsyncClient) -> None:
    token = await _owner_token(client)
    response = await client.get(
        "/api/v1/backup/export?include_credentials=false", headers=_auth(token)
    )
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["includes_credentials"] is False
    assert all("password_hash" not in u for u in snapshot["data"]["users"])


async def test_restore_round_trip_preserves_users(client: httpx.AsyncClient) -> None:
    token = await _owner_token(client)
    created = await user_repository.create(
        username="editor_rt", password="Secret@12345", role=Role.EDITOR
    )
    editor_id = created["id"]

    exported = (await client.get("/api/v1/backup/export", headers=_auth(token))).json()
    usernames_before = {u["username"] for u in exported["data"]["users"]}
    assert "editor_rt" in usernames_before

    # Remove the editor, then restore the snapshot — it must come back.
    await user_repository.delete(editor_id)
    assert "editor_rt" not in {u["username"] for u in user_repository._users.values()}

    restored = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={"snapshot": exported, "replace": False},
    )
    assert restored.status_code == 200, restored.text
    assert {u["username"] for u in user_repository._users.values()} == usernames_before


async def test_restore_dry_run_writes_nothing(client: httpx.AsyncClient) -> None:
    token = await _owner_token(client)
    created = await user_repository.create(
        username="editor_dry", password="Secret@12345", role=Role.EDITOR
    )
    exported = (await client.get("/api/v1/backup/export", headers=_auth(token))).json()
    await user_repository.delete(created["id"])

    result = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={"snapshot": exported, "dry_run": True},
    )
    assert result.status_code == 200
    assert result.json()["dry_run"] is True
    # A dry run must not write anything: the deleted user stays deleted.
    assert "editor_dry" not in {u["username"] for u in user_repository._users.values()}


async def test_restore_rejects_a_foreign_file(client: httpx.AsyncClient) -> None:
    token = await _owner_token(client)
    response = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={"snapshot": {"hello": "world"}},
    )
    assert response.status_code == 422, response.text


async def test_backup_service_export_is_json_serialisable() -> None:
    import json

    snapshot = await backup_service.export(include_credentials=True)
    blob = backup_service.export_bytes(snapshot)
    # Must round-trip through JSON without a custom encoder.
    assert json.loads(blob.decode("utf-8"))["format"] == "yemen-legal-backup"
