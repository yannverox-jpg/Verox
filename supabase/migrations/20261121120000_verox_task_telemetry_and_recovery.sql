-- Verox task lifecycle telemetry and stale-task recovery.
-- Uses the same server-only token hash provisioned for verox_task_api.
create table if not exists public.verox_task_events (
  event_id bigint generated always as identity primary key,
  task_id uuid not null,
  event_type text not null check (length(event_type) between 1 and 80),
  agent_role text not null default 'unknown' check (length(agent_role) <= 100),
  task_status text not null default 'unknown' check (length(task_status) <= 32),
  details jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index if not exists verox_task_events_task_time_idx
  on public.verox_task_events(task_id, occurred_at);
create index if not exists verox_task_events_role_time_idx
  on public.verox_task_events(agent_role, occurred_at desc);
alter table public.verox_task_events enable row level security;
revoke all on public.verox_task_events from public, anon, authenticated;

create or replace function public.verox_task_telemetry_api(
  p_token text,
  p_action text,
  p_task_id uuid default null,
  p_event_type text default null,
  p_role text default 'unknown',
  p_status text default 'unknown',
  p_details jsonb default '{}'::jsonb,
  p_occurred_at timestamptz default now()
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, private
as $$
declare
  v_expected text;
  v_ids jsonb;
begin
  select token_hash into v_expected
    from private.verox_service_secrets where name = 'verox-task-api';
  if v_expected is null or p_token is null
     or encode(sha256(convert_to(p_token, 'UTF8')), 'hex') <> v_expected then
    raise exception 'unauthorized';
  end if;

  if p_action = 'event' then
    if p_task_id is null or coalesce(p_event_type, '') = '' then
      raise exception 'task id and event type required';
    end if;
    insert into public.verox_task_events(task_id, event_type, agent_role, task_status, details, occurred_at)
    values (p_task_id, left(p_event_type, 80), left(coalesce(p_role, 'unknown'), 100),
            left(coalesce(p_status, 'unknown'), 32),
            case when jsonb_typeof(p_details) = 'object' then p_details else '{}'::jsonb end,
            coalesce(p_occurred_at, now()));
    return jsonb_build_object('recorded', true);
  elsif p_action = 'recover_stale' then
    -- Requeue interrupted running tasks, and re-publish older queued tasks whose
    -- process may have crashed after the DB update but before queue_send completed.
    with recovered as (
      update public.nexus_tasks
      set status = 'queued', error = 'Récupération automatique après interruption.',
          updated_at = now()
      where (
        status = 'running' and updated_at < now() - interval '5 minutes'
        and attempts < 3
      ) or (
        status = 'queued' and updated_at < now() - interval '2 minutes'
        and attempts < 3
      )
      returning id, payload
    )
    select coalesce(jsonb_agg(jsonb_build_object(
      'task_id', id,
      'agent_role', coalesce(payload->>'agent_role', 'brain_coordination')
    )), '[]'::jsonb) into v_ids from recovered;
    return jsonb_build_object('tasks', v_ids, 'count', jsonb_array_length(v_ids));
  elsif p_action = 'metrics' then
    return jsonb_build_object(
      'event_counts', (select coalesce(jsonb_object_agg(event_type, n), '{}'::jsonb)
        from (select event_type, count(*) n from public.verox_task_events
              where occurred_at > now() - interval '7 days' group by event_type) s),
      'role_counts', (select coalesce(jsonb_object_agg(agent_role, n), '{}'::jsonb)
        from (select agent_role, count(*) n from public.verox_task_events
              where occurred_at > now() - interval '7 days' group by agent_role) s),
      'window_days', 7, 'verified_at', now()
    );
  else
    raise exception 'unsupported action';
  end if;
end;
$$;
revoke all on function public.verox_task_telemetry_api(text,text,uuid,text,text,text,jsonb,timestamptz) from public;
grant execute on function public.verox_task_telemetry_api(text,text,uuid,text,text,text,jsonb,timestamptz) to anon, authenticated;
