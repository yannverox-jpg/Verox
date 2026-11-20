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

### P4 — recherche et opportunités
- [ ] Exécuter les flux de recherche configurés, conserver la source, la date, les preuves et un score explicable.
- [ ] Dédupliquer et classer les opportunités; distinguer pistes détectées, vérifiées et exécutables.
- [ ] Tester le cron et la persistance des résultats avant de considérer le scan autonome.
- [ ] Aucun rendement financier ne doit être promis; toute décision de trading/paris reste en mode analyse jusqu'à autorisation explicite et intégration vérifiée.

### P5 — exploitation contrôlée
- [ ] Surveiller les erreurs, les délais, le nombre d'appels et la consommation.
- [ ] Prévoir des budgets/limites et arrêter les tâches en cas de dépassement.
- [ ] Produire un rapport quotidien de tâches terminées, échouées, en attente et de preuves.
- [ ] Définir des critères d'acceptation mesurables avant de déclarer Verox autonome.

## Critère d'acceptation minimal
Le cerveau n'est validé que si une requête réelle passe par Vercel AI Gateway, produit une réponse non vide, identifie le fournisseur/modèle réellement utilisé, persiste le résultat dans Supabase et permet de relire ce résultat après la fin de la requête. Le statut `READY` d'un déploiement seul ne suffit pas.
