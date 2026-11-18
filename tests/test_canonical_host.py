"""The duplicate-host redirect keeps a single authoritative deployment.

Two extra Vercel projects deploy this same code but carry no database
credentials, so each answers from the in-memory store and loses every account on
a cold start. Requests that arrive under those names must be sent to the official
host instead of being served locally.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings, settings
from app.main import app

CANONICAL = "yemeni-judiciary.vercel.app"
DUPLICATES = (
    "yemeni-judiciary-j5et.vercel.app",
    "yemeni-judiciary-ii94.vercel.app",
)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _canonical_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the settings the middleware reads, independent of the .env file."""
    monkeypatch.setattr(settings, "canonical_host", CANONICAL)
    monkeypatch.setattr(settings, "redirect_hosts", ",".join(DUPLICATES))


async def _get(client: httpx.AsyncClient, path: str, host: str) -> httpx.Response:
    """A GET carrying an explicit Host header, which httpx would otherwise set from the URL."""
    request = client.build_request("GET", path)
    request.headers["host"] = host
    return await client.send(request)


async def test_the_official_host_is_never_redirected(client: httpx.AsyncClient) -> None:
    """A redirect loop here would take the whole platform down."""
    response = await _get(client, "/api/v1/health/ready", CANONICAL)
    assert response.status_code == 200


@pytest.mark.parametrize("host", DUPLICATES)
async def test_a_duplicate_host_is_redirected(client: httpx.AsyncClient, host: str) -> None:
    response = await _get(client, "/api/v1/health/ready", host)
    assert response.status_code == 308
    assert response.headers["location"] == f"https://{CANONICAL}/api/v1/health/ready"


async def test_the_redirect_keeps_the_path_and_query(client: httpx.AsyncClient) -> None:
    """A deep link must survive the move, not land on the home page."""
    response = await _get(client, "/ui/login.html?next=%2Fui%2Fadmin.html", DUPLICATES[0])
    assert response.status_code == 308
    assert (
        response.headers["location"]
        == f"https://{CANONICAL}/ui/login.html?next=%2Fui%2Fadmin.html"
    )


async def test_a_port_in_the_host_does_not_defeat_the_match(client: httpx.AsyncClient) -> None:
    response = await _get(client, "/api/v1/health/ready", f"{DUPLICATES[0]}:443")
    assert response.status_code == 308


async def test_an_unlisted_host_is_served_locally(client: httpx.AsyncClient) -> None:
    """A Vercel preview URL is a new host every time, so it must keep working."""
    response = await _get(client, "/api/v1/health/ready", "yemeni-judiciary-git-main.vercel.app")
    assert response.status_code == 200


async def test_nothing_redirects_when_no_canonical_host_is_set(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-hosted and test runs stay reachable under their own name."""
    monkeypatch.setattr(settings, "canonical_host", None)
    response = await _get(client, "/api/v1/health/ready", DUPLICATES[0])
    assert response.status_code == 200


async def test_a_preflight_is_answered_not_redirected(client: httpx.AsyncClient) -> None:
    """A redirected preflight fails the CORS check, so it must be handled here."""
    request = client.build_request("OPTIONS", "/api/v1/health/ready")
    request.headers["host"] = DUPLICATES[0]
    response = await client.send(request)
    assert response.status_code != 308


async def test_the_forwarded_host_decides_behind_a_proxy(client: httpx.AsyncClient) -> None:
    """Vercel may rewrite Host, so the public name in X-Forwarded-Host is what counts."""
    request = client.build_request("GET", "/api/v1/health/ready")
    request.headers["host"] = "10.0.0.5"
    request.headers["x-forwarded-host"] = f"{DUPLICATES[1]}, internal.vercel.app"
    response = await client.send(request)
    assert response.status_code == 308
    assert response.headers["location"] == f"https://{CANONICAL}/api/v1/health/ready"


def test_the_official_host_is_not_in_the_redirect_list() -> None:
    """The shipped defaults must not redirect the platform to itself."""
    assert settings.canonical_host == CANONICAL
    assert CANONICAL not in settings.redirect_host_names
    assert set(DUPLICATES).issubset(settings.redirect_host_names)


def test_a_comma_separated_host_list_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """REDIRECT_HOSTS=a,b must load; pydantic-settings alone would demand JSON."""
    monkeypatch.setenv("REDIRECT_HOSTS", "a.vercel.app, b.vercel.app")
    fresh = Settings()
    assert fresh.redirect_host_names == {"a.vercel.app", "b.vercel.app"}
