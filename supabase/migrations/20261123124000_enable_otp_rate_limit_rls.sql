-- The OTP limiter is reachable only through its service_role-only SECURITY DEFINER RPC.
-- Keep direct table access closed and make the database posture explicit to Supabase advisors.
ALTER TABLE public.verox_otp_rate_limits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.verox_otp_rate_limits FROM PUBLIC, anon, authenticated, service_role;
