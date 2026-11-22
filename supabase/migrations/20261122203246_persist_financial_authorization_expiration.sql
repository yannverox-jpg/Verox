CREATE OR REPLACE FUNCTION public.consume_human_financial_authorization(
    p_authorization_id text,
    p_operation_fingerprint text,
    p_run_id text,
    p_event_id text,
    p_action text,
    p_target text,
    p_domain text,
    p_source text DEFAULT 'verox_financial_guard',
    p_agent text DEFAULT 'financial_execution_guard',
    p_stage text DEFAULT 'financial_execution_guard'
)
RETURNS TABLE (
    consumed boolean,
    authorization_id text,
    approved_by text,
    approved_at_utc timestamptz,
    expires_at_utc timestamptz,
    consumed_at_utc timestamptz,
    operation_fingerprint text,
    reason text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_auth public.verox_financial_authorizations%ROWTYPE;
    v_audit_event_id text;
BEGIN
    IF p_authorization_id IS NULL OR btrim(p_authorization_id) = ''
       OR p_operation_fingerprint IS NULL
       OR p_operation_fingerprint !~ '^[0-9a-f]{64}$'
       OR p_run_id IS NULL OR btrim(p_run_id) = ''
       OR p_event_id IS NULL OR btrim(p_event_id) = ''
       OR p_action IS NULL OR btrim(p_action) = ''
       OR p_target IS NULL OR btrim(p_target) = ''
       OR p_domain IS NULL OR btrim(p_domain) = '' THEN
        consumed := false;
        authorization_id := COALESCE(p_authorization_id, '');
        approved_by := NULL;
        approved_at_utc := NULL;
        expires_at_utc := NULL;
        consumed_at_utc := NULL;
        operation_fingerprint := COALESCE(p_operation_fingerprint, '');
        reason := 'missing_or_invalid_required_parameters';
        RETURN NEXT;
        RETURN;
    END IF;

    UPDATE public.verox_financial_authorizations AS a
       SET status = 'consumed',
           consumed_at_utc = v_now,
           consumed_by_run_id = btrim(p_run_id),
           consumed_by_event_id = btrim(p_event_id)
     WHERE a.authorization_id = btrim(p_authorization_id)
       AND a.operation_fingerprint = lower(btrim(p_operation_fingerprint))
       AND a.domain = btrim(p_domain)
       AND a.operation_type = btrim(p_action)
       AND a.target = btrim(p_target)
       AND a.run_id = btrim(p_run_id)
       AND a.event_id = btrim(p_event_id)
       AND a.status = 'approved'
       AND a.approved_by IS NOT NULL
       AND btrim(a.approved_by) <> ''
       AND a.approved_at_utc <= v_now
       AND a.expires_at_utc > v_now
     RETURNING a.* INTO v_auth;

    IF NOT FOUND THEN
        SELECT * INTO v_auth
          FROM public.verox_financial_authorizations AS a
         WHERE a.authorization_id = btrim(p_authorization_id);

        consumed := false;
        authorization_id := btrim(p_authorization_id);
        approved_by := v_auth.approved_by;
        approved_at_utc := v_auth.approved_at_utc;
        expires_at_utc := v_auth.expires_at_utc;
        consumed_at_utc := v_auth.consumed_at_utc;
        operation_fingerprint := lower(btrim(p_operation_fingerprint));

        IF v_auth.authorization_id IS NULL THEN
            reason := 'authorization_not_found';
        ELSIF v_auth.operation_fingerprint IS DISTINCT FROM lower(btrim(p_operation_fingerprint)) THEN
            reason := 'fingerprint_mismatch';
        ELSIF v_auth.status = 'consumed' THEN
            reason := 'already_consumed_or_not_eligible';
        ELSIF v_auth.status <> 'approved' THEN
            reason := 'invalid_status:' || COALESCE(v_auth.status, 'unknown');
        ELSIF v_auth.domain IS DISTINCT FROM btrim(p_domain)
           OR v_auth.operation_type IS DISTINCT FROM btrim(p_action)
           OR v_auth.target IS DISTINCT FROM btrim(p_target)
           OR v_auth.run_id IS DISTINCT FROM btrim(p_run_id)
           OR v_auth.event_id IS DISTINCT FROM btrim(p_event_id) THEN
            reason := 'operation_context_mismatch';
        ELSIF v_auth.approved_at_utc > v_now THEN
            reason := 'approval_not_yet_valid';
        ELSIF v_auth.expires_at_utc <= v_now THEN
            -- Persist expiration state; never leave an expired approval looking usable.
            UPDATE public.verox_financial_authorizations AS a
               SET status = 'expired'
             WHERE a.authorization_id = btrim(p_authorization_id)
               AND a.status = 'approved'
               AND a.expires_at_utc <= v_now;
            reason := 'approval_expired';
        ELSE
            reason := 'already_consumed_or_not_eligible';
        END IF;
        RETURN NEXT;
        RETURN;
    END IF;

    v_audit_event_id := 'evt_auth_' || md5(v_auth.authorization_id || ':' || btrim(p_event_id));
    INSERT INTO public.nexus_runtime_events (
        event_id, run_id, task_id, "timestamp", source, agent, stage,
        status, action, target, proof, metadata
    ) VALUES (
        v_audit_event_id, btrim(p_run_id), NULL, v_now, btrim(p_source),
        btrim(p_agent), btrim(p_stage), 'authorized', btrim(p_action), btrim(p_target),
        jsonb_build_object(
            'authorization_id', v_auth.authorization_id,
            'approved_by', v_auth.approved_by,
            'approved_at_utc', v_auth.approved_at_utc,
            'expires_at_utc', v_auth.expires_at_utc,
            'consumed_at_utc', v_auth.consumed_at_utc,
            'operation_fingerprint', v_auth.operation_fingerprint,
            'domain', btrim(p_domain)
        ),
        jsonb_build_object(
            'source_event_id', btrim(p_event_id),
            'domain', btrim(p_domain),
            'operation_fingerprint', v_auth.operation_fingerprint
        )
    );

    consumed := true;
    authorization_id := v_auth.authorization_id;
    approved_by := v_auth.approved_by;
    approved_at_utc := v_auth.approved_at_utc;
    expires_at_utc := v_auth.expires_at_utc;
    consumed_at_utc := v_auth.consumed_at_utc;
    operation_fingerprint := v_auth.operation_fingerprint;
    reason := 'consumed_atomically';
    RETURN NEXT;
END;
$function$;

REVOKE ALL ON FUNCTION public.consume_human_financial_authorization(
    text, text, text, text, text, text, text, text, text, text
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_human_financial_authorization(
    text, text, text, text, text, text, text, text, text, text
) TO service_role;
