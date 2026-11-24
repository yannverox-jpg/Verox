# Configuration du fournisseur Gemini de Verox

Verox utilise uniquement l'API Google Gemini pour l'inférence. Aucune clé de fournisseur IA n'est requise dans GitHub Actions.

## Configurer Gemini côté Render

1. Crée une clé pour la Gemini Developer API dans Google AI Studio.
2. Dans les variables d'environnement du service Render **verox**, ajoute `GEMINI_API_KEY` comme secret.
3. Laisse `GEMINI_MODEL` absent pour utiliser le modèle par défaut, ou configure un modèle Gemini disponible pour ton compte.
4. Redéploie le service et vérifie que `/api/health` indique `gemini_configured: true`.

Les quotas et conditions de l'offre Gemini sont gérés par Google et peuvent évoluer. N'envoie pas de secrets, mots de passe ou données privées dans les prompts.
