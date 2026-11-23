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

-- Atomic, persistent TOTP rate limiting. The key is a server-side HMAC of the client IP.
create table if not exists public.verox_approval_auth_limits (
  ip_key text primary key,
  window_started_at timestamptz not null default now(),
  attempts integer not null default 0 check (attempts >= 0),
  blocked_until timestamptz
);
alter table public.verox_approval_auth_limits enable row level security;
revoke all on public.verox_approval_auth_limits from public, anon, authenticated;
grant select, insert, update, delete on public.verox_approval_auth_limits to service_role;

create or replace function public.verox_approval_auth_attempt(p_ip_key text, p_success boolean)
returns table (allowed boolean, attempts_remaining integer)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare v_row public.verox_approval_auth_limits%rowtype; v_now timestamptz := clock_timestamp();
begin
  if p_ip_key is null or p_ip_key !~ '^[0-9a-f]{64}$' then
    return query select false, 0; return;
  end if;
  insert into public.verox_approval_auth_limits(ip_key, window_started_at, attempts, blocked_until)
  values (p_ip_key, v_now, 0, null) on conflict (ip_key) do nothing;
  select * into v_row from public.verox_approval_auth_limits where ip_key=p_ip_key for update;
  if v_row.blocked_until is not null and v_row.blocked_until > v_now then
    return query select false, 0; return;
  end if;
  if v_row.window_started_at < v_now - interval '10 minutes' then
    update public.verox_approval_auth_limits set window_started_at=v_now, attempts=0, blocked_until=null where ip_key=p_ip_key;
    v_row.attempts := 0;
  end if;
  if p_success then
    update public.verox_approval_auth_limits set attempts=0, blocked_until=null, window_started_at=v_now where ip_key=p_ip_key;
    return query select true, 5; return;
  end if;
  update public.verox_approval_auth_limits
     set attempts=attempts+1,
         blocked_until=case when attempts+1 >= 5 then v_now + interval '15 minutes' else null end
   where ip_key=p_ip_key returning * into v_row;
  return query select (v_row.blocked_until is null), greatest(0,5-v_row.attempts);
end;
$$;
revoke all on function public.verox_approval_auth_attempt(text, boolean) from public, anon, authenticated;
grant execute on function public.verox_approval_auth_attempt(text, boolean) to service_role;
