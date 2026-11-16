-- Verox reservation lifecycle: atomic release, capture and reconciliation.
-- Safe for disposable PostgreSQL CI. Does not call or submit anything to a provider.
ALTER TABLE public.verox_orders
  ADD COLUMN reservation_status text NOT NULL DEFAULT 'reserved'
    CHECK (reservation_status IN ('reserved', 'captured', 'released', 'reconciliation_required')),
  ADD COLUMN reservation_evidence jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(reservation_evidence) = 'object');

COMMENT ON COLUMN public.verox_orders.reservation_status IS
  'Accounting disposition of the stake; intentionally separate from provider workflow status.';
COMMENT ON COLUMN public.verox_orders.reservation_evidence IS
  'Auditable provider/reconciliation evidence attached to reservation lifecycle transitions.';

CREATE TABLE public.verox_reservation_commands (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id uuid NOT NULL REFERENCES public.verox_orders(id),
  command text NOT NULL CHECK (command IN ('release', 'capture', 'mark_reconciliation', 'reconcile_release', 'reconcile_capture')),
  idempotency_key text NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) BETWEEN 8 AND 200),
  request_hash bytea NOT NULL,
  result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX verox_reservation_commands_order_idx
  ON public.verox_reservation_commands(order_id, created_at);

ALTER TABLE public.verox_reservation_commands ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.verox_reservation_commands FROM PUBLIC, anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public.verox_reservation_commands FROM service_role;
GRANT SELECT ON public.verox_reservation_commands TO service_role;

-- Defense in depth: ledger rows are append-only even if future grants change.
CREATE OR REPLACE FUNCTION public.verox_reject_ledger_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'treasury ledger is append-only';
END;
$$;
REVOKE ALL ON FUNCTION public.verox_reject_ledger_mutation() FROM PUBLIC, anon, authenticated, service_role;
DROP TRIGGER IF EXISTS verox_treasury_ledger_immutable ON public.verox_treasury_ledger;
CREATE TRIGGER verox_treasury_ledger_immutable
  BEFORE UPDATE OR DELETE ON public.verox_treasury_ledger
  FOR EACH ROW EXECUTE FUNCTION public.verox_reject_ledger_mutation();

-- A single narrow RPC implements every lifecycle transition. The caller must pass
-- authoritative evidence for capture/reconciliation; this function never calls a network.
CREATE OR REPLACE FUNCTION public.transition_verox_reservation(
  p_order_id uuid,
  p_command text,
  p_idempotency_key text,
  p_evidence jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
  v_account_id uuid;
  v_account public.verox_treasury_accounts%ROWTYPE;
  v_order public.verox_orders%ROWTYPE;
  v_key text := pg_catalog.btrim(p_idempotency_key);
  v_command text := pg_catalog.btrim(p_command);
  v_evidence jsonb := coalesce(p_evidence, '{}'::jsonb);
  v_hash bytea;
  v_existing public.verox_reservation_commands%ROWTYPE;
  v_result jsonb;
  v_next_status text;
  v_ledger_type text;
  v_outbox_type text;
  v_outcome text;
BEGIN
  IF p_order_id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'order_id is required';
  END IF;
  IF v_command NOT IN ('release', 'capture', 'mark_reconciliation', 'reconcile_release', 'reconcile_capture') THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'unsupported reservation command';
  END IF;
  IF v_key IS NULL OR pg_catalog.length(v_key) NOT BETWEEN 8 AND 200 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'idempotency_key length must be between 8 and 200';
  END IF;
  IF pg_catalog.jsonb_typeof(v_evidence) <> 'object'
     OR pg_catalog.octet_length(pg_catalog.convert_to(v_evidence::text, 'UTF8')) > 16384 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'evidence must be a JSON object no larger than 16 KiB';
  END IF;

  v_hash := extensions.digest(pg_catalog.convert_to(pg_catalog.jsonb_build_object(
    'order_id', p_order_id, 'command', v_command, 'evidence', v_evidence
  )::text, 'UTF8'), 'sha256');

  -- Serialize a command key before reading/writing its idempotency record.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('verox:reservation-command:' || v_key, 0)
  );
  SELECT * INTO v_existing FROM public.verox_reservation_commands c
    WHERE c.idempotency_key = v_key;
  IF FOUND THEN
    IF v_existing.request_hash <> v_hash THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'idempotency key reused with a different command or evidence';
    END IF;
    RETURN v_existing.result || pg_catalog.jsonb_build_object('idempotent_replay', true);
  END IF;

  SELECT o.account_id INTO v_account_id
    FROM public.verox_orders o WHERE o.id = p_order_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'order not found';
  END IF;

  -- All lifecycle commands lock account then order, matching reservation's account lock.
  SELECT * INTO v_account FROM public.verox_treasury_accounts a
    WHERE a.id = v_account_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'treasury account not found';
  END IF;
  SELECT * INTO v_order FROM public.verox_orders o
    WHERE o.id = p_order_id FOR UPDATE;
  IF NOT FOUND OR v_order.account_id <> v_account_id THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'order/account relationship changed';
  END IF;

  IF v_order.reservation_status IN ('captured', 'released')
     AND v_command IN ('release', 'capture', 'reconcile_release', 'reconcile_capture') THEN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'reservation already has a terminal disposition';
  END IF;

  IF v_command = 'mark_reconciliation' THEN
    IF v_order.reservation_status <> 'reserved' THEN
      RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'only a reserved order can enter reconciliation';
    END IF;
    IF coalesce(v_evidence->>'reason', '') = '' THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'reconciliation reason is required';
    END IF;
    v_next_status := 'reconciliation_required';
    v_outbox_type := 'verox.reservation.reconciliation_required';
    v_outcome := 'reconciliation_required';
    UPDATE public.verox_orders
      SET reservation_status = v_next_status,
          status = 'reconciliation_required',
          reservation_evidence = v_evidence,
          updated_at = pg_catalog.now()
      WHERE id = p_order_id;
  ELSIF v_command IN ('release', 'reconcile_release') THEN
    IF v_command = 'release' THEN
      IF v_order.reservation_status <> 'reserved'
         OR v_order.status NOT IN ('rejected', 'cancelled', 'expired') THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
          MESSAGE = 'release requires a reserved order with definitive rejected/cancelled/expired status';
      END IF;
    ELSE
      IF v_order.reservation_status <> 'reconciliation_required'
         OR coalesce(v_evidence->>'reviewed_by', '') = ''
         OR coalesce(v_evidence->>'evidence_reference', '') = ''
         OR v_evidence->>'provider_outcome' <> 'not_executed' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
          MESSAGE = 'reconciliation release requires reviewer, evidence reference and provider_outcome=not_executed';
      END IF;
    END IF;
    IF v_account.reserved_balance < v_order.stake THEN
      RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'reserved balance is below the order stake';
    END IF;
    UPDATE public.verox_treasury_accounts
      SET available_balance = available_balance + v_order.stake,
          reserved_balance = reserved_balance - v_order.stake,
          updated_at = pg_catalog.now()
      WHERE id = v_account_id;
    v_next_status := 'released';
    v_ledger_type := 'release';
    v_outbox_type := 'verox.reservation.released';
    v_outcome := 'released';
    UPDATE public.verox_orders
      SET reservation_status = v_next_status,
          status = CASE WHEN v_command = 'reconcile_release' THEN 'rejected' ELSE status END,
          reservation_evidence = v_evidence,
          updated_at = pg_catalog.now()
      WHERE id = p_order_id;
    INSERT INTO public.verox_treasury_ledger(account_id, order_id, currency, entry_type, amount, idempotency_key, metadata)
      VALUES (v_account_id, p_order_id, v_order.currency, v_ledger_type, v_order.stake,
        'reservation:' || v_key, pg_catalog.jsonb_build_object('command', v_command, 'evidence', v_evidence));
  ELSE
    -- Capture only after an explicit provider-confirmed settlement; reconciliation
    -- capture requires an independently reviewed, referenced reconciliation result.
    IF v_command = 'capture' THEN
      IF v_order.reservation_status <> 'reserved'
         OR v_order.status <> 'confirmed'
         OR v_evidence->>'provider_verified' <> 'true'
         OR coalesce(v_evidence->>'settlement_reference', '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
          MESSAGE = 'capture requires confirmed order, provider_verified=true and settlement_reference';
      END IF;
    ELSE
      IF v_order.reservation_status <> 'reconciliation_required'
         OR coalesce(v_evidence->>'reviewed_by', '') = ''
         OR coalesce(v_evidence->>'evidence_reference', '') = ''
         OR v_evidence->>'provider_outcome' <> 'executed'
         OR v_evidence->>'provider_verified' <> 'true'
         OR coalesce(v_evidence->>'settlement_reference', '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
          MESSAGE = 'reconciliation capture requires reviewer, evidence reference and verified executed settlement';
      END IF;
    END IF;
    IF v_account.reserved_balance < v_order.stake THEN
      RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'reserved balance is below the order stake';
    END IF;
    UPDATE public.verox_treasury_accounts
      SET reserved_balance = reserved_balance - v_order.stake,
          updated_at = pg_catalog.now()
      WHERE id = v_account_id;
    v_next_status := 'captured';
    v_ledger_type := 'capture';
    v_outbox_type := 'verox.reservation.captured';
    v_outcome := 'captured';
    UPDATE public.verox_orders
      SET reservation_status = v_next_status,
          status = CASE WHEN v_command = 'reconcile_capture' THEN 'confirmed' ELSE status END,
          reservation_evidence = v_evidence,
          updated_at = pg_catalog.now()
      WHERE id = p_order_id;
    INSERT INTO public.verox_treasury_ledger(account_id, order_id, currency, entry_type, amount, idempotency_key, metadata)
      VALUES (v_account_id, p_order_id, v_order.currency, v_ledger_type, v_order.stake,
        'reservation:' || v_key, pg_catalog.jsonb_build_object('command', v_command, 'evidence', v_evidence));
  END IF;

  INSERT INTO public.verox_event_outbox(event_type, aggregate_id, idempotency_key, payload)
    VALUES (v_outbox_type, p_order_id, 'reservation:' || v_key,
      pg_catalog.jsonb_build_object(
        'order_id', p_order_id, 'command', v_command,
        'order_status', (SELECT o.status FROM public.verox_orders o WHERE o.id = p_order_id),
        'reservation_status', v_next_status, 'currency', v_order.currency,
        'amount', v_order.stake, 'evidence', v_evidence
      ));

  v_result := pg_catalog.jsonb_build_object(
    'order_id', p_order_id, 'command', v_command,
    'order_status', (SELECT o.status FROM public.verox_orders o WHERE o.id = p_order_id),
    'reservation_status', v_next_status, 'outcome', v_outcome,
    'idempotent_replay', false
  );
  INSERT INTO public.verox_reservation_commands(order_id, command, idempotency_key, request_hash, result)
    VALUES (p_order_id, v_command, v_key, v_hash, v_result);
  RETURN v_result;
END;
$function$;

REVOKE ALL ON FUNCTION public.transition_verox_reservation(uuid, text, text, jsonb)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.transition_verox_reservation(uuid, text, text, jsonb)
  TO service_role;
