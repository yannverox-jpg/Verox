-- Atomically bind Android human approval to the canonical T6 authorization ledger.
-- The RPC is callable only by service_role; OTP verification is performed by the server API first.
create or replace function public.decide_verox_human_approval_t6(
  p_request_id uuid,
  p_decision text,
  p_reason text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_request public.verox_human_approval_requests%rowtype;
  v_now timestamptz := clock_timestamp();
  v_details jsonb;
  v_auth_id text;
  v_domain text;
  v_amount numeric(28,8);
  v_currency text;
  v_run_id text;
  v_event_id text;
  v_evidence_id text;
begin
  if p_request_id is null or p_decision not in ('approve','reject') then
    return jsonb_build_object('ok',false,'reason','invalid_decision');
  end if;

  select * into v_request
    from public.verox_human_approval_requests
   where id = p_request_id
   for update;

  if not found then
    return jsonb_build_object('ok',false,'reason','request_not_found');
  end if;
  if v_request.status <> 'pending' or v_request.expires_at <= v_now then
    if v_request.status = 'pending' and v_request.expires_at <= v_now then
      update public.verox_human_approval_requests
         set status='expired'
       where id=p_request_id and status='pending';
    end if;
    return jsonb_build_object('ok',false,'reason','request_expired_or_already_decided');
  end if;

  if p_decision = 'reject' then
    update public.verox_human_approval_requests
       set status='rejected', decided_by='verox-human-operator',
           decision_reason=left(coalesce(p_reason,''),500), decided_at=v_now
     where id=p_request_id and status='pending';
    return jsonb_build_object('ok',true,'status','rejected','request_id',p_request_id);
  end if;

  v_details := coalesce(v_request.details, '{}'::jsonb);
  v_domain := nullif(btrim(v_details->>'domain'),'');
  v_currency := upper(nullif(btrim(v_details->>'currency'),''));
  v_run_id := nullif(btrim(v_details->>'run_id'),'');
  v_event_id := nullif(btrim(v_details->>'event_id'),'');
  v_evidence_id := nullif(btrim(v_details->>'evidence_id'),'');
  v_auth_id := coalesce(nullif(btrim(v_details->>'authorization_id'),''), v_request.id::text);

  if v_domain is null or v_domain not in ('trading','sports_betting','horse_racing','transfer')
     or v_currency is null or v_currency !~ '^[A-Z0-9]{2,12}
     or v_run_id is null or v_event_id is null
     or btrim(v_request.action_type) = '' or btrim(v_request.target) = ''
     or v_request.operation_fingerprint !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok',false,'reason','t6_context_missing_or_invalid');
  end if;

  begin
    v_amount := (v_details->>'amount')::numeric(28,8);
  exception when others then
    return jsonb_build_object('ok',false,'reason','t6_amount_missing_or_invalid');
  end;
  if v_amount is null or v_amount <= 0 then
    return jsonb_build_object('ok',false,'reason','t6_amount_missing_or_invalid');
  end if;

  -- Lock + status check + insert happen in one transaction. Duplicate authorization IDs fail closed.
  insert into public.verox_financial_authorizations (
    authorization_id, operation_fingerprint, domain, operation_type, target,
    amount, currency, run_id, event_id, evidence_id, approved_by,
    approved_at_utc, expires_at_utc, status
  ) values (
    v_auth_id, lower(v_request.operation_fingerprint), v_domain, v_request.action_type,
    v_request.target, v_amount, v_currency, v_run_id, v_event_id, v_evidence_id,
    'verox-human-operator', v_now, v_request.expires_at, 'approved'
  );

  update public.verox_human_approval_requests
     set status='approved', decided_by='verox-human-operator',
         decision_reason=left(coalesce(p_reason,''),500), decided_at=v_now,
         details = v_details || jsonb_build_object('t6_authorization_id',v_auth_id)
   where id=p_request_id and status='pending';

  return jsonb_build_object(
    'ok',true,'status','approved','request_id',p_request_id,
    'authorization_id',v_auth_id,'operation_fingerprint',v_request.operation_fingerprint,
    'domain',v_domain,'operation_type',v_request.action_type,'target',v_request.target,
    'run_id',v_run_id,'event_id',v_event_id,'expires_at_utc',v_request.expires_at
  );
exception
  when unique_violation then
    return jsonb_build_object('ok',false,'reason','authorization_id_already_exists');
end;
$$;

revoke all on function public.decide_verox_human_approval_t6(uuid,text,text) from public, anon, authenticated;
grant execute on function public.decide_verox_human_approval_t6(uuid,text,text) to service_role;

     or v_run_id is null or v_event_id is null
     or btrim(v_request.action_type) = '' or btrim(v_request.target) = ''
     or v_request.operation_fingerprint !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok',false,'reason','t6_context_missing_or_invalid');
  end if;

  begin
    v_amount := (v_details->>'amount')::numeric(28,8);
  exception when others then
    return jsonb_build_object('ok',false,'reason','t6_amount_missing_or_invalid');
  end;
  if v_amount is null or v_amount <= 0 then
    return jsonb_build_object('ok',false,'reason','t6_amount_missing_or_invalid');
  end if;

  -- Lock + status check + insert happen in one transaction. Duplicate authorization IDs fail closed.
  insert into public.verox_financial_authorizations (
    authorization_id, operation_fingerprint, domain, operation_type, target,
    amount, currency, run_id, event_id, evidence_id, approved_by,
    approved_at_utc, expires_at_utc, status
  ) values (
    v_auth_id, lower(v_request.operation_fingerprint), v_domain, v_request.action_type,
    v_request.target, v_amount, v_currency, v_run_id, v_event_id, v_evidence_id,
    'verox-human-operator', v_now, v_request.expires_at, 'approved'
  );

  update public.verox_human_approval_requests
     set status='approved', decided_by='verox-human-operator',
         decision_reason=left(coalesce(p_reason,''),500), decided_at=v_now,
         details = v_details || jsonb_build_object('t6_authorization_id',v_auth_id)
   where id=p_request_id and status='pending';

  return jsonb_build_object(
    'ok',true,'status','approved','request_id',p_request_id,
    'authorization_id',v_auth_id,'operation_fingerprint',v_request.operation_fingerprint,
    'domain',v_domain,'operation_type',v_request.action_type,'target',v_request.target,
    'run_id',v_run_id,'event_id',v_event_id,'expires_at_utc',v_request.expires_at
  );
exception
  when unique_violation then
    return jsonb_build_object('ok',false,'reason','authorization_id_already_exists');
end;
$$;

revoke all on function public.decide_verox_human_approval_t6(uuid,text,text) from public, anon, authenticated;
grant execute on function public.decide_verox_human_approval_t6(uuid,text,text) to service_role;
