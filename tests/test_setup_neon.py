"""The Neon provisioning helper must build the right connection-URI request.

``scripts/setup_neon.py`` is the documented way to link the platform to Neon.
Its API calls are the part that fails silently when a parameter is wrong, so the
request it builds is pinned here. No network: ``_neon_request`` is replaced.
"""
from __future__ import annotations

import pytest

from scripts import setup_neon


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
        calls.append((method, path))
        return {"uri": "postgresql://neondb_owner:pw@ep-x-pooler.neon.tech/neondb"}

    monkeypatch.setattr(setup_neon, "_neon_request", fake)
    return calls


def test_project_id_skips_the_name_lookup(captured: list[tuple[str, str]]) -> None:
    setup_neon.provision_neon("napi_x", "legal-ai-yemen", None, "rough-shape-45628230")
    assert len(captured) == 1
    method, path = captured[0]
    assert method == "GET"
    assert path.startswith("/projects/rough-shape-45628230/connection_uri?")


def test_branch_id_is_forwarded(captured: list[tuple[str, str]]) -> None:
    setup_neon.provision_neon(
        "napi_x", "legal-ai-yemen", None, "rough-shape-45628230", "br-lively-poetry-b745ltwg"
    )
    assert "branch_id=br-lively-poetry-b745ltwg" in captured[0][1]


def test_pooled_endpoint_is_requested(captured: list[tuple[str, str]]) -> None:
    setup_neon.provision_neon("napi_x", "legal-ai-yemen", None, "rough-shape-45628230")
    assert "pooled=true" in captured[0][1]


def test_existing_project_is_reused_by_name(captured: list[tuple[str, str]], monkeypatch) -> None:
    def fake(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
        captured.append((method, path))
        if method == "GET" and path == "/projects":
            return {"projects": [{"id": "rough-shape-45628230", "name": "legal-ai-yemen"}]}
        return {"uri": "postgresql://neondb_owner:pw@ep-x-pooler.neon.tech/neondb"}

    monkeypatch.setattr(setup_neon, "_neon_request", fake)
    uri = setup_neon.provision_neon("napi_x", "legal-ai-yemen", None)
    assert uri.startswith("postgresql://")
    assert [p for m, p in captured if m == "POST"] == []


def test_missing_uri_is_a_loud_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_neon, "_neon_request", lambda *a, **k: {})
    with pytest.raises(SystemExit):
        setup_neon.provision_neon("napi_x", "legal-ai-yemen", None, "rough-shape-45628230")
