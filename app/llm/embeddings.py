"""Embedding providers.

* ``HashEmbeddingProvider`` - deterministic, offline, zero-dependency. It uses a
  feature-hashing (hashing trick) bag-of-ngrams projection. It is NOT a semantic
  model, but it gives stable, reproducible vectors that make the whole pipeline
  testable without network access; the lexical half of the hybrid retriever
  (Arabic FTS + trigram) compensates for its lack of semantics.
* ``OpenAIEmbeddingProvider`` - real semantic embeddings, recommended for
  production (``text-embedding-3-large`` with ``dimensions=EMBEDDING_DIM``).
"""
from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Sequence
from typing import Protocol

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic

logger = get_logger(__name__)


class EmbeddingProvider(Protocol):
    model: str
    dim: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    """Deterministic hashing-trick embeddings over words, bigrams and 4-grams."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        self.model = f"hash-{self.dim}"

    @staticmethod
    def _features(text: str) -> list[str]:
        tokens = normalize_arabic(text).split()
        features = list(tokens)
        features.extend(f"{a}_{b}" for a, b in itertools.pairwise(tokens))
        joined = "".join(tokens)
        features.extend(joined[i : i + 4] for i in range(max(0, len(joined) - 3)))
        return features or ["∅"]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class OpenAIEmbeddingProvider:
    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("مكتبة openai غير مثبتة", details="pip install openai") from exc

        if not settings.llm_api_key:
            raise LLMError("EMBEDDING_PROVIDER=openai يتطلب مفتاح API (LLM_API_KEY)")
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key, base_url=settings.llm_base_url or None
        )
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(
                model=self.model, input=list(texts), dimensions=self.dim
            )
        except Exception as exc:
            raise LLMError("فشل توليد المتجهات (embeddings)", details=str(exc)) from exc
        return [item.embedding for item in response.data]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


def build_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider()
    return HashEmbeddingProvider()


embedding_provider: EmbeddingProvider = build_embedding_provider()
