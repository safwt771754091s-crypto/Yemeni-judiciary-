"""Article-aware chunking for Yemeni legal codes.

Legal text must not be split blindly: an article (المادة) is the atomic unit of
legal citation.  We therefore split on article boundaries first, and only
sub-split an article when it exceeds `max_chars`, keeping a stable chunk index
so re-ingestion is idempotent.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.text_utils import (
    estimate_tokens,
    extract_chapter,
    format_article_label,
    split_into_articles,
)


@dataclass(slots=True)
class LegalChunk:
    article_number: str | None
    article_label: str
    content: str
    chunk_index: int = 0
    chapter: str | None = None
    section: str | None = None
    heading: str | None = None
    token_count: int = 0
    metadata: dict = field(default_factory=dict)


def _split_oversized(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split a long article on sentence/paragraph boundaries with overlap."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs or [text]:
        if len(paragraph) > max_chars:
            for sentence in _sentences(paragraph):
                if len(buffer) + len(sentence) + 1 > max_chars and buffer:
                    pieces.append(buffer.strip())
                    buffer = buffer[-overlap:] if overlap else ""
                buffer += (" " if buffer else "") + sentence
        elif len(buffer) + len(paragraph) + 1 > max_chars and buffer:
            pieces.append(buffer.strip())
            buffer = buffer[-overlap:] if overlap else ""
            buffer += paragraph
        else:
            buffer += ("\n" if buffer else "") + paragraph
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces or [text[:max_chars]]


def _sentences(text: str) -> list[str]:
    import re

    return [s for s in re.split(r"(?<=[.؟!؛:])\s+", text) if s.strip()]


def chunk_legal_document(
    text: str,
    *,
    max_chars: int = 1800,
    overlap: int = 200,
    default_article: str | None = None,
) -> list[LegalChunk]:
    """Turn raw statute text into citation-ready chunks."""
    chunks: list[LegalChunk] = []
    for article_number, body in split_into_articles(text):
        number = article_number or default_article
        label = format_article_label(number, fallback="ديباجة")
        chapter = extract_chapter(body)
        heading = _heading_of(body)
        pieces = _split_oversized(body, max_chars, overlap) if len(body) > max_chars else [body]
        for index, piece in enumerate(pieces):
            chunks.append(
                LegalChunk(
                    article_number=number,
                    article_label=label,
                    content=piece,
                    chunk_index=index,
                    chapter=chapter,
                    heading=heading if index == 0 else None,
                    token_count=estimate_tokens(piece),
                    metadata={"part": index + 1, "parts": len(pieces)} if len(pieces) > 1 else {},
                )
            )
    return chunks


def _heading_of(article_body: str) -> str | None:
    """First short line of an article often acts as its heading."""
    first_line = article_body.split("\n", 1)[0].strip()
    if 0 < len(first_line) <= 120 and not first_line.endswith("."):
        return first_line
    return None
