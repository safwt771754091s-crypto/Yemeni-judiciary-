-- ===========================================================================
--  Legal AI Platform (Yemen) - Supabase / PostgreSQL + pgvector schema
--  File: sql/001_init_pgvector.sql
--  Run in: Supabase SQL Editor  (or `psql "$SUPABASE_DB_URL" -f sql/001_init_pgvector.sql`)
--  Idempotent: safe to re-run.
-- ===========================================================================

create extension if not exists vector;      -- pgvector
create extension if not exists pg_trgm;     -- trigram similarity (Arabic fuzzy match)
create extension if not exists unaccent;    -- optional latin accent folding
create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------------------
-- 1. ENUMS
-- ---------------------------------------------------------------------------
do $$ begin
  create type legal_source_type as enum (
    'civil_law','commercial_law','evidence_law','civil_procedure',
    'criminal_law','criminal_procedure','family_law','labour_law',
    'constitution','executive_regulation','judicial_precedent','fatwa','other'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type case_status as enum ('draft','submitted','under_review','judgment_ready','closed','archived');
exception when duplicate_object then null; end $$;

do $$ begin
  create type judgment_status as enum ('draft','ai_generated','human_reviewed','approved','rejected','final');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- 2. LEGAL DOCUMENTS  (one row per statute / code)
-- ---------------------------------------------------------------------------
create table if not exists legal_documents (
    id             uuid primary key default uuid_generate_v4(),
    source_type    legal_source_type not null default 'other',
    title_ar       text not null,                      -- اسم القانون
    title_en       text,
    law_number     text,                               -- رقم القانون
    promulgation_year int,                             -- سنة الإصدار
    issuing_authority text default 'مجلس النواب',
    jurisdiction   text not null default 'الجمهورية اليمنية',
    source_url     text,
    metadata       jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (source_type, law_number, promulgation_year)
);

comment on table legal_documents is 'Yemeni legal codes and statutes (القوانين والتشريعات اليمنية)';

-- ---------------------------------------------------------------------------
-- 3. LEGAL ARTICLES  (chunks + embeddings; the retrieval unit)
-- ---------------------------------------------------------------------------
-- EMBEDDING_DIM must match EMBEDDING_DIM in .env.
--   text-embedding-3-large / -small with dimensions=1536  -> 1536
--   Cohere embed-multilingual-v3                          -> 1024
--   bge-m3                                                -> 1024
create table if not exists legal_articles (
    id             uuid primary key default uuid_generate_v4(),
    document_id    uuid not null references legal_documents(id) on delete cascade,
    source_type    legal_source_type not null default 'other',  -- denormalised for fast filtering
    article_number text,                               -- رقم المادة  e.g. '1045'
    article_label  text,                               -- 'المادة (1045)'
    chapter        text,                               -- الباب / الفصل
    section        text,                               -- القسم
    heading_ar     text,                               -- عنوان المادة
    content_ar     text not null,                      -- النص الكامل
    content_norm   text,                               -- normalised (diacritics/punct stripped) for FTS
    chunk_index    int  not null default 0,
    token_count    int,
    embedding      vector(1536),
    metadata       jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now(),
    unique (document_id, article_number, chunk_index)
);

comment on table legal_articles is 'Chunked + embedded articles of Yemeni law (المواد القانونية)';

-- ANN index (HNSW: no training step, good recall/latency trade-off)
create index if not exists legal_articles_embedding_hnsw
    on legal_articles using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 64);

create index if not exists legal_articles_doc_idx        on legal_articles (document_id);
create index if not exists legal_articles_source_idx     on legal_articles (source_type);
create index if not exists legal_articles_article_idx    on legal_articles (article_number);
-- Arabic full-text search
create index if not exists legal_articles_content_fts
    on legal_articles using gin (to_tsvector('simple', coalesce(content_norm, content_ar)));
-- Trigram index for fuzzy / partial article lookups
create index if not exists legal_articles_content_trgm
    on legal_articles using gin (coalesce(content_norm, content_ar) gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 4. CASES  (القضايا)
-- ---------------------------------------------------------------------------
create table if not exists cases (
    id                uuid primary key default uuid_generate_v4(),
    case_number       text unique,                       -- رقم القضية
    case_type         text not null default 'مدني',      -- مدني/تجاري/جنائي/أسري/عمالي
    court_name        text not null default 'المحكمة الابتدائية',
    circuit           text,                              -- الدائرة
    judge_name        text,                              -- القاضي
    jurisdiction      text not null default 'الجمهورية اليمنية',
    status            case_status not null default 'submitted',
    -- parties
    plaintiff_name    text not null,                     -- المدعي
    plaintiff_details jsonb not null default '{}'::jsonb,
    defendant_name    text not null,                     -- المدعى عليه
    defendant_details jsonb not null default '{}'::jsonb,
    -- claims & responses (structured)
    plaintiff_claims  jsonb not null default '[]'::jsonb,
    defendant_responses jsonb not null default '[]'::jsonb,
    subject_matter    text,                              -- موضوع الدعوى
    claim_value       numeric(18,2),                     -- قيمة الدعوى
    currency          text default 'YER',
    -- free text
    facts_summary     text,
    extra             jsonb not null default '{}'::jsonb,
    created_by        uuid,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index if not exists cases_status_idx     on cases (status);
create index if not exists cases_case_type_idx  on cases (case_type);
create index if not exists cases_created_at_idx on cases (created_at desc);

-- ---------------------------------------------------------------------------
-- 5. CASE EVIDENCE  (الأدلة والأسانيد)
-- ---------------------------------------------------------------------------
create table if not exists case_evidence (
    id            uuid primary key default uuid_generate_v4(),
    case_id       uuid not null references cases(id) on delete cascade,
    submitted_by  text not null default 'plaintiff',     -- plaintiff | defendant | court
    evidence_type text not null default 'document',      -- document|contract|witness|expert|receipt|correspondence|audio|video|photo|digital
    title         text not null,
    description   text,
    -- Yemeni Law of Evidence classification
    proof_category text,                                 -- كتابة|شهادة|قرينة|إقرار|يمين|خبرة|معاينة
    strength      text,                                  -- decisive|strong|moderate|weak|inadmissible
    admissibility text,                                  -- admissible|inadmissible|conditional|requires_verification
    weight        numeric(4,3),                          -- 0..1 probative weight assigned by the engine
    document_ref  text,                                  -- storage path / URL
    mime_type     text,
    file_size     bigint,
    sha256        text,
    extracted_text text,                                 -- OCR / text extraction result
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now()
);

create index if not exists case_evidence_case_idx on case_evidence (case_id);
create index if not exists case_evidence_type_idx on case_evidence (evidence_type);

-- ---------------------------------------------------------------------------
-- 6. JUDGMENTS  (مسودات الأحكام)
-- ---------------------------------------------------------------------------
create table if not exists judgments (
    id               uuid primary key default uuid_generate_v4(),
    case_id          uuid not null references cases(id) on delete cascade,
    version          int  not null default 1,
    status           judgment_status not null default 'ai_generated',
    language         text not null default 'ar',
    model            text,
    -- structured judgment (matches JudgmentDraft schema)
    preamble         jsonb,                              -- الديباجة
    facts            jsonb,                              -- الوقائع
    reasoning        jsonb,                              -- الأسباب والحيثيات
    verdict          jsonb,                              -- المنطوق
    citations        jsonb not null default '[]'::jsonb, -- المواد المستند إليها
    evidence_analysis jsonb not null default '[]'::jsonb,
    markdown_ar      text,                               -- rendered مسودة الحكم
    confidence       numeric(4,3),
    grounding_score  numeric(4,3),                       -- how well verdict is grounded in citations
    warnings         jsonb not null default '[]'::jsonb,
    raw_llm          jsonb,
    created_by       uuid,
    reviewed_by      uuid,
    created_at       timestamptz not null default now(),
    unique (case_id, version)
);

create index if not exists judgments_case_idx   on judgments (case_id);
create index if not exists judgments_status_idx on judgments (status);

-- ---------------------------------------------------------------------------
-- 7. AUDIT LOG
-- ---------------------------------------------------------------------------
create table if not exists audit_log (
    id         bigserial primary key,
    entity     text not null,
    entity_id  uuid,
    action     text not null,
    actor      text,
    payload    jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists audit_log_entity_idx on audit_log (entity, entity_id);

-- ---------------------------------------------------------------------------
-- 8. updated_at trigger
-- ---------------------------------------------------------------------------
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

drop trigger if exists trg_legal_documents_updated on legal_documents;
create trigger trg_legal_documents_updated before update on legal_documents
    for each row execute function set_updated_at();
drop trigger if exists trg_cases_updated on cases;
create trigger trg_cases_updated before update on cases
    for each row execute function set_updated_at();
