-- Verox live experience library. Apply in the intended Verox Supabase project only.
-- This migration does not alter legacy Nexus tables or financial balances.
create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists public.verox_experiences (
  id uuid primary key default gen_random_uuid(),
  source_url text not null,
  source_domain text not null,
  title text not null,
  content text not null,
  content_hash text not null unique,
  observed_at timestamptz not null default now(),
  trust text not null default 'unverified'
    check (trust in ('unverified', 'community', 'official', 'human_verified')),
  tags text[] not null default '{}',
  embedding vector(768),
  created_at timestamptz not null default now()
);

create index if not exists verox_experiences_observed_at_idx
  on public.verox_experiences (observed_at desc);
create index if not exists verox_experiences_source_domain_idx
  on public.verox_experiences (source_domain);
create index if not exists verox_experiences_tags_idx
  on public.verox_experiences using gin (tags);

alter table public.verox_experiences enable row level security;
-- No client-facing policies: access is server-side only until explicit user-scoped
-- policies and an authenticated ingestion flow are implemented.
revoke all on public.verox_experiences from anon, authenticated;
grant select, insert, update, delete on public.verox_experiences to service_role;

create or replace function public.match_verox_experiences(
  query_embedding vector(768),
  match_count int default 5
)
returns table (
  id uuid,
  source_url text,
  source_domain text,
  title text,
  content text,
  observed_at timestamptz,
  trust text,
  tags text[],
  similarity float
)
language sql stable
as $$
  select e.id, e.source_url, e.source_domain, e.title, e.content,
         e.observed_at, e.trust, e.tags,
         1 - (e.embedding <=> query_embedding) as similarity
  from public.verox_experiences e
  where e.embedding is not null
  order by e.embedding <=> query_embedding
  limit greatest(1, least(match_count, 20));
$$;

revoke all on function public.match_verox_experiences(vector, int) from public, anon, authenticated;
grant execute on function public.match_verox_experiences(vector, int) to service_role;
