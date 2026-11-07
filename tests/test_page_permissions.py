"""Tests for the Harvey-style per-page permissions.

The page gate is enforced server-side (a gated page redirects a caller who is
not signed in, or whose role does not unlock the page) and mirrored client-side.
These tests exercise the real middleware and the real permission registry, and
assert that the owner keeps full access to every page.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.permissions import (
    ALL_PERMISSIONS,
    PAGE_PERMISSIONS,
    PUBLIC_PAGES,
    Role,
    can_open_page,
    has_permission,
    page_permission,
    permissions_for,
)
from app.db.user_repository import user_repository
from app.main import app
from app.services.auth import auth_service

OWNER = "owner"
OWNER_PW = "Owner@Yemen2026"

GATED_PAGES = sorted(PAGE_PERMISSIONS)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_every_page_permission_is_in_the_catalogue() -> None:
    for permission in PAGE_PERMISSIONS.values():
        assert permission in ALL_PERMISSIONS, permission


def test_owner_can_open_every_page() -> None:
    for path in GATED_PAGES:
        assert can_open_page({"role": "owner"}, path), path


def test_page_permission_ignores_unknown_and_public_paths() -> None:
    assert page_permission("/ui/portal.css") is None
    assert page_permission("/api/v1/health") is None
    for path in PUBLIC_PAGES:
        assert page_permission(path) is None


def test_each_role_reaches_only_its_own_portal() -> None:
    judge = {"role": "judge"}
    assert can_open_page(judge, "/ui/judge.html")
    assert can_open_page(judge, "/ui/workspace.html")
    assert not can_open_page(judge, "/ui/admin.html")
    assert not can_open_page(judge, "/ui/lawyer.html")

    lawyer = {"role": "lawyer"}
    assert can_open_page(lawyer, "/ui/lawyer.html")
    assert not can_open_page(lawyer, "/ui/judge.html")

    researcher = {"role": "researcher"}
    assert can_open_page(researcher, "/ui/researcher.html")
    assert not can_open_page(researcher, "/ui/prosecution.html")


def test_extra_permission_opens_an_otherwise_denied_page() -> None:
    viewer = {"role": "viewer"}
    assert not can_open_page(viewer, "/ui/judge.html")
    granted = {"role": "viewer", "permissions": ["portal.judge"]}
    assert can_open_page(granted, "/ui/judge.html")


def test_owner_role_holds_every_page_permission() -> None:
    owner = permissions_for(Role.OWNER)
    assert owner == ALL_PERMISSIONS
    for permission in PAGE_PERMISSIONS.values():
        assert has_permission(Role.OWNER, permission), permission


# --------------------------------------------------------------------------- #
# Server-side gate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", GATED_PAGES)
async def test_gated_page_redirects_anonymous_to_login(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path)
    assert response.status_code == 302, path
    assert response.headers["location"].startswith("/ui/login.html")


@pytest.mark.parametrize("path", GATED_PAGES)
async def test_owner_reaches_every_gated_page(client: httpx.AsyncClient, path: str) -> None:
    await auth_service.ensure_owner()
    token = await _login(client, OWNER, OWNER_PW)
    response = await client.get(path, headers=_auth(token))
    assert response.status_code == 200, path


async def test_role_is_redirected_from_a_foreign_portal(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    await user_repository.create(username="judge_pg", password="Judge@12345", role=Role.JUDGE)
    token = await _login(client, "judge_pg", "Judge@12345")

    # The judge's own portal is served...
    assert (await client.get("/ui/judge.html", headers=_auth(token))).status_code == 200
    # ...but the admin console and the lawyer portal are refused with a redirect.
    forbidden = await client.get("/ui/admin.html", headers=_auth(token))
    assert forbidden.status_code == 302
    assert forbidden.headers["location"] == "/ui/forbidden.html"
    assert (await client.get("/ui/lawyer.html", headers=_auth(token))).status_code == 302


async def test_session_cookie_alone_opens_a_gated_page(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    # Logging in sets the HttpOnly cookie; the follow-up request sends no bearer
    # token, so the page is served purely from the cookie.
    await _login(client, OWNER, OWNER_PW)
    response = await client.get("/ui/judge.html")
    assert response.status_code == 200


async def test_logout_clears_the_session_cookie(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    await _login(client, OWNER, OWNER_PW)
    assert (await client.get("/ui/judge.html")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/ui/judge.html")).status_code == 302


async def test_public_pages_stay_open(client: httpx.AsyncClient) -> None:
    for path in ("/ui/landing.html", "/ui/index.html", "/ui/login.html", "/ui/forbidden.html"):
        assert (await client.get(path)).status_code == 200, path


# --------------------------------------------------------------------------- #
# Admin permission matrix
# --------------------------------------------------------------------------- #
async def test_page_permission_matrix_lists_every_page(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    token = await _login(client, OWNER, OWNER_PW)
    data = (await client.get("/api/v1/admin/page-permissions", headers=_auth(token))).json()
    paths = {p["path"] for p in data["pages"]}
    assert paths == set(PAGE_PERMISSIONS)
    roles = {r["role"] for r in data["roles"]}
    assert "owner" in roles and "judge" in roles


async def test_page_permission_matrix_requires_admin_page(client: httpx.AsyncClient) -> None:
    await auth_service.ensure_owner()
    await user_repository.create(username="lawyer_pm", password="Lawyer@123", role=Role.LAWYER)
    token = await _login(client, "lawyer_pm", "Lawyer@123")
    response = await client.get("/api/v1/admin/page-permissions", headers=_auth(token))
    assert response.status_code == 403
