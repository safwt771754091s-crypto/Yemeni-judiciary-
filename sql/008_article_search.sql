-- ---------------------------------------------------------------------------
-- Public statute search: look up an article by its number and/or by a text
-- fragment, with no embedding involved. Powers GET/POST /api/v1/retrieval/articles
-- so the landing-page search box works even when the embedding model is
-- unreachable. The Python in-memory store mirrors this ranking exactly
-- (see InMemoryVectorStore.search_articles).
--
-- Ranking: an exact article-number match always outranks a text hit, and text
-- hits are ordered by normalized-term overlap. When a number is supplied only
-- that number is returned — a caller asking for «المادة 15» wants that article,
-- not articles that merely mention the number.
-- ---------------------------------------------------------------------------
drop function if exists search_legal_articles(text, text, text[], int);
create or replace function search_legal_articles(
    p_query          text  default null,   -- a fragment of the article text
    p_article_number text  default null,   -- the article number (Arabic digits accepted)
    p_source_types   text[] default null,   -- e.g. array['civil_law','evidence_law']
    p_limit          int   default 20
)
returns table (
    article_id     uuid,
    document_id    uuid,
    source_type    text,
    title_ar       text,
    law_number     text,
    article_number text,
    article_label  text,
    chapter        text,
    heading_ar     text,
    content_ar     text,
    source_url     text,
    text_quality   text,
    verbatim       boolean
)
language sql stable as $$
with params as (
    select
      nullif(normalize_article_number(coalesce(p_article_number, '')), '') as num,
      nullif(btrim(coalesce(p_query, '')), '') as q
),
qterms as (
    select array(
        select t
        from unnest(string_to_array(normalize_arabic((select q from params)), ' ')) as t
        where length(t) > 1
    ) as terms
),
scored as (
    select
        a.id as article_id,
        a.document_id,
        a.source_type::text as source_type,
        d.title_ar,
        d.law_number,
        a.article_number,
        a.article_label,
        a.chapter,
        a.heading_ar,
        a.content_ar,
        d.source_url,
        d.metadata -> 'provenance' ->> 'quality' as text_quality,
        (d.metadata -> 'provenance' ->> 'verbatim')::boolean as verbatim,
        a.chunk_index,
        (p.num is not null
         and regexp_replace(normalize_digits(a.article_number), '\D', '', 'g') = p.num) as num_match,
        case
            when cardinality(qt.terms) = 0 then 0.0
            else (
                select count(*)::float
                from unnest(qt.terms) as term
                where term = any (string_to_array(normalize_arabic(a.content_ar), ' '))
            ) / sqrt(
                cardinality(qt.terms)
                * greatest(cardinality(string_to_array(normalize_arabic(a.content_ar), ' ')), 1)
            )
        end as lexical
    from legal_articles a
    join legal_documents d on d.id = a.document_id
    cross join params p
    cross join qterms qt
    where (p_source_types is null or a.source_type::text = any (p_source_types))
      and (p.num is not null or cardinality(qt.terms) > 0)
)
select article_id, document_id, source_type, title_ar, law_number, article_number,
       article_label, chapter, heading_ar, content_ar, source_url, text_quality, verbatim
from scored
where (num_match or (lexical > 0 and p_article_number is null))
order by num_match desc, lexical desc, chunk_index asc
limit p_limit;
$$;

comment on function search_legal_articles is
  'Deterministic article lookup by number and/or text fragment (no embedding needed).';
