-- Android approvals are a notification surface for existing T6 pending intents,
-- never a second authority ledger. This migration must run after the secure owner OTP RPC.
alter table public.verox_human_approval_requests add column if not exists t6_authorization_id text;
create unique index if not exists verox_human_approval_t6_authorization_unique
  on public.verox_human_approval_requests(t6_authorization_id) where t6_authorization_id is not null;

create or replace function public.decide_verox_human_approval_t6(
  p_request_id uuid, p_owner_id text, p_decision text, p_reason text default ''
) returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  v_request public.verox_human_approval_requests%rowtype;
  v_details jsonb; v_now timestamptz := clock_timestamp(); v_auth_id text;
  v_domain text; v_action text; v_target text; v_amount numeric(28,8);
  v_currency text; v_run_id text; v_event_id text; v_approval record;
begin
  if NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role' is distinct from 'service_role' then
    raise exception 'server-side approval role required';
  end if;
  if p_request_id is null or p_owner_id is null
     or p_owner_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     or p_decision not in ('approve','reject') then
    return jsonb_build_object('ok',false,'reason','invalid_decision_or_owner');
  end if;
  select * into v_request from public.verox_human_approval_requests where id=p_request_id for update;
  if not found then return jsonb_build_object('ok',false,'reason','request_not_found'); end if;
  if lower(v_request.requested_by) <> lower(p_owner_id) then return jsonb_build_object('ok',false,'reason','owner_mismatch'); end if;
  if v_request.status <> 'pending' or v_request.expires_at <= v_now then
    if v_request.status='pending' and v_request.expires_at <= v_now then
      update public.verox_human_approval_requests set status='expired' where id=p_request_id and status='pending';
    end if;
    return jsonb_build_object('ok',false,'reason','request_expired_or_already_decided');
  end if;
  if p_decision='reject' then
    update public.verox_human_approval_requests set status='rejected', decided_by=lower(p_owner_id),
      decision_reason=left(coalesce(p_reason,''),500), decided_at=v_now where id=p_request_id and status='pending';
    return jsonb_build_object('ok',true,'status','rejected','request_id',p_request_id);
  end if;
  v_details := coalesce(v_request.details,'{}'::jsonb);
  v_auth_id := coalesce(nullif(btrim(v_request.t6_authorization_id),''),nullif(btrim(v_details->>'authorization_id'),''));
  v_domain := nullif(btrim(v_details->>'domain'),''); v_action := v_request.action_type; v_target := v_request.target;
  v_currency := upper(nullif(btrim(v_details->>'currency'),''));
  v_run_id := nullif(btrim(v_details->>'run_id'),''); v_event_id := nullif(btrim(v_details->>'event_id'),'');
  begin v_amount := (v_details->>'amount')::numeric(28,8);
  exception when others then return jsonb_build_object('ok',false,'reason','t6_amount_missing_or_invalid'); end;
  if v_auth_id is null or v_domain is null or v_domain not in ('trading','sports_betting','horse_racing','transfer')
     or v_amount is null or v_amount <= 0 or v_currency is null or v_currency !~ '^[A-Z0-9]{2,12}$'
     or v_run_id is null or v_event_id is null or v_request.operation_fingerprint !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok',false,'reason','t6_context_missing_or_invalid');
  end if;
  select * into v_approval from public.approve_otp_owner_financial_authorization(
    lower(p_owner_id),v_auth_id,lower(v_request.operation_fingerprint),v_domain,v_action,v_target,
    v_amount,v_currency,v_run_id,v_event_id);
  if coalesce(v_approval.approved,false) is not true then
    return jsonb_build_object('ok',false,'reason',coalesce(v_approval.reason,'t6_approval_not_confirmed'));
  end if;
  update public.verox_human_approval_requests set status='approved', decided_by=lower(p_owner_id),
    decision_reason=left(coalesce(p_reason,''),500), decided_at=v_now, t6_authorization_id=v_auth_id,
    details=v_details || jsonb_build_object('t6_authorization_id',v_auth_id)
   where id=p_request_id and status='pending';
  return jsonb_build_object('ok',true,'status','approved','request_id',p_request_id,'authorization_id',v_auth_id,
    'approved_by',lower(p_owner_id),'approved_at_utc',v_approval.approved_at_utc,
    'operation_fingerprint',v_request.operation_fingerprint,'domain',v_domain,'operation_type',v_action,
    'target',v_target,'run_id',v_run_id,'event_id',v_event_id,'expires_at_utc',v_request.expires_at);
end; $$;
revoke all on function public.decide_verox_human_approval_t6(uuid,text,text,text) from public, anon, authenticated;
grant execute on function public.decide_verox_human_approval_t6(uuid,text,text,text) to service_role;
