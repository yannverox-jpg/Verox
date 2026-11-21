# Verox — architecture de latence et distribution des agents

## Décision
Ne pas ajouter plusieurs serveurs applicatifs ni un GPU OSINT par défaut avant d'avoir mesuré les goulots d'étranglement. Pour Verox, la première optimisation est une file de tâches durable, des workers horizontaux et spécialisés, des appels parallèles bornés, une persistance rapide et une mesure de latence par étape. Un GPU n'accélère pas Playwright, les requêtes HTTP, les attentes réseau ou les opérations SQL ordinaires.

## Architecture cible
1. **UI / API Vercel** : endpoints courts, streaming pour les réponses interactives, validation des entrées et mise en file des missions longues. Retourner rapidement un identifiant de mission plutôt que bloquer la requête HTTP pendant la collecte.
2. **Orchestrateur** : décompose la mission en tâches idempotentes, applique les priorités, les délais et les budgets de calcul; lance en parallèle uniquement les étapes indépendantes.
3. **File durable** : sépare la réception de la mission et son exécution. Chaque message porte un identifiant de corrélation, un identifiant de mission, une clé idempotente, une échéance et un nombre de tentatives borné.
4. **Pools de workers** : séparer au minimum les catégories navigateur/collecte, analyse CPU, appels de modèles, et tâches de longue durée. Les workers consomment la file avec une concurrence configurable et une limite par domaine/source.
5. **Supabase** : garder les tâches, états, résultats compacts et références de preuves; éviter de transporter de gros artefacts dans les lignes SQL. Pool de connexions, requêtes ciblées, index justifiés et écritures groupées lorsque sûres.
6. **Stockage d'artefacts** : déposer captures, fichiers publics collectés et rapports dans un stockage objet privé; ne conserver dans la base que métadonnées, empreintes, taille, type et référence.
7. **Observabilité** : corrélation de bout en bout entre UI, API, file, worker, modèle et Supabase; histogrammes p50/p95/p99; nombre de tâches en attente; durée par étape; taux de retry; timeout; erreurs; coût et taille de réponse.
8. **Récupération** : heartbeat de worker, bail/lease expirant, retry avec backoff et jitter, dead-letter queue, reprise idempotente, et alerte sur la file qui vieillit.

## Routage des agents
- Envoyer au modèle le plus petit et le plus rapide qui atteint le seuil de qualité de la tâche.
- Réserver les modèles de raisonnement plus lents aux tâches complexes, ambiguës ou à fort enjeu.
- Exécuter en parallèle les recherches indépendantes, puis consolider leurs résultats avec les références de preuve.
- Réutiliser les clients HTTP et les connexions, mettre en cache uniquement les données publiques stables et versionner les caches.
- Limiter la taille des pages, des prompts, des résultats d'outils et des historiques envoyés aux modèles.
- Utiliser le streaming pour réduire le temps perçu avant le premier résultat; mesurer séparément le time-to-first-token et la durée totale.
- Ne jamais utiliser un fallback payant sans configuration et autorisation explicites; respecter les limites du fournisseur.

## Faut-il un GPU ?
### Pas pour le premier palier
Les tâches OSINT publiques et Playwright sont généralement limitées par le réseau, les temps de réponse des sites, les quotas, la navigation et le parsing. Des workers CPU bien dimensionnés, proches des services qu'ils consultent, donnent généralement un meilleur rapport latence/coût. Multiplier les serveurs sans mesure peut augmenter les coûts, la contention et les appels redondants.

### Ajouter un GPU seulement si les mesures le justifient
Un GPU devient pertinent pour l'inférence de modèles auto-hébergés, l'embedding à gros volume, le reranking massif, l'OCR/vision à grande échelle ou une analyse locale qui domine réellement le temps total. Il faut comparer le coût total, la latence p95, le débit, la mémoire VRAM, la disponibilité, la consommation et la maintenance contre un endpoint d'inférence managé. Le GPU ne doit pas être un serveur OSINT généraliste.

## Protocole de mesure avant mise à l'échelle
Instrumenter au minimum ces horodatages : `request_received`, `task_enqueued`, `worker_started`, `source_request_started`, `source_response_received`, `model_request_started`, `first_token`, `model_completed`, `persistence_completed`, `task_completed`.
Calculer :
- latence de file = worker_started − task_enqueued ;
- latence source = source_response_received − source_request_started ;
- latence modèle = model_completed − model_request_started ;
- temps avant premier jeton = first_token − model_request_started ;
- persistance = persistence_completed − model_completed ;
- durée de bout en bout et percentiles p50/p95/p99 ;
- débit, erreurs, retries, coût moyen et qualité vérifiée.

Effectuer un test de charge progressif avec jeux de données autorisés, puis déterminer si le goulot est le modèle, le réseau, la file, le navigateur ou Supabase. N'augmenter le nombre de workers que jusqu'au point où le débit augmente sans dégrader les percentiles, les quotas ou les coûts.

## Sécurité et réputation
Les workers doivent avoir des permissions minimales, des secrets serveur uniquement, des limites de concurrence et de ressources, des journaux sans secrets, et des politiques de destination. La navigation autorisée doit respecter les conditions d'accès, les quotas et les limites des services. Les missions et actions financières restent distinctes des analyses et requièrent les autorisations appropriées.

## Critères d'acceptation
- Chaque tâche a un identifiant de corrélation et une trace temporelle exploitable.
- L'API interactive ne reste pas bloquée par une collecte longue.
- Le worker peut redémarrer sans créer de doublons ni perdre silencieusement une tâche.
- La file et les erreurs sont visibles dans le dashboard.
- Les tests de charge publient les p50/p95/p99, le débit et le coût.
- Une décision GPU ou multi-serveurs est appuyée par un benchmark reproductible.
