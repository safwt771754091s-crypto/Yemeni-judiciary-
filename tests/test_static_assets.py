"""Static assets the install page depends on must be served with the right type.

An APK served as text/plain is refused by some Android browsers, which reads to
the user as "the download does nothing".
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_apk_is_served_as_an_android_package():
    client = TestClient(create_app())
    resp = client.get("/ui/downloads/yemeni-legal-ai.apk")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.android.package-archive"
    )
    assert resp.content[:2] == b"PK", "an APK is a zip archive"


def test_install_page_links_the_apk():
    client = TestClient(create_app())
    html = client.get("/ui/install.html").text
    assert "/ui/downloads/yemeni-legal-ai.apk" in html


def test_statute_index_is_served_precompressed():
    """The 3.8 MB offline index must not be re-gzipped on every request.

    Compressing it per request cost ~0.5 s of CPU on the event loop and
    serialised concurrent visitors, so the server hands over the pre-built
    ``.gz`` instead.
    """
    client = TestClient(create_app())
    resp = client.get("/ui/legal-index.json", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    # CORS appends Origin, so assert membership rather than equality.
    assert "Accept-Encoding" in resp.headers["vary"]
    # The declared length is the compressed size, far below the raw JSON.
    assert int(resp.headers["content-length"]) < 1_000_000
    # httpx transparently decompresses, so the body is the usable index.
    assert resp.json()["count"] > 0


def test_statute_index_falls_back_to_plain_json_without_gzip():
    client = TestClient(create_app())
    resp = client.get("/ui/legal-index.json", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.content[:1] == b"{"


def test_immutable_assets_are_cached_for_a_year():
    """Icons and fonts must not be re-fetched on every page load.

    Starlette's default ``max-age=0, must-revalidate`` made the browser (and
    Vercel's edge) re-request every icon and script on every navigation, which
    turns a page load into a cold start per asset.
    """
    client = TestClient(create_app())
    for path in ("/ui/icons/icon-192.png", "/ui/icons/icon-512.png"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable", path


def test_scripts_and_stylesheets_are_cached_at_the_edge():
    """Scripts and stylesheets must be served from the CDN, not the function.

    They keep their filename across deploys, so the browser still revalidates
    them; ``s-maxage`` lets the edge answer that revalidation instead of waking
    the serverless function on every visit.
    """
    client = TestClient(create_app())
    for path in ("/ui/portal.js", "/ui/portal.css", "/ui/statute-search.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        cache_control = resp.headers["cache-control"]
        assert "s-maxage=" in cache_control, (path, cache_control)
        assert "max-age=0" in cache_control, (path, cache_control)


def test_html_pages_stay_revalidated():
    """A page must never be served stale from cache after a deploy."""
    client = TestClient(create_app())
    for path in ("/", "/ui/admin.html"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "must-revalidate" in resp.headers["cache-control"], path


def test_landing_page_defers_the_offline_index():
    """The 3.9 MB index must not be fetched during the first paint.

    Warming it synchronously blocked the landing page's first render; it is now
    deferred to an idle callback while the first search still loads it on demand.
    """
    client = TestClient(create_app())
    html = client.get("/").text
    assert "requestIdleCallback" in html
    assert "loadIndex()" in html
