"""Contract checks on the generated OpenAPI schema.

The 204 regression: FastAPI asserts that a 204 response carries no body, and
resolves a `-> None` return annotation to NoneType when the module uses
`from __future__ import annotations`. That combination made importing app.main
raise, which broke collection for the whole suite. These checks fail fast and
name the offending route instead.
"""
from __future__ import annotations

from app.core.config import settings
from app.main import app


def test_app_imports_and_exposes_routes() -> None:
    paths = app.openapi()["paths"]
    prefix = settings.api_prefix
    assert f"{prefix}/cases/submit" in paths
    assert f"{prefix}/retrieval/search" in paths


def test_no_content_routes_declare_no_response_body() -> None:
    offenders = []
    for route in app.routes:
        status_code = getattr(route, "status_code", None)
        if status_code != 204:
            continue
        response_model = getattr(route, "response_model", None)
        if response_model is not None:
            offenders.append(f"{route.path} declares response_model={response_model!r}")
    assert not offenders, "204 routes must not declare a response body: " + "; ".join(offenders)
