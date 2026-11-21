# Verox — registre IA-to-IA et intelligence économique

## Objectif
Tracer chaque étape d'une mission professionnelle entre Verox et un autre agent : réception, qualification, acceptation, exécution, livraison, validation, facturation et paiement. Le registre conserve des métadonnées minimales et des références de preuve, pas les prompts complets, les secrets, les identifiants de paiement ni les données personnelles non nécessaires.

## Événements recommandés
- `request_received`, `request_qualified`, `request_declined`
- `mission_accepted`, `tool_discovered`, `tool_validated`
- `work_started`, `test_passed`, `test_failed`
- `deliverable_submitted`, `deliverable_accepted`, `revision_requested`
- `invoice_issued`, `payment_confirmed`, `refund_or_dispute`

Chaque événement doit avoir une clé idempotente, un type d'acteur, un statut, une date serveur, des références de preuve et, lorsque possible, les empreintes SHA-256 de la demande et du résultat. La date fournie par un système externe reste séparée de l'horodatage serveur. Les doublons ne doivent pas créer deux recettes ou deux livraisons.

## Comptabilité de mission
Séparer obligatoirement :
- revenu potentiel / devis ;
- contrat ou mission obtenu ;
- livraison soumise ;
- livraison acceptée ;
- facture émise ;
- paiement effectivement confirmé ;
- remboursement, frais, coûts d'inférence, hébergement et autres coûts ;
- marge nette calculée à partir de montants documentés.

Un statut de paiement déclaré par un agent n'est pas une preuve bancaire. La preuve doit référencer une confirmation fiable, en conservant seulement les données nécessaires. Une estimation n'est jamais comptabilisée comme argent encaissé.

## Recherche de marché et mécanismes observables
### Plateformes IA et services professionnels
Cartographier les besoins récurrents, les critères d'acceptation, les modalités de rémunération, les coûts, les délais et les API officielles. Mesurer le coût par tâche réussie, la qualité et le taux d'acceptation. Ne pas supposer qu'une API ou une intégration implique une rémunération.

### Commerce et boutique miroir sans stock
Étudier le parcours public de recherche, la correspondance produit, les redirections, les variantes, les frais, les disponibilités, les conditions de livraison et les retours. Utiliser des données et API autorisées, documenter la provenance et vérifier le coût total rendu avant de présenter une offre. Ne pas annoncer un stock, un prix ou un délai qui n'est pas confirmé par le fournisseur. Une commande, un achat ou une publication au nom de Verox nécessite une autorisation distincte.

### Sports et courses hippiques
Ne pas réduire l'analyse à la prédiction du vainqueur. Des variables contextuelles peuvent être testées : discipline, conditions, historique des pénalités/cartons, officiels, surface, météo, poids, jockey/driver, entraîneur, forme, marché et mouvements de cotes. Chaque facteur doit être vérifié dans des sources datées et évalué hors échantillon, en contrôlant les biais et la disponibilité des données. Un signal historique n'établit pas la causalité ni un avantage rentable. Mesurer le rendement après marge, commissions et pertes; conserver les résultats négatifs. Aucune mise automatique n'est déclenchée par une analyse.

### Trading et marchés financiers
Étudier la microstructure observable, les coûts, le spread, la liquidité, les événements, le slippage, la latence et le risque de modèle. Tester d'abord en simulation reproductible avec données horodatées et séparation stricte entre entraînement et validation. Ne jamais présenter le trading comme une source de gain garanti ni confondre backtest et résultat réel. Les ordres réels restent un flux séparé nécessitant une autorisation explicite.

## Cycle d'amélioration
1. Observer et horodater une source autorisée.
2. Séparer faits directs, inférences et inconnues.
3. Formuler une hypothèse falsifiable et son mécanisme causal.
4. Choisir le test le moins coûteux et définir à l'avance le critère d'arrêt.
5. Comparer à une référence et valider hors échantillon.
6. Enregistrer les preuves, les coûts, les échecs et la décision.
7. Ne faire évoluer les priorités qu'à partir de résultats mesurés.

## État de cette livraison
Le client serveur `SupabaseDataPlane.record_ia2ia_event` et la migration append-only constituent une première fondation. Ils ne signifient pas encore que tous les parcours de mission émettent automatiquement ces événements. L'intégration de bout en bout, l'application de la migration et la vérification du déploiement restent des étapes distinctes à démontrer.
