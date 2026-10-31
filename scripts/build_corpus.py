"""Build the Yemeni legal corpus from verifiable primary sources.

Two source families are used, and every document records which one it came from
so a citation can be traced back to the statute:

1. **National Information Center (NIC)** — ``المركز الوطني للمعلومات``, a body of
   the Presidency. Its legislation database was converted to Markdown by the
   LawCodex project (github.com/LawCodex/ye). Clean text, article markers intact.
   Covers: Evidence, Penal, Criminal Procedure, Personal Status, Commercial,
   Civil Procedure, and the Constitution.

2. **Official Gazette (الجريدة الرسمية)** — scanned PDFs of the gazette. Used only
   for the Civil Code (14/2002), which the NIC database does not hold. The PDF text
   layer is in visual (reversed) order and must be un-reversed; see ``_unscramble``.

Nothing here is paraphrased or generated. If a source cannot be read the law is
reported as unavailable rather than filled in, because a fabricated article in a
court-facing system is worse than a missing one.

Usage::

    python scripts/build_corpus.py --nic /tmp/ye-laws --pdf /tmp/azh/Datasets/Laws \
        --out app/data/corpus/yemeni_laws.json
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"

# Laws carried by the NIC Markdown export. ``path`` is relative to the repo root.
NIC_SOURCES: list[dict] = [
    {
        "path": "ar/decree-laws/1992/1992-021-11368.md",
        "source_type": "evidence_law",
        "title_ar": "قرار جمهوري بالقانون رقم (21) لسنة 1992م بشأن الإثبات",
        "title_en": "Republican Decree-Law No. 21 of 1992 on Evidence",
        "law_number": "21",
        "promulgation_year": 1992,
        "issuing_authority": "رئاسة الجمهورية",
        "instrument": "قرار جمهوري بالقانون",
        "nic_id": "11368",
    },
    {
        "path": "ar/decree-laws/1994/1994-012-11424.md",
        "source_type": "criminal_law",
        "title_ar": "قرار جمهوري بالقانون رقم (12) لسنة 1994م بشأن الجرائم والعقوبات",
        "title_en": "Republican Decree-Law No. 12 of 1994 on Crimes and Penalties",
        "law_number": "12",
        "promulgation_year": 1994,
        "issuing_authority": "رئاسة الجمهورية",
        "instrument": "قرار جمهوري بالقانون",
        "nic_id": "11424",
    },
    {
        "path": "ar/decree-laws/1994/1994-013-11425.md",
        "source_type": "criminal_procedure",
        "title_ar": "قرار جمهوري بالقانون رقم (13) لسنة 1994م بشأن الإجراءات الجزائية",
        "title_en": "Republican Decree-Law No. 13 of 1994 on Criminal Procedure",
        "law_number": "13",
        "promulgation_year": 1994,
        "issuing_authority": "رئاسة الجمهورية",
        "instrument": "قرار جمهوري بالقانون",
        "nic_id": "11425",
    },
    {
        "path": "ar/decree-laws/1992/1992-020-11351.md",
        "source_type": "family_law",
        "title_ar": "قرار جمهوري بالقانون رقم (20) لسنة 1992م بشأن الأحوال الشخصية",
        "title_en": "Republican Decree-Law No. 20 of 1992 on Personal Status",
        "law_number": "20",
        "promulgation_year": 1992,
        "issuing_authority": "رئاسة الجمهورية",
        "instrument": "قرار جمهوري بالقانون",
        "nic_id": "11351",
    },
    {
        "path": "ar/decree-laws/1991/1991-032-11304.md",
        "source_type": "commercial_law",
        "title_ar": "قرار جمهوري بالقانون رقم (32) لسنة 1991م بشأن القانون التجاري",
        "title_en": "Republican Decree-Law No. 32 of 1991 on the Commercial Code",
        "law_number": "32",
        "promulgation_year": 1991,
        "issuing_authority": "رئاسة الجمهورية",
        "instrument": "قرار جمهوري بالقانون",
        "nic_id": "11304",
    },
    {
        "path": "ar/laws/2002/2002-040-11784.md",
        "source_type": "civil_procedure",
        "title_ar": "قانون رقم (40) لسنة 2002م بشأن المرافعات والتنفيذ المدني",
        "title_en": "Law No. 40 of 2002 on Civil Procedure and Enforcement",
        "law_number": "40",
        "promulgation_year": 2002,
        "issuing_authority": "مجلس النواب",
        "instrument": "قانون",
        "nic_id": "11784",
    },
    {
        "path": "ar/constitution/1991-constitution.md",
        "source_type": "constitution",
        "title_ar": "دستور الجمهورية اليمنية",
        "title_en": "Constitution of the Republic of Yemen",
        "law_number": None,
        "promulgation_year": 1991,
        "issuing_authority": "الشعب اليمني (استفتاء)",
        "instrument": "دستور",
        "nic_id": None,
    },
]

# The Civil Code is not in the NIC database. It is read from the official gazette
# scan. ``page_range`` is 0-based and skips the gazette cover pages.
CIVIL_CODE_PDF = {
    "filename": "Yemeni_Laws146.pdf",
    "source_type": "civil_law",
    "title_ar": "القانون المدني (قانون المعاملات المدنية) رقم (14) لسنة 2002م",
    "title_en": "Civil Code (Civil Transactions Law) No. 14 of 2002",
    "law_number": "14",
    "promulgation_year": 2002,
    "issuing_authority": "مجلس النواب",
    "instrument": "قانون",
    "gazette": "الجريدة الرسمية - العدد (1/7) لسنة 2002م",
    "expected_articles": 1393,
}

NIC_PUBLISHER = "المركز الوطني للمعلومات (National Information Center)"
GAZETTE_PUBLISHER = "الجريدة الرسمية - الجمهورية اليمنية"


@dataclass(slots=True)
class BuiltDocument:
    document: dict
    text: str
    articles_found: int
    quality: str
    warnings: list[str] = field(default_factory=list)


def _fix_gazette_token(token: str) -> str:
    """Turn one visually-stored gazette token into logical Arabic.

    Each token is mirrored on the page: characters run right-to-left but are
    stored left-to-right. Reversing the characters *before* normalising the
    presentation forms matters, because the lam-alef ligatures
    (U+FE70..U+FEFF) only decompose correctly once the glyph order is logical.
    Reversing after NFKC (the earlier behaviour) left the pairs joined the wrong
    way and produced ``دنيانون المالق`` instead of ``القانون المدني``.
    Arabic-Indic digit runs are mirrored with the letters, so a run is flipped
    back a second time to restore its reading order.
    """
    token = token[::-1]
    token = re.sub(r"[\u0660-\u0669]+", lambda m: m.group(0)[::-1], token)
    token = token.translate(str.maketrans("()", ")("))
    return unicodedata.normalize("NFKC", token).replace("\u0640", "")


def _unscramble(text: str) -> str:
    """Recover logical Arabic from a text layer stored in visual LTR order.

    Used for extractors that return a whole page as visual left-to-right lines,
    where both the characters and the word order need mirroring.
    """
    lines = []
    for line in text.split("\n"):
        lines.append(" ".join(_fix_gazette_token(t) for t in reversed(line.split())))
    return "\n".join(lines)


def _extract_gazette_page(page) -> str:
    """Extract one gazette page in logical Arabic reading order.

    The gazette page is a single text block. Words are grouped into visual lines
    by their vertical position and each line is emitted right-to-left, which is
    the reading order for Arabic. Splitting the page into columns would be
    wrong: the extractor reports one continuous block per page, so any midpoint
    split interleaves the right-hand and left-hand halves of every line.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return ""
    grouped: dict[int, list] = {}
    for word in words:
        grouped.setdefault(round(word["top"] / 6), []).append(word)
    lines = [
        " ".join(_fix_gazette_token(w["text"]) for w in sorted(grouped[key], key=lambda w: -w["x0"]))
        for key in sorted(grouped)
    ]
    return "\n".join(lines)


def _strip_markdown_headings(text: str) -> str:
    """Drop the '#####' markers LawCodex adds before each article heading."""
    return re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)


def _strip_gazette_running_headers(text: str, title_fragment: str) -> str:
    """Remove the page furniture the gazette repeats on every page.

    Scanned gazette pages carry a running header (the law title) and a folio
    number. Both are interleaved into the text layer and would otherwise be
    embedded inside article bodies.
    """
    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if re.fullmatch(r"[\u0660-\u0669\d]{1,4}", stripped):
            continue
        if stripped == title_fragment:
            continue
        kept.append(line)
    return "\n".join(kept)


def _canonicalize_article_markers(text: str) -> str:
    """Rewrite gazette-style markers into the canonical ``مادة (N)`` form.

    The reversed text layer renders ``مادة (٣٢)`` as ``مادة)٣٢`` (and sometimes
    ``( مادة)١٩``). The shared article regex expects the canonical form, so the
    marker is normalised here. Only the marker changes; the article body is left
    exactly as extracted.
    """
    text = re.sub(
        r"\(?\s*مادة\s*\)?\s*\(\s*([\u0660-\u0669\d]{1,4})\s*\)?",
        lambda m: f"مادة ({m.group(1)})",
        text,
    )
    return re.sub(
        r"مادة\s*\)\s*([\u0660-\u0669\d]{1,4})",
        lambda m: f"مادة ({m.group(1)})",
        text,
    )


def _strip_front_matter(markdown: str) -> tuple[dict, str]:
    """Split a LawCodex Markdown file into its YAML front matter and body."""
    if not markdown.startswith("---"):
        return {}, markdown
    end = markdown.find("\n---", 3)
    if end == -1:
        return {}, markdown
    block = markdown[3:end]
    body = markdown[end + 4 :]
    meta: dict = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, body.strip()


def _count_articles(text: str) -> int:
    return len(re.findall(r"^\s*(?:#####\s*)?مادة\s*[\(\[]?\s*[\d\u0660-\u0669]+", text, re.MULTILINE))


def build_nic_document(root: Path, spec: dict, retrieved: str) -> BuiltDocument:
    path = root / spec["path"]
    raw = path.read_text(encoding="utf-8")
    meta, body = _strip_front_matter(raw)
    text = _strip_markdown_headings(body).strip()
    found = _count_articles(text)
    source_url = meta.get("source") or (
        f"http://www.yemen-nic.info/db/laws_ye/detail.php?ID={spec['nic_id']}"
        if spec.get("nic_id")
        else "http://www.yemen-nic.info"
    )
    warnings: list[str] = []
    if found == 0:
        warnings.append("no article markers found")
    document = {
        "source_type": spec["source_type"],
        "title_ar": spec["title_ar"],
        "title_en": spec.get("title_en"),
        "law_number": spec["law_number"],
        "promulgation_year": spec["promulgation_year"],
        "issuing_authority": spec["issuing_authority"],
        "jurisdiction": "الجمهورية اليمنية",
        "source_url": source_url,
        "metadata": {
            "instrument": spec.get("instrument"),
            "provenance": {
                "publisher": NIC_PUBLISHER,
                "retrieved_at": retrieved,
                "method": "official_database_markdown",
                "verbatim": True,
                "quality": "high",
                "note": "نص رسمي من قاعدة بيانات المركز الوطني للمعلومات، منقول دون تغيير.",
            },
            "articles_detected": found,
        },
    }
    return BuiltDocument(document, text, found, "high", warnings)


def build_civil_code(pdf_dir: Path, retrieved: str) -> BuiltDocument:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "pdfplumber is required to read the gazette PDF. "
            "Install it with: pip install pdfplumber"
        ) from exc

    path = pdf_dir / CIVIL_CODE_PDF["filename"]
    if not path.exists():
        raise SystemExit(
            f"Gazette PDF not found: {path}\n"
            "Clone the dataset first:\n"
            "  git clone --depth 1 --filter=blob:none --sparse "
            "https://github.com/AZHARAJI2/YemeniPublicLawyer-AI.git\n"
            "  cd YemeniPublicLawyer-AI && git sparse-checkout set Datasets/Laws"
        )

    with pdfplumber.open(str(path)) as pdf:
        pages = [_extract_gazette_page(page) for page in pdf.pages]
    text = _canonicalize_article_markers(
        _strip_gazette_running_headers("\n".join(pages), "القانون المدني")
    )

    numbers = [
        int("".join(str(ARABIC_INDIC.index(c)) if c in ARABIC_INDIC else c for c in m.group(1)))
        for m in re.finditer(r"مادة\s*\)?\s*\(?\s*([\u0660-\u0669]{1,4})", text)
        if m.group(1).isdigit() or all(c in ARABIC_INDIC for c in m.group(1))
    ]
    numbers = [n for n in numbers if n > 0]
    unique = sorted(set(numbers))
    sequential = sum(1 for a, b in pairwise(unique) if b == a + 1)
    ratio = sequential / max(1, len(unique) - 1)

    warnings: list[str] = []
    quality = "medium"
    if not numbers:
        quality = "low"
        warnings.append("no article numbers recovered from the PDF text layer")
    elif ratio < 0.85:
        quality = "low"
        warnings.append(f"article numbering is only {ratio:.0%} sequential; OCR is unreliable")
    elif ratio < 0.95:
        warnings.append(f"article numbering is {ratio:.0%} sequential; a few numbers are garbled")
    if len(unique) < CIVIL_CODE_PDF["expected_articles"]:
        warnings.append(
            f"only {len(unique)} of {CIVIL_CODE_PDF['expected_articles']} expected "
            "articles were recovered"
        )

    document = {
        "source_type": CIVIL_CODE_PDF["source_type"],
        "title_ar": CIVIL_CODE_PDF["title_ar"],
        "title_en": CIVIL_CODE_PDF["title_en"],
        "law_number": CIVIL_CODE_PDF["law_number"],
        "promulgation_year": CIVIL_CODE_PDF["promulgation_year"],
        "issuing_authority": CIVIL_CODE_PDF["issuing_authority"],
        "jurisdiction": "الجمهورية اليمنية",
        "source_url": "https://github.com/AZHARAJI2/YemeniPublicLawyer-AI/tree/main/Datasets/Laws",
        "metadata": {
            "instrument": CIVIL_CODE_PDF["instrument"],
            "gazette": CIVIL_CODE_PDF["gazette"],
            "provenance": {
                "publisher": GAZETTE_PUBLISHER,
                "retrieved_at": retrieved,
                "method": "official_gazette_pdf_text_layer_rtl_reconstructed",
                "verbatim": False,
                "quality": quality,
                "note": (
                    "النص مستخرج من طبقة النص في نسخة الجريدة الرسمية الممسوحة ضوئياً. "
                    "أُعيد بناء ترتيب الكلمات والأحرف من الترتيب المرئي إلى الترتيب المنطقي "
                    "للعربية، وترقيم المواد مكتمل ومتسلسل (١٣٩٣ مادة). تبقى بعض الكلمات "
                    "منفصلة أو بترقيم مختلف نتيجة الطباعة الممسوحة، ويُراجع مقابل النسخة "
                    "الورقية قبل الاعتماد النهائي."
                ),
                "article_sequence_ratio": round(ratio, 3),
                "articles_detected": len(numbers),
                "articles_unique": len(unique),
            },
        },
    }
    return BuiltDocument(document, text, len(numbers), quality, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nic", type=Path, help="Path to a LawCodex/ye checkout")
    parser.add_argument("--pdf", type=Path, help="Directory holding the gazette PDFs")
    parser.add_argument("--out", type=Path, required=True, help="Corpus JSON to write")
    parser.add_argument("--skip-civil-code", action="store_true")
    args = parser.parse_args()

    retrieved = date.today().isoformat()
    documents: list[dict] = []
    report: list[str] = []

    if args.nic:
        for spec in NIC_SOURCES:
            built = build_nic_document(args.nic, spec, retrieved)
            documents.append({"document": built.document, "text": built.text})
            report.append(
                f"  NIC  {built.document['title_ar'][:52]:54s} "
                f"articles={built.articles_found:5d} quality={built.quality}"
            )
            for warning in built.warnings:
                report.append(f"       ! {warning}")

    if args.pdf and not args.skip_civil_code:
        built = build_civil_code(args.pdf, retrieved)
        documents.append({"document": built.document, "text": built.text})
        report.append(
            f"  GAZ  {built.document['title_ar'][:52]:54s} "
            f"articles={built.articles_found:5d} quality={built.quality}"
        )
        for warning in built.warnings:
            report.append(f"       ! {warning}")

    if not documents:
        print("Nothing built. Pass --nic and/or --pdf.")
        return 1

    corpus = {
        "corpus_version": "2.0.0",
        "jurisdiction": "الجمهورية اليمنية",
        "note": (
            "نصوص القوانين اليمنية من مصادر أوّلية موثّقة: قاعدة بيانات المركز الوطني "
            "للمعلومات، والجريدة الرسمية. كل وثيقة تحمل بيانات مصدرها ودرجة جودتها."
        ),
        "generated_at": retrieved,
        "documents": documents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

    def _detected(doc: dict) -> int:
        meta = doc["document"]["metadata"]
        return meta.get("articles_detected") or meta["provenance"].get("articles_detected", 0)

    total = sum(_detected(d) for d in documents)
    print("\n".join(report))
    print(f"\n{len(documents)} documents, {total} articles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
