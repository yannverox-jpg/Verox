-- Transactional reservation + outbox foundation.
-- This records a reservation only. It does NOT place a bet, trade, payment, or provider order.
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.verox_treasury_accounts (
  id uuid primary key default gen_random_uuid(),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  available_balance numeric(20,6) not null default 0 check (available_balance >= 0),
  reserved_balance numeric(20,6) not null default 0 check (reserved_balance >= 0),
  status text not null default 'active' check (status in ('active', 'frozen', 'closed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.verox_orders (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.verox_treasury_accounts(id),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  stake numeric(20,6) not null check (stake > 0),
  provider text not null check (length(btrim(provider)) between 1 and 80),
  market_key text not null check (length(btrim(market_key)) between 1 and 240),
  selection_key text not null check (length(btrim(selection_key)) between 1 and 240),
  status text not null default 'reserved'
    check (status in ('reserved', 'queued', 'submitted', 'confirmed', 'rejected',
                      'cancelled', 'expired', 'reconciliation_required')),
  idempotency_key text not null unique check (length(btrim(idempotency_key)) between 8 and 200),
  request_hash bytea not null,
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists verox_orders_account_created_idx
  on public.verox_orders (account_id, created_at desc);
create index if not exists verox_orders_status_created_idx
  on public.verox_orders (status, created_at);

create table if not exists public.verox_treasury_ledger (
  id bigint generated always as identity primary key,
  account_id uuid not null references public.verox_treasury_accounts(id),
  order_id uuid references public.verox_orders(id),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  entry_type text not null check (entry_type in ('reserve', 'capture', 'release', 'credit', 'debit')),
  amount numeric(20,6) not null check (amount > 0),
  idempotency_key text not null unique check (length(btrim(idempotency_key)) between 8 and 240),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists verox_treasury_ledger_account_created_idx
  on public.verox_treasury_ledger (account_id, created_at desc);
create index if not exists verox_treasury_ledger_order_idx
  on public.verox_treasury_ledger (order_id) where order_id is not null;

create table if not exists public.verox_event_outbox (
  id bigint generated always as identity primary key,
  event_type text not null check (length(btrim(event_type)) between 1 and 120),
  aggregate_id uuid not null,
  idempotency_key text not null unique check (length(btrim(idempotency_key)) between 8 and 240),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'published', 'dead')),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text,
  published_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((status = 'processing') = (locked_at is not null and locked_by is not null)),
  check ((status = 'published') = (published_at is not null))
);

create index if not exists verox_event_outbox_pending_idx
  on public.verox_event_outbox (available_at, id)
  where status in ('pending', 'processing');

alter table public.verox_treasury_accounts enable row level security;
alter table public.verox_orders enable row level security;
alter table public.verox_treasury_ledger enable row level security;
alter table public.verox_event_outbox enable row level security;

revoke all on public.verox_treasury_accounts, public.verox_orders,
  public.verox_treasury_ledger, public.verox_event_outbox from public, anon, authenticated;
grant select, insert, update, delete on public.verox_treasury_accounts,
  public.verox_orders, public.verox_treasury_ledger, public.verox_event_outbox to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Reserve funds and persist the order + ledger entry + outbox event in one transaction.
-- Repeating the same key and same request returns the original order. Reusing a key
-- for a different request is rejected. This RPC never contacts an execution provider.
create or replace function public.reserve_validated_order(
  p_account_id uuid,
  p_currency text,
  p_stake numeric,
  p_provider text,
  p_market_key text,
  p_selection_key text,
  p_idempotency_key text,
  p_payload jsonb default '{}'::jsonb
)
returns table (order_id uuid, order_status text, idempotent_replay boolean)
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_account public.verox_treasury_accounts%rowtype;
  v_existing public.verox_orders%rowtype;
  v_currency text;
  v_key text;
  v_provider text;
  v_market text;
  v_selection text;
  v_payload jsonb;
  v_request_hash bytea;
  v_order_id uuid;
begin
  v_currency := pg_catalog.upper(pg_catalog.btrim(p_currency));
  v_key := pg_catalog.btrim(p_idempotency_key);
  v_provider := pg_catalog.btrim(p_provider);
  v_market := pg_catalog.btrim(p_market_key);
  v_selection := pg_catalog.btrim(p_selection_key);
  v_payload := coalesce(p_payload, '{}'::jsonb);

  if p_account_id is null then
    raise exception using errcode = '22023', message = 'account_id is required';
  end if;
  if v_currency !~ '^[A-Z]{3}$' then
    raise exception using errcode = '22023', message = 'currency must be a three-letter uppercase code';
  end if;
  if p_stake is null or p_stake <= 0 or p_stake <> pg_catalog.round(p_stake, 6) then
    raise exception using errcode = '22023', message = 'stake must be positive and have at most six decimal places';
  end if;
  if v_key is null or pg_catalog.length(v_key) < 8 or pg_catalog.length(v_key) > 200 then
    raise exception using errcode = '22023', message = 'idempotency_key length must be between 8 and 200';
  end if;
  if v_provider is null or pg_catalog.length(v_provider) not between 1 and 80
     or v_market is null or pg_catalog.length(v_market) not between 1 and 240
     or v_selection is null or pg_catalog.length(v_selection) not between 1 and 240 then
    raise exception using errcode = '22023', message = 'provider, market_key and selection_key are required and bounded';
  end if;
  if pg_catalog.jsonb_typeof(v_payload) <> 'object' then
    raise exception using errcode = '22023', message = 'payload must be a JSON object';
  end if;

  v_request_hash := extensions.digest(
    pg_catalog.convert_to(
      pg_catalog.jsonb_build_object(
        'account_id', p_account_id,
        'currency', v_currency,
        'stake', p_stake::numeric(20,6),
        'provider', v_provider,
        'market_key', v_market,
        'selection_key', v_selection,
        'payload', v_payload
      )::text,
      'UTF8'
    ),
    'sha256'
  );

  -- Fast replay path. The unique constraint below remains the final concurrency guard.
  select * into v_existing
    from public.verox_orders o
    where o.idempotency_key = v_key;
  if found then
    if v_existing.request_hash <> v_request_hash then
      raise exception using errcode = '22023', message = 'idempotency key reused with a different request';
    end if;
    return query select v_existing.id, v_existing.status, true;
    return;
  end if;

  -- Serialize balance changes for this account. The order/ledger/outbox writes below
  -- share the same transaction and are rolled back together on any error.
  select * into v_account
    from public.verox_treasury_accounts a
    where a.id = p_account_id
    for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'treasury account not found';
  end if;
  if v_account.status <> 'active' then
    raise exception using errcode = '55000', message = 'treasury account is not active';
  end if;
  if v_account.currency <> v_currency then
    raise exception using errcode = '22023', message = 'currency does not match treasury account';
  end if;

  begin
    if v_account.available_balance < p_stake then
      raise exception using errcode = '23514', message = 'insufficient available balance';
    end if;

    update public.verox_treasury_accounts
      set available_balance = available_balance - p_stake,
          reserved_balance = reserved_balance + p_stake,
          updated_at = pg_catalog.now()
      where id = p_account_id;

    insert into public.verox_orders (
      account_id, currency, stake, provider, market_key, selection_key,
      status, idempotency_key, request_hash, payload
    ) values (
      p_account_id, v_currency, p_stake, v_provider, v_market, v_selection,
      'reserved', v_key, v_request_hash, v_payload
    ) returning id into v_order_id;

    insert into public.verox_treasury_ledger (
      account_id, order_id, currency, entry_type, amount, idempotency_key, metadata
    ) values (
      p_account_id, v_order_id, v_currency, 'reserve', p_stake,
      'reserve:' || v_key,
      pg_catalog.jsonb_build_object('source', 'reserve_validated_order')
    );

    insert into public.verox_event_outbox (
      event_type, aggregate_id, idempotency_key, payload
    ) values (
      'verox.order.reserved', v_order_id, 'order-reserved:' || v_key,
      pg_catalog.jsonb_build_object(
        'order_id', v_order_id,
        'status', 'reserved',
        'currency', v_currency,
        'stake', p_stake::numeric(20,6)
      )
    );

    return query select v_order_id, 'reserved'::text, false;
    return;
  exception when unique_violation then
    -- The inner block rolls back the balance update and every insert. If another
    -- transaction won the idempotency race, return that committed order only when
    -- the request fingerprint is identical.
    select * into v_existing
      from public.verox_orders o
      where o.idempotency_key = v_key;
    if not found then
      raise;
    end if;
    if v_existing.request_hash <> v_request_hash then
      raise exception using errcode = '22023', message = 'idempotency key reused with a different request';
    end if;
    return query select v_existing.id, v_existing.status, true;
    return;
  end;
end;
$function$;

revoke all on function public.reserve_validated_order(uuid, text, numeric, text, text, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.reserve_validated_order(uuid, text, numeric, text, text, text, text, jsonb)
  to service_role;
