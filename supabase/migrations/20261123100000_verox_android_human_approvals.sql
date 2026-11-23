-- Verox Android human-approval notifications.
-- No public/anon access: all table access is server-side through the service role.
create table if not exists public.verox_approval_push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  endpoint text not null unique,
  subscription jsonb not null,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  disabled_at timestamptz
);
create table if not exists public.verox_human_approval_requests (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  summary text not null,
  action_type text not null,
  target text not null,
  operation_fingerprint text not null,
  details jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending','approved','rejected','expired','consumed','revoked')),
  requested_by text not null,
  decided_by text,
  decision_reason text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  decided_at timestamptz,
  consumed_at timestamptz,
  constraint verox_approval_fingerprint_sha256 check (operation_fingerprint ~ '^[0-9a-f]{64}$'),
  constraint verox_approval_expiration check (expires_at > created_at)
);
create index if not exists verox_approval_pending_idx
  on public.verox_human_approval_requests(status, expires_at, created_at desc);
alter table public.verox_approval_push_subscriptions enable row level security;
alter table public.verox_human_approval_requests enable row level security;
revoke all on public.verox_approval_push_subscriptions from public, anon, authenticated;
revoke all on public.verox_human_approval_requests from public, anon, authenticated;
grant select, insert, update, delete on public.verox_approval_push_subscriptions to service_role;
grant select, insert, update on public.verox_human_approval_requests to service_role;
