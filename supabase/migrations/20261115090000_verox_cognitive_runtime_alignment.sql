-- Verox CognitiveBrain alignment with the existing Nexus durable runtime schema.
-- No legacy data is dropped or renamed. The worker uses nexus_tasks as its queue,
-- nexus_execution_events as its audit trail, and nexus_memory for persistent memory.
create index if not exists nexus_tasks_queue_idx
  on public.nexus_tasks (status, scheduled_at, created_at);
create index if not exists nexus_memory_namespace_created_idx
  on public.nexus_memory (namespace, created_at desc);
create index if not exists nexus_execution_events_task_created_idx
  on public.nexus_execution_events (task_id, created_at desc);
comment on table public.nexus_tasks is
  'Canonical Verox CognitiveBrain task queue, migrated from Nexus runtime.';
comment on table public.nexus_memory is
  'Canonical persistent cognitive memory for Nexus/Verox.';
comment on table public.nexus_execution_events is
  'Canonical execution audit events for Nexus/Verox.';
