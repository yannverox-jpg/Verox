-- Domain datasets for Verox behavioral intelligence.
-- Server-only tables: public browser roles receive no grants.
create table if not exists public.verox_sports_odds_snapshots (
  id uuid primary key default gen_random_uuid(),
  event_key text not null,
  market_key text not null,
  bookmaker text not null,
  outcome_prices jsonb not null,
  observed_at timestamptz not null,
  available_at timestamptz not null,
  source_id text references public.verox_sources(source_id) on delete restrict,
  source_url text not null check (source_url like 'https://%'),
  source_quality numeric(5,4) check (source_quality is null or source_quality between 0 and 1),
  snapshot_hash text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (event_key, market_key, bookmaker, observed_at, snapshot_hash)
);
create index if not exists verox_sports_odds_event_time_idx
  on public.verox_sports_odds_snapshots(event_key, market_key, observed_at);

create table if not exists public.verox_sports_event_results (
  event_key text primary key,
  competition text,
  start_time timestamptz,
  completed_at timestamptz,
  result jsonb not null,
  event_statistics jsonb not null default '{}'::jsonb,
  lineup_data jsonb not null default '{}'::jsonb,
  source_refs jsonb not null default '[]'::jsonb,
  verification_status text not null default 'unverified'
    check (verification_status in ('unverified','verified','rejected')),
  updated_at timestamptz not null default now()
);

create table if not exists public.verox_market_observations (
  id uuid primary key default gen_random_uuid(),
  instrument_key text not null,
  venue_key text not null,
  observation_type text not null check (observation_type in ('quote','trade','order_book','spread','news','session_marker')),
  event_time timestamptz not null,
  received_at timestamptz not null,
  bid numeric,
  ask numeric,
  spread numeric,
  last_price numeric,
  volume numeric,
  payload jsonb not null default '{}'::jsonb,
  source_id text references public.verox_sources(source_id) on delete restrict,
  source_url text check (source_url is null or source_url like 'https://%'),
  verification_status text not null default 'unverified'
    check (verification_status in ('unverified','verified','rejected','stale')),
  created_at timestamptz not null default now(),
  check (bid is null or bid >= 0),
  check (ask is null or ask >= 0),
  check (spread is null or spread >= 0)
);
create index if not exists verox_market_observations_instrument_time_idx
  on public.verox_market_observations(instrument_key, event_time);
create index if not exists verox_market_observations_received_idx
  on public.verox_market_observations(received_at);

create table if not exists public.verox_supplier_catalog_snapshots (
  id uuid primary key default gen_random_uuid(),
  supplier_key text not null,
  supplier_product_key text not null,
  observed_at timestamptz not null,
  product_name text not null,
  product_url text not null check (product_url like 'https://%'),
  currency text,
  unit_price numeric check (unit_price is null or unit_price >= 0),
  stock_status text not null default 'unknown'
    check (stock_status in ('in_stock','out_of_stock','backorder','unknown')),
  shipping_terms jsonb not null default '{}'::jsonb,
  rights_metadata jsonb not null default '{}'::jsonb,
  source_id text references public.verox_sources(source_id) on delete restrict,
  source_quality numeric(5,4) check (source_quality is null or source_quality between 0 and 1),
  created_at timestamptz not null default now(),
  unique (supplier_key, supplier_product_key, observed_at)
);
create index if not exists verox_supplier_product_time_idx
  on public.verox_supplier_catalog_snapshots(supplier_key, supplier_product_key, observed_at desc);

create table if not exists public.verox_business_opportunities (
  id uuid primary key default gen_random_uuid(),
  opportunity_key text not null unique,
  opportunity_type text not null check (opportunity_type in ('tender','grant','supplier_request','commercial_signal','partnership')),
  title text not null,
  source_url text not null check (source_url like 'https://%'),
  published_at timestamptz,
  deadline_at timestamptz,
  geography text,
  eligibility jsonb not null default '{}'::jsonb,
  value_metadata jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  fit_score numeric(5,4) check (fit_score is null or fit_score between 0 and 1),
  status text not null default 'discovered'
    check (status in ('discovered','review_required','qualified','rejected','expired','submitted')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists verox_business_opportunities_deadline_idx
  on public.verox_business_opportunities(deadline_at) where deadline_at is not null;
create index if not exists verox_business_opportunities_status_idx
  on public.verox_business_opportunities(status, published_at desc);

alter table public.verox_sports_odds_snapshots enable row level security;
alter table public.verox_sports_event_results enable row level security;
alter table public.verox_market_observations enable row level security;
alter table public.verox_supplier_catalog_snapshots enable row level security;
alter table public.verox_business_opportunities enable row level security;

revoke all on table public.verox_sports_odds_snapshots, public.verox_sports_event_results,
  public.verox_market_observations, public.verox_supplier_catalog_snapshots,
  public.verox_business_opportunities from anon, authenticated;
grant select, insert, update, delete on table public.verox_sports_odds_snapshots,
  public.verox_sports_event_results, public.verox_market_observations,
  public.verox_supplier_catalog_snapshots, public.verox_business_opportunities to service_role;

comment on table public.verox_sports_odds_snapshots is 'Timestamped bookmaker price snapshots; odds are evidence, not guaranteed probabilities.';
comment on table public.verox_sports_event_results is 'Verified historical sports results and structured match statistics.';
comment on table public.verox_market_observations is 'Timestamped market observations preserving event time and receive time for latency analysis.';
comment on table public.verox_supplier_catalog_snapshots is 'Supplier catalog history; stock and prices must be revalidated before checkout.';
comment on table public.verox_business_opportunities is 'Public opportunity registry with source evidence and human-review status.';
