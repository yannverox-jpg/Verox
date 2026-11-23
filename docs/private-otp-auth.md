# Verox — authentification privée par Google Authenticator

Verox utilise un code TOTP Google Authenticator comme unique facteur de connexion à l'application. Il n'y a pas de connexion e-mail/mot de passe dans ce mécanisme.

## Variables d'environnement serveur obligatoires

Configurer ces variables dans le projet d'hébergement (environnement Production et Preview uniquement si nécessaire), jamais dans le HTML, GitHub, le navigateur ou un message :

- `VEROX_TOTP_SECRET` : secret TOTP Base32 aléatoire, associé une seule fois à Google Authenticator.
- `VEROX_SESSION_SECRET` : secret aléatoire indépendant d'au moins 32 caractères, utilisé pour signer les sessions.
- `VEROX_OWNER_USER_ID` : UUID du propriétaire Supabase, requis pour lier les intentions financières à l'unique propriétaire.
- Les fonctions de persistance financière nécessitent également `SUPABASE_URL`, `SUPABASE_ANON_KEY` et `SUPABASE_SERVICE_ROLE_KEY`. La clé service-role doit rester exclusivement côté serveur.

## Générer les secrets localement sur une machine de confiance

Générer le secret TOTP :
```bash
python -c "import secrets,base64; print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='))"
```

Générer le secret de session, séparément :
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Ne pas réutiliser le même secret pour les deux variables. Ajouter la valeur TOTP dans Google Authenticator via « Ajouter un code » puis « Saisir une clé de configuration ». Choisir un compte nommé « Verox », type « Basé sur le temps », avec 6 chiffres et un intervalle de 30 secondes. Enregistrer la même clé Base32 dans `VEROX_TOTP_SECRET` côté serveur. Ne jamais committer ces valeurs.

## Comportement de sécurité

- Le serveur refuse les API privées sans session TOTP valide.
- Le code TOTP est vérifié côté serveur avec une fenêtre temporelle courte.
- Les sessions expirent après 15 minutes et sont stockées dans un cookie `HttpOnly; Secure; SameSite=Strict`.
- Les tentatives OTP sont limitées en mémoire par instance. Cette limite n'est pas un verrouillage distribué : ajouter un rate limit au niveau du fournisseur/hébergement avant de considérer la défense anti-bruteforce comme complète.
- Si les secrets ne sont pas configurés, la connexion doit échouer fermée.
- Le HTML statique peut toujours être servi par l'hébergeur; c'est l'API qui applique le contrôle de sécurité. Pour cacher aussi le document HTML, activer une protection d'accès au niveau de l'hébergement.
- L'approbation financière est volontairement désactivée pour l'instant : la RPC Supabase actuelle attend une identité JWT Supabase. Elle doit être adaptée et testée pour accepter le modèle propriétaire TOTP sans affaiblir la provenance d'approbation. Aucun ordre, pari ou transfert ne doit être exécuté dans l'intervalle.

## Avant mise en production

1. Configurer les variables côté hébergeur sans les enregistrer dans le dépôt.
2. Ajouter la clé dans Google Authenticator et vérifier un code de test.
3. Exécuter les tests unitaires et l'intégration isolée.
4. Vérifier que chaque route API privée renvoie 401 sans session et accepte uniquement une session signée valide.
5. Ajouter un rate limit partagé, puis revoir la compatibilité de la RPC financière.
6. Ne pas fusionner ni déployer avant vérification des workflows et revue de sécurité.
