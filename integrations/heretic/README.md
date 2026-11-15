# Heretic — intégration facultative de modèles locaux

Heretic est un **outil de transformation de modèles à poids ouverts**, pas un modèle d'IA, pas un serveur d'inférence et pas un routeur pour tous les modèles utilisés par CognitiveBrain.

Projet officiel : https://github.com/p-e-w/heretic  
Documentation : https://heretic-project.org/tutorial

## Rôle envisagé dans Verox

- Le registre de modèles de CognitiveBrain référence séparément le modèle d'origine, le modèle transformé, sa licence, son empreinte et son emplacement.
- Heretic peut être lancé manuellement sur une machine locale compatible pour transformer un modèle sélectionné.
- Le modèle obtenu peut ensuite être évalué et, si un moteur d'inférence compatible est configuré, servi par ce moteur.
- CognitiveBrain doit continuer à fonctionner sans Heretic et doit pouvoir appeler d'autres modèles via leurs connecteurs respectifs.

## Limites de déploiement

Ne pas installer Heretic dans la construction Render principale par défaut. Heretic dépend de PyTorch et bénéficie d'un GPU adapté; le traitement peut nécessiter beaucoup de mémoire vidéo, d'espace disque et de temps. Le service Render gratuit de Verox n'est pas une cible appropriée pour transformer des modèles.

Ne jamais transformer automatiquement tous les modèles découverts. Une transformation est une opération explicite, coûteuse et versionnée. Il faut conserver les licences et métadonnées du modèle de base, tester les résultats, et maintenir les contrôles de sécurité de CognitiveBrain indépendamment du modèle.

## État

Cette documentation décrit une intégration planifiée. Elle ne signifie pas que Heretic ou un modèle transformé est installé, connecté ou opérationnel dans Render.
