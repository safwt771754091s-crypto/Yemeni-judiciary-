#!/usr/bin/env python3
"""Build the static statute index used by the offline search box.

The landing page must let a visitor search the Yemeni statutes by article
number or by a fragment of the text even with no connection and no backend.
The corpus is small enough (a few thousand articles) to ship as one JSON file
that the service worker caches; the page then searches it in the browser.

Usage
-----
    python scripts/build_search_index.py
    python scripts/build_search_index.py --file app/data/samples/yemeni_laws_excerpt.json
    python scripts/build_search_index.py --out app/static/legal-index.json

The output is a compact object:

    {"version": 1, "count": N, "articles": [
        {"i": "<article_id>", "n": "146", "l": "المادة 146", "t": "القانون المدني",
         "s": "civil_law", "u": "<source_url>", "c": "<content>"}]}

Keys are single letters to keep the file small: ``n`` number, ``l`` label,
``t`` law title, ``s`` source type, ``u`` source url, ``c`` content. The build
is deterministic (no timestamp) so CI can diff it against the committed copy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.text_utils import split_into_articles

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "app/data/corpus/yemeni_laws.json"
DEFAULT_OUT = ROOT / "app/static/legal-index.json"


def build(corpus_path: Path) -> dict:
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    articles: list[dict] = []
    for entry in documents:
        document = entry.get("document") or {}
        text = entry.get("text") or ""
        title = document.get("title_ar") or ""
        source_type = document.get("source_type") or "other"
        source_url = document.get("source_url")
        for index, (number, body) in enumerate(split_into_articles(text)):
            body = (body or "").strip()
            if not body:
                continue
            articles.append(
                {
                    "i": f"{source_type}:{number or index}:{index}",
                    "n": number or "",
                    "l": f"المادة {number}" if number else "مادة",
                    "t": title,
                    "s": source_type,
                    "u": source_url,
                    "c": body,
                }
            )
    return {
        "version": 1,
        "count": len(articles),
        "articles": articles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_CORPUS, help="corpus JSON")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSON")
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"corpus not found: {args.file}", file=sys.stderr)
        return 1

    index = build(args.file)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {index['count']} articles to {args.out} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
