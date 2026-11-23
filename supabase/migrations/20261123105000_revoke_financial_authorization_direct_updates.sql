-- State transitions are performed only by SECURITY DEFINER RPCs that enforce
-- expiry, operation context, one-time consumption, and audit invariants.
REVOKE UPDATE ON TABLE public.verox_financial_authorizations
FROM PUBLIC, anon, authenticated, service_role;
