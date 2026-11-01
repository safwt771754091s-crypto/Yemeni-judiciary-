-- =============================================================================
-- 003_auth_rbac.sql — platform users, roles, granular permissions and sections
--
-- Adds the identity layer the legal engine was missing: who may read, submit,
-- edit the corpus, approve judgments, and manage the platform itself. The
-- "owner" role is the platform superuser and always holds every permission.
--
-- Idempotent: safe to re-run on an existing database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_type where typname = 'user_role') then
        create type user_role as enum (
            'owner',      -- platform owner: unrestricted
            'admin',      -- administers content and users
            'editor',     -- edits the legal corpus
            'reviewer',   -- reviews/approves judgments
            'viewer'      -- read-only
        );
    end if;
end$$;

-- ---------------------------------------------------------------------------
-- platform_users
-- ---------------------------------------------------------------------------
create table if not exists platform_users (
    id            uuid primary key default uuid_generate_v4(),
    username      text not null unique,
    email         text unique,
    full_name     text not null default '',
    password_hash text not null,
    role          user_role not null default 'viewer',
    is_active     boolean not null default true,
    -- Extra permissions granted on top of the role defaults (owner ignores this).
    permissions   jsonb not null default '[]',
    last_login_at timestamptz,
    created_by    uuid references platform_users(id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists idx_platform_users_role on platform_users (role);

drop trigger if exists trg_platform_users_updated on platform_users;
create trigger trg_platform_users_updated
    before update on platform_users
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- platform_sections — the top-level divisions of the platform (أقسام المنصة).
-- Each legal document may belong to one section.
-- ---------------------------------------------------------------------------
create table if not exists platform_sections (
    id          uuid primary key default uuid_generate_v4(),
    key         text not null unique,
    title_ar    text not null,
    description text,
    icon        text,
    sort_order  int not null default 0,
    is_active   boolean not null default true,
    metadata    jsonb not null default '{}',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists idx_platform_sections_sort on platform_sections (sort_order, title_ar);

drop trigger if exists trg_platform_sections_updated on platform_sections;
create trigger trg_platform_sections_updated
    before update on platform_sections
    for each row execute function set_updated_at();

-- Link documents to a section without breaking existing rows.
alter table legal_documents
    add column if not exists section_id uuid references platform_sections(id) on delete set null;

create index if not exists idx_legal_documents_section on legal_documents (section_id);

-- ---------------------------------------------------------------------------
-- Owner account
--
-- The password hash is written by the application on startup (it needs the
-- PBKDF2 implementation), so this migration only guarantees the table exists.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Convenience view: users without their password hash
-- ---------------------------------------------------------------------------
create or replace view platform_users_safe as
    select id, username, email, full_name, role, is_active, permissions,
           last_login_at, created_by, created_at, updated_at
    from platform_users;
