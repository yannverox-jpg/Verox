-- Idempotent technical bootstrap facts; no historical user data is fabricated.
insert into public.nexus_memory_facts (fact_key, category, content, source, active, updated_at)
values
 ('verox.runtime.queue', 'architecture', 'La file canonique des tâches CognitiveBrain est public.nexus_tasks; états: queued, running, succeeded, failed, cancelled, needs_approval.', 'verox_bootstrap', true, now()),
 ('verox.runtime.memory', 'architecture', 'La mémoire cognitive persistante est enregistrée dans public.nexus_memory; la mémoire SQLite locale n''est pas la source de vérité distante.', 'verox_bootstrap', true, now()),
 ('verox.runtime.audit', 'architecture', 'Les événements d''exécution sont enregistrés dans public.nexus_execution_events et liés aux tâches par task_id.', 'verox_bootstrap', true, now()),
 ('verox.runtime.security', 'security', 'La clé service-role Supabase doit rester côté serveur dans SUPABASE_SERVICE_ROLE_KEY; ne jamais la committer dans Git ni l''exposer au navigateur.', 'verox_bootstrap', true, now())
on conflict (fact_key) do update set category=excluded.category, content=excluded.content, source=excluded.source, active=true, updated_at=now();

insert into public.nexus_memory (namespace, memory_key, content, source, importance)
select 'verox.cognitive_brain', 'runtime_alignment_v1',
       jsonb_build_object('project','Verox','runtime','CognitiveBrain',
         'queue_table','nexus_tasks','memory_table','nexus_memory',
         'audit_table','nexus_execution_events','schema_alignment','20261115090000'),
       'verox_bootstrap', 4
where not exists (
  select 1 from public.nexus_memory
  where namespace='verox.cognitive_brain' and memory_key='runtime_alignment_v1'
);
