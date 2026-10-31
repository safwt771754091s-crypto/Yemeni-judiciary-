"""Arabic text normalisation and legal-text parsing helpers.

`normalize_arabic` MUST stay behaviourally identical to the SQL function
`normalize_arabic` in sql/002_functions.sql so that lexical scores computed in
Postgres line up with what the Python layer expects.
"""
from __future__ import annotations

import re
import unicodedata

# Tashkeel (harakat), superscript alef, tatweel/kashida
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
# Arabic-Indic and extended Arabic-Indic digits
_ARABIC_DIGITS = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
                               "\u06F0\u06F1\u06F2\u06F3\u06F4\u06F5\u06F6\u06F7\u06F8\u06F9",
                               "0123456789" * 2)
_ALEF_FORMS = re.compile(r"[أإآٱ]")
_YA_FORMS = re.compile(r"[ىئ]")
_HAMZA_FORMS = re.compile(r"[ؤء]")
_NON_WORD = re.compile(r"[^\u0621-\u064Aa-zA-Z0-9\u0660-\u0669\s]+")
_WHITESPACE = re.compile(r"\s+")

# Arabic article markers as they appear in Yemeni codes
ARTICLE_RE = re.compile(
    r"(?:المادة|مادة|م\.)\s*[\(\[]?\s*([0-9\u0660-\u0669]{1,5})\s*[\)\]]?\s*(?:مكرر(?:اً|ا)?)?",
)
CHAPTER_RE = re.compile(r"^\s*(?:الباب|الفصل|القسم|الفرع)\s+[^\n]{0,120}$", re.MULTILINE)


def normalize_arabic(text: str | None) -> str:
    """Strip diacritics/punctuation and unify Arabic letter variants."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = _DIACRITICS.sub("", out)
    out = out.translate(_ARABIC_DIGITS)
    out = _ALEF_FORMS.sub("ا", out)
    out = _YA_FORMS.sub("ي", out)
    out = _HAMZA_FORMS.sub("ي", out)
    out = out.replace("\u0629", "\u0647")  # ta marbuta -> ha
    out = _NON_WORD.sub(" ", out)
    return _WHITESPACE.sub(" ", out).strip()


def to_western_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)


def extract_article_numbers(text: str) -> list[str]:
    """Return article numbers mentioned in a piece of text ('المادة 1045')."""
    if not text:
        return []
    seen: list[str] = []
    for match in ARTICLE_RE.finditer(to_western_digits(text)):
        number = match.group(1).strip()
        if number and number not in seen:
            seen.append(number)
    return seen


def split_into_articles(text: str) -> list[tuple[str | None, str]]:
    """Split a Yemeni legal code into (article_number, article_body) pairs.

    Falls back to a single (None, whole_text) pair when no article markers are
    found, which keeps free-form documents ingestible.
    """
    normalised = text.replace("\u00a0", " ")
    matches = list(ARTICLE_RE.finditer(normalised))
    if not matches:
        stripped = normalised.strip()
        return [(None, stripped)] if stripped else []

    articles: list[tuple[str | None, str]] = []
    preamble = normalised[: matches[0].start()].strip()
    if preamble:
        articles.append((None, preamble))

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalised)
        body = normalised[start:end].strip()
        if body:
            # Numbers are stored in Western digits so that a citation typed as
            # "المادة 146" matches a statute whose source prints "١٤٦".
            articles.append((to_western_digits(match.group(1).strip()), body))
    return articles


def extract_chapter(text: str) -> str | None:
    """Best-effort extraction of the most recent chapter/section heading."""
    headings = CHAPTER_RE.findall(text)
    return headings[-1].strip() if headings else None


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) avoiding a hard tiktoken dependency."""
    return max(1, len(text) // 4)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_article_label(article_number: str | None, fallback: str = "") -> str:
    if article_number:
        return f"المادة ({article_number})"
    return fallback
