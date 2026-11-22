create table if not exists public.verox_a2a_traces (
  id uuid primary key default gen_random_uuid(),
  trace_id text not null check (trace_id ~ '^trc_[a-zA-Z0-9_-]{8,64}$'),
  message_id text not null check (message_id ~ '^msg_[a-zA-Z0-9_-]{8,64}$'),
  event_id text not null check (event_id ~ '^evt_[a-zA-Z0-9_-]{8,64}$'),
  parent_message_id text null check (parent_message_id is null or parent_message_id ~ '^msg_[a-zA-Z0-9_-]{8,64}$'),
  parent_event_id text null check (parent_event_id is null or parent_event_id ~ '^evt_[a-zA-Z0-9_-]{8,64}$'),
  opportunity_id text not null check (opportunity_id ~ '^opp_[a-zA-Z0-9_-]{6,64}$'),
  source_agent text not null,
  target_agent text not null,
  execution_mode text not null check (execution_mode in ('read_only', 'read_write')),
  status text not null,
  validation_passed boolean not null default true,
  payload jsonb not null,
  evidence jsonb not null,
  uncertainties jsonb not null,
  next_action jsonb not null,
  created_at timestamptz not null default now(),
  unique (message_id),
  unique (event_id),
  unique (trace_id, message_id, event_id),
  foreign key (trace_id, parent_message_id, parent_event_id)
    references public.verox_a2a_traces (trace_id, message_id, event_id)
    deferrable initially immediate,
  check (
    (source_agent = 'discovery_agent' and parent_message_id is null and parent_event_id is null)
    or
    (source_agent <> 'discovery_agent' and parent_message_id is not null and parent_event_id is not null)
  ),
  check (
    source_agent not in ('discovery_agent', 'verification_agent')
    or execution_mode = 'read_only'
  )
);

create index if not exists idx_verox_a2a_traces_trace_id
  on public.verox_a2a_traces (trace_id, created_at asc);
create index if not exists idx_verox_a2a_traces_opportunity_id
  on public.verox_a2a_traces (opportunity_id, created_at desc);

alter table public.verox_a2a_traces enable row level security;

-- Deliberately no client-facing policies: service-role/server-side access only.
