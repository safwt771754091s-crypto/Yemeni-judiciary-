"""Tests for the public statute search (by article number or text fragment).

The feature must answer both online (via /api/v1/retrieval/articles, backed by
the in-memory store in the test suite) and offline (via the shipped
/ui/legal-index.json that the browser searches). These tests cover both halves
plus the static index the offline path depends on.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.text_utils import normalize_arabic
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
INDEX = STATIC / "legal-index.json"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- #
# Endpoint — article number
# --------------------------------------------------------------------------- #
async def test_search_by_article_number(client: httpx.AsyncClient, seeded_store) -> None:
    response = await client.get("/api/v1/retrieval/articles", params={"number": "1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 1
    for hit in body["results"]:
        assert hit["article_number"].lstrip("0") == "1"


async def test_search_by_arabic_digits(client: httpx.AsyncClient, seeded_store) -> None:
    """Arabic-Indic digits must resolve to the same article as Western ones."""
    western = await client.get("/api/v1/retrieval/articles", params={"number": "2"})
    arabic = await client.get("/api/v1/retrieval/articles", params={"number": "٢"})
    assert arabic.status_code == 200
    assert arabic.json()["count"] == western.json()["count"] >= 1
    assert {h["article_id"] for h in arabic.json()["results"]} == {
        h["article_id"] for h in western.json()["results"]
    }


async def test_number_match_excludes_other_numbers(
    client: httpx.AsyncClient, seeded_store
) -> None:
    """A number query must not leak articles that merely mention the number."""
    response = await client.get(
        "/api/v1/retrieval/articles", params={"number": "10", "limit": 50}
    )
    body = response.json()
    assert body["count"] >= 1
    assert all(h["article_number"].lstrip("0") == "10" for h in body["results"])


async def test_leading_zeros_are_ignored(client: httpx.AsyncClient, seeded_store) -> None:
    """«0146» and «146» name the same article."""
    plain = await client.get("/api/v1/retrieval/articles", params={"number": "146"})
    padded = await client.get("/api/v1/retrieval/articles", params={"number": "0146"})
    assert padded.status_code == 200
    assert padded.json()["count"] == plain.json()["count"] >= 1
    assert {h["article_id"] for h in padded.json()["results"]} == {
        h["article_id"] for h in plain.json()["results"]
    }


# --------------------------------------------------------------------------- #
# Endpoint — text fragment
# --------------------------------------------------------------------------- #
async def test_search_by_text_fragment(client: httpx.AsyncClient, seeded_store) -> None:
    response = await client.get(
        "/api/v1/retrieval/articles", params={"q": "البينة على المدعي"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 1
    joined = normalize_arabic(body["results"][0]["content"])
    assert "البينه علي المدعي" in joined or "البينة على المدعي" in body["results"][0]["content"]


async def test_text_search_is_ranked_by_overlap(
    client: httpx.AsyncClient, seeded_store
) -> None:
    """The best text hit must rank first, so the result order is meaningful."""
    response = await client.get(
        "/api/v1/retrieval/articles", params={"q": "عقد البيع تسليم المبيع", "limit": 10}
    )
    body = response.json()
    assert body["count"] >= 2
    # Scores are non-increasing (the store sorts by lexical relevance).
    scores = [h["fused_score"] for h in body["results"]]
    assert scores == sorted(scores, reverse=True)


async def test_text_and_number_combined(client: httpx.AsyncClient, seeded_store) -> None:
    response = await client.post(
        "/api/v1/retrieval/articles",
        json={"query": "الإثبات", "article_number": "1", "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    # A number was supplied, so every hit is that number.
    assert all(h["article_number"].lstrip("0") == "1" for h in body["results"])


async def test_empty_query_returns_nothing(client: httpx.AsyncClient, seeded_store) -> None:
    response = await client.get("/api/v1/retrieval/articles")
    assert response.status_code == 200
    assert response.json()["count"] == 0


async def test_source_type_filter(client: httpx.AsyncClient, seeded_store) -> None:
    response = await client.get(
        "/api/v1/retrieval/articles",
        params={"q": "المدعي", "source_type": "evidence_law", "limit": 20},
    )
    body = response.json()
    assert body["count"] >= 1
    assert all(h["source_type"] == "evidence_law" for h in body["results"])


async def test_search_needs_no_embedding(client: httpx.AsyncClient, seeded_store) -> None:
    """The lookup is deterministic: it must not depend on the embedding model.

    Embedding is monkeypatched to explode; a number search must still succeed.
    """
    from app.services.retrieval import retrieval_service

    original = retrieval_service.embedder.embed_query

    async def _boom(_text: str) -> list[float]:
        raise AssertionError("embedding must not be called for an article lookup")

    retrieval_service.embedder.embed_query = _boom  # type: ignore[assignment]
    try:
        response = await client.get("/api/v1/retrieval/articles", params={"number": "5"})
        assert response.status_code == 200
        assert response.json()["count"] >= 1
    finally:
        retrieval_service.embedder.embed_query = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Offline index
# --------------------------------------------------------------------------- #
def test_offline_index_exists_and_is_valid() -> None:
    assert INDEX.is_file(), "run scripts/build_search_index.py"
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    assert data["version"] >= 1
    assert data["count"] == len(data["articles"]) > 0


def test_offline_index_article_shape() -> None:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    article = data["articles"][0]
    for key in ("i", "n", "l", "t", "s", "c"):
        assert key in article, key
    assert article["c"].strip()


def test_offline_index_has_searchable_numbers() -> None:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    numbers = {a["n"] for a in data["articles"] if a["n"]}
    # The bundled corpus has article 1 in every code it covers.
    assert "1" in numbers


def test_service_worker_caches_the_offline_index() -> None:
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "/ui/legal-index.json" in sw
    assert "/ui/statute-search.js" in sw


def test_landing_page_exposes_the_search() -> None:
    html = (STATIC / "landing.html").read_text(encoding="utf-8")
    assert "statute-search.js" in html
    assert 'id="sNum"' in html and 'id="sText"' in html and 'id="sResults"' in html
    assert "legal-index.json" in (STATIC / "statute-search.js").read_text(encoding="utf-8")
