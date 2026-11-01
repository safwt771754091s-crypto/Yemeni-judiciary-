# AGENTS.md

Working notes for agents in this repository (Legal AI Platform — Yemen).

## Setup

The suite is hermetic: no database and no hosted LLM are required.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 39 tests
.venv/bin/python -m ruff check app tests scripts
```

Use the venv interpreter (`.venv/bin/python`); the system Python lacks `pydantic`.

`tests/conftest.py` forces the offline code paths before `app.core.config` is
imported: `SUPABASE_DB_URL=""`, `EMBEDDING_PROVIDER=hash`, `LLM_PROVIDER=mock`.
With no `SUPABASE_DB_URL` the app falls back to `InMemoryVectorStore`, and the
deterministic mock LLM provider stands in for OpenAI/Anthropic. These tests
exercise the real chunking, retrieval, evidence-weighing, citation-validation
and markdown-rendering code paths — only the network edges are stubbed.

Run the API and submit a case end to end:

```bash
.venv/bin/python -m uvicorn app.main:app --port 8000
curl -X POST localhost:8000/api/v1/ingestion/text -d @...   # seed a corpus first
curl -X POST localhost:8000/api/v1/cases/submit -H 'Content-Type: application/json' \
     -d @examples/submit_case.json
```

## Layout

| Path | Role |
|---|---|
| `app/api/v1/routes/` | FastAPI routers: `cases`, `ingestion`, `retrieval`, `health` |
| `app/services/pipeline.py` | End-to-end orchestration for a submission |
| `app/services/retrieval.py` | Query planning, corpus selection, article pinning |
| `app/services/evidence.py` | Weighing under the Yemeni Law of Evidence |
| `app/services/judgment.py` | Draft coercion + Markdown rendering |
| `app/llm/validator.py` | Grounding: drops invented articles and quotes |
| `app/db/vector_store.py` | `PgVectorStore` (pgvector hybrid RRF) + `InMemoryVectorStore` |
| `sql/` | Supabase schema and SQL functions |
| `app/data/corpus/yemeni_laws_full.json` | The authentic corpus (16 codes) |

## Domain invariants

**Article numbers repeat across Yemeni codes.** «المادة 15» exists in the Civil,
Evidence, Commercial and Civil Procedure codes; «المادة 191» in both the Civil
Code and the Civil Procedure Code. A bare number is therefore ambiguous, and
grounding a citation against the wrong statute is a correctness bug, not a
cosmetic one. Three layers cooperate:

- `vector_store.find_by_article_number()` does an exact digit lookup and
  normalises Arabic-Indic and decorated labels («١٩١», «المادة (191)» → `191`)
  so a citation is never resolved by embedding similarity.
- `retrieval.law_priority()` ranks the codes of the case type; a pinned article
  resolves to the code the court would actually apply, with the other
  candidates kept as fallback.
- `validator._law_matches()` indexes retrieved articles per article number and
  lets the citation's own `law_title` select the candidate it grounds against.
  A citation naming a code that does not contain the number is left unverified
  rather than silently grounded in another code's text.

**Identity comes from the case file, never the model.** `judgment_service._coerce_draft()`
overrides party names and court with the submitted values, so a loose generation
cannot rename a party in the draft.

**Chunking is article-aware.** `split_into_articles()` treats only a
line-start «المادة (N)» as a heading; a mid-sentence reference is a
cross-reference and must not start a new chunk (otherwise one article yields two
chunks and a citation can resolve to the wrong text).

**Never fabricate.** With an empty corpus the engine warns
(«لم يتم العثور على نصوص») and returns `grounding_score == 0.0` instead of
inventing articles. Keep the `GROUNDING_CONTRACT` in `app/llm/prompts.py` intact
when editing prompts.

## Conventions

- Arabic text is normalised through `app/core/text_utils.normalize_arabic()`
  before any comparison; do not compare raw Arabic strings.
- Tests that need a corpus use the `seeded_store` fixture (the bundled seed
  sample), not the full corpus, to keep the suite fast.
- `pr1` tracks `fix/grounding-identity-and-n8n-auth`; `main` is the base branch.
