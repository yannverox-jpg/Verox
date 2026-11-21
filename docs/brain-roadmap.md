# Verox Brain — fondations et feuille de route

## Mission
Verox est un agent de travail et de recherche, pas seulement un chatbot. Il doit comprendre une demande, planifier des étapes, utiliser les outils autorisés, exécuter, vérifier, conserver l'état et rendre compte honnêtement du résultat.

## Architecture canonique
- **Modèle principal : Vercel AI Gateway**, via le Virtual Model `vmc/verox-free-brain`.
- **Routage du modèle :** le Virtual Model `verox-free-brain` est configuré avec `nvidia/nemotron-3.5-lightning-free` en principal et `reflection/beam-free` en secours. Ne pas ajouter de modèle payant ou de repli payant.
- **Authentification :** sur Vercel, utiliser le jeton OIDC fourni dans l'en-tête `x-vercel-oidc-token` de la requête entrante. Une clé AI Gateway explicite n'est requise que si l'OIDC n'est pas disponible. Ne jamais exposer ni journaliser les jetons.
- **Secours fournisseur :** les clés Gemini configurées sont un secours explicite si le Gateway échoue. Elles ne sont pas le cerveau principal. Le fournisseur et le modèle réellement utilisés doivent être enregistrés dans le résultat de chaque tâche.
- **Mémoire et état :** Supabase est la source persistante pour les tâches, leur statut et les résultats. Ne jamais annoncer une mémoire conversationnelle durable tant que la lecture/écriture de messages n'est pas implémentée et testée.
- **Exécution :** GitHub est le dépôt de code; Vercel héberge l'API. Une tâche n'est terminée que lorsque son état final et son résultat sont persistés et vérifiables.

## Règles de conduite
1. Séparer explicitement les faits vérifiés, les hypothèses et les actions proposées.
2. Ne jamais prétendre avoir modifié un dépôt, exécuté un test, déployé, transféré de l'argent ou exécuté une transaction sans preuve vérifiable.
3. Aucune dépense, transaction, pari, ordre de trading, publication externe, suppression ou action irréversible sans autorisation humaine explicite.
4. Ne jamais considérer un résultat de recherche, une opportunité, un solde ou un rendement comme vérifié sans source et horodatage.
5. Respecter la confidentialité : pas de secrets dans les prompts, résultats publics, journaux ou réponses.
6. Ne pas sélectionner silencieusement un modèle payant. Si aucun modèle gratuit/authentifié n'est disponible, échouer clairement et signaler la cause.
7. Ne pas boucler indéfiniment : limiter les tentatives, les appels d'outils, la taille des prompts et la durée de chaque tâche.

## Feuille de route

### P0 — rendre le cerveau accessible
- [ ] Confirmer que le déploiement de production utilise le code Gateway-first.
- [ ] Vérifier `/api/health` : OIDC Gateway disponible, modèle configuré et Supabase vérifié.
- [ ] Faire un appel réel au Virtual Model et vérifier le fournisseur/modèle renvoyé dans les métadonnées.
- [ ] Confirmer dans les logs AI Gateway qu'une requête a été enregistrée et qu'aucun modèle payant n'a été utilisé.

### P1 — fiabiliser les tâches
- [ ] Examiner les tâches échouées dans Supabase sans afficher de secrets ni de prompts privés.
- [ ] Corriger la course entre file durable et traitement inline; ne pas répondre « queued » si la tâche est déjà terminée ou échouée.
- [ ] Ajouter des transitions d'état contrôlées, des tentatives bornées, un délai d'expiration et une reprise sûre.
- [ ] Tester création → exécution → persistance → lecture du résultat, puis interruption/reprise.

### P2 — donner une vraie capacité d'action
- [ ] Définir des outils explicites et à permissions minimales : lecture GitHub, modification sur branche dédiée, tests, vérification du déploiement, recherche web et analyse d'opportunités.
- [ ] Chaque outil doit retourner une preuve structurée : action, résultat, horodatage, statut et référence vérifiable.
- [ ] Les changements de code passent par branche et pull request; exécuter les tests avant fusion.
- [ ] Exiger une validation humaine avant toute action financière, publication ou opération destructive.

### P3 — mémoire et continuité
- [ ] Implémenter l'enregistrement et la récupération des messages de chat.
- [ ] Enregistrer les objectifs, décisions, contraintes et état de travail, avec horodatage et provenance.
- [ ] Résumer les sessions sans confondre souvenirs, suppositions et faits vérifiés.
- [ ] Ajouter des tests de continuité après redéploiement et reprise de tâche.

### P4 — rétro-ingénierie des plateformes et recherche de revenus
- [ ] Cartographier les pages publiques, composants visibles, formulaires, parcours, prix, frais, récompenses et appels à l'action sans contourner les contrôles d'accès.
- [ ] Relier chaque hypothèse économique à des observations horodatées et à une source; distinguer les signaux de demande des revenus effectivement encaissés.
- [ ] Prioriser les pistes de revenus légitimes selon revenu net horaire mesuré, coût, délai de paiement, répétabilité, éligibilité, risque et qualité des preuves.
- [ ] Construire des expériences contrôlées (groupe témoin, période comparable, métrique de résultat, facteurs de confusion), utiliser différence-de-différences lorsque les hypothèses sont défendables, puis valider hors échantillon.
- [ ] Ne jamais traiter une corrélation, un score ou une estimation comme une causalité démontrée ou un revenu acquis.
- [ ] Exécuter les flux de recherche configurés, conserver la source, la date, les preuves et un score explicable; dédupliquer et distinguer pistes détectées, vérifiées et exécutables.
- [ ] Tester le cron et la persistance des résultats avant de considérer le scan autonome. Les mises, transactions, commandes et actions financières restent interdites sans autorisation spécifique.

### P5 — rétro-ingénierie et auto-réparation
- [ ] Inventorier imports, routes, variables de configuration, workflows et dépendances sans exécuter le code inspecté.
- [ ] Exécuter des contrôles déterministes à durée limitée, classifier les erreurs, conserver des rapports expurgés et borner les nouvelles tentatives.
- [ ] Tester toute proposition de correctif dans une copie temporaire, vérifier les chemins autorisés, la compilation et les tests ciblés; aucune fusion ni mise en production automatique.
- [ ] Mesurer les régressions et réutiliser les résultats des cycles précédents pour améliorer les prochains diagnostics.

### P6 — exploitation contrôlée
- [ ] Surveiller les erreurs, les délais, le nombre d'appels et la consommation.
- [ ] Prévoir des budgets/limites et arrêter les tâches en cas de dépassement.
- [ ] Produire un rapport quotidien de tâches terminées, échouées, en attente et de preuves.
- [ ] Définir des critères d'acceptation mesurables avant de déclarer Verox autonome.

## Critère d'acceptation minimal
Le cerveau n'est validé que si une requête réelle passe par Vercel AI Gateway, produit une réponse non vide, identifie le fournisseur/modèle réellement utilisé, persiste le résultat dans Supabase et permet de relire ce résultat après la fin de la requête. Le statut `READY` d'un déploiement seul ne suffit pas.


## Connecteurs additionnels (optionnels, désactivés par défaut)

### Hugging Face Inference Providers
- Variables : `VEROX_HF_ENABLED=true`, `HF_TOKEN`, `VEROX_HF_MODEL`.
- Endpoint OpenAI-compatible : `https://router.huggingface.co/v1/chat/completions`.
- Hugging Face Inference Providers n'est pas garanti gratuit : le compte Free n'inclut pas de crédits d'inférence mensuels selon la tarification publiée. Ne pas activer sans vérifier le coût/les crédits du compte. Le connecteur reste désactivé tant que `VEROX_HF_ENABLED` n'est pas explicitement à `true`.

### PocketPal (inférence locale sur Android)
- PocketPal peut exposer un serveur API local compatible OpenAI quand un modèle est chargé et que le serveur local est activé.
- Variables : `VEROX_POCKETPAL_ENABLED=true`, `POCKETPAL_BASE_URL`, `POCKETPAL_MODEL`, `POCKETPAL_API_KEY`.
- L'URL doit être HTTPS pour ce connecteur cloud. Une adresse privée comme `http://192.168.x.x:port` n'est pas accessible depuis une fonction Vercel. Pour relier le téléphone au cerveau cloud, il faut un tunnel HTTPS authentifié et stable, ou bien exécuter l'orchestrateur local sur le même réseau. Ne jamais exposer le serveur du téléphone sur Internet sans authentification.
- Ce connecteur reste désactivé par défaut. La connexion ne sera déclarée opérationnelle qu'après un appel réel réussi et une vérification de sécurité.

### Ordre de routage
1. Vercel AI Gateway et le modèle virtuel `vmc/verox-free-brain`.
2. PocketPal si explicitement activé et accessible.
3. Hugging Face si explicitement activé et authentifié.
4. Les clés Gemini configurées, comme secours.
Aucun connecteur optionnel n'est activé automatiquement et aucun modèle payant n'est sélectionné silencieusement.


## Registre de sources OSINT (P4)
- [x] Ajouter un catalogue structuré de sources candidates pour micro-tâches, sport/turf, trading, e-commerce et communautés publiques.
- [x] Exposer une API de lecture `GET /api/sources`, avec filtres `domain` et `q`.
- [x] Afficher un état explicite `NON_CONFIGURÉE` et `verified_live=false` pour éviter de confondre présence au catalogue et connexion réelle.
- [ ] Ajouter un connecteur source par source uniquement après validation des conditions d'accès, de la fraîcheur, des limites et des tests réels.
- [ ] Ne pas automatiser mises, transactions, commandes ou publications; la détection d'une opportunité n'est jamais une autorisation d'action.
