-- ===========================================================================
--  Legal AI Platform (Yemen) - retrieval & helper functions
--  File: sql/002_functions.sql   (run AFTER 001_init_pgvector.sql)
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Arabic normalisation: strip tashkeel/tatweel, unify alef/ya/ta-marbuta,
-- drop punctuation.  MUST stay byte-compatible with app/core/text_utils.py.
-- ---------------------------------------------------------------------------
create or replace function normalize_arabic(txt text)
returns text language sql immutable as $$
  -- Mirrors app/core/text_utils.normalize_arabic exactly:
  --   1. drop tashkeel / superscript alef / tatweel
  --   2. unify alef, alef-maqsura, ya, hamza variants (one target per source!)
  --   3. ta-marbuta -> ha
  --   4. collapse every run of non-word characters to a single space, then trim
  select case when txt is null then null else
    btrim(
      regexp_replace(
        regexp_replace(
          translate(
            regexp_replace(txt, '[\u064B-\u0652\u0670\u0640]', '', 'g'),
            'أإآٱىئيؤء',
            'ااااييييي'
          ),
          '[\u0629]', 'ه', 'g'
        ),
        '[^\u0621-\u064Aa-zA-Z0-9]+', ' ', 'g'
      )
    )
  end
$$;

-- Arabic-Indic (٠-٩) and extended Arabic-Indic (۰-۹) digits -> ASCII.
-- Kept as a separate function so normalize_arabic stays a single expression;
-- app/core/text_utils.py applies the same mapping.
create or replace function normalize_digits(txt text)
returns text language sql immutable as $$
  select translate(
    coalesce(txt, ''),
    E'\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669'
    || E'\u06F0\u06F1\u06F2\u06F3\u06F4\u06F5\u06F6\u06F7\u06F8\u06F9',
    '01234567890123456789'
  )
$$;

-- ---------------------------------------------------------------------------
-- Canonical form of an article number for exact matching: Arabic-Indic digits
-- to Western, drop every non-digit, and strip leading zeros. This is the SQL
-- counterpart of ``_digits_only`` in app/db/vector_store.py and of
-- ``digitsOnly`` in app/static/statute-search.js, so "١٤٦", "146" and "0146"
-- all resolve to the same article in every mode.
-- ---------------------------------------------------------------------------
create or replace function normalize_article_number(txt text)
returns text language sql immutable as $$
  select ltrim(
    regexp_replace(normalize_digits(coalesce(txt, '')), '\D', '', 'g'),
    '0'
  )
$$;

-- ---------------------------------------------------------------------------
-- Hybrid search: dense (cosine) + lexical (Arabic FTS + trigram), fused with
-- Reciprocal Rank Fusion.  Returns the most relevant Yemeni legal articles.
-- ---------------------------------------------------------------------------
drop function if exists match_legal_articles(vector, text, int, float, text[], text, uuid);
create or replace function match_legal_articles(
    query_embedding  vector(1536),
    query_text       text,
    match_count      int     default 8,
    min_similarity   float   default 0.0,
    filter_sources   text[]  default null,   -- e.g. array['civil_law','evidence_law']
    filter_law_query text    default null,   -- optional ILIKE on law title
    filter_document  uuid    default null,
    rrf_k            int     default 60,
    vector_weight    float   default 0.65,
    keyword_weight   float   default 0.35
)
returns table (
    article_id     uuid,
    document_id    uuid,
    source_type    legal_source_type,
    title_ar       text,
    law_number     text,
    article_number text,
    article_label  text,
    chapter        text,
    heading_ar     text,
    content_ar     text,
    similarity     float,     -- cosine similarity (0..1)
    keyword_rank   int,
    vector_rank    int,
    fused_score    float,
    source_url     text,
    text_quality   text,
    verbatim       boolean
)
language sql stable as $$
with q as (
    select
        normalize_arabic(coalesce(query_text, ''))                       as norm,
        websearch_to_tsquery('simple', normalize_arabic(coalesce(query_text, ''))) as tsq
),
base as (
    select a.id, a.document_id, a.source_type, d.title_ar, d.law_number,
           a.article_number, a.article_label, a.chapter, a.heading_ar, a.content_ar,
           a.embedding, a.content_norm,
           d.source_url,
           d.metadata -> 'provenance' ->> 'quality'  as text_quality,
           (d.metadata -> 'provenance' ->> 'verbatim')::boolean as verbatim
    from legal_articles a
    join legal_documents d on d.id = a.document_id
    where (filter_sources  is null or a.source_type::text = any(filter_sources))
      and (filter_document is null or a.document_id = filter_document)
      and (filter_law_query is null or d.title_ar ilike '%' || filter_law_query || '%')
      and a.embedding is not null
),
scored as (
    select b.*,
           1 - (b.embedding <=> query_embedding)                              as similarity,
           ts_rank(to_tsvector('simple', coalesce(b.content_norm, b.content_ar)), q.tsq) as kw_rank
    from base b cross join q
),
vector_ranked as (
    select id, row_number() over (order by similarity desc) as vrank
    from scored
    order by similarity desc
    limit greatest(match_count * 4, 40)
),
keyword_ranked as (
    select id, row_number() over (order by kw_rank desc, similarity desc) as krank
    from scored
    where kw_rank > 0
       or coalesce(content_norm, content_ar) % (select norm from q)
    order by kw_rank desc, similarity desc
    limit greatest(match_count * 4, 40)
),
fused as (
    select s.id,
           coalesce(vr.vrank, 100000)  as vrank,
           coalesce(kr.krank, 100000)  as krank,
           (vector_weight  * (1.0 / (rrf_k + coalesce(vr.vrank, 100000)))) +
           (keyword_weight * (1.0 / (rrf_k + coalesce(kr.krank, 100000)))) as fused
    from scored s
    left join vector_ranked  vr on vr.id = s.id
    left join keyword_ranked kr on kr.id = s.id
    where vr.id is not null or kr.id is not null
)
select s.id, s.document_id, s.source_type, s.title_ar, s.law_number,
       s.article_number, s.article_label, s.chapter, s.heading_ar, s.content_ar,
       s.similarity::float,
       nullif(f.krank, 100000)::int,
       nullif(f.vrank, 100000)::int,
       f.fused::float,
       s.source_url,
       s.text_quality,
       s.verbatim
from fused f
join scored s on s.id = f.id
where s.similarity >= min_similarity
order by f.fused desc, s.similarity desc
limit match_count;
$$;

comment on function match_legal_articles is
  'Hybrid (vector + Arabic FTS + trigram) retrieval over Yemeni legal articles, RRF-fused.';

-- ---------------------------------------------------------------------------
-- Pure lexical lookup by article number (e.g. "المادة 1045")
-- ---------------------------------------------------------------------------
drop function if exists lookup_article_by_number(text, text, int);
create or replace function lookup_article_by_number(
    p_article_number text,
    p_source_type    text default null,
    p_limit          int  default 5
)
returns table (
    article_id uuid, title_ar text, law_number text,
    article_number text, article_label text, content_ar text
)
language sql stable as $$
    select a.id, d.title_ar, d.law_number, a.article_number, a.article_label, a.content_ar
    from legal_articles a
    join legal_documents d on d.id = a.document_id
    where normalize_article_number(p_article_number) <> ''
      and normalize_article_number(a.article_number) =
          normalize_article_number(p_article_number)
      and (p_source_type is null or a.source_type::text = p_source_type)
    order by a.chunk_index
    limit p_limit;
$$;

-- ---------------------------------------------------------------------------
-- Ingestion upsert: one article chunk (used by the Python ingestion service)
-- ---------------------------------------------------------------------------
drop function if exists upsert_legal_article(uuid, text, text, text, text, text, text, text, int, int, vector, jsonb);
create or replace function upsert_legal_article(
    p_document_id    uuid,
    p_source_type    text,
    p_article_number text,
    p_article_label  text,
    p_chapter        text,
    p_section        text,
    p_heading_ar     text,
    p_content_ar     text,
    p_chunk_index    int,
    p_token_count    int,
    p_embedding      vector(1536),
    p_metadata       jsonb default '{}'::jsonb
)
returns uuid language plpgsql as $$
declare v_id uuid;
begin
    insert into legal_articles (
        document_id, source_type, article_number, article_label, chapter, section,
        heading_ar, content_ar, content_norm, chunk_index, token_count, embedding, metadata
    ) values (
        p_document_id, p_source_type::legal_source_type, p_article_number, p_article_label,
        p_chapter, p_section, p_heading_ar, p_content_ar, normalize_arabic(p_content_ar),
        p_chunk_index, p_token_count, p_embedding, coalesce(p_metadata, '{}'::jsonb)
    )
    on conflict (document_id, article_number, chunk_index) do update set
        article_label = excluded.article_label,
        chapter       = excluded.chapter,
        section       = excluded.section,
        heading_ar    = excluded.heading_ar,
        content_ar    = excluded.content_ar,
        content_norm  = excluded.content_norm,
        token_count   = excluded.token_count,
        embedding     = excluded.embedding,
        metadata      = excluded.metadata
    returning id into v_id;
    return v_id;
end $$;

-- ---------------------------------------------------------------------------
-- Ingestion upsert: legal document
-- ---------------------------------------------------------------------------
drop function if exists upsert_legal_document(text, text, text, text, int, text, text, text, jsonb);
create or replace function upsert_legal_document(
    p_source_type     text,
    p_title_ar        text,
    p_title_en        text default null,
    p_law_number      text default null,
    p_year            int  default null,
    p_issuing_authority text default 'مجلس النواب',
    p_jurisdiction    text default 'الجمهورية اليمنية',
    p_source_url      text default null,
    p_metadata        jsonb default '{}'::jsonb
)
returns uuid language plpgsql as $$
declare v_id uuid;
begin
    insert into legal_documents (
        source_type, title_ar, title_en, law_number, promulgation_year,
        issuing_authority, jurisdiction, source_url, metadata
    ) values (
        p_source_type::legal_source_type, p_title_ar, p_title_en, p_law_number, p_year,
        p_issuing_authority, p_jurisdiction, p_source_url, coalesce(p_metadata, '{}'::jsonb)
    )
    on conflict (source_type, law_number, promulgation_year) do update set
        title_ar   = excluded.title_ar,
        title_en   = excluded.title_en,
        source_url = excluded.source_url,
        metadata   = legal_documents.metadata || excluded.metadata
    returning id into v_id;
    return v_id;
end $$;

-- ---------------------------------------------------------------------------
-- Corpus statistics (used by /ingestion/stats)
-- ---------------------------------------------------------------------------
create or replace function legal_corpus_stats()
returns table (
    source_type text, documents bigint, articles bigint, embedded bigint, total_tokens bigint
)
language sql stable as $$
    select a.source_type::text, count(distinct a.document_id), count(*),
           count(a.embedding), coalesce(sum(a.token_count), 0)
    from legal_articles a
    group by a.source_type
    order by 3 desc;
$$;
