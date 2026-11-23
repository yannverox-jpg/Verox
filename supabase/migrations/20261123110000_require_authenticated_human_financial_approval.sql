-- Enforce a real authenticated human approval boundary.
-- Backends may create pending intents, but cannot mint approved records by writing
-- an arbitrary approved_by value. The human approves only their own exact operation.
ALTER TABLE public.verox_financial_authorizations
    ADD COLUMN IF NOT EXISTS requested_by text,
    ALTER COLUMN approved_by DROP NOT NULL,
    ALTER COLUMN approved_at_utc DROP NOT NULL,
    ALTER COLUMN status SET DEFAULT 'pending';

ALTER TABLE public.verox_financial_authorizations
    DROP CONSTRAINT IF EXISTS chk_vfa_status,
    DROP CONSTRAINT IF EXISTS chk_vfa_approved_by,
    DROP CONSTRAINT IF EXISTS chk_vfa_expiration,
    DROP CONSTRAINT IF EXISTS chk_vfa_requested_by;

-- Existing approvals cannot be attributed to an authenticated human retroactively.
-- Revoke still-live approvals and clear their untrusted approver labels.
UPDATE public.verox_financial_authorizations
   SET status = 'revoked', approved_by = NULL, approved_at_utc = NULL
 WHERE status = 'approved';
UPDATE public.verox_financial_authorizations
   SET approved_by = NULL
 WHERE status = 'revoked';

ALTER TABLE public.verox_financial_authorizations
    ADD CONSTRAINT chk_vfa_status
        CHECK (status IN ('pending', 'approved', 'consumed', 'expired', 'revoked')),
    ADD CONSTRAINT chk_vfa_approved_by
        CHECK (
            (status IN ('approved', 'consumed', 'expired') AND approved_by IS NOT NULL AND btrim(approved_by) <> '')
            OR (status IN ('pending', 'revoked') AND approved_by IS NULL)
        ),
    ADD CONSTRAINT chk_vfa_expiration
        CHECK (
            status IN ('pending', 'revoked')
            OR (approved_at_utc IS NOT NULL AND expires_at_utc > approved_at_utc)
        ),
    ADD CONSTRAINT chk_vfa_requested_by
        CHECK (
            status NOT IN ('pending', 'approved')
            OR (requested_by IS NOT NULL AND btrim(requested_by) <> '')
        );

CREATE OR REPLACE FUNCTION public.enforce_human_approval_provenance()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_role text := current_setting('request.jwt.claims', true)::jsonb ->> 'role';
    v_sub text := current_setting('request.jwt.claims', true)::jsonb ->> 'sub';
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'pending' THEN
        RAISE EXCEPTION 'financial authorization must be created as pending';
    END IF;

    IF NEW.status = 'pending' AND (NEW.requested_by IS NULL OR btrim(NEW.requested_by) = '') THEN
        RAISE EXCEPTION 'pending financial authorization requires requester identity';
    END IF;

    IF NEW.status = 'approved' AND (
        v_role IS DISTINCT FROM 'authenticated'
        OR v_sub IS NULL OR btrim(v_sub) = ''
        OR NEW.approved_by IS DISTINCT FROM v_sub
        OR NEW.requested_by IS DISTINCT FROM v_sub
        OR NEW.approved_at_utc IS NULL
    ) THEN
        RAISE EXCEPTION 'approved financial authorization requires authenticated owner provenance';
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_enforce_human_approval_provenance
    ON public.verox_financial_authorizations;
CREATE TRIGGER trg_enforce_human_approval_provenance
BEFORE INSERT OR UPDATE ON public.verox_financial_authorizations
FOR EACH ROW EXECUTE FUNCTION public.enforce_human_approval_provenance();

DROP FUNCTION IF EXISTS public.approve_human_financial_authorization(text);
CREATE OR REPLACE FUNCTION public.approve_human_financial_authorization(
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
    v_role text := current_setting('request.jwt.claims', true)::jsonb ->> 'role';
    v_sub text := current_setting('request.jwt.claims', true)::jsonb ->> 'sub';
    v_now timestamptz := clock_timestamp();
    v_auth public.verox_financial_authorizations%ROWTYPE;
BEGIN
    IF v_role IS DISTINCT FROM 'authenticated' OR v_sub IS NULL OR btrim(v_sub) = '' THEN
        RAISE EXCEPTION 'authenticated human identity required';
    END IF;

    IF p_authorization_id IS NULL OR btrim(p_authorization_id) = ''
       OR p_operation_fingerprint IS NULL OR p_operation_fingerprint !~ '^[0-9a-f]{64}$'
       OR p_domain IS NULL OR btrim(p_domain) = ''
       OR p_action IS NULL OR btrim(p_action) = ''
       OR p_target IS NULL OR btrim(p_target) = ''
       OR p_amount IS NULL OR p_amount <= 0
       OR p_currency IS NULL OR btrim(p_currency) = ''
       OR p_run_id IS NULL OR btrim(p_run_id) = ''
       OR p_event_id IS NULL OR btrim(p_event_id) = '' THEN
        RAISE EXCEPTION 'complete operation context required for human approval';
    END IF;

    UPDATE public.verox_financial_authorizations AS a
       SET status = 'approved',
           approved_by = v_sub,
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
       AND a.requested_by = v_sub
       AND a.status = 'pending'
       AND a.expires_at_utc > v_now
     RETURNING a.* INTO v_auth;

    IF NOT FOUND THEN
        approved := false;
        authorization_id := btrim(p_authorization_id);
        approved_by := v_sub;
        approved_at_utc := NULL;
        reason := 'pending_intent_missing_expired_or_context_mismatch';
        RETURN NEXT;
        RETURN;
    END IF;

    approved := true;
    authorization_id := v_auth.authorization_id;
    approved_by := v_auth.approved_by;
    approved_at_utc := v_auth.approved_at_utc;
    reason := 'approved_by_authenticated_owner_for_exact_operation';
    RETURN NEXT;
END;
$function$;

-- The backend may create pending intents and read their state. State transitions
-- are performed only by the approved/consume SECURITY DEFINER RPCs.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON TABLE public.verox_financial_authorizations
FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON TABLE public.verox_financial_authorizations TO service_role;

REVOKE ALL ON FUNCTION public.enforce_human_approval_provenance()
FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.approve_human_financial_authorization(
    text, text, text, text, text, numeric, text, text, text
) FROM PUBLIC, anon, service_role;
GRANT EXECUTE ON FUNCTION public.approve_human_financial_authorization(
    text, text, text, text, text, numeric, text, text, text
) TO authenticated;
