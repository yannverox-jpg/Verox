# Feuille de route : moteur d’opportunités et de revenus Verox

## But mesurable

Transformer Verox en outil qui découvre des occasions compatibles avec le profil du propriétaire, prépare le travail autorisé, suit son acceptation et ses paiements, et présente clairement le résultat net. « Machine à cash » est un objectif produit, pas une promesse de rendement : aucun worker ne peut garantir qu’une plateforme accepte une candidature, paie une mission, ou qu’un pari, trade ou produit soit rentable.

**Métrique principale :** revenu net effectivement encaissé par heure de travail humain + coût d’exploitation Verox, par catégorie. Afficher séparément revenu trouvé, soumis, approuvé, payé et retiré.

## Règles de livraison

- Construire une tranche verticale de bout en bout avant d’ajouter de nombreuses plateformes.
- Une plateforme n’est exécutable qu’après vérification documentée de la région, l’éligibilité, des conditions d’automatisation, du mode d’authentification et des permissions.
- Une tâche qui exige une présence humaine, une opinion personnelle, une preuve de travail ou un test de perception reste attribuée au propriétaire.
- Les statuts et revenus viennent des réponses réelles du service; jamais de l’inférence du modèle.
- Conserver un journal d’audit sans identifiants secrets.
- Garder chaque domaine financier isolé du worker de micro-tâches.

## Priorités et lots de travail

### P0 — Rendre la branche déployable

**Travaux**
- Vérifier le dernier commit et les contrôles CI.
- Résoudre la limite Vercel « build-rate-limit » avec la capacité/plan du projet; vérifier Services, variables requises, Cron et queue.
- Déployer un preview, puis un environnement de production après validation.
- Vérifier le flux réel chat → Gemini tool call → file de navigation → worker Playwright → résultat → dashboard.

**Acceptation**
- Le chat répond en production.
- Une demande de recherche appelle le bon outil, crée au plus cinq jobs et rend leur état.
- Une conversation normale n’ouvre pas de navigateur.
- Aucune session de compte ni action externe n’est créée par le test.
- Le statut du déploiement est observable dans le tableau de bord.

**Blocage constaté dans le dépôt :** le PR #70 est ouvert/non fusionné et son contrôle Vercel était bloqué par la limite de déploiement. La branche n’est pas la production.

### P1 — Choisir et qualifier la première plateforme rémunératrice

Choisir une seule plateforme de micro-tâches pour le premier connecteur, parmi celles auxquelles le propriétaire est réellement admissible. Avant le code, remplir une fiche vérifiée :

| Champ | Valeur à documenter |
|---|---|
| Pays et critères d’éligibilité | Source officielle et date |
| API/OAuth ou interface disponible | Documentation officielle |
| Automatisation permise | Portée exacte, fréquence, attribution |
| Actions autorisées | Lecture, préparation, soumission, paiement |
| Travail humain requis | Étapes qui ne peuvent être déléguées |
| Paiement | Seuil, devise, frais, délais, preuve de paiement |
| Limites | Quotas, fréquence, usage du compte, révocation |

Ne pas choisir sur la base d’une page de marketing ou d’une entrée dans le catalogue OSINT.

**Acceptation**
- La connexion est révocable et ses permissions sont visibles.
- Le dashboard affiche le profil/éligibilité et les offres retournées par la plateforme.
- Les offres non éligibles ou non automatisables sont exclues ou présentées comme étapes manuelles.
- Les missions et paiements sont rapprochés par identifiant externe et horodatage.

### P2 — Construire le premier flux de micro-tâche de bout en bout

**Travaux**
- Implémenter un adaptateur utilisant l’API officielle si elle existe; sinon, utiliser uniquement la voie d’automatisation explicitement permise.
- Ajouter déduplication, expiration, reprise idempotente, limites de débit, gestion des erreurs et révocation.
- Permettre à Gemini de lire une mission et préparer un brouillon structuré; n’autoriser une soumission automatique que si la plateforme l’autorise et si le travail est réellement terminé.
- Signaler clairement toute étape humaine.
- Créer le registre des revenus : trouvé → préparé → soumis → accepté/rejeté → payé → retiré.

**Acceptation**
- Une mission de test est suivie sans double soumission.
- Le dashboard ne compte pas une offre ou une soumission comme revenu.
- Un paiement n’est marqué encaissé qu’après retour confirmé ou rapprochement explicite.
- Chaque état est retraçable jusqu’à la mission et au connecteur.

### P3 — Prioriser le portefeuille d’opportunités

Créer un score explicable, sans promesse de gain :

`score = éligibilité × probabilité historique d’acceptation × rémunération nette estimée / (temps humain + temps machine + délai de paiement)`

Afficher séparément les valeurs observées et estimées, l’échantillon, la date, les frais et les hypothèses. Ne pas afficher un score quand les données sont insuffisantes.

**Mesures**
- opportunités qualifiées / examinées;
- taux d’éligibilité;
- missions réellement terminées;
- taux d’acceptation/rejet;
- rémunération brute et nette payée;
- revenu net par heure humaine;
- coût Gemini, navigateur, hébergement et paiement;
- délai entre soumission et paiement.

### P4 — Ajouter les services « IA à IA »

Utiliser le worker pour découvrir des appels d’offres, demandes de services et tâches de production compatibles. Gemini peut analyser le brief, estimer effort/compétences, produire un brouillon de proposition, générer des livrables quand cela est permis et vérifier leur cohérence.

Le profil, les références, l’identité, les qualifications, les résultats et les heures de travail doivent rester véridiques. Les plateformes exigeant une validation humaine, un entretien ou une compétence personnelle présentent une étape au propriétaire. L’usage d’une autre IA comme sous-traitant n’est activé que si les règles de la place de marché le permettent et que les fournisseurs sont configurés explicitement.

**Acceptation**
- aucune candidature envoyée deux fois;
- provenance et contribution de l’IA conservées;
- coûts de calcul et frais inclus dans le calcul net;
- aucun livrable déclaré validé avant la réponse réelle du client.

### P5 — Ajouter séparément e-commerce, trading et paris

Ne pas mélanger ces modules avec le moteur de micro-tâches ni avec son solde de revenus.

- **E-commerce :** commencer par veille produit, fournisseurs, prix, frais, livraison, retours et marges estimées; utiliser le stock, commandes, publicité et ventes seulement via un connecteur qualifié.
- **Trading :** commencer par la lecture de données et un portefeuille simulé. Mesurer frais, glissement, volatilité et pertes simulées avant toute possibilité d’ordre réel.
- **Paris :** commencer par la lecture de données publiques et l’historique simulé. Les probabilités estimées ne sont pas des garanties. Toute interface d’exécution doit être vérifiée séparément pour l’opérateur, l’accès autorisé et les règles d’automatisation.

Toute opération réelle doit être un type d’action distinct avec montant, plateforme, identifiant d’idempotence, limite de perte/exposition définie par le propriétaire et preuve du résultat. Le système ne déduit jamais qu’un solde de micro-tâches autorise automatiquement un transfert ou une mise. Le propriétaire décide séparément si les fonds demeurent sur l’opérateur, sont retirés ou affectés à un autre usage.

**Acceptation**
- lecture, simulation et exécution ont des états et permissions distincts;
- aucun mouvement de fonds n’est déclaré sans confirmation de l’opérateur;
- limites, coûts et résultat net sont affichés avant et après action;
- les pertes possibles ne sont jamais omises ou présentées comme récupérables.

### P6 — Exploitation fiable

- Ajouter alertes pour worker arrêté, file bloquée, échec auth, source désactivée, paiements en attente et coût anormal.
- Prévoir arrêt d’urgence par connecteur, révocation des secrets, plafond de requêtes et reprise contrôlée.
- Publier un rapport quotidien : nouvelles opportunités, tâches nécessitant le propriétaire, états, paiements confirmés, coûts, bénéfice net et anomalies.
- Examiner les métriques après une période de fonctionnement avant d’augmenter le volume.

## Modèle de données suggéré

- `connector_accounts`: identifiant, plateforme, statut, régions, date de connexion, référence au coffre (aucun secret brut).
- `connector_capabilities`: lecture/préparation/soumission/paiement/exécution, état, justification/URL de règle, date de vérification.
- `opportunities`: identifiant externe, source, critères, rémunération annoncée, expiration, état, observation datée.
- `work_items`: opportunité, propriétaire/worker, étapes, artefacts, temps, soumission et identifiant externe.
- `ledger_entries`: brut, frais, net, devise, état, référence source, preuve.
- `execution_requests`: domaine, plateforme, type, montant maximum, décision, identifiant idempotent, état retourné.
- `audit_events`: transitions, agent, outil, motif et référence; jamais les secrets ni données de connexion.

## Ordre immédiat

1. Débloquer et vérifier le déploiement de la PR #70.
2. Sélectionner le premier service de micro-tâches admissible et documenter ses règles/API.
3. Livrer lecture d’offres + éligibilité + dashboard.
4. Livrer préparation et suivi d’une mission réelle, en gardant manuelles les étapes réservées à l’utilisateur.
5. Réconcilier les premiers paiements confirmés et publier les métriques de coût/net.
6. Étendre à un second service uniquement après que la première tranche fonctionne.
7. Construire les modules e-commerce, trading et paris comme projets isolés, par lecture/simulation avant toute exécution autorisée.

## Hors portée de ce plan

Le plan ne suppose pas que les plateformes autorisent des robots, que l’utilisateur soit admissible partout, qu’une mission soit disponible, ni qu’un pari, un trade ou une boutique génère un bénéfice. Il ne change pas les paramètres des applications tierces. Il ne transforme pas la recherche OSINT publique déjà présente en accès authentifié : chaque connecteur doit être construit, documenté et livré séparément.
