# Registre OSINT Verox

Le registre de sources est une liste de départ structurée, pas une preuve d'intégration active.

## API de lecture
- `GET /api/sources`: toutes les sources et catégories.
- `GET /api/sources?domain=trading`: filtre par domaine.
- `GET /api/sources?q=odds`: recherche textuelle dans nom, type et description.

Domaines disponibles : `microtasks_services`, `sports_horse_betting`, `trading`, `ecommerce`, `communities`.

## Statuts et vérité opérationnelle
Toutes les sources commencent en `NON_CONFIGURÉE`, avec `integration_mode=catalog_only` et `verified_live=false`. Un lien présent ne veut pas dire qu'un compte, une API ou un flux en direct est connecté. Les statuts CONNECTÉE ou TESTÉE ne devront être attribués qu'après un test réel, daté et traçable.

## Politique de sécurité
- Privilégier les pages publiques et API officielles autorisées; respecter les conditions d'utilisation, quotas, droits d'auteur et règles locales.
- Aucun contournement de connexion, scraping agressif, achat, pari, ordre de trading, transfert, publication ou action destructive.
- Les sources de pronostics, publicités et réseaux sociaux sont des pistes à vérifier, jamais des preuves de rendement.
- Ne pas stocker les secrets dans le registre ni exposer les clés API.
- Le registre ne confirme ni revenu ni profit et ne déclenche aucune action financière.
