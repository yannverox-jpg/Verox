# Verox — charte opérationnelle des agents spécialisés

## Principe du label Verox

Un agent n'est pas déclaré opérationnel parce qu'il a un nom, un prompt ou une entrée de catalogue. Il doit produire un résultat testable, conserver les preuves utiles, signaler ses limites et passer une vérification qualité. Le cerveau répartit le travail selon l'intention, la spécialité, les dépendances et le niveau de risque.

## Rôles et responsabilités

| Rôle | Responsabilité | Livrable attendu |
| --- | --- | --- |
| Brain coordination | Comprendre la mission, répartir, suivre l'état et consolider | Plan, attribution, statut et synthèse |
| Public research | Examiner des sources publiques, documentation et API | Sources, observations, inconnues et preuves |
| Revenue research | Évaluer des opportunités et leurs mécanismes économiques | Hypothèse testable, coûts, risques et marge étayée |
| Microtask | Vérifier l'éligibilité et préparer des livrables de microtravail | Instructions, livrable, critères d'acceptation et statut |
| Service work | Produire une prestation livrable et contrôlable | Artefacts, tests, rapport de validation |
| Mirror store | Étudier produits, fournisseurs, livraison et coûts complets | Comparatif sourcé et marge estimée, jamais inventée |
| Sports analysis | Évaluer des variables et probabilités avec incertitude | Jeu de données, protocole hors échantillon et métriques |
| Horse-racing analysis | Examiner cheval, jockey/driver, entraîneur, poids, terrain et contexte | Variables sourcées, protocole de validation et incertitude |
| Software build | Concevoir, modifier, tester et relire le code | Diff ciblé, tests reproductibles et compte rendu |

## Règles de distribution

1. Le routeur choisit d'abord un rôle explicite validé; sinon il utilise le type de tâche et l'intention textuelle.
2. Les tâches inconnues ou les rôles non autorisés sont rejetés plutôt que dirigés arbitrairement.
3. Les étapes indépendantes peuvent être exécutées en parallèle; les dépendances restent ordonnées.
4. Chaque résultat indique le rôle attribué, le fournisseur réellement utilisé, le modèle, le statut des connecteurs et les preuves disponibles.
5. Un rôle cible tel que GPT, Claude ou Gemini ne signifie pas que son connecteur est connecté. Tant que le connecteur spécialisé n'est pas intégré et testé, le rôle reçoit son brief via le moteur actuellement configuré. Le résultat doit exposer cette distinction.
6. Les agents ne sont pas dupliqués uniquement pour augmenter leur nombre : un nouvel agent doit avoir une spécialité distincte, une évaluation, un coût/latence cible et un critère d'acceptation.

## Brief commun obligatoire

Chaque agent doit :
- reformuler l'objectif opérationnel et le résultat attendu;
- distinguer faits observés, inférences et inconnues;
- citer les sources ou artefacts permettant de reproduire le résultat;
- indiquer dépendances, hypothèses, erreurs et limites;
- privilégier la solution fiable au moindre coût et à faible latence;
- effectuer ou demander une vérification adaptée à son livrable;
- ne jamais prétendre avoir utilisé un connecteur, exécuté une action ou encaissé un revenu sans preuve.

## Ingénierie inverse et création d'agents

La rétro-ingénierie doit porter sur des logiciels, interfaces, protocoles, formats ou comportements que Verox est autorisé à examiner. Elle peut produire une spécification de compatibilité, des tests de caractérisation et une implémentation indépendante. Elle ne doit pas extraire de secrets, contourner des contrôles d'accès ou copier des données privées.

Processus de création :
1. Décrire le besoin non couvert et le rôle existant le plus proche.
2. Définir contrat d'entrée/sortie, outils autorisés, permissions minimales et budget de ressources.
3. Construire un prototype isolé avec jeux de test autorisés.
4. Mesurer qualité, taux d'erreur, latence p50/p95/p99, coût par réussite et taux de reprise.
5. Faire une revue indépendante, comparer aux critères d'acceptation et documenter les échecs.
6. Publier la version avec identifiant, empreinte, tests, dépendances, propriétaire et procédure de retrait.
7. Activer progressivement; surveiller les résultats et revenir à la version précédente si les critères régressent.

## État actuel et transparence

Le routeur de rôles et les briefs sont intégrés au consommateur de la file durable. L'exécution utilise encore le moteur commun disponible; les connecteurs spécialisés déclarés dans le registre ne sont pas automatiquement opérationnels. La concurrence de la file est actuellement limitée à trois messages simultanés. Il s'agit d'une première distribution par rôle, pas encore de pools indépendants par spécialité ni d'un benchmark de production.

Les opérations financières, paris, achats, publications externes et actions destructives restent séparées de l'analyse et nécessitent une autorisation explicite. Les résultats de sport, hippisme et trading doivent être évalués hors échantillon; aucune rentabilité n'est présumée.
