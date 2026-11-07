-- =============================================================================
-- 007_account_selfservice.sql — email verification, password reset, Google SSO
--
-- Adds the storage the account self-service needs:
--   * platform_users.email_verified / google_sub, so an account can record a
--     confirmed address and be linked to a Google identity;
--   * account_tokens, one-time hashed tokens for password reset and email
--     verification. Only the SHA-256 hash is stored, so a database leak cannot
--     be replayed as a working reset link.
--
-- Idempotent: safe to re-run on an existing database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- platform_users: verification flag and Google link
-- ---------------------------------------------------------------------------
alter table platform_users
    add column if not exists email_verified boolean not null default false;

alter table platform_users
    add column if not exists google_sub text;

-- A Google subject id is unique across accounts; a partial index keeps the
-- many NULL rows (accounts with no Google link) out of the constraint.
create unique index if not exists idx_platform_users_google_sub
    on platform_users (google_sub)
    where google_sub is not null;

-- ---------------------------------------------------------------------------
-- account_tokens — one-time tokens for reset and verification
-- ---------------------------------------------------------------------------
create table if not exists account_tokens (
    id         uuid primary key default uuid_generate_v4(),
    user_id    uuid not null references platform_users(id) on delete cascade,
    -- SHA-256 of the raw token; the raw value only ever lives in the email.
    token_hash text not null unique,
    purpose    text not null check (purpose in ('password_reset', 'email_verify')),
    expires_at timestamptz not null,
    used_at    timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_account_tokens_user_purpose
    on account_tokens (user_id, purpose);

-- A live token is the one a lookup actually needs; index only those.
create index if not exists idx_account_tokens_live
    on account_tokens (token_hash)
    where used_at is null;
