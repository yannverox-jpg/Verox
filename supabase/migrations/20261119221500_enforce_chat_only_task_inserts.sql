-- Enforce that new Verox tasks originate from an explicit frontend chat submission.
-- Existing tasks are unaffected; the worker also filters the queue to these fields.
create or replace function public.enforce_verox_chat_only_task_insert()
returns trigger
language plpgsql
as $$
begin
  if new.payload->>'source' is distinct from 'verox_dashboard'
     or new.payload->>'user_initiated' is distinct from 'true' then
    raise exception 'Task creation is restricted to explicit Verox frontend chat requests'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists nexus_tasks_chat_only_insert on public.nexus_tasks;
create trigger nexus_tasks_chat_only_insert
before insert on public.nexus_tasks
for each row execute function public.enforce_verox_chat_only_task_insert();
