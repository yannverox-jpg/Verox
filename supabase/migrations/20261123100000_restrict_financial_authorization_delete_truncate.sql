-- The runtime only needs to read, insert, and atomically update approvals.
-- Deletion/truncation is not part of the approval lifecycle and must not be granted
-- to API roles or service_role.
REVOKE DELETE, TRUNCATE
ON TABLE public.verox_financial_authorizations
FROM PUBLIC, anon, authenticated, service_role;
