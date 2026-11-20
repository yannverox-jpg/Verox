-- Verox durable agent task RPC for Vercel Queues.
-- The server token hash is provisioned separately and is never committed to Git.
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table if not exists private.verox_service_secrets (
  name text primary key,
  token_hash text not null,
  updated_at timestamptz not null default now()
);
revoke all on private.verox_service_secrets from public, anon, authenticated;

-- Provision private.verox_service_secrets(name='verox-task-api', token_hash=SHA256(VEROX_INTERNAL_TASK_TOKEN))
-- through a trusted database session before enabling task endpoints.
create or replace function public.verox_task_api(
  p_token text,
  p_action text,
  p_task_id uuid default null,
  p_task_type text default null,
  p_payload jsonb default '{}'::jsonb,
  p_result jsonb default null,
  p_error text default null,
  p_status text default null
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, private
as $$
declare
  v_task public.nexus_tasks%rowtype;
  v_expected text;
  v_client_ip text;
begin
  select token_hash into v_expected from private.verox_service_secrets where name = 'verox-task-api';
  if v_expected is null or p_token is null
     or encode(sha256(convert_to(p_token, 'UTF8')), 'hex') <> v_expected then
    raise exception 'unauthorized';
  end if;

  if p_action = 'create' then
    if p_task_type is null or length(p_task_type) > 80 then raise exception 'invalid task type'; end if;
    v_client_ip := coalesce(p_payload->>'_client_ip', 'unknown');
    if v_client_ip = 'unknown' or length(v_client_ip) > 64 then raise exception 'client identity unavailable'; end if;
    if (select count(*) from public.nexus_tasks where payload->>'_client_ip' = v_client_ip and created_at > now() - interval '1 hour') >= 20 then
      raise exception 'rate limit exceeded';
    end if;
    insert into public.nexus_tasks(task_type, status, payload, task_key)
    values (p_task_type, 'queued', coalesce(p_payload, '{}'::jsonb), 'verox:' || gen_random_uuid()::text)
    returning * into v_task;
    return jsonb_build_object('id', v_task.id, 'task_type', v_task.task_type, 'status', v_task.status, 'created_at', v_task.created_at);
  elsif p_action = 'get' then
    if p_task_id is null then raise exception 'task id required'; end if;
    select * into v_task from public.nexus_tasks where id = p_task_id;
    if not found then return jsonb_build_object('found', false); end if;
    return jsonb_build_object('found', true, 'id', v_task.id, 'task_type', v_task.task_type, 'status', v_task.status, 'payload', v_task.payload, 'result', v_task.result, 'error', v_task.error, 'attempts', v_task.attempts, 'created_at', v_task.created_at, 'updated_at', v_task.updated_at, 'completed_at', v_task.completed_at);
  elsif p_action = 'complete' or p_action = 'fail' then
    if p_task_id is null then raise exception 'task id required'; end if;
    update public.nexus_tasks set status = case when p_action = 'complete' then 'succeeded' else 'failed' end,
      result = case when p_action = 'complete' then p_result else null end,
      error = case when p_action = 'fail' then left(coalesce(p_error, 'Task failed'), 1000) else null end,
      completed_at = now(), updated_at = now()
    where id = p_task_id and status in ('queued','running');
    if not found then
      select * into v_task from public.nexus_tasks where id = p_task_id;
      if not found then return jsonb_build_object('found', false); end if;
      return jsonb_build_object('id', v_task.id, 'status', v_task.status, 'unchanged', true);
    end if;
    return jsonb_build_object('id', p_task_id, 'status', case when p_action = 'complete' then 'succeeded' else 'failed' end);
  elsif p_action = 'start' then
    if p_task_id is null then raise exception 'task id required'; end if;
    update public.nexus_tasks set status = 'running', attempts = attempts + 1,
      started_at = coalesce(started_at, now()), updated_at = now()
    where id = p_task_id and status = 'queued' returning * into v_task;
    if not found then return jsonb_build_object('accepted', false); end if;
    return jsonb_build_object('accepted', true, 'id', v_task.id, 'status', v_task.status, 'task_type', v_task.task_type, 'payload', v_task.payload);
  elsif p_action = 'metrics' then
    return jsonb_build_object(
      'counts', (select coalesce(jsonb_object_agg(status, n), '{}'::jsonb) from
        (select status, count(*) as n from public.nexus_tasks where created_at > now() - interval '7 days' group by status) s),
      'window_days', 7, 'source', 'public.nexus_tasks', 'verified_at', now()
    );
  else
    raise exception 'unsupported action';
  end if;
end;
$$;
revoke all on function public.verox_task_api(text,text,uuid,text,jsonb,jsonb,text,text) from public;
grant execute on function public.verox_task_api(text,text,uuid,text,jsonb,jsonb,text,text) to anon, authenticated;
