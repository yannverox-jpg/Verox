# Manuel du cerveau Verox : outils de recherche Playwright

## Chemin d’exécution

Le chat de production crée une tâche `chat` ou `research`, consommée par
`verox_queue_worker.py`. Ce worker appelle Gemini avec un manuel système et
trois déclarations de fonctions. Les fonctions sont exécutées côté serveur par
`verox_brain/playwright_tools.py`; le navigateur ne reçoit jamais les secrets
Gemini ni la requête libre du modèle comme URL. Les recherches sont placées dans
`verox-browser-jobs`, puis exécutées par le service Playwright.

Le paquet `cognitive_brain/` est une autre implémentation de mémoire/boucle
cognitive. Il n’est pas le cerveau qui sert le chat de production et ne pilote
pas la file Playwright. Le chemin de production documenté ici est le worker
Gemini Vercel.

## Outils donnés à Gemini

| Fonction | Usage | Limites |
|---|---|---|
| `list_public_research_sources` | Chercher dans le catalogue par domaine ou terme | Ne retourne que les entrées autorisées à la consultation publique |
| `start_public_research` | Demander une collecte à partir d’identifiants du catalogue | 5 sources maximum par appel, sources publiques enregistrées, objectif limité à 2 000 caractères |
| `get_public_research_status` | Lire un résultat existant | Identifiant d’analyse au format UUID |

Gemini peut appeler ces outils de façon autonome quand la demande porte sur une
recherche ou une collecte. La collecte est asynchrone; le chat doit communiquer
l’identifiant retourné et peut ensuite appeler l’outil d’état. Les observations
ne prouvent ni l’éligibilité d’un compte, ni l’existence d’une mission payée,
ni un revenu encaissé.

## Règles du navigateur

Le navigateur de recherche utilise un contexte sans cookies, sans session
connectée, JavaScript ni téléchargement. Les destinations proviennent du
registre des sources et restent limitées à leur hôte HTTPS enregistré. Le worker
ne se connecte pas aux comptes, ne résout pas de CAPTCHA, ne contourne pas de
paywall ou de refus, et ne soumet pas de mission. Une entrée marquée publique
dans le registre n’est pas à elle seule une validation juridique ou contractuelle
des conditions d’automatisation du site; les sources doivent être examinées et
désactivées si leurs règles interdisent cette collecte.

## État de déploiement

Ces outils sont du code de la branche de travail. Ils deviennent accessibles au
chat servi par la production seulement après fusion et déploiement réussi. Le
statut de déploiement doit être vérifié séparément; le code ne garantit aucun
revenu.
