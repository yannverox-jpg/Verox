-- Canonical schema snapshot for the existing Nexus/Verox Supabase project.
-- Safe for bootstrapping an empty database; existing tables are not dropped.
-- Operational changes should be added as timestamped files under supabase/migrations/.

create extension if not exists pgcrypto;

create table if not exists public.nexus_memory (
  id uuid primary key default gen_random_uuid(),
  namespace text not null default 'default',
  memory_key text,
  content jsonb not null,
  source text not null default 'runtime',
  importance smallint not null default 3 check (importance between 1 and 5),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.nexus_tasks (
  id uuid primary key default gen_random_uuid(),
  task_key text unique,
  task_type text not null,
  status text not null default 'queued'
    check (status in ('queued','running','succeeded','failed','cancelled','needs_approval')),
  payload jsonb not null default '{}'::jsonb,
  result jsonb,
  error text,
  attempts integer not null default 0 check (attempts >= 0),
  scheduled_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.nexus_execution_events (
  id bigint generated always as identity primary key,
  task_id uuid references public.nexus_tasks(id) on delete set null,
  event_type text not null,
  severity text not null default 'info'
    check (severity in ('debug','info','warning','error','critical')),
  message text not null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.nexus_sessions (
  id uuid primary key default gen_random_uuid(),
  session_key text unique,
  agent_name text not null default 'nexus',
  status text not null default 'active'
    check (status in ('active','paused','completed','failed')),
  state jsonb not null default '{}'::jsonb,
  last_activity_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.nexus_chat_messages (
  id bigint generated always as identity primary key,
  conversation_id text not null,
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  provider text not null default 'unknown',
  model text not null default 'unknown',
  created_at timestamptz not null default now()
);

create table if not exists public.nexus_memory_facts (
  fact_key text primary key,
  category text not null,
  content text not null,
  source text not null default 'user',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.nexus_runtime_tasks (
  task_id text primary key,
  task_type text not null,
  source text not null default 'NEXUS',
  agent text not null default 'runtime',
  priority text not null default 'MEDIUM',
  status text not null default 'PLANNED',
  payload jsonb not null default '{}'::jsonb,
  result jsonb,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  verified_at timestamptz,
  run_id text,
  parent_task_id text,
  correlation_id text,
  proof jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  mission_id text,
  execution_mode text not null default 'UNDECLARED'
);

create table if not exists public.nexus_runtime_events (
  event_id text primary key,
  run_id text not null,
  task_id text,
  "timestamp" timestamptz not null default now(),
  source text not null default 'nexus',
  agent text not null default 'runtime',
  stage text not null default 'BOOT',
  status text not null,
  action text not null default 'CREATE',
  target text not null default 'runtime',
  proof jsonb not null default '{}'::jsonb,
  result jsonb,
  error text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.nexus_decisions (
  decision_id text primary key,
  run_id text,
  task_id text,
  agent text not null default 'runtime',
  decision_type text not null,
  content jsonb not null default '{}'::jsonb,
  proof jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists nexus_tasks_queue_idx
  on public.nexus_tasks (status, scheduled_at, created_at);
create index if not exists nexus_memory_namespace_created_idx
  on public.nexus_memory (namespace, created_at desc);
create index if not exists nexus_execution_events_task_created_idx
  on public.nexus_execution_events (task_id, created_at desc);

alter table public.nexus_memory enable row level security;
alter table public.nexus_tasks enable row level security;
alter table public.nexus_execution_events enable row level security;
alter table public.nexus_sessions enable row level security;
alter table public.nexus_chat_messages enable row level security;
alter table public.nexus_memory_facts enable row level security;
alter table public.nexus_runtime_tasks enable row level security;
alter table public.nexus_runtime_events enable row level security;
alter table public.nexus_decisions enable row level security;

-- Deliberately no anon/public policies. The trusted server uses SUPABASE_SERVICE_ROLE_KEY.
