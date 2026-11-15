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
