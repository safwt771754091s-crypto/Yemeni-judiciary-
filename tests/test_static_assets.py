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
