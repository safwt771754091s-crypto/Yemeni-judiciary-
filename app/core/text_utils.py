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

# Arabic article markers as they appear in Yemeni codes. The bare abbreviation
# «م.» is guarded against a preceding Arabic letter: the sentence-ending period
# after a word that ends in «م» (e.g. «... بالحكم.» before a numbered paragraph)
# otherwise matched as «م. 2», minting a phantom «المادة 2» from a list item and
# letting a citation for that number resolve to the wrong text.
ARTICLE_RE = re.compile(
    r"(?:المادة|مادة|(?<![\u0621-\u064A])م\.)\s*[\(\[]?\s*([0-9\u0660-\u0669]{1,5})\s*[\)\]]?\s*(?:مكرر(?:اً|ا)?)?",
)
# Heading and cross-reference share the same marker («المادة (23)»), so the two
# can only be told apart from the surrounding words. A marker that continues a
# sentence is a reference; one that opens a line is a heading. Treating a
# reference as a heading split its article in two and produced a second chunk
# for the referenced number, so a citation for that number could resolve to the
# wrong text. Yemeni sources print headings glued to the previous sentence with
# no line break, which is why the line position alone is not enough.
_CROSS_REF_BEFORE = re.compile(
    r"(?<![\u0621-\u064A])[وفبلك]?"
    r"(?:احكام|أحكام|وفق|طبق|تطبيق|بموجب|موجب|مقتضي|مقتضى|مقتض|نصوص|نص|حكم|شان|بشان|بشأن|"
    r"في|من|الي|إلى|على|عن|مع|حسب|سند|استناد|عملا|عملاً|مراعاة|المنصوص|المشار|المذكور|"
    r"الوارد|الواردة|السابق|السابقة|ذات)\s*$"
)
_CROSS_REF_AFTER = re.compile(
    r"^\s*(?:من\s+(?:هذا|هذه|ذلك|تلك|القانون|النظام|الدستور|المرسوم|القرار|اللائحة)"
    r"|اعلاه|أعلاه|المذكور|المذكورة|المشار|السابق|السابقة|الاتي|الآتي|الآتية|ادناه|أدناه|مكرر)"
)
CHAPTER_RE = re.compile(r"^\s*(?:الباب|الفصل|القسم|الفرع)\s+[^\n]{0,120}$", re.MULTILINE)


def _is_article_heading(text: str, match: re.Match[str]) -> bool:
    """True when `match` opens an article rather than continuing a sentence."""
    window_start = max(0, match.start() - 40)
    line_start = text.rfind("\n", window_start, match.start())
    if line_start != -1:
        window_start = line_start + 1
    if _CROSS_REF_BEFORE.search(text[window_start : match.start()]):
        return False
    return not _CROSS_REF_AFTER.match(text[match.end() : match.end() + 40])



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

    A marker that continues a sentence is a cross-reference such as
    «وفقاً لأحكام المادة (23) من هذا القانون», not a new article; only headings
    open an article. See `_is_article_heading` for how the two are told apart.

    Falls back to a single (None, whole_text) pair when no article markers are
    found, which keeps free-form documents ingestible.
    """
    normalised = text.replace("\u00a0", " ")
    matches = [m for m in ARTICLE_RE.finditer(normalised) if _is_article_heading(normalised, m)]
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


def format_law_title(title: str | None, law_number: str | None) -> str:
    """Return a law title carrying its number exactly once.

    Yemeni statute titles are self-describing and already embed the number
    («القانون المدني (قانون المعاملات المدنية) رقم (14) لسنة 2002م»), while the
    stored ``law_number`` repeats it. Appending unconditionally printed the
    number twice in the judgment and in the prompt's statute block. The number
    is only appended when the title does not already carry that same number.
    """
    title = (title or "").strip()
    if not title:
        return "قانون"
    number = (law_number or "").strip()
    if not number:
        return title
    present = re.search(rf"\(\s*{re.escape(number)}\s*\)", to_western_digits(title))
    if present:
        return title
    return f"{title} رقم ({number})"
