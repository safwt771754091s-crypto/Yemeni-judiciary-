-- =============================================================================
-- 005_portal_workspace.sql — role-portal workspace items
--
-- A workspace item is any unit of work a portal owns: a hearing session, a memo,
-- a contract, an investigation record, a charge decision or a research note. The
-- same table serves every portal; ``kind`` distinguishes them.
--
-- Idempotent: safe to re-run on an existing database.
-- =============================================================================

create table if not exists portal_workspace_items (
    id           uuid primary key default uuid_generate_v4(),
    owner_id     uuid not null references platform_users(id) on delete cascade,
    kind         text not null,                 -- session|memo|contract|investigation|charge|note
    title        text not null,
    case_id      uuid references cases(id) on delete set null,
    case_type    text,
    status       text not null default 'draft', -- draft|active|scheduled|closed|archived
    scheduled_at timestamptz,
    body         text,
    payload      jsonb not null default '{}',
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists idx_portal_workspace_kind
    on portal_workspace_items (kind, status);
create index if not exists idx_portal_workspace_owner
    on portal_workspace_items (owner_id, updated_at desc);
create index if not exists idx_portal_workspace_case
    on portal_workspace_items (case_id);
create index if not exists idx_portal_workspace_schedule
    on portal_workspace_items (scheduled_at);

drop trigger if exists trg_portal_workspace_updated on portal_workspace_items;
create trigger trg_portal_workspace_updated
    before update on portal_workspace_items
    for each row execute function set_updated_at();
