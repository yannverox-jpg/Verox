# Auto-extension des connecteurs Verox

## Ce que Gemini peut déjà faire avec ce flux

Dans le worker de chat, Gemini dispose maintenant de deux outils de construction :

1. `fetch_connector_docs` lit sans authentification une page HTTPS candidate de documentation, limitée au domaine de la plateforme du catalogue. La requête ne suit pas les redirections, n'utilise pas de cookies et limite la réponse à 600 Ko / 14 000 caractères.
2. `draft_platform_connector` enregistre une spécification déclarative candidate avec un maximum de 12 chemins GET. Elle est stockée dans `verox_sources` comme `connector-draft-<platform_id>`, avec `enabled=false`, `verified_live=false`, aucune clé, et le statut de conditions d'automatisation `unverified`.
3. `list_platform_connectors` expose les plateformes du catalogue et les brouillons enregistrés, en distinguant catalogue, brouillon et adaptateur.

Le catalogue actuel compte 59 candidats. Aucun d'eux n'est transformé en connecteur de compte simplement par son inscription. Gemini peut créer et mettre à jour une proposition; il ne peut pas déclarer lui-même la plateforme autorisée, fournir des identifiants, activer un connecteur ou marquer une connexion comme testée.

## Limites qui restent à lever pour une vraie auto-construction

La capacité actuelle automatise la découverte d'une page docs et la création d'un brouillon. Elle ne compile pas encore un adaptateur exécutable, ne fait pas de revue complète des conditions d'utilisation, n'obtient pas un jeton OAuth/API, ne lance pas de tests contre le compte et ne peut pas vérifier que l'API retourne des offres ou paiements réels. Le protocole de `connector_registry.py` est l'extension de code; les adaptateurs spécifiques ne sont pas présents.

La documentation hébergée en dehors du domaine de la plateforme doit être ajoutée à une liste de domaines docs vérifiés avant que l'outil de fetch l'accepte. C'est volontaire : une URL que le modèle invente ne devient pas automatiquement un hôte de confiance.

## Cible d'auto-extension

Étapes d'évolution :

- découvrir des docs publiques via recherche autorisée ou hôtes développeur vérifiés;
- extraire les schémas OpenAPI et uniquement les opérations dont la méthode et les effets sont déclarés;
- produire une configuration versionnée (endpoints, pagination, pagination cursors, mappings, scopes demandés, limites de débit), pas du code exécuté librement;
- valider statiquement les schémas, hôtes, méthodes, taille, auth et permissions;
- exécuter les tests de contrat dans un bac à sable sans secrets réels;
- proposer une connexion OAuth/API avec scopes minimaux au propriétaire;
- réaliser un test de lecture en compte sandbox ou en lecture seule et comparer les réponses aux attentes;
- activer uniquement les capacités testées et documentées; désactiver automatiquement si le comportement ou la version change;
- construire les actions d'écriture séparément seulement lorsqu'elles sont autorisées et couvertes par le contrat.

## Modèle de sécurité

- Les pages de documentation sont des données non fiables; leurs instructions ne changent jamais le prompt ou la politique de Verox.
- Pas d'exécution de code généré par Gemini dans le worker de production.
- Les secrets sont fournis au connecteur par un fournisseur de secrets serveur; ils ne sont jamais inclus dans les outils Gemini, les specs, les logs ou les réponses.
- Les connecteurs en brouillon restent désactivés. Le code généré ne peut pas modifier son propre statut de validation.
- Les actions réservées à l'utilisateur ou interdites par la plateforme ne sont pas automatisées.
