# Notifications Android et validation humaine Verox

## Fonctionnement
- L'utilisateur ouvre `/approvals.html` sur HTTPS, s'authentifie par TOTP puis active les notifications Web Push.
- L'API enregistre les abonnements Web Push côté serveur.
- Les demandes sont conservées dans Supabase et une notification signale qu'une décision est attendue.
- L'utilisateur examine les détails puis approuve ou refuse en ressaisissant un code TOTP.
- Les décisions sont liées à une empreinte SHA-256 de l'opération et les demandes expirent.

## Variables Vercel (Preview d'abord)
- `VEROX_OWNER_USER_ID`: UUID du seul propriétaire dans Supabase Auth; requis pour lier la session et l'approbation au propriétaire.
- `VEROX_TOTP_SECRET`: secret TOTP Base32 propre à Verox, à enrôler dans Google Authenticator.
- `VEROX_SESSION_SECRET`: secret aléatoire fort, au moins 32 caractères.
- `VEROX_INTERNAL_APPROVAL_TOKEN`: jeton aléatoire distinct, appelé uniquement par les composants serveur qui créent une demande.
- `VAPID_PUBLIC_KEY` et `VAPID_PRIVATE_KEY`: paire Web Push VAPID; la clé privée est sensible.
- `VAPID_SUBJECT`: URI de contact `mailto:` valide.
- `SUPABASE_URL` et `SUPABASE_SECRET_KEY` (ou `SUPABASE_SERVICE_ROLE_KEY`): accès serveur à Supabase. La clé secrète ne doit jamais être envoyée au navigateur.

## Préparation
1. Appliquer après les migrations T6/OTP du PR de base les migrations `20261123121000_verox_android_human_approvals.sql` puis `20261123123000_bind_android_approval_to_t6.sql`, uniquement dans une base de test isolée.
2. Définir les variables ci-dessus dans Preview, puis déployer une preview.
3. Ouvrir `/approvals.html` sur Android, s'authentifier par TOTP puis activer les notifications.
4. Tester avec une demande synthétique sans action financière réelle.
5. Ne pas activer en production avant d'avoir vérifié le parcours complet, l'expiration, la concurrence et l'usage unique.

## Limite de sécurité
Cette API enregistre les décisions humaines mais ne lance pas elle-même une opération financière. Le worker doit consommer l'approbation de manière atomique et vérifier empreinte, type d'action, cible et contexte avant toute exécution. L'endpoint TOTP nécessite une protection anti-brute-force persistante avant une mise en production.

## Liaison avec T6 (contrat strict)
Pour qu'une approbation « approve » produise une autorisation consommable par le garde-fou canonique, le demandeur serveur doit fournir dans `details` :
- `domain`: `trading`, `sports_betting`, `horse_racing` ou `transfer` ;
- `amount` strictement positif et `currency` (2 à 12 caractères alphanumériques majuscules) ;
- `run_id` et `event_id` canoniques ; `evidence_id` est facultatif ;
- `authorization_id` doit être celui de l'intention T6 déjà créée ; il n'est jamais généré par la couche de notification.

Le runtime crée d'abord une autorisation T6 `pending`, puis publie la notification avec le même `authorization_id` et le même fingerprint. L'approbation passe par la RPC transactionnelle `decide_verox_human_approval_t6`, qui délègue à `approve_otp_owner_financial_authorization` pour approuver l'intention T6 existante et met à jour la demande de notification dans la même transaction. Elle ne crée pas une seconde autorisation. L'exécuteur doit consommer cette autorisation avec `consume_human_financial_authorization` en vérifiant **le même** fingerprint, domaine, type d'opération, cible, run et event. Une décision HTTP positive seule ne constitue jamais une preuve d'exécution autorisée. Les demandes sans contexte T6 complet sont refusées en approbation (fail-closed).

## Limites avant activation
- Appliquer les migrations uniquement à une base de test isolée possédant le schéma T6 et `nexus_runtime_events` ; ne pas exécuter sur la base de production pour tester.
- Les secrets `SUPABASE_SERVICE_ROLE_KEY` ou `SUPABASE_SECRET_KEY` doivent être présents côté serveur et ne jamais être exposés au navigateur. La clé publishable/anon ne suffit pas.
- L'émetteur de notification est raccordé à la création d'une intention T6 dans `api/index.py`. Le test Android de bout en bout et la vérification de consommation par l'exécuteur restent à faire avant d'activer la fonctionnalité.
