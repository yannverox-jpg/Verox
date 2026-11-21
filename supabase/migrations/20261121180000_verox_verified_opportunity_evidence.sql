-- Evidence-linked opportunity verification for Verox.
-- Uses the existing canonical dedupe_key; does not delete or rewrite legacy rows.

ALTER TABLE public.verox_opportunities
  ADD COLUMN IF NOT EXISTS observation_id uuid REFERENCES public.verox_observations(id),
  ADD COLUMN IF NOT EXISTS analysis_run_id uuid REFERENCES public.verox_analysis_runs(id),
  ADD COLUMN IF NOT EXISTS model_used text,
  ADD COLUMN IF NOT EXISTS confidence_score numeric,
  ADD COLUMN IF NOT EXISTS is_actionable boolean NOT NULL DEFAULT false;

ALTER TABLE public.verox_opportunities
  DROP CONSTRAINT IF EXISTS verox_opportunities_status_check;

ALTER TABLE public.verox_opportunities
  ADD CONSTRAINT verox_opportunities_status_check
  CHECK (status IN ('detected_unverified','triaged','validated','rejected','ai_verified'));

ALTER TABLE public.verox_opportunities
  ADD CONSTRAINT verox_opportunities_confidence_score_check
  CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1);

ALTER TABLE public.verox_opportunities
  ADD CONSTRAINT verox_opportunities_verified_requires_trace_chk
  CHECK (
    status <> 'ai_verified'
    OR (
      observation_id IS NOT NULL
      AND analysis_run_id IS NOT NULL
      AND model_used IS NOT NULL AND btrim(model_used) <> ''
      AND confidence_score IS NOT NULL AND confidence_score >= 0.600
      AND is_actionable = true
    )
  );

CREATE INDEX IF NOT EXISTS idx_verox_opportunities_observation_id
  ON public.verox_opportunities (observation_id)
  WHERE observation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_verox_opportunities_analysis_run_id
  ON public.verox_opportunities (analysis_run_id)
  WHERE analysis_run_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.verox_enforce_verified_opportunity_evidence()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
  v_collection_status text;
  v_run public.verox_analysis_runs%ROWTYPE;
BEGIN
  IF NEW.status <> 'ai_verified' THEN
    RETURN NEW;
  END IF;

  IF NEW.observation_id IS NULL OR NEW.analysis_run_id IS NULL
     OR NEW.model_used IS NULL OR btrim(NEW.model_used) = ''
     OR NEW.confidence_score IS NULL OR NEW.confidence_score < 0.600
     OR NEW.is_actionable IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ai_verified requires observation, successful analysis, model, actionable=true and confidence >= 0.600';
  END IF;

  SELECT payload #>> '{metadata,collection_status}'
    INTO v_collection_status
    FROM public.verox_observations
   WHERE id = NEW.observation_id;

  IF v_collection_status IS DISTINCT FROM 'collected_valid' THEN
    RAISE EXCEPTION 'ai_verified requires a collected_valid observation';
  END IF;

  SELECT * INTO v_run
    FROM public.verox_analysis_runs
   WHERE id = NEW.analysis_run_id;

  IF NOT FOUND
     OR v_run.status <> 'succeeded'
     OR v_run.model_name IS NULL
     OR btrim(v_run.model_name) = ''
     OR v_run.model_name <> NEW.model_used
     OR NOT (NEW.observation_id = ANY(v_run.input_observation_ids)) THEN
    RAISE EXCEPTION 'ai_verified requires a succeeded analysis run linked to the same observation and model';
  END IF;

  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS verox_verified_opportunity_evidence_guard ON public.verox_opportunities;
CREATE TRIGGER verox_verified_opportunity_evidence_guard
BEFORE INSERT OR UPDATE OF status, observation_id, analysis_run_id, model_used, confidence_score, is_actionable
ON public.verox_opportunities
FOR EACH ROW
EXECUTE FUNCTION public.verox_enforce_verified_opportunity_evidence();

CREATE OR REPLACE FUNCTION public.verox_opportunity_api(
  p_token text,
  p_action text,
  p_items jsonb DEFAULT '[]'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public', 'private'
AS $function$
DECLARE
  v_expected text;
  v_item jsonb;
  v_title text;
  v_url text;
  v_department text;
  v_source_name text;
  v_observation_id uuid;
  v_analysis_run_id uuid;
  v_model_used text;
  v_confidence numeric;
  v_actionable boolean;
  v_collection_status text;
  v_verified boolean;
  v_count integer := 0;
  v_rows jsonb;
BEGIN
  SELECT token_hash INTO v_expected
    FROM private.verox_service_secrets
   WHERE name = 'verox-task-api';

  IF v_expected IS NULL OR p_token IS NULL
     OR encode(sha256(convert_to(p_token, 'UTF8')), 'hex') <> v_expected THEN
    RAISE EXCEPTION 'unauthorized';
  END IF;

  IF p_action = 'upsert' THEN
    IF jsonb_typeof(coalesce(p_items, '[]'::jsonb)) <> 'array'
       OR jsonb_array_length(coalesce(p_items, '[]'::jsonb)) > 250 THEN
      RAISE EXCEPTION 'invalid opportunity batch';
    END IF;

    FOR v_item IN
      SELECT value FROM jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) AS x(value)
    LOOP
      v_title := left(btrim(coalesce(v_item->>'title','')), 300);
      v_url := left(btrim(coalesce(v_item->>'source_url','')), 2000);
      v_source_name := left(btrim(coalesce(v_item->>'source_name','Unknown source')), 120);
      v_department := coalesce(v_item->>'department','microtasks_services');

      IF v_title = '' OR v_url NOT LIKE 'https://%' THEN CONTINUE; END IF;
      IF v_department NOT IN ('microtasks_services','trading','sports_horse_betting','ecommerce') THEN
        v_department := 'microtasks_services';
      END IF;

      v_observation_id := NULLIF(v_item->>'observation_id','')::uuid;
      v_analysis_run_id := NULLIF(v_item->>'analysis_run_id','')::uuid;
      v_model_used := NULLIF(btrim(v_item->>'model_used'),'');
      v_confidence := NULLIF(v_item->>'confidence_score','')::numeric;
      v_actionable := coalesce((v_item->>'is_actionable')::boolean, false);
      v_collection_status := NULLIF(v_item->>'collection_status','');

      v_verified := false;
      IF v_item->>'requested_status' = 'ai_verified'
         AND v_collection_status = 'collected_valid'
         AND v_observation_id IS NOT NULL
         AND v_analysis_run_id IS NOT NULL
         AND v_model_used IS NOT NULL
         AND v_confidence >= 0.600
         AND v_actionable THEN
        SELECT EXISTS (
          SELECT 1
            FROM public.verox_observations o
            JOIN public.verox_analysis_runs a
              ON a.id = v_analysis_run_id
           WHERE o.id = v_observation_id
             AND o.payload #>> '{metadata,collection_status}' = 'collected_valid'
             AND a.status = 'succeeded'
             AND a.model_name = v_model_used
             AND v_observation_id = ANY(a.input_observation_ids)
        ) INTO v_verified;
      END IF;

      INSERT INTO public.verox_opportunities (
        dedupe_key, title, summary, source_name, source_url, department,
        published_at, matched_keywords, score, status, last_seen_at, updated_at,
        observation_id, analysis_run_id, model_used, confidence_score, is_actionable
      ) VALUES (
        md5(lower(regexp_replace(v_title, '[[:space:]]+', ' ', 'g')) || '|' || lower(v_url)),
        v_title,
        left(coalesce(v_item->>'summary',''), 1500),
        coalesce(NULLIF(v_source_name,''),'Unknown source'),
        v_url,
        v_department,
        left(coalesce(v_item->>'published_at',''), 80),
        CASE WHEN jsonb_typeof(v_item->'matched_keywords') = 'array'
             THEN v_item->'matched_keywords' ELSE '[]'::jsonb END,
        least(100, greatest(0, coalesce((v_item->>'score')::integer, 0))),
        CASE WHEN v_verified THEN 'ai_verified' ELSE 'detected_unverified' END,
        now(), now(),
        CASE WHEN v_verified THEN v_observation_id ELSE NULL END,
        CASE WHEN v_verified THEN v_analysis_run_id ELSE NULL END,
        CASE WHEN v_verified THEN v_model_used ELSE NULL END,
        CASE WHEN v_verified THEN v_confidence ELSE NULL END,
        CASE WHEN v_verified THEN true ELSE false END
      )
      ON CONFLICT (dedupe_key) DO UPDATE SET
        summary = EXCLUDED.summary,
        source_name = EXCLUDED.source_name,
        department = EXCLUDED.department,
        published_at = EXCLUDED.published_at,
        matched_keywords = EXCLUDED.matched_keywords,
        score = EXCLUDED.score,
        last_seen_at = now(),
        updated_at = now(),
        observation_id = COALESCE(EXCLUDED.observation_id, public.verox_opportunities.observation_id),
        analysis_run_id = COALESCE(EXCLUDED.analysis_run_id, public.verox_opportunities.analysis_run_id),
        model_used = COALESCE(EXCLUDED.model_used, public.verox_opportunities.model_used),
        confidence_score = COALESCE(EXCLUDED.confidence_score, public.verox_opportunities.confidence_score),
        is_actionable = CASE WHEN EXCLUDED.status = 'ai_verified' THEN true ELSE public.verox_opportunities.is_actionable END,
        status = CASE
          WHEN public.verox_opportunities.status = 'ai_verified' THEN 'ai_verified'
          WHEN EXCLUDED.status = 'ai_verified' THEN 'ai_verified'
          ELSE 'detected_unverified'
        END;

      v_count := v_count + 1;
    END LOOP;
    RETURN jsonb_build_object('persisted', v_count, 'status', 'ok');
  ELSIF p_action = 'list' THEN
    SELECT coalesce(jsonb_agg(to_jsonb(q) ORDER BY q.score DESC, q.last_seen_at DESC), '[]'::jsonb)
      INTO v_rows
      FROM (
        SELECT id, title, summary, source_name, source_url, department, published_at,
               matched_keywords, score, status, observation_id, analysis_run_id,
               model_used, confidence_score, is_actionable, first_seen_at, last_seen_at, updated_at
          FROM public.verox_opportunities
         ORDER BY score DESC, last_seen_at DESC
         LIMIT 250
      ) q;
    RETURN jsonb_build_object('opportunities', v_rows, 'count', jsonb_array_length(v_rows));
  ELSE
    RAISE EXCEPTION 'unsupported action';
  END IF;
END;
$function$;

REVOKE EXECUTE ON FUNCTION public.verox_opportunity_api(text, text, jsonb) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION public.verox_opportunity_api(text, text, jsonb) TO anon;
