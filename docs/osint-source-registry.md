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


## Qui télécharge ?
Le cerveau Verox lance la récupération de pages publiques via son endpoint de lecture, pas l'utilisateur manuellement :
- `GET /api/sources/<source_id>/fetch` récupère une page HTTPS d'une source déjà inscrite au catalogue.
- Seules les sources explicitement publiques sont éligibles; les plateformes marquées compte/abonnement sont refusées par défaut.
- Requête GET uniquement, sans cookie, identifiant, mot de passe, formulaire, redirection suivie ni action d'écriture.
- Limites de temps, de taille et de texte extrait pour éviter les téléchargements illimités.
- Le contenu récupéré est renvoyé pour analyse; il n'est pas encore archivé durablement. L'exécution serverless peut être éphémère.
- Ce mécanisme télécharge/lit des pages et textes publics, pas des applications mobiles ou des APK, et n'installe rien sur un appareil.

## Limite opérationnelle de la lecture seule
Le cerveau peut collecter et analyser des informations, repérer des offres, classer des pistes et préparer des rapports. Il ne peut pas soumettre des missions, cliquer sur des paris, passer des ordres, acheter des produits, modifier des comptes ou réclamer des paiements. Ces actions écrivent sur les plateformes et sortent du mode lecture seule. Une source ne devient `CONNECTÉE` ou `TESTÉE` qu'après preuve technique datée, distincte de sa simple présence au catalogue.
