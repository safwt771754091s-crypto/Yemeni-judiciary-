"""Extract a small *verbatim* excerpt of the Yemeni corpus for the test suite.

The test suite must exercise real statute text, but loading all ~4,300 articles
on every run is needlessly slow. This script copies whole articles straight out
of ``app/data/corpus/yemeni_laws.json`` into a trimmed fixture, so the tests read
the same text a court would, just less of it.

Nothing is rewritten: articles are selected by number and copied unchanged.

    python scripts/make_test_excerpt.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.text_utils import split_into_articles  # noqa: E402

FULL = ROOT / "app/data/corpus/yemeni_laws.json"
EXCERPT = ROOT / "app/data/samples/yemeni_laws_excerpt.json"

# Which articles to keep, per source type. Enough to cover contract formation,
# damages, evidence and procedure without shipping the whole corpus into tests.
KEEP: dict[str, set[str]] = {
    "civil_law": {str(n) for n in range(1, 31)} | {"146", "147", "148", "149", "150", "235", "246", "247"},
    "evidence_law": {str(n) for n in range(1, 31)} | {"50", "51", "52", "53"},
    "civil_procedure": {str(n) for n in range(1, 21)} | {"100", "101"},
    "commercial_law": {str(n) for n in range(1, 21)},
    "criminal_law": {str(n) for n in range(1, 21)},
    "family_law": {str(n) for n in range(1, 21)},
    "criminal_procedure": {str(n) for n in range(1, 11)},
}

def articles_of(text: str) -> dict[str, str]:
    """Map article number -> that article's verbatim text.

    Uses the application's own splitter so the excerpt is cut exactly the way the
    ingestion pipeline will cut it.
    """
    out: dict[str, str] = {}
    for number, body in split_into_articles(text):
        if not number:
            continue
        digits = "".join(
            str("٠١٢٣٤٥٦٧٨٩".index(c)) if c in "٠١٢٣٤٥٦٧٨٩" else c for c in number
        )
        out.setdefault(digits, body.strip())
    return out


def main() -> int:
    if not FULL.exists():
        print(f"full corpus missing: {FULL}", file=sys.stderr)
        print("run scripts/build_corpus.py first", file=sys.stderr)
        return 2

    corpus = json.loads(FULL.read_text(encoding="utf-8"))
    kept_documents = []
    for entry in corpus["documents"]:
        source_type = entry["document"]["source_type"]
        wanted = KEEP.get(source_type)
        if not wanted:
            continue
        found = articles_of(entry["text"])
        selected = [found[n] for n in sorted(wanted, key=int) if n in found]
        if not selected:
            continue
        kept_documents.append(
            {"document": entry["document"], "text": "\n\n".join(selected)}
        )

    excerpt = {
        "corpus_version": corpus["corpus_version"] + "+excerpt",
        "jurisdiction": corpus["jurisdiction"],
        "note": (
            "مقتطف حرفي من المدونة الكاملة (app/data/corpus/yemeni_laws.json) "
            "لأغراض الاختبار. النصوص منقولة كما هي دون تغيير."
        ),
        "derived_from": "app/data/corpus/yemeni_laws.json",
        "documents": kept_documents,
    }
    EXCERPT.parent.mkdir(parents=True, exist_ok=True)
    EXCERPT.write_text(json.dumps(excerpt, ensure_ascii=False, indent=2), encoding="utf-8")

    total = 0
    for entry in kept_documents:
        count = len(articles_of(entry["text"]))
        total += count
        print(f"  {entry['document']['title_ar'][:48]:50s} {count:4d} مادة")
    print(f"\n{len(kept_documents)} laws, {total} articles -> {EXCERPT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
