"""Tests for platform backup and restore (النسخة الاحتياطية).

They exercise the real export/restore code paths against the in-memory stores,
including a round trip that proves data survives export then import.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

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


# --------------------------------------------------------------------------- #
# Serialisation of the types only a real PostgreSQL connection produces
# --------------------------------------------------------------------------- #
class _FakeRecord:
    """Stand-in for ``asyncpg.Record``: key/value-addressable, not a dict.

    The real type is a C extension that cannot be constructed directly, but it
    exposes ``keys()`` and ``__getitem__`` and is neither a ``dict`` nor a
    ``Mapping`` — the exact shape that broke a database export.
    """

    def __init__(self, **values: object) -> None:
        self._values = values

    def keys(self) -> list[str]:
        return list(self._values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]


def test_jsonable_converts_a_record_like_row() -> None:
    from app.services.backup import _jsonable

    row = _FakeRecord(id=uuid.uuid4(), key="civil", created_at=datetime.now(UTC))
    out = _jsonable(row)
    assert isinstance(out, dict)
    assert isinstance(out["id"], str)
    assert isinstance(out["created_at"], str)


def test_jsonable_converts_decimal_and_date() -> None:
    from app.services.backup import _jsonable

    assert _jsonable(Decimal("0.60")) == 0.6
    assert _jsonable(date(2026, 11, 4)) == "2026-11-04"
    # A bytes column (bytea) must not break the encoder either.
    assert _jsonable(b"abc") == "abc"


def test_export_bytes_handles_database_typed_rows() -> None:
    """A snapshot carrying Record-like rows and Decimals must serialise.

    Regression: ``POST /backup/export`` returned 500 with
    ``Unable to serialize unknown type: <class 'asyncpg.Record'>`` because the
    in-memory stores used by the tests only ever hold plain dicts and floats.
    """
    import json

    from app.schemas.backup import BackupSnapshot
    from app.services.backup import _jsonable, backup_service

    snapshot = BackupSnapshot(
        created_at=datetime.now(UTC),
        app_name="test",
        data={
            "sections": [_jsonable(_FakeRecord(id=uuid.uuid4(), key="civil"))],
            "judgments": _jsonable(
                [{"id": str(uuid.uuid4()), "confidence": Decimal("0.60")}]
            ),
        },
    )
    decoded = json.loads(backup_service.export_bytes(snapshot).decode("utf-8"))
    assert isinstance(decoded["data"]["sections"][0]["id"], str)
    assert decoded["data"]["judgments"][0]["confidence"] == 0.6


def test_as_datetime_coerces_snapshot_timestamps() -> None:
    """Restore must hand asyncpg real datetimes, not ISO strings.

    Regression: a replace-restore into PostgreSQL failed with ``invalid input
    for query argument ... (expected a datetime.date or datetime.datetime
    instance, got 'str')`` because the snapshot stores timestamps as strings.
    """
    from app.services.backup import _as_datetime

    parsed = _as_datetime("2026-11-04T18:26:48.932012+00:00")
    assert isinstance(parsed, datetime)
    assert parsed.year == 2026
    assert _as_datetime("2026-11-04T18:26:48Z") is not None
    # A non-string passes through unchanged; junk becomes None.
    assert _as_datetime(None) is None
    assert _as_datetime("not-a-date") is None
