-- Verox financial event identity and idempotent reservation entry point.
-- Extends the existing canonical verox_orders / verox_treasury_ledger /
-- verox_event_outbox model. Deliberately does not create parallel financial tables.
-- This records a reservation only; it never contacts an execution provider.

alter table public.verox_orders
  add column if not exists event_id text,
  add column if not exists source text,
  add column if not exists external_reference text;

alter table public.verox_orders
  add constraint verox_orders_event_id_nonblank
    check (event_id is null or length(btrim(event_id)) between 8 and 200),
  add constraint verox_orders_source_nonblank
    check (source is null or length(btrim(source)) between 1 and 80),
  add constraint verox_orders_external_reference_nonblank
    check (external_reference is null or length(btrim(external_reference)) between 1 and 240);

create unique index if not exists verox_orders_event_id_uidx
  on public.verox_orders (event_id) where event_id is not null;

create unique index if not exists verox_orders_source_external_reference_uidx
  on public.verox_orders (source, external_reference)
  where source is not null and external_reference is not null;

comment on column public.verox_orders.event_id is
  'Stable upstream event identity; unique when provided.';
comment on column public.verox_orders.source is
  'Trusted normalized source identifier for the upstream event.';
comment on column public.verox_orders.external_reference is
  'Stable upstream order/event reference; unique per source when provided.';

create or replace function public.commit_validated_bet_order(
  p_event_id text,
  p_source text,
  p_external_reference text,
  p_account_id uuid,
  p_currency text,
  p_stake numeric,
  p_provider text,
  p_market_key text,
  p_selection_key text,
  p_order_payload jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_event_id text := pg_catalog.btrim(p_event_id);
  v_source text := pg_catalog.btrim(p_source);
  v_external_reference text := pg_catalog.btrim(p_external_reference);
  v_currency text := pg_catalog.upper(pg_catalog.btrim(p_currency));
  v_provider text := pg_catalog.btrim(p_provider);
  v_market_key text := pg_catalog.btrim(p_market_key);
  v_selection_key text := pg_catalog.btrim(p_selection_key);
  v_payload jsonb;
  v_request_hash bytea;
  v_existing public.verox_orders%rowtype;
  v_order_id uuid;
  v_order_status text;
  v_idempotent_replay boolean;
  v_remaining_balance numeric(20,6);
  v_outbox_id bigint;
begin
  if v_event_id is null or pg_catalog.length(v_event_id) not between 8 and 200 then
    raise exception using errcode = '22023', message = 'event_id must be between 8 and 200 characters';
  end if;
  if v_source is null or pg_catalog.length(v_source) not between 1 and 80 then
    raise exception using errcode = '22023', message = 'source must be between 1 and 80 characters';
  end if;
  if v_external_reference is null or pg_catalog.length(v_external_reference) not between 1 and 240 then
    raise exception using errcode = '22023', message = 'external_reference must be between 1 and 240 characters';
  end if;
  if p_account_id is null then
    raise exception using errcode = '22023', message = 'account_id is required';
  end if;
  if v_currency !~ '^[A-Z]{3}$' then
    raise exception using errcode = '22023', message = 'currency must be a three-letter uppercase code';
  end if;
  if p_stake is null or p_stake::text in ('NaN', 'Infinity', '-Infinity')
     or p_stake <= 0 or p_stake <> pg_catalog.round(p_stake, 6) then
    raise exception using errcode = '22023', message = 'stake must be finite, positive and have at most six decimal places';
  end if;
  if v_provider is null or pg_catalog.length(v_provider) not between 1 and 80
     or v_market_key is null or pg_catalog.length(v_market_key) not between 1 and 240
     or v_selection_key is null or pg_catalog.length(v_selection_key) not between 1 and 240 then
    raise exception using errcode = '22023', message = 'provider, market_key and selection_key are required';
  end if;

  v_payload := coalesce(p_order_payload, '{}'::jsonb);
  if pg_catalog.jsonb_typeof(v_payload) <> 'object' then
    raise exception using errcode = '22023', message = 'order_payload must be a JSON object';
  end if;
  v_payload := v_payload || pg_catalog.jsonb_build_object(
    'event_id', v_event_id,
    'source', v_source,
    'external_reference', v_external_reference
  );
  if pg_catalog.octet_length(pg_catalog.convert_to(v_payload::text, 'UTF8')) > 32768 then
    raise exception using errcode = '22023', message = 'order_payload exceeds the 32 KiB limit';
  end if;

  -- Stable request fingerprint mirrors reserve_validated_order's canonical fields.
  v_request_hash := extensions.digest(
    pg_catalog.convert_to(
      pg_catalog.jsonb_build_object(
        'account_id', p_account_id,
        'currency', v_currency,
        'stake', p_stake::numeric(20,6),
        'provider', v_provider,
        'market_key', v_market_key,
        'selection_key', v_selection_key,
        'payload', v_payload
      )::text,
      'UTF8'
    ),
    'sha256'
  );

  -- Serialize retries by both stable event identity and source/reference identity.
  -- The account-row FOR UPDATE inside reserve_validated_order separately serializes
  -- balance updates; advisory locks here protect identities across accounts/currencies.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('verox:event:' || v_event_id, 0)
  );
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('verox:external:' || v_source || ':' || v_external_reference, 0)
  );

  select * into v_existing
    from public.verox_orders o
    where o.event_id = v_event_id;
  if found then
    if v_existing.request_hash <> v_request_hash
       or v_existing.source is distinct from v_source
       or v_existing.external_reference is distinct from v_external_reference then
      raise exception using errcode = '22023',
        message = 'event_id already exists with a different request or external identity';
    end if;
    return pg_catalog.jsonb_build_object(
      'status', 'duplicate_ignored',
      'order_id', v_existing.id,
      'event_id', v_event_id,
      'order_status', v_existing.status,
      'execution_status', 'not_submitted',
      'idempotent_replay', true
    );
  end if;

  select * into v_existing
    from public.verox_orders o
    where o.source = v_source and o.external_reference = v_external_reference;
  if found then
    if v_existing.request_hash <> v_request_hash or v_existing.event_id is distinct from v_event_id then
      raise exception using errcode = '22023',
        message = 'source/external_reference already exists with a different event or request';
    end if;
    return pg_catalog.jsonb_build_object(
      'status', 'duplicate_ignored',
      'order_id', v_existing.id,
      'event_id', v_event_id,
      'order_status', v_existing.status,
      'execution_status', 'not_submitted',
      'idempotent_replay', true
    );
  end if;

  select r.order_id, r.order_status, r.idempotent_replay
    into v_order_id, v_order_status, v_idempotent_replay
    from public.reserve_validated_order(
      p_account_id,
      v_currency,
      p_stake,
      v_provider,
      v_market_key,
      v_selection_key,
      v_event_id,
      v_payload
    ) as r;

  -- The unique indexes remain the final guard against unexpected writers.
  update public.verox_orders
    set event_id = v_event_id,
        source = v_source,
        external_reference = v_external_reference,
        updated_at = pg_catalog.now()
    where id = v_order_id;

  -- The ledger entry remains immutable; its order_id links to the identity fields
  -- persisted on verox_orders. Do not update the ledger after insertion.

  select a.available_balance into v_remaining_balance
    from public.verox_treasury_accounts a
    where a.id = p_account_id;

  select o.id into v_outbox_id
    from public.verox_event_outbox o
    where o.idempotency_key = 'order-reserved:' || v_event_id;

  return pg_catalog.jsonb_build_object(
    'status', case when v_idempotent_replay then 'duplicate_ignored' else 'reserved' end,
    'order_id', v_order_id,
    'event_id', v_event_id,
    'outbox_id', v_outbox_id,
    'order_status', v_order_status,
    'execution_status', 'not_submitted',
    'remaining_balance', v_remaining_balance,
    'idempotent_replay', v_idempotent_replay
  );
end;
$function$;

revoke all on function public.commit_validated_bet_order(
  text, text, text, uuid, text, numeric, text, text, text, jsonb
) from public, anon, authenticated;

grant execute on function public.commit_validated_bet_order(
  text, text, text, uuid, text, numeric, text, text, text, jsonb
) to service_role;
