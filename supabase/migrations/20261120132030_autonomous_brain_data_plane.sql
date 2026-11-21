-- Durable data plane for the autonomous Verox brain.
-- No credentials, cookies, session tokens, or secrets belong in these tables.
create table if not exists public.verox_sources (
  source_id text primary key,
  domain text not null check (domain in ('sports_horse_betting','trading','ecommerce','microtasks_services','communities','public_sector')),
  name text not null,
  canonical_url text not null check (canonical_url like 'https://%'),
  source_kind text not null check (source_kind in ('website','rss','api','browser','file','dataset')),
  access_mode text not null default 'public_only' check (access_mode in ('public_only','authorized_account','licensed_api','local_connector')),
  status text not null default 'catalog_only' check (status in ('catalog_only','configured','verified','disabled','blocked')),
  trust_level text not null default 'unrated' check (trust_level in ('unrated','low','medium','high','official')),
  enabled boolean not null default false,
  last_checked_at timestamptz,
  last_success_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.verox_observations (
  id uuid primary key default gen_random_uuid(),
  source_id text not null references public.verox_sources(source_id) on delete restrict,
  subject_key text,
  observed_at timestamptz not null default now(),
  source_published_at timestamptz,
  content_type text,
  content_hash text not null,
  payload jsonb not null default '{}'::jsonb,
  quality_score numeric(5,4) check (quality_score is null or quality_score between 0 and 1),
  verification_status text not null default 'unverified' check (verification_status in ('unverified','verified','rejected','stale')),
  verification_notes text,
  created_at timestamptz not null default now(),
  unique (source_id, content_hash)
);
create index if not exists verox_observations_source_time_idx on public.verox_observations(source_id, observed_at desc);
create index if not exists verox_observations_subject_time_idx on public.verox_observations(subject_key, observed_at desc);
create index if not exists verox_observations_status_idx on public.verox_observations(verification_status, observed_at desc);

create table if not exists public.verox_analysis_runs (
  id uuid primary key default gen_random_uuid(),
  domain text not null check (domain in ('sports_horse_betting','trading','ecommerce','microtasks_services','communities','public_sector')),
  subject_key text,
  status text not null default 'queued' check (status in ('queued','running','succeeded','failed','needs_review')),
  model_provider text,
  model_name text,
  prompt_version text,
  input_observation_ids uuid[] not null default '{}'::uuid[],
  findings jsonb not null default '{}'::jsonb,
  metrics jsonb not null default '{}'::jsonb,
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists verox_analysis_runs_domain_time_idx on public.verox_analysis_runs(domain, created_at desc);
create index if not exists verox_analysis_runs_status_idx on public.verox_analysis_runs(status, created_at);

create table if not exists public.verox_hypotheses (
  id uuid primary key default gen_random_uuid(),
  domain text not null check (domain in ('sports_horse_betting','trading','ecommerce','microtasks_services','communities','public_sector')),
  hypothesis_key text not null unique,
  statement text not null,
  status text not null default 'candidate' check (status in ('candidate','testing','supported','refuted','insufficient_data','retired')),
  sample_size integer not null default 0 check (sample_size >= 0),
  metrics jsonb not null default '{}'::jsonb,
  evidence_summary jsonb not null default '{}'::jsonb,
  validation_window jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists verox_hypotheses_domain_status_idx on public.verox_hypotheses(domain, status);

create table if not exists public.verox_hypothesis_evaluations (
  id uuid primary key default gen_random_uuid(),
  hypothesis_id uuid not null references public.verox_hypotheses(id) on delete restrict,
  analysis_run_id uuid references public.verox_analysis_runs(id) on delete set null,
  dataset_start timestamptz,
  dataset_end timestamptz,
  sample_size integer not null default 0 check (sample_size >= 0),
  metrics jsonb not null default '{}'::jsonb,
  outcome text not null check (outcome in ('supported','refuted','inconclusive','invalidated')),
  notes text,
  evaluated_at timestamptz not null default now()
);
create index if not exists verox_hypothesis_evaluations_history_idx on public.verox_hypothesis_evaluations(hypothesis_id, evaluated_at desc);

create table if not exists public.verox_connector_registry (
  connector_id text primary key,
  connector_type text not null check (connector_type in ('browser_playwright','http_readonly','rss','licensed_api','file_import','local_endpoint')),
  status text not null default 'catalog_only' check (status in ('catalog_only','configured','verified','disabled','blocked')),
  permission_profile text not null default 'read_only' check (permission_profile in ('read_only','read_with_download','human_approved_write')),
  capabilities jsonb not null default '[]'::jsonb,
  configuration jsonb not null default '{}'::jsonb,
  last_tested_at timestamptz,
  last_test_result jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- Store metadata only; credentials must be held in Vercel encrypted environment variables
-- or an approved secret manager, never in connector configuration JSON.

create table if not exists public.verox_browser_jobs (
  id uuid primary key default gen_random_uuid(),
  task_type text not null check (task_type in ('inspect_page','collect_public_data','download_public_file','test_connector','capture_evidence')),
  source_id text references public.verox_sources(source_id) on delete restrict,
  status text not null default 'queued' check (status in ('queued','running','succeeded','failed','needs_approval','cancelled')),
  policy_mode text not null default 'read_only' check (policy_mode in ('read_only','read_with_download','human_approved_write')),
  requested_capabilities jsonb not null default '[]'::jsonb,
  input jsonb not null default '{}'::jsonb,
  artifact_refs jsonb not null default '[]'::jsonb,
  audit jsonb not null default '{}'::jsonb,
  error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);
create index if not exists verox_browser_jobs_status_time_idx on public.verox_browser_jobs(status, created_at);

-- Server-only data plane. RLS remains enabled and browser-facing roles have no table grants.
alter table public.verox_sources enable row level security;
alter table public.verox_observations enable row level security;
alter table public.verox_analysis_runs enable row level security;
alter table public.verox_hypotheses enable row level security;
alter table public.verox_hypothesis_evaluations enable row level security;
alter table public.verox_connector_registry enable row level security;
alter table public.verox_browser_jobs enable row level security;

revoke all on table public.verox_sources, public.verox_observations, public.verox_analysis_runs,
  public.verox_hypotheses, public.verox_hypothesis_evaluations, public.verox_connector_registry,
  public.verox_browser_jobs from anon, authenticated;
grant select, insert, update, delete on table public.verox_sources, public.verox_observations,
  public.verox_analysis_runs, public.verox_hypotheses, public.verox_hypothesis_evaluations,
  public.verox_connector_registry, public.verox_browser_jobs to service_role;

comment on table public.verox_sources is 'Source registry with explicit verification and access status; catalog entries are not assumed live.';
comment on table public.verox_observations is 'Append-oriented historical observations with timestamps, hashes and quality status.';
comment on table public.verox_analysis_runs is 'Traceable analysis runs and structured findings; model outputs are not ground truth.';
comment on table public.verox_hypotheses is 'Falsifiable behavioral hypotheses tracked across independent validation windows.';
comment on table public.verox_hypothesis_evaluations is 'Historical out-of-sample evaluation records for hypotheses.';
comment on table public.verox_connector_registry is 'Connector metadata only; never store credentials, cookies or session tokens here.';
comment on table public.verox_browser_jobs is 'Audited Playwright/browser tasks with read-only policy by default.';
