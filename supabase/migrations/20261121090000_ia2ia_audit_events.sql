-- Append-only ledger for traceable IA-to-IA missions and commercial outcomes.
-- Raw prompts, credentials, personal data and full payloads must not be stored here.
create table if not exists public.verox_ia2ia_audit_events (
    id bigint generated always as identity primary key,
    event_key text not null unique check (length(event_key) between 1 and 200),
    event_type text not null check (length(event_type) between 1 and 100),
    mission_id text,
    actor_type text not null,
    actor_id text,
    counterparty_type text,
    counterparty_id text,
    direction text,
    status text not null,
    request_hash text check (request_hash is null or request_hash ~ '^[0-9a-f]{64}$'),
    result_hash text check (result_hash is null or result_hash ~ '^[0-9a-f]{64}$'),
    evidence_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_refs) = 'array'),
    economics jsonb not null default '{}'::jsonb check (jsonb_typeof(economics) = 'object'),
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    source_occurred_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists verox_ia2ia_audit_mission_created_idx
    on public.verox_ia2ia_audit_events (mission_id, created_at desc);
create index if not exists verox_ia2ia_audit_type_created_idx
    on public.verox_ia2ia_audit_events (event_type, created_at desc);
create index if not exists verox_ia2ia_audit_status_created_idx
    on public.verox_ia2ia_audit_events (status, created_at desc);

alter table public.verox_ia2ia_audit_events enable row level security;
revoke all on public.verox_ia2ia_audit_events from anon, authenticated;
grant select, insert on public.verox_ia2ia_audit_events to service_role;
grant usage, select on sequence public.verox_ia2ia_audit_events_id_seq to service_role;

create or replace function public.verox_reject_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'verox_ia2ia_audit_events is append-only';
end;
$$;

drop trigger if exists verox_ia2ia_audit_immutable on public.verox_ia2ia_audit_events;
create trigger verox_ia2ia_audit_immutable
before update or delete on public.verox_ia2ia_audit_events
for each row execute function public.verox_reject_audit_mutation();

comment on table public.verox_ia2ia_audit_events is
'Append-only trace of IA-to-IA work: events, hashes, evidence references and separated estimated/actual economics. Never store raw prompts, secrets or payment credentials.';
