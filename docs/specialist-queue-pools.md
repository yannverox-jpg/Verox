# Pools de tâches spécialisés Verox

## Architecture livrée dans cette branche

L'API choisit un rôle à partir du type et de l'intention de la mission, écrit la tâche dans Supabase puis publie un message dans le topic durable dédié. Chaque rôle possède son propre abonné Vercel Queue et son budget de concurrence. Les neuf topics sont définis par `verox_brain/queue_config.py`.

- `verox-agent-brain_coordination`
- `verox-agent-public_research`
- `verox-agent-revenue_research`
- `verox-agent-microtask`
- `verox-agent-service_work`
- `verox-agent-mirror_store`
- `verox-agent-sports_analysis`
- `verox-agent-horse_racing_analysis`
- `verox-agent-software_build`

Les files sont isolées par spécialité, mais les rôles utilisent encore le moteur IA commun configuré. Les connecteurs GPT/Claude/Gemini/Grok ne sont pas déclarés opérationnels tant qu'un appel réel n'a pas été vérifié.

## Configuration Vercel

Chaque variable de concurrence est facultative; la valeur par défaut est 2 et les valeurs sont bornées entre 1 et 20.

| Variable | Rôle |
| --- | --- |
| `VEROX_CONCURRENCY_BRAIN_COORDINATION` | Coordination du cerveau |
| `VEROX_CONCURRENCY_PUBLIC_RESEARCH` | Recherche publique |
| `VEROX_CONCURRENCY_REVENUE_RESEARCH` | Recherche de revenus |
| `VEROX_CONCURRENCY_MICROTASK` | Microtravail |
| `VEROX_CONCURRENCY_SERVICE_WORK` | Prestations |
| `VEROX_CONCURRENCY_MIRROR_STORE` | Boutique miroir |
| `VEROX_CONCURRENCY_SPORTS_ANALYSIS` | Analyse sportive |
| `VEROX_CONCURRENCY_HORSE_RACING_ANALYSIS` | Analyse hippique |
| `VEROX_CONCURRENCY_SOFTWARE_BUILD` | Développement logiciel |
| `VEROX_QUEUE_MAX_ATTEMPTS` | Nombre maximal de tentatives, par défaut 3, maximum 8 |

Commencer avec les valeurs par défaut. Augmenter les limites uniquement après mesure des délais, quotas du fournisseur et taux d'échec. Une concurrence plus forte peut accroître la latence côté fournisseur et provoquer des limitations.

## Traces et reprise

Le worker enregistre les événements `enqueued`, `started`, `completed`, `retry_scheduled`, `retry_exhausted` et les erreurs de configuration dans `verox_task_events`. Les traces incluent le rôle, le statut, l'heure et les durées de file/modèle; aucun prompt ni secret n'est écrit dans le registre de télémétrie.

Le point protégé `GET /api/tasks/recover` est planifié toutes les cinq minutes par Vercel Cron. Il récupère les tâches bloquées après une interruption et republie leur message sur la file du rôle. L'opération de démarrage en base reste atomique afin que les doublons de message ne déclenchent pas deux exécutions simultanées de la même tâche.

## Migration requise avant activation

La migration `supabase/migrations/20261121120000_verox_task_telemetry_and_recovery.sql` doit être appliquée au projet Supabase avant d'utiliser la télémétrie persistante ou le mécanisme de récupération. Elle réutilise le jeton interne existant `verox-task-api`; aucun secret ne doit être commité.

Après migration et déploiement, valider :
1. une tâche par rôle, et le topic réellement consommé;
2. deux tâches concurrentes dans le même rôle et deux rôles différents;
3. une panne simulée du modèle puis une reprise;
4. une tâche laissée en état `running`, puis récupérée par le cron;
5. les métriques et événements persistés;
6. p50/p95/p99 de l'attente en file, du modèle et du cycle total.

## Limite de preuve actuelle

L'ajout du code et des tests ne signifie pas que le déploiement Vercel est actif ou que la migration a été appliquée. Le statut du build doit être vérifié séparément. Le coût par réussite et les percentiles de latence ne peuvent être annoncés qu'après collecte de mesures réelles.
