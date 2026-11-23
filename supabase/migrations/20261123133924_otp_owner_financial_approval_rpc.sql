-- Allow the private OTP-authenticated API to attest the configured owner
-- without pretending its HMAC session is a Supabase Auth JWT.
-- This RPC is callable only by service_role, whose key must remain server-side.
CREATE OR REPLACE FUNCTION public.enforce_human_approval_provenance()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_claims jsonb := NULLIF(current_setting('request.jwt.claims', true), '')::jsonb;
    v_role text := v_claims ->> 'role';
    v_sub text := v_claims ->> 'sub';
    v_otp_owner text := current_setting('verox.otp_owner_id', true);
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'pending' THEN
        RAISE EXCEPTION 'financial authorization must be created as pending';
    END IF;
    IF NEW.status = 'pending' AND (NEW.requested_by IS NULL OR btrim(NEW.requested_by) = '') THEN
        RAISE EXCEPTION 'pending financial authorization requires requester identity';
    END IF;
    IF NEW.status = 'approved' THEN
        IF v_role = 'authenticated'
           AND v_sub IS NOT NULL AND btrim(v_sub) <> ''
           AND NEW.approved_by = v_sub
           AND NEW.requested_by = v_sub
           AND NEW.approved_at_utc IS NOT NULL THEN
            RETURN NEW;
        END IF;
        IF v_role = 'service_role'
           AND v_otp_owner IS NOT NULL AND btrim(v_otp_owner) <> ''
           AND NEW.approved_by = v_otp_owner
           AND NEW.requested_by = v_otp_owner
           AND NEW.approved_at_utc IS NOT NULL THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'financial approval requires authenticated owner provenance';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.approve_otp_owner_financial_authorization(
    p_owner_id text,
    p_authorization_id text,
    p_operation_fingerprint text,
    p_domain text,
    p_action text,
    p_target text,
    p_amount numeric,
    p_currency text,
    p_run_id text,
    p_event_id text
)
RETURNS TABLE (
    approved boolean,
    authorization_id text,
    approved_by text,
    approved_at_utc timestamptz,
    reason text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_claims jsonb := NULLIF(current_setting('request.jwt.claims', true), '')::jsonb;
    v_role text := v_claims ->> 'role';
    v_now timestamptz := clock_timestamp();
    v_auth public.verox_financial_authorizations%ROWTYPE;
BEGIN
    IF v_role IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'server-side OTP attestation role required';
    END IF;
    IF p_owner_id IS NULL OR p_owner_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_authorization_id IS NULL OR btrim(p_authorization_id) = ''
       OR p_operation_fingerprint IS NULL OR p_operation_fingerprint !~ '^[0-9a-f]{64}$'
       OR p_domain IS NULL OR btrim(p_domain) = ''
       OR p_action IS NULL OR btrim(p_action) = ''
       OR p_target IS NULL OR btrim(p_target) = ''
       OR p_amount IS NULL OR p_amount <= 0
       OR p_currency IS NULL OR btrim(p_currency) = ''
       OR p_run_id IS NULL OR btrim(p_run_id) = ''
       OR p_event_id IS NULL OR btrim(p_event_id) = '' THEN
        RAISE EXCEPTION 'complete owner and operation context required';
    END IF;

    -- Transaction-local attestation is consumed by the trigger and cannot leak
    -- to another request. The caller must be service_role and the exact pending
    -- intent must already bind requested_by to this owner UUID.
    PERFORM set_config('verox.otp_owner_id', lower(p_owner_id), true);
    UPDATE public.verox_financial_authorizations AS a
       SET status = 'approved',
           approved_by = lower(p_owner_id),
           approved_at_utc = v_now
     WHERE a.authorization_id = btrim(p_authorization_id)
       AND a.operation_fingerprint = lower(btrim(p_operation_fingerprint))
       AND a.domain = btrim(p_domain)
       AND a.operation_type = btrim(p_action)
       AND a.target = btrim(p_target)
       AND a.amount = p_amount
       AND a.currency = upper(btrim(p_currency))
       AND a.run_id = btrim(p_run_id)
       AND a.event_id = btrim(p_event_id)
       AND lower(a.requested_by) = lower(p_owner_id)
       AND a.status = 'pending'
       AND a.expires_at_utc > v_now
     RETURNING a.* INTO v_auth;

    IF NOT FOUND THEN
        approved := false;
        authorization_id := btrim(p_authorization_id);
        approved_by := lower(p_owner_id);
        approved_at_utc := NULL;
        reason := 'pending_intent_missing_expired_or_context_mismatch';
        RETURN NEXT;
        RETURN;
    END IF;
    approved := true;
    authorization_id := v_auth.authorization_id;
    approved_by := v_auth.approved_by;
    approved_at_utc := v_auth.approved_at_utc;
    reason := 'approved_by_otp_authenticated_owner_for_exact_operation';
    RETURN NEXT;
END;
$function$;

REVOKE ALL ON FUNCTION public.approve_otp_owner_financial_authorization(
    text, text, text, text, text, text, numeric, text, text, text
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_otp_owner_financial_authorization(
    text, text, text, text, text, text, numeric, text, text, text
) TO service_role;
