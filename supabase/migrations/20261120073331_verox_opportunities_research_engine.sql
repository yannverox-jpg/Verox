create table if not exists public.verox_opportunities (
  id uuid primary key default gen_random_uuid(),
  dedupe_key text not null unique,
  title text not null,
  summary text not null default '',
  source_name text not null,
  source_url text not null check (source_url like 'https://%'),
  department text not null check (department in ('microtasks_services','trading','sports_horse_betting','ecommerce')),
  published_at text,
  matched_keywords jsonb not null default '[]'::jsonb,
  score smallint not null default 0 check (score between 0 and 100),
  status text not null default 'detected_unverified'
    check (status in ('detected_unverified','triaged','validated','rejected')),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.verox_opportunities enable row level security;
revoke all on table public.verox_opportunities from anon, authenticated, public;

create or replace function public.verox_opportunity_api(
  p_token text,
  p_action text,
  p_items jsonb default '[]'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path to 'pg_catalog', 'public', 'private'
as $function$
declare
  v_expected text;
  v_item jsonb;
  v_title text;
  v_url text;
  v_department text;
  v_count integer := 0;
  v_rows jsonb;
begin
  select token_hash into v_expected
  from private.verox_service_secrets
  where name = 'verox-task-api';

  if v_expected is null or p_token is null
     or encode(sha256(convert_to(p_token, 'UTF8')), 'hex') <> v_expected then
    raise exception 'unauthorized';
  end if;

  if p_action = 'upsert' then
    if jsonb_typeof(coalesce(p_items, '[]'::jsonb)) <> 'array'
       or jsonb_array_length(coalesce(p_items, '[]'::jsonb)) > 250 then
      raise exception 'invalid opportunity batch';
    end if;

    for v_item in select value from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) as x(value)
    loop
      v_title := left(btrim(coalesce(v_item->>'title','')), 300);
      v_url := left(btrim(coalesce(v_item->>'source_url','')), 2000);
      v_department := coalesce(v_item->>'department','microtasks_services');
      if v_title = '' or v_url not like 'https://%' then continue; end if;
      if v_department not in ('microtasks_services','trading','sports_horse_betting','ecommerce') then
        v_department := 'microtasks_services';
      end if;

      insert into public.verox_opportunities (
        dedupe_key, title, summary, source_name, source_url, department,
        published_at, matched_keywords, score, status, last_seen_at, updated_at
      ) values (
        md5(lower(regexp_replace(v_title, '[[:space:]]+', ' ', 'g')) || '|' || lower(v_url)),
        v_title,
        left(coalesce(v_item->>'summary',''), 1500),
        left(coalesce(v_item->>'source_name','Unknown source'), 120),
        v_url,
        v_department,
        left(coalesce(v_item->>'published_at',''), 80),
        case when jsonb_typeof(v_item->'matched_keywords') = 'array'
             then v_item->'matched_keywords' else '[]'::jsonb end,
        least(100, greatest(0, coalesce((v_item->>'score')::integer, 0))),
        'detected_unverified',
        now(),
        now()
      )
      on conflict (dedupe_key) do update set
        summary = excluded.summary,
        source_name = excluded.source_name,
        department = excluded.department,
        published_at = excluded.published_at,
        matched_keywords = excluded.matched_keywords,
        score = excluded.score,
        last_seen_at = now(),
        updated_at = now();

      v_count := v_count + 1;
    end loop;
    return jsonb_build_object('persisted', v_count, 'status', 'ok');
  elsif p_action = 'list' then
    select coalesce(jsonb_agg(to_jsonb(q) order by q.score desc, q.last_seen_at desc), '[]'::jsonb)
    into v_rows
    from (
      select id, title, summary, source_name, source_url, department, published_at,
             matched_keywords, score, status, first_seen_at, last_seen_at, updated_at
      from public.verox_opportunities
      order by score desc, last_seen_at desc
      limit 250
    ) q;
    return jsonb_build_object('opportunities', v_rows, 'count', jsonb_array_length(v_rows));
  else
    raise exception 'unsupported action';
  end if;
end;
$function$;

revoke execute on function public.verox_opportunity_api(text, text, jsonb) from public, authenticated;
grant execute on function public.verox_opportunity_api(text, text, jsonb) to anon;
