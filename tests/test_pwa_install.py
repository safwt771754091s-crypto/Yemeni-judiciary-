"""Tests for the installable PWA ("add to home screen" on Android).

These exercise the real served files: the manifest, the root-scoped service
worker, the shared install runtime and the install buttons wired into every
page. They are static checks — the suite stays offline and needs no browser.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

#: Every page that must offer "install the app".
INSTALL_PAGES = [
    "index.html",
    "judge.html",
    "lawyer.html",
    "prosecution.html",
    "researcher.html",
    "assistant.html",
    "admin.html",
    "intake.html",
]


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def test_manifest_is_installable() -> None:
    manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    # Android/Chrome requires these to offer the native install prompt.
    assert manifest["name"]
    assert manifest["short_name"]
    assert manifest["start_url"]
    assert manifest["display"] in {"standalone", "fullscreen", "minimal-ui"}
    assert manifest["theme_color"] and manifest["background_color"]
    assert manifest["dir"] == "rtl" and manifest["lang"] == "ar"


def test_manifest_has_required_icon_sizes() -> None:
    manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    sizes = {(i["sizes"], i["purpose"]) for i in manifest["icons"]}
    # A 192 and a 512 "any" icon plus a maskable icon are the install minimum.
    assert ("192x192", "any") in sizes
    assert ("512x512", "any") in sizes
    assert any(purpose == "maskable" for _, purpose in sizes)


def test_manifest_icons_exist_on_disk() -> None:
    manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        rel = icon["src"].replace("/ui/", "")
        path = STATIC / rel
        assert path.is_file(), f"missing icon {icon['src']}"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{rel} is not a PNG"


# --------------------------------------------------------------------------- #
# Install runtime
# --------------------------------------------------------------------------- #
def test_install_runtime_captures_prompt_and_exposes_api() -> None:
    source = (STATIC / "install.js").read_text(encoding="utf-8")
    # The one-shot event must be captured at load, not only inside a button.
    assert "beforeinstallprompt" in source
    assert "window.PWAInstall" in source
    for member in ("wireButton", "promptInstall", "isInstalled", "showHelp", "register"):
        assert member in source, f"PWAInstall.{member} missing"


def test_portal_runtime_delegates_to_the_shared_installer() -> None:
    source = (STATIC / "portal.js").read_text(encoding="utf-8")
    # Single source of truth: portal.js must not re-implement install logic.
    assert "window.PWAInstall" in source
    assert "beforeinstallprompt" not in source


@pytest.mark.parametrize("page", INSTALL_PAGES)
def test_every_page_loads_the_install_runtime(page: str) -> None:
    html = (STATIC / page).read_text(encoding="utf-8")
    assert "/ui/install.js" in html, f"{page} does not load install.js"


@pytest.mark.parametrize("page", INSTALL_PAGES)
def test_every_page_has_an_install_button(page: str) -> None:
    html = (STATIC / page).read_text(encoding="utf-8")
    # Static pages carry the button in markup; the role portals render their
    # shell (including the button) from portal-app.js, so accept either.
    has_static = 'id="installBtn"' in html
    renders_from_shell = "/ui/portal-app.js" in html and 'id="installBtn"' in (
        STATIC / "portal-app.js"
    ).read_text(encoding="utf-8")
    assert has_static or renders_from_shell, f"{page} has no install button"


def test_service_worker_caches_the_install_runtime() -> None:
    source = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "/ui/install.js" in source


# --------------------------------------------------------------------------- #
# Served over HTTP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path",
    ["/manifest.json", "/sw.js", "/ui/install.js", "/ui/icons/icon-192.png",
     "/ui/icons/icon-512.png", "/ui/icons/icon-maskable-512.png"],
)
async def test_install_assets_are_served(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200, path


async def test_service_worker_has_root_scope_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/sw.js")
    # Without this header the worker could only claim /ui/, not the whole origin,
    # which breaks offline start-up for the installed app.
    assert response.headers.get("service-worker-allowed") == "/"


async def test_manifest_served_with_correct_media_type(client: httpx.AsyncClient) -> None:
    response = await client.get("/manifest.json")
    assert response.headers["content-type"].startswith("application/manifest+json")
