# Verox Brain — fondations et feuille de route

## Mission
Verox est un agent de travail et de recherche, pas seulement un chatbot. Il doit comprendre une demande, planifier des étapes, utiliser les outils autorisés, exécuter, vérifier, conserver l'état et rendre compte honnêtement du résultat.

## Architecture canonique
- **Modèle : Gemini** via l'API Google, avec les clés `GEMINI_API_KEY`, `GEMINI_API_KEY_2` ou `Gemini` configurées côté serveur.
- **Fournisseur unique :** aucune inférence secondaire n'est activée. Le fournisseur et le modèle réellement utilisés sont enregistrés avec chaque résultat.
- **Mémoire et état :** Supabase est la source persistante pour les tâches, leur statut et les résultats. Ne jamais annoncer une mémoire conversationnelle durable tant que la lecture/écriture de messages n'est pas implémentée et vérifiée.
- **Exécution :** GitHub héberge le code, Render exécute le worker et Supabase conserve l'état. Une tâche n'est terminée que lorsque son état final et son résultat sont persistés et vérifiables.

## Règles de conduite
1. Séparer explicitement les faits vérifiés, les hypothèses et les actions proposées.
2. Ne jamais prétendre avoir modifié un dépôt, exécuté un test, déployé, transféré de l'argent ou exécuté une transaction sans preuve vérifiable.
3. Aucune dépense, transaction, pari, ordre de trading, publication externe, suppression ou action irréversible sans autorisation humaine explicite.
4. Ne jamais considérer un résultat de recherche, une opportunité, un solde ou un rendement comme vérifié sans source et horodatage.
5. Respecter la confidentialité : pas de secrets dans les prompts, résultats publics, journaux ou réponses.
6. Ne pas sélectionner silencieusement un modèle payant. Si aucun modèle gratuit/authentifié n'est disponible, échouer clairement et signaler la cause.
7. Ne pas boucler indéfiniment : limiter les tentatives, les appels d'outils, la taille des prompts et la durée de chaque tâche.

## Critère d’autonomie économique vérifiable

Verox ne sera qualifié d’agent économique autonome qu’après démonstration, avec des traces et preuves persistées, de toute la chaîne :

1. Opportunité réelle découverte depuis une source publique ou autrement autorisée.
2. Existence, date limite, conditions et adéquation aux compétences revérifiées.
3. Besoin client explicité, avec provenance et éléments manquants.
4. Solution développée, tests exécutés, sécurité contrôlée et déploiement démontré.
5. Offre chiffrée avec hypothèses, coûts, marge et durée.
6. Réponse et échanges client suivis avec leur état réel.
7. Travail accepté exécuté dans le périmètre convenu.
8. Facture produite et paiement rapproché d’une preuve de transaction.
9. Revenu net encaissé calculé à partir des recettes reçues moins coûts et frais documentés.
10. Résultats réels réutilisés pour évaluer les décisions suivantes.

Chaque étape garde son propre état et sa preuve. Une détection n’est pas une opportunité validée; un score n’est pas une probabilité de gain; une proposition n’est pas un contrat; une facture n’est pas un encaissement. Les revenus de services, micro-tâches et e-commerce restent séparés des résultats simulés et réels de trading ou de paris. Les montants espérés, engagés, facturés et encaissés ne sont jamais agrégés sous un même indicateur.

## Départements opérationnels

Les six départements partagent le même cerveau, la mémoire persistante, l’orchestrateur, le registre des permissions et la source de vérité. Ils utilisent des connecteurs spécialisés, activés un par un :

- **Micro-tâches et services numériques :** découvrir les missions rémunérées, comparer rémunération nette et éligibilité, évaluer les compétences/outils, exécuter uniquement selon les conditions et vérifier la qualité et le paiement.
- **Trading, sport et turf :** trois moteurs et historiques distincts. Commencer par la collecte autorisée, l’analyse, le test historique ou papier et la validation; refuser les opérations dont les preuves ou le rendement attendu ne satisfont pas les règles.
- **E-commerce :** distinguer boutique de redirection et boutique qui reçoit les commandes. Le catalogue, le coût rendu, le stock, l’expédition, les retours et remboursements suivent les conditions des interfaces autorisées.
- **Recherche commerciale (prioritaire côté revenus) :** suivre les besoins publics des éditeurs logiciels, entreprises technologiques/commerciales, banques, secteur public et organismes internationaux; joindre preuves, éligibilité et date limite; préparer analyse, solution et proposition. Les sources confidentielles exigent une autorisation adaptée.
- **Développement de solutions :** planifier, concevoir, coder, tester, examiner la sécurité, vérifier le déploiement et préparer une démonstration. Du code généré seul ne vaut jamais livraison.
- **Opérations commerciales et financières :** devis, réponses, contrats, factures, paiements et rapprochement, chacun avec un statut distinct et une autorisation explicite pour les engagements, publications ou mouvements d’argent.

Le cerveau central planifie les recherches, distribue le travail aux agents spécialisés, suit leur progression, évalue les preuves, détecte erreurs et doublons, reprend les opérations échouées avec des tentatives bornées, conserve l’historique des décisions et réaffecte les ressources d’après les résultats mesurés. Vercel héberge des services et Supabase conserve l’état; ni l’un ni l’autre ne constitue seul l’orchestrateur.

## Feuille de route priorisée

### P0 — fiabiliser la recherche et prouver la première chaîne Supabase
- Vérifier une collecte autorisée de bout en bout : source enregistrée, tâche, observation avec hash et horodatage, exécution du modèle, analyse liée à l’observation, relecture de la preuve.
- Enregistrer fournisseur/modèle réellement utilisés et les métriques sans journaliser de secrets.
- Dédupliquer les observations, garder les résultats non vérifiés en état non vérifié, et séparer explicitement collecte réussie et analyse réussie.
- Accepter ce jalon seulement après une recherche fraîche, persistée et relue dans Supabase; l’état du déploiement seul ne suffit pas.
- Le worker planifié est déclenché par GitHub Actions, mais s'exécute sur Render avec ses propres identifiants Gemini et Supabase. GitHub Actions ne reçoit aucun secret Supabase ni clé Gemini.

### P1 — construire l’orchestrateur central
- Relier planification, agents spécialisés et connecteurs via des contrats de capacité avec permissions minimales.
- Conserver état, preuves, décisions, budget, échéance, corrélation et provenance dans la file durable.
- Ajouter claim atomique, leases/heartbeat, tentatives bornées, reprise idempotente, détection de doublons, annulation et dead-letter.
- Mesurer progression, erreurs et limites de coût; réaffecter seulement sur la base de résultats mesurés.
- Vérifier interruption puis reprise sans perdre ni dupliquer le travail.

### P2 — recherche commerciale et clients
- Couvrir les sources publiques autorisées des éditeurs logiciels, entreprises technologiques/commerciales, banques, secteur public et organismes internationaux.
- Qualifier besoin, date limite, localisation, éligibilité et adéquation aux compétences.
- Construire la chaîne besoin → preuve → analyse technique → solution testée → coûts → proposition, sans envoyer d’engagement sans validation.
- Mesurer besoins vérifiés, propositions préparées, réponses, contrats et revenus reçus séparément.

### P3 — micro-tâches et services
- Ajouter un connecteur propre à chaque service, selon son API officielle, son interface et ses conditions.
- Avant exécution, comparer éligibilité, temps, rémunération, frais, risque et revenu net estimé.
- Refuser les automatisations que la plateforme n’autorise pas; enregistrer qualité, temps, rejet, paiement attendu et paiement confirmé.
- Évaluer la rentabilité sur l’argent réellement encaissé.

### P4 — e-commerce
- Distinguer modèle de redirection et prise de commande directe avant de créer la boutique.
- Qualifier fournisseur, coûts rendus, stock daté, délais, marge et obligations applicables.
- Ajouter synchronisation, commande, livraison, retour et remboursement seulement via des interfaces autorisées.
- Garder ventes simulées, commandes acceptées, livraisons et remboursements dans des états différents.

### P5 — trading, paris sportifs et hippiques
- Séparer les trois domaines, les jeux de données, modèles, autorisations, budgets et mesures.
- Commencer par données autorisées, historique, coûts, frais, slippage, incertitude, backtests et paper trading.
- Comparer résultats simulés et réels; ne pas convertir une prédiction ou une cote en promesse de revenu.
- Les ordres et mises restent désactivés sans connecteur permis et approbation explicite.

### P6 — opérations commerciales et financières
- Préparer devis, dossiers techniques, factures et rapprochements avec calcul traçable des coûts.
- Suivre réponses, négociations, contrats signés, factures dues, paiements reçus, remboursements et revenu net.
- Exiger une autorisation humaine vérifiable pour signature, engagement commercial, dépense, ordre, mise ou transfert.
- Ne jamais utiliser des revenus simulés, commissions espérées ou contrats signés comme revenus encaissés.

## Conditions de conception

Privilégier sources publiques, API officielles, composants existants et modèles locaux ou offres gratuites réellement disponibles. Un accès gratuit, un droit de lecture et une capacité opérationnelle sont trois états distincts. Les limites de quotas et les coûts potentiels sont affichés avant activation; aucun service payant ou connecteur d’écriture n’est activé implicitement.

## Critère de validation de l’application

La première version fiable n’exige pas toute l’autonomie économique; elle doit prouver le jalon P0. Chaque jalon suivant possède ses propres critères mesurables, preuves et permissions. Verox ne sera décrit comme autonome qu’après démonstration de la chaîne complète ci-dessus, sur des résultats réels et séparés par département.



## Fournisseur IA
Le client d'inférence utilise exclusivement Gemini. Configure une clé Gemini chiffrée dans l'environnement du service qui exécute le cerveau. Les autres connecteurs du catalogue ne sont pas des fournisseurs d'inférence actifs.


## Registre de sources OSINT (P4)
- [x] Ajouter un catalogue structuré de sources candidates pour micro-tâches, sport/turf, trading, e-commerce et communautés publiques.
- [x] Exposer une API de lecture `GET /api/sources`, avec filtres `domain` et `q`.
- [x] Afficher un état explicite `NON_CONFIGURÉE` et `verified_live=false` pour éviter de confondre présence au catalogue et connexion réelle.
- [ ] Ajouter un connecteur source par source uniquement après validation des conditions d'accès, de la fraîcheur, des limites et des tests réels.
- [ ] Ne pas automatiser mises, transactions, commandes ou publications; la détection d'une opportunité n'est jamais une autorisation d'action.
