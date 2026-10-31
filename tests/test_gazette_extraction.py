"""Regression tests for the scanned Official Gazette extraction.

The Civil Code only exists as a scanned gazette PDF. Its text layer stores each
line in visual order (characters mirrored, presentation forms), so the raw
extraction is unreadable. These tests pin the reconstruction that turns it back
into logical Arabic, and pin the article count that the corpus depends on.
"""
from __future__ import annotations

import pytest

from scripts.build_corpus import _extract_gazette_page, _fix_gazette_token


def test_fix_token_reverses_characters_before_normalising_ligatures():
    # "ﱐﺪﳌﺍ" is "المدني" mirrored; the lam-alef ligature must survive the fix.
    assert _fix_gazette_token("ﱐﺪﳌﺍ") == "المدني"
    assert _fix_gazette_token("ﻥﻮﻧﺎﻘﻟﺍ") == "القانون"


def test_fix_token_keeps_digits_in_reading_order():
    # The whole token is mirrored, so a digit run is flipped twice: once with the
    # letters and once on its own. The two flips cancel, which leaves the
    # Arabic-Indic digits in reading order inside the recovered token.
    assert _fix_gazette_token("(١٤٢)ﺓﺩﺎﻤ") == "مادة(١٤٢)"
    assert _fix_gazette_token("(٢٥١)ﺓﺩﺎﻤ") == "مادة(٢٥١)"


def test_fix_token_unmirrors_parentheses():
    # Parentheses are mirrored with the rest of the line and must flip back.
    assert _fix_gazette_token("(") == ")"
    assert _fix_gazette_token(")") == "("
    assert _fix_gazette_token("(لﺤﻤﻟﺍ)") == "(المحل)"


class _Word(dict):
    """Stand-in for a pdfplumber word: only the fields the extractor reads."""


class _Page:
    def __init__(self, words):
        self._words = words

    def extract_words(self, **_kwargs):
        return self._words


def _word(text, x0, top):
    return _Word(text=text, x0=x0, top=top, bottom=top + 14)


def test_extract_page_reads_lines_right_to_left():
    page = _Page(
        [
            _word("ﻥﻮﻧﺎﻘﻟﺍ", 420, 50),
            _word("ﱐﺪﳌﺍ", 390, 50),
            _word("لﻭﻻﺍ", 420, 70),
        ]
    )
    assert _extract_gazette_page(page) == "القانون المدني\nالاول"


@pytest.mark.skip(reason="requires the gazette PDF; run scripts/build_corpus.py --pdf")
def test_civil_code_recovers_all_articles():
    from pathlib import Path

    from app.core.text_utils import split_into_articles
    from scripts.build_corpus import build_civil_code

    pdf_dir = Path("/tmp/yelaw")
    if not pdf_dir.exists():
        pytest.skip("gazette PDF not present")
    built = build_civil_code(pdf_dir, "test")
    numbers = sorted(int(n) for n, _ in split_into_articles(built.text) if n and n.isdigit())
    assert numbers == list(range(1, 1394))
