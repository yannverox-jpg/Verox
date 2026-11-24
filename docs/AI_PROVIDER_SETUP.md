# Configuration du fournisseur Gemini de Verox

Verox utilise uniquement l’API Google Gemini pour l’inférence. La clé n’est pas nécessaire dans GitHub Actions.

## Configurer Gemini côté Vercel

1. Crée une clé pour la Gemini Developer API dans Google AI Studio.
2. Ajoute `GEMINI_API_KEY` comme variable secrète côté serveur du projet Vercel.
3. Laisse `GEMINI_MODEL` absent pour utiliser le modèle par défaut, ou configure un modèle Gemini disponible pour ton compte.
4. Redéploie puis vérifie que `/api/health` indique `gemini_configured: true`.

Les quotas et conditions Gemini sont gérés par Google et peuvent évoluer. Ne place pas de secrets, mots de passe ou données privées dans les prompts.
