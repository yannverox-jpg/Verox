-- Canonical atomic claim + bounded worker leases for public.nexus_tasks.
-- Applied to Supabase project nruxwnfiwxeomeqalgat; retained here for repository auditability.

alter table public.nexus_tasks
  add column if not exists worker_id text,
  add column if not exists lease_expires_at timestamptz,
  add column if not exists heartbeat_at timestamptz,
  add column if not exists max_attempts integer not null default 3,
  add column if not exists last_error text,
  add column if not exists department_id text,
  add column if not exists strategy_id text,
  add column if not exists mission_id text;

alter table public.nexus_tasks
  drop constraint if exists nexus_tasks_max_attempts_check;
alter table public.nexus_tasks
  add constraint nexus_tasks_max_attempts_check check (max_attempts between 1 and 20);

create index if not exists nexus_tasks_claim_idx
  on public.nexus_tasks (status, scheduled_at, created_at, id);
create index if not exists nexus_tasks_expired_lease_idx
  on public.nexus_tasks (lease_expires_at)
  where status = 'running' and lease_expires_at is not null;

create or replace function public.claim_next_nexus_task(
  p_worker_id text,
  p_lease_seconds integer default 60
)
returns setof public.nexus_tasks
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then
    raise exception 'p_worker_id is required';
  end if;
  if p_lease_seconds < 15 or p_lease_seconds > 900 then
    raise exception 'p_lease_seconds must be between 15 and 900';
  end if;

  return query
  with candidate as (
    select t.id
    from public.nexus_tasks as t
    where t.status = 'queued'
      and t.scheduled_at <= pg_catalog.now()
      and t.attempts < t.max_attempts
    order by t.scheduled_at asc, t.created_at asc, t.id asc
    for update skip locked
    limit 1
  )
  update public.nexus_tasks as t
  set status = 'running',
      worker_id = p_worker_id,
      lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
      heartbeat_at = pg_catalog.now(),
      attempts = t.attempts + 1,
      started_at = pg_catalog.now(),
      updated_at = pg_catalog.now(),
      last_error = null
  from candidate
  where t.id = candidate.id
  returning t.*;
end;
$$;

create or replace function public.reclaim_expired_nexus_tasks(
  p_batch_size integer default 50
)
returns table (
  task_id uuid,
  new_status text,
  attempts integer
)
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if p_batch_size < 1 or p_batch_size > 500 then
    raise exception 'p_batch_size must be between 1 and 500';
  end if;

  return query
  with expired as (
    select t.id
    from public.nexus_tasks as t
    where t.status = 'running'
      and t.lease_expires_at is not null
      and t.lease_expires_at < pg_catalog.now()
    order by t.lease_expires_at asc, t.id asc
    for update skip locked
    limit p_batch_size
  ),
  updated as (
    update public.nexus_tasks as t
    set status = case when t.attempts >= t.max_attempts then 'failed' else 'queued' end,
        worker_id = null,
        lease_expires_at = null,
        heartbeat_at = null,
        completed_at = case when t.attempts >= t.max_attempts then pg_catalog.now() else null end,
        error = case when t.attempts >= t.max_attempts
          then 'Lease expired after reaching max_attempts'
          else null end,
        last_error = case when t.attempts >= t.max_attempts
          then 'Lease expired after reaching max_attempts'
          else 'Lease expired; task reclaimed for retry' end,
        updated_at = pg_catalog.now()
    from expired
    where t.id = expired.id
    returning t.id, t.status, t.attempts
  )
  select updated.id, updated.status, updated.attempts from updated;
end;
$$;

revoke all on function public.claim_next_nexus_task(text, integer) from public, anon, authenticated;
revoke all on function public.reclaim_expired_nexus_tasks(integer) from public, anon, authenticated;
grant execute on function public.claim_next_nexus_task(text, integer) to service_role;
grant execute on function public.reclaim_expired_nexus_tasks(integer) to service_role;
