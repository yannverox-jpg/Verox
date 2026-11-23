-- Distributed, atomic OTP rate limiting shared by all Vercel instances.
-- Keys are HMAC-SHA256 digests generated server-side; raw IPs are never persisted.
CREATE TABLE IF NOT EXISTS public.verox_otp_rate_limits (
    key_hash text PRIMARY KEY CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    window_started_at timestamptz NOT NULL,
    failure_count integer NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    locked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

REVOKE ALL ON TABLE public.verox_otp_rate_limits FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.verox_otp_rate_limit(
    p_key_hash text,
    p_action text
)
RETURNS TABLE (
    allowed boolean,
    failures integer,
    retry_after_seconds integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_role text := NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role';
    v_now timestamptz := clock_timestamp();
    v_row public.verox_otp_rate_limits%ROWTYPE;
BEGIN
    IF v_role IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'server-side OTP rate-limit role required';
    END IF;
    IF p_key_hash IS NULL OR p_key_hash !~ '^[0-9a-f]{64}$'
       OR p_action NOT IN ('check', 'failure', 'success') THEN
        RAISE EXCEPTION 'valid rate-limit key and action required';
    END IF;

    IF p_action = 'success' THEN
        DELETE FROM public.verox_otp_rate_limits AS r WHERE r.key_hash = p_key_hash;
        RETURN QUERY SELECT true, 0, 0;
        RETURN;
    END IF;

    IF p_action = 'check' THEN
        SELECT * INTO v_row
          FROM public.verox_otp_rate_limits AS r
         WHERE r.key_hash = p_key_hash;
        IF NOT FOUND THEN
            RETURN QUERY SELECT true, 0, 0;
            RETURN;
        END IF;
        IF v_row.window_started_at <= v_now - interval '5 minutes' THEN
            UPDATE public.verox_otp_rate_limits AS r
               SET window_started_at = v_now, failure_count = 0,
                   locked_until = NULL, updated_at = v_now
             WHERE r.key_hash = p_key_hash;
            RETURN QUERY SELECT true, 0, 0;
            RETURN;
        END IF;
        IF v_row.locked_until IS NOT NULL AND v_row.locked_until > v_now THEN
            RETURN QUERY SELECT false, v_row.failure_count,
                greatest(1, ceil(extract(epoch FROM (v_row.locked_until - v_now)))::integer);
            RETURN;
        END IF;
        RETURN QUERY SELECT true, v_row.failure_count, 0;
        RETURN;
    END IF;

    INSERT INTO public.verox_otp_rate_limits AS existing
        (key_hash, window_started_at, failure_count, locked_until, updated_at)
    VALUES (p_key_hash, v_now, 1, NULL, v_now)
    ON CONFLICT (key_hash) DO UPDATE
       SET window_started_at = CASE
               WHEN existing.window_started_at <= v_now - interval '5 minutes' THEN v_now
               ELSE existing.window_started_at
           END,
           failure_count = CASE
               WHEN existing.window_started_at <= v_now - interval '5 minutes' THEN 1
               ELSE existing.failure_count + 1
           END,
           locked_until = CASE
               WHEN (CASE
                   WHEN existing.window_started_at <= v_now - interval '5 minutes' THEN 1
                   ELSE existing.failure_count + 1
               END) >= 5 THEN v_now + interval '5 minutes'
               WHEN existing.locked_until > v_now THEN existing.locked_until
               ELSE NULL
           END,
           updated_at = v_now
    RETURNING existing.* INTO v_row;

    RETURN QUERY SELECT
        (v_row.locked_until IS NULL OR v_row.locked_until <= v_now),
        v_row.failure_count,
        CASE WHEN v_row.locked_until > v_now
             THEN greatest(1, ceil(extract(epoch FROM (v_row.locked_until - v_now)))::integer)
             ELSE 0 END;
END;
$function$;

REVOKE ALL ON FUNCTION public.verox_otp_rate_limit(text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.verox_otp_rate_limit(text, text) TO service_role;
