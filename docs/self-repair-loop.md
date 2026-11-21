# Boucle d'auto-diagnostic Verox

Le module `verox_brain.self_repair` est exécuté avant chaque cycle du worker Playwright.
Il lance un ensemble fixe de contrôles, impose un délai maximal, retente chaque contrôle
au plus deux fois par défaut, classe les échecs et produit un rapport JSON sans secrets.
Un échec non résolu bloque le cycle de collecte afin de ne pas accumuler de résultats
à partir d'un runtime défaillant.

## Limites de sécurité de cette première version

- Les commandes sont définies dans le code du dépôt; aucune commande générée par un modèle n'est exécutée.
- Le diagnostic ne modifie pas automatiquement les sources, n'installe pas de paquet arbitraire et ne déploie rien.
- Les erreurs persistantes sont classées et accompagnées d'une prochaine action corrective.
- Les observations collectées restent non vérifiées jusqu'à une validation indépendante.
- Le worker continue d'exiger les secrets serveur autorisés et fonctionne en lecture seule sur les sources publiques.

## Exécution

```bash
python -m verox_brain.self_repair --json
python -m unittest discover -s tests -p "test_self_repair.py" -v
```

Un rapport `healthy` signifie uniquement que les contrôles codés dans cette boucle ont réussi.
Il ne certifie ni les secrets de déploiement, ni l'accès réel à Supabase, ni la disponibilité
du modèle, ni le fonctionnement de bout en bout de la collecte.
