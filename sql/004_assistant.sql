-- =============================================================================
-- 004_assistant.sql — AI legal assistant (المساعد القانوني الذكي) conversations
--
-- The assistant answers a legal question strictly from retrieved Yemeni
-- statutes and keeps a per-user conversation history so a follow-up question
-- ("وما الحكم إن كان العقد شفهياً؟") is answered in the context of the thread.
--
-- The thread is scoped to the owning user: every read filters on user_id, so a
-- user can never see another user's conversations, whatever their role.
--
-- Idempotent: safe to re-run on an existing database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- assistant_conversations — one row per thread
-- ---------------------------------------------------------------------------
create table if not exists assistant_conversations (
    id          uuid primary key default uuid_generate_v4(),
    user_id     uuid not null references platform_users(id) on delete cascade,
    title       text not null default 'محادثة جديدة',
    -- Optional context: the assistant may be invoked about a specific case.
    case_id     uuid references cases(id) on delete set null,
    -- Domain hint so retrieval can be scoped (مدني/تجاري/جنائي/...).
    case_type   text,
    is_archived boolean not null default false,
    metadata    jsonb not null default '{}',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists idx_assistant_conversations_user
    on assistant_conversations (user_id, updated_at desc);

drop trigger if exists trg_assistant_conversations_updated on assistant_conversations;
create trigger trg_assistant_conversations_updated
    before update on assistant_conversations
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- assistant_messages — the turns of a thread
-- ---------------------------------------------------------------------------
create table if not exists assistant_messages (
    id              uuid primary key default uuid_generate_v4(),
    conversation_id uuid not null references assistant_conversations(id) on delete cascade,
    role            text not null check (role in ('user', 'assistant', 'system')),
    content         text not null,
    -- Retrieved statutes cited by an assistant turn (the grounding evidence).
    citations       jsonb not null default '[]',
    grounding_score double precision,
    model           text,
    provider        text,
    -- Set when the answer could not be grounded and needs human verification.
    needs_review    boolean not null default false,
    created_at      timestamptz not null default now()
);

create index if not exists idx_assistant_messages_conversation
    on assistant_messages (conversation_id, created_at);
