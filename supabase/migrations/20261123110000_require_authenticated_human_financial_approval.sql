-- Enforce a real authenticated human approval boundary.
-- Backends may create pending intents, but cannot mint approved records by writing
-- an arbitrary approved_by value. The human approves through an authenticated RPC.
ALTER TABLE public.verox_financial_authorizations
    ALTER COLUMN approved_by DROP NOT NULL,
    ALTER COLUMN approved_at_utc DROP NOT NULL,
    ALTER COLUMN status SET DEFAULT 'pending';

ALTER TABLE public.verox_financial_authorizations
    DROP CONSTRAINT IF EXISTS chk_vfa_status,
    DROP CONSTRAINT IF EXISTS chk_vfa_approved_by,
    DROP CONSTRAINT IF EXISTS chk_vfa_expiration;

-- Existing approvals cannot be attributed to an authenticated human retroactively.
-- Revoke still-live approvals and clear untrusted approver labels before tightening checks.
UPDATE public.verox_financial_authorizations
   SET status = 'revoked', approved_by = NULL, approved_at_utc = NULL
 WHERE status = 'approved';
UPDATE public.verox_financial_authorizations
   SET approved_by = NULL
 WHERE status IN ('expired', 'revoked');

ALTER TABLE public.verox_financial_authorizations
    ADD CONSTRAINT chk_vfa_status
        CHECK (status IN ('pending', 'approved', 'consumed', 'expired', 'revoked')),
    ADD CONSTRAINT chk_vfa_approved_by
        CHECK (
            (status IN ('approved', 'consumed') AND approved_by IS NOT NULL AND btrim(approved_by) <> '')
            OR (status IN ('pending', 'expired', 'revoked') AND approved_by IS NULL)
        ),
    ADD CONSTRAINT chk_vfa_expiration
        CHECK (
            status = 'pending'
            OR (approved_at_utc IS NOT NULL AND expires_at_utc > approved_at_utc)
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

    IF NEW.status = 'approved' AND (
        v_role IS DISTINCT FROM 'authenticated'
        OR v_sub IS NULL OR btrim(v_sub) = ''
        OR NEW.approved_by IS DISTINCT FROM v_sub
        OR NEW.approved_at_utc IS NULL
    ) THEN
        RAISE EXCEPTION 'approved financial authorization requires authenticated human provenance';
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_enforce_human_approval_provenance
    ON public.verox_financial_authorizations;
CREATE TRIGGER trg_enforce_human_approval_provenance
BEFORE INSERT OR UPDATE ON public.verox_financial_authorizations
FOR EACH ROW EXECUTE FUNCTION public.enforce_human_approval_provenance();

CREATE OR REPLACE FUNCTION public.approve_human_financial_authorization(
    p_authorization_id text
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

    UPDATE public.verox_financial_authorizations AS a
       SET status = 'approved',
           approved_by = v_sub,
           approved_at_utc = v_now
     WHERE a.authorization_id = btrim(p_authorization_id)
       AND a.status = 'pending'
       AND a.expires_at_utc > v_now
     RETURNING a.* INTO v_auth;

    IF NOT FOUND THEN
        approved := false;
        authorization_id := btrim(COALESCE(p_authorization_id, ''));
        approved_by := v_sub;
        approved_at_utc := NULL;
        reason := 'pending_authorization_not_found_or_expired';
        RETURN NEXT;
        RETURN;
    END IF;

    approved := true;
    authorization_id := v_auth.authorization_id;
    approved_by := v_auth.approved_by;
    approved_at_utc := v_auth.approved_at_utc;
    reason := 'approved_by_authenticated_human';
    RETURN NEXT;
END;
$function$;

-- Only trusted backend may create a pending intent. Neither API role may write the
-- table directly; authenticated users can approve only through the identity-bound RPC.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON TABLE public.verox_financial_authorizations
FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON TABLE public.verox_financial_authorizations TO service_role;

REVOKE ALL ON FUNCTION public.enforce_human_approval_provenance()
FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.approve_human_financial_authorization(text)
FROM PUBLIC, anon, service_role;
GRANT EXECUTE ON FUNCTION public.approve_human_financial_authorization(text)
TO authenticated;
