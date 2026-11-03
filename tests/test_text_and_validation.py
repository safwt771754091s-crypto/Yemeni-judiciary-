"""Unit tests: Arabic normalisation, chunking and citation validation."""
from __future__ import annotations

from app.core.chunking import chunk_legal_document
from app.core.text_utils import (
    extract_article_numbers,
    normalize_arabic,
    split_into_articles,
)
from app.llm.validator import validate_citations


def test_normalize_strips_diacritics_and_unifies_letters():
    assert normalize_arabic("المَادَّةُ") == "الماده"
    assert normalize_arabic("أحمد") == "احمد"
    assert normalize_arabic("مصطفى") == "مصطفي"
    assert normalize_arabic("١٢٣") == "123"
    assert normalize_arabic("نصٌّ، فيه. علامات!") == "نص فيه علامات"


def test_extract_article_numbers_handles_arabic_indic_digits():
    text = "واستناداً إلى المادة (1045) والمادة ١٤٦ من القانون المدني"
    assert extract_article_numbers(text) == ["1045", "146"]


def test_split_into_articles_preserves_preamble_and_numbers():
    text = (
        "الباب الأول: مصادر الالتزام\n\n"
        "المادة (1) تسري النصوص التشريعية على جميع المسائل.\n\n"
        "المادة (2) لا يجوز إلغاء نص تشريعي إلا بتشريع لاحق."
    )
    articles = split_into_articles(text)
    numbers = [number for number, _ in articles]
    assert "1" in numbers and "2" in numbers
    assert any(number is None for number in numbers)  # the preamble


def test_split_into_articles_ignores_mid_line_cross_references():
    """A mid-line «المادة (1)» is a cross-reference, not a new article heading."""
    text = (
        "المادة (1)\n\n"
        "يلتزم البائع بتسليم المبيع.\n\n"
        "المادة (2)\n\n"
        "وفقاً لأحكام المادة (1) من هذا القانون يلتزم المشتري بالثمن.\n\n"
        "المادة (3)\n\n"
        "نص آخر."
    )
    articles = split_into_articles(text)
    numbers = [number for number, _ in articles]
    assert numbers == ["1", "2", "3"], numbers
    article_two = next(body for number, body in articles if number == "2")
    assert "المادة (1) من هذا القانون" in article_two
    assert len([n for n in numbers if n == "1"]) == 1


def test_split_into_articles_keeps_headings_glued_to_previous_sentence():
    """Yemeni sources often print a heading with no line break before it.

    The Civil Code is published this way, so anchoring on the line start alone
    would drop almost every article in it.
    """
    text = (
        "في تطبيق القانونمادة (١) يسري هذا القانون على المعاملات.\n"
        "مبادئ الشريعة الاسلامية.مادة (٢) لا يجوز الغاء القوانين.\n"
        "والمباني.مادة (٣) الشريعة مبنية على رعاية المصالح."
    )
    numbers = [number for number, _ in split_into_articles(text) if number]
    assert numbers == ["1", "2", "3"], numbers


def test_split_into_articles_converts_arabic_indic_digits():
    """A source printing «مادة (١٤٦)» must be citable as «المادة 146»."""
    numbers = [number for number, _ in split_into_articles("مادة (١٤٦) نص المادة.") if number]
    assert numbers == ["146"], numbers


def test_chunking_is_article_aware_and_stable():
    text = "المادة (10) " + "نص طويل. " * 400 + "\n\nالمادة (11) نص قصير."
    chunks = chunk_legal_document(text, max_chars=500, overlap=50)
    ten = [c for c in chunks if c.article_number == "10"]
    assert len(ten) > 1, "a long article must be sub-split"
    assert [c.chunk_index for c in ten] == list(range(len(ten))), "indices must be sequential"
    assert all(c.article_label == "المادة (10)" for c in ten)
    assert any(c.article_number == "11" for c in chunks)


def test_split_into_articles_ignores_the_م_abbreviation_inside_a_sentence():
    """The sentence-final period must not read as the «م.» article marker.

    «... تاريخ النطق بالحكم.» followed by the numbered paragraph «2 - ...» used to
    match as «م. 2» and mint a phantom «المادة 2», so a citation for article 2
    could resolve to a sub-paragraph of article 228.
    """
    text = (
        "مادة (1)\n\n"
        "يسمى هذا القانون قانون المرافعات والتنفيذ المدني.\n\n"
        "مادة (2)\n\n"
        "يقصد بالكلمات والعبارات الواردة في هذه المادة المعاني المبينة قرين كل منها.\n\n"
        "مادة (228)\n\n"
        "1- يجب على المحكمة تحرير نسخة الحكم الأصلية.\n\n"
        "2 - بمجرد الانتهاء من ختم النسخة الأصلية للحكم.\n\n"
        "م. 5 نص قانوني بالاختصار."
    )
    articles = split_into_articles(text)
    numbers = [number for number, _ in articles if number]
    assert numbers == ["1", "2", "228", "5"], numbers
    article_two = next(body for number, body in articles if number == "2")
    assert "يقصد بالكلمات والعبارات" in article_two
    assert "بمجرد الانتهاء من ختم" not in article_two


def test_validate_citations_drops_invented_articles():
    retrieved = [
        {
            "article_label": "المادة (146)",
            "law_title": "القانون المدني اليمني",
            "content": "العقد شريعة المتعاقدين، فلا يجوز نقضه ولا تعديله إلا باتفاق الطرفين.",
            "similarity": 0.8,
            "article_id": "a1",
            "source_type": "civil_law",
        }
    ]
    citations = [
        {
            "article_label": "المادة (146)",
            "law_title": "القانون المدني اليمني",
            "quote": "العقد شريعة المتعاقدين",
            "relevance": "أصل إلزامية العقد",
        },
        {
            "article_label": "المادة (9999)",
            "law_title": "قانون مختلق",
            "quote": "نص لا وجود له",
            "relevance": "استشهاد ملفّق",
        },
    ]
    report = validate_citations(citations, retrieved)
    assert len(report.verified) == 1
    assert report.verified[0]["article_label"] == "المادة (146)"
    assert report.unverified == ["المادة (9999)"]
    assert report.grounding_score == 0.5
    assert report.warnings


def test_validate_citations_flags_fabricated_quote():
    retrieved = [
        {
            "article_label": "المادة (191)",
            "law_title": "القانون المدني اليمني",
            "content": "يلتزم من أحدث ضرراً بخطئه أن يعوض الضرر.",
            "similarity": 0.7,
            "article_id": "a2",
            "source_type": "civil_law",
        }
    ]
    citations = [
        {
            "article_label": "المادة (191)",
            "law_title": "القانون المدني اليمني",
            "quote": "يجب على الدولة إنشاء صندوق تعويضات فوري لكل المتضررين",
            "relevance": "استشهاد بنص غير موجود في المادة",
        }
    ]
    report = validate_citations(citations, retrieved)
    assert report.quote_mismatches == ["المادة (191)"]
    assert report.verified[0]["quote"] is None
    assert any("لا يطابق" in w for w in report.warnings)


def test_validate_citations_rejects_number_from_the_wrong_code():
    """A citation naming the Civil Code must not verify against the Procedure Code.

    Both codes contain «المادة 191»; only the cited code's text may ground it.
    """
    retrieved = [
        {
            "article_label": "المادة (191)",
            "law_title": "قانون المرافعات المدنية والتجارية اليمني",
            "content": "إذا تعدد المدعون أو المدعى عليهم جاز إدخالهم في دعوى واحدة.",
            "similarity": 0.9,
            "article_id": "p191",
            "source_type": "civil_procedure",
        },
        {
            "article_label": "المادة (191)",
            "law_title": "القانون المدني اليمني",
            "content": "كل إضرار بالغير يلزم من أحدثه بالتعويض.",
            "similarity": 0.4,
            "article_id": "c191",
            "source_type": "civil_law",
        },
    ]
    citations = [
        {
            "article_label": "المادة (191)",
            "law_title": "القانون المدني اليمني",
            "source_type": "civil_law",
            "quote": "كل إضرار بالغير يلزم من أحدثه بالتعويض",
            "relevance": "التعويض عن الضرر",
        }
    ]
    report = validate_citations(citations, retrieved)
    assert report.verified, "the Civil Code citation must verify"
    assert report.verified[0]["article_id"] == "c191"
    assert report.verified[0]["law_title"] == "القانون المدني اليمني"


def test_law_title_number_is_not_duplicated():
    """A law title that already carries «رقم (14)» must not have it appended again.

    Yemeni statute titles are self-describing: the Civil Code's ``title_ar`` is
    «القانون المدني (قانون المعاملات المدنية) رقم (14) لسنة 2002م». Appending the
    stored ``law_number`` produced «... رقم (14) لسنة 2002م رقم (14)» in the
    judgment, the prompt and the citation property.
    """
    from app.core.text_utils import format_law_title

    civil = "القانون المدني (قانون المعاملات المدنية) رقم (14) لسنة 2002م"
    assert format_law_title(civil, "14") == civil

    # An Arabic-Indic number in the title still counts as present.
    assert format_law_title("قانون رقم (٢١) بشأن الإثبات", "21") == "قانون رقم (٢١) بشأن الإثبات"

    # A title that does NOT mention its number still gets it appended.
    assert (
        format_law_title("القانون المدني اليمني", "14")
        == "القانون المدني اليمني رقم (14)"
    )

    # No number at all -> title unchanged.
    assert format_law_title("القانون المدني اليمني", None) == "القانون المدني اليمني"

    # A different number in the title must not suppress the append.
    assert (
        format_law_title("قانون رقم (40) لسنة 2002م", "14")
        == "قانون رقم (40) لسنة 2002م رقم (14)"
    )
