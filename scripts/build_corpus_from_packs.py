#!/usr/bin/env python3
"""Build the production corpus JSON from the Yemeni law packs.

Source
------
`yemeni-laws` (gh-pages) publishes 413 Yemeni laws / 28,583 articles as
article-level JSON. Each record carries ``law_id``, ``article_number``,
``heading`` and ``content``, plus amendment metadata.

Run this in an environment that can reach the pack data:

    python scripts/build_corpus_from_packs.py --packs-dir ./packs --laws ./laws.json

It writes ``app/data/corpus/yemeni_laws_full.json`` in the schema consumed by
``scripts/seed_corpus.py``. The codes below are the ones the reasoning engine
cites most; add to ``CODE_MAP`` to extend coverage.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
from pathlib import Path

# law_id -> (source_type, title_ar, title_en, issuing_authority)
CODE_MAP: dict[int, tuple[str, str, str, str]] = {
    298: ("constitution", "دستور الجمهورية اليمنية المعدل 2001م", "Constitution of the Republic of Yemen (2001 amendment)", "مجلس النواب"),
    109: ("civil_law", "القانون المدني اليمني", "Yemeni Civil Code", "مجلس النواب"),
    68: ("criminal_law", "قانون الجرائم والعقوبات اليمني", "Yemeni Penal Code", "مجلس النواب"),
    32: ("criminal_procedure", "قانون الإجراءات الجزائية اليمني", "Yemeni Criminal Procedure Code", "مجلس النواب"),
    26: ("family_law", "قانون الأحوال الشخصية اليمني", "Yemeni Personal Status Law", "مجلس النواب"),
    30: ("evidence_law", "قانون الإثبات اليمني", "Yemeni Law of Evidence", "مجلس النواب"),
    110: ("civil_procedure", "قانون المرافعات والتنفيذ المدني اليمني", "Yemeni Civil Procedure & Execution Code", "مجلس النواب"),
    54: ("commercial_law", "القانون التجاري اليمني", "Yemeni Commercial Code", "مجلس النواب"),
    95: ("commercial_law", "قانون الشركات التجارية اليمني", "Yemeni Commercial Companies Law", "مجلس النواب"),
    88: ("commercial_law", "قانون السجل التجاري اليمني", "Yemeni Commercial Register Law", "مجلس النواب"),
    27: ("commercial_law", "قانون الأسماء التجارية اليمني", "Yemeni Trade Names Law", "مجلس النواب"),
    104: ("commercial_law", "قانون العلامات التجارية والمؤشرات الجغرافية", "Yemeni Trademarks & Geographical Indications Law", "مجلس النواب"),
    55: ("commercial_law", "قانون التحكيم اليمني", "Yemeni Arbitration Law", "مجلس النواب"),
    105: ("labour_law", "قانون العمل اليمني", "Yemeni Labour Code", "مجلس النواب"),
    78: ("other", "قانون الخدمة المدنية اليمني", "Yemeni Civil Service Law", "مجلس النواب"),
    50: ("other", "قانون التأمين الإلزامي من المسؤولية المدنية", "Yemeni Compulsory Civil Liability Insurance Law", "مجلس النواب"),
}




def load_articles(packs_dir: Path) -> dict[int, list[dict]]:
    by_law: dict[int, list[dict]] = collections.defaultdict(list)
    for f in sorted(glob.glob(str(packs_dir / "*.json"))):
        for a in json.loads(Path(f).read_text(encoding="utf-8")):
            by_law[a["law_id"]].append(a)
    return by_law


def article_sort_key(a: dict) -> tuple[int, int, str]:
    num = str(a.get("article_number") or "")
    return (0, int(num), "") if num.isdigit() else (1, 0, num)


def render_document(law: dict, records: list[dict], title_ar: str) -> str:
    """Render one law as text the chunker can split back into articles."""
    parts = [f"{title_ar} — قانون رقم ({law.get('law_number') or '—'}) لسنة {law.get('year') or '—'}م"]
    for a in sorted(records, key=article_sort_key):
        num = str(a.get("article_number") or "").strip()
        heading = (a.get("heading") or "").strip()
        body = (a.get("content") or "").strip()
        if not body:
            continue
        if num.isdigit():
            # `المادة (N)` is what ARTICLE_RE in app/core/text_utils.py matches.
            chapter = heading.split("—", 1)[1].strip() if "—" in heading else ""
            header = f"المادة ({num})"
            if chapter and len(chapter) < 180:
                header += f" {chapter}"
            parts.append(f"{header}\n{body}")
        else:
            parts.append(body)
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs-dir", required=True, type=Path)
    ap.add_argument("--laws", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("app/data/corpus/yemeni_laws_full.json"))
    ap.add_argument("--all-laws", action="store_true", help="include every law, not just CODE_MAP")
    args = ap.parse_args()

    laws = {rec["id"]: rec for rec in json.loads(args.laws.read_text(encoding="utf-8"))}
    by_law = load_articles(args.packs_dir)

    if args.all_laws:
        mapping = {}
        for lid, rec in laws.items():
            if not by_law.get(lid):
                continue
            cat = rec.get("category")
            st = {"دستور": "constitution", "اتفاقية": "other", "حكم": "judicial_precedent"}.get(cat, "other")
            mapping[lid] = (st, rec.get("title") or f"قانون {lid}", None, "مجلس النواب")
    else:
        mapping = CODE_MAP

    documents = []
    for lid, (source_type, title_ar, title_en, authority) in sorted(mapping.items()):
        records = by_law.get(lid) or []
        if not records:
            print(f"  ! law {lid} ({title_ar}) has no records - skipped")
            continue
        law = laws.get(lid, {})
        text = render_document(law, records, title_ar)
        numbered = sum(1 for a in records if str(a.get("article_number") or "").isdigit())
        documents.append({
            "document": {
                "source_type": source_type,
                "title_ar": title_ar,
                "title_en": title_en,
                "law_number": str(law.get("law_number")) if law.get("law_number") else None,
                "promulgation_year": int(law["year"]) if str(law.get("year") or "").isdigit() else None,
                "issuing_authority": authority,
                "jurisdiction": "الجمهورية اليمنية",
                "source_url": "https://github.com/yahyaaljudiey-rgb/yemeni-laws",
                "metadata": {"law_id": lid, "article_count": numbered},
            },
            "text": text,
        })
        print(f"  + {title_ar:<52} {numbered:>5} articles, {len(text):>8} chars")

    out = {
        "corpus_version": "2.0.0",
        "jurisdiction": "الجمهورية اليمنية",
        "note": (
            "النصوص الرسمية للقوانين اليمنية. المصدر: مجموعة القوانين اليمنية "
            "(yemeni-laws). دستور 2001 المعدل هو النسخة السارية. "
            "يجب التحقق من النص الرسمي في الجريدة الرسمية قبل الاستخدام في حكم نهائي."
        ),
        "documents": documents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(d["document"]["metadata"]["article_count"] for d in documents)
    print(f"\nwrote {args.out} - {len(documents)} laws, {total} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
