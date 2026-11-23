# Notifications Android et validation humaine Verox

## Fonctionnement
- L'utilisateur ouvre `/approvals.html) sur HTTPS, s'authentifie par TOTP puis active les notifications Web Push.
- L'API enregistre les abonnements Web Push côté serveur.
- Les demandes sont conservées dans Supabase et une notification signale qu'une décision est attendue.
- L'utilisateur examine les détails puis approuve ou refuse en ressaisissant un code TOTP.
- Les décisions sont liées à une empreinte SHA-256 de l'opération et les demandes expirent.

## Variables Vercel (Preview d'abord)
- `VEROX_TOTP_SECRET`: secret TOTP Base32 propre à Verox.
- `VEROX_SESSION_SECRET`: secret aléatoire fort, au moins 32 caractères.
- `VEROX_INTERNAL_APPROVAL_TOKEN`: jeton aléatoire distinct, appelé uniquement par les composants serveur qui créent une demande.
- `VAPID_PUBLIC_KEY` et `VAPID_PRIVATE_KEY`: paire Web Push VAPID; la clé privée est sensible.
- `VAPID_SUBJECT`: URI de contact `mailto:` valide.
- `SUPABASE_URL` et `SUPABASE_SERVICE_ROLE_KEY`: accès serveur à Supabase. La clé service-role ne doit jamais être envoyée au navigateur.

## Préparation
1. Appliquer la migration `supabase/migrations/20261123100000_verox_android_human_approvals.sql`.
2. Définir les variables ci-dessus dans Preview, puis déployer une preview.
3. Ouvrir `/approvals.html` sur Android, s'authentifier par TOTP puis activer les notifications.
4. Tester avec une demande synthétique sans action financière réelle.
5. Ne pas activer en production avant d'avoir relié la consommation de l'approbation à l'exécuteur canonique et testé expiration, concurrence et usage unique.

## Limite de sécurité
Cette API enregistre les décisions humaines mais ne lance pas elle-même une opération financière. Le worker doit consommer l'approbation de manière atomique et vérifier empreinte, type d'action, cible et contexte avant toute exécution. L'endpoint TOTP nécessite une protection anti-brute-force persistante avant une mise en production.
