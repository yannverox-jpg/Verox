"""Curated, read-only OSINT source registry for Verox.

Catalog entries are leads for research, not evidence of live connectivity.
No credentials, account scraping, betting, trading, or purchases are performed here.
"""
from __future__ import annotations

from typing import Any

DOMAINS = {
    "microtasks_services": "Micro-tâches & revenus de services",
    "sports_horse_betting": "Données sportives & hippiques",
    "trading": "Marchés financiers & crypto",
    "ecommerce": "E-commerce & sourcing",
    "communities": "Canaux publics & communautés OSINT",
}

# id, name, domain, kind, url, access, notes
_ROWS = [
    ("clickworker", "Clickworker", "microtasks_services", "work_platform", "https://www.clickworker.com/", "account", "Missions et rémunération selon disponibilité et pays."),
    ("microworkers", "Microworkers", "microtasks_services", "work_platform", "https://www.microworkers.com/", "account", "Micro-missions; vérifier les conditions de paiement."),
    ("mturk", "Amazon Mechanical Turk", "microtasks_services", "work_platform", "https://www.mturk.com/", "account", "Éligibilité et paiements dépendants du pays et du compte."),
    ("outlier", "Outlier", "microtasks_services", "work_platform", "https://outlier.ai/", "account", "Travail d'entraînement IA; disponibilité variable."),
    ("appen", "Appen", "microtasks_services", "work_platform", "https://appen.com/", "account", "Projets de données et d'évaluation IA."),
    ("jumptask", "JumpTask", "microtasks_services", "work_platform", "https://www.jumptask.io/", "account", "Vérifier les récompenses, frais et conditions."),
    ("roamler", "Roamler", "microtasks_services", "field_tasks", "https://www.roamler.com/", "app_account", "Missions terrain dépendantes de la région."),
    ("bemyeye", "BeMyEye", "microtasks_services", "field_tasks", "https://www.bemyeye.com/", "app_account", "Audits et missions en magasin."),
    ("usertesting", "UserTesting", "microtasks_services", "ux_testing", "https://www.usertesting.com/", "account", "Tests UX soumis à sélection et disponibilité."),
    ("freecash", "Freecash", "microtasks_services", "rewards", "https://freecash.com/", "account", "Offres partenaires; contrôler conditions et données collectées."),
    ("footystats", "FootyStats", "sports_horse_betting", "sports_statistics", "https://footystats.org/", "web_or_api", "Statistiques de football; vérifier limites et licence d'utilisation."),
    ("totalcorner", "TotalCorner", "sports_horse_betting", "corners_statistics", "https://www.totalcorner.com/", "web_or_account", "Données corners et matchs selon accès disponible."),
    ("soccerstats", "SoccerSTATS", "sports_horse_betting", "sports_statistics", "https://www.soccerstats.com/", "web", "Statistiques historiques de football."),
    ("whoscored", "WhoScored", "sports_horse_betting", "sports_statistics", "https://www.whoscored.com/", "web", "Compositions et statistiques selon couverture."),
    ("flashscore", "Flashscore", "sports_horse_betting", "live_scores", "https://www.flashscore.com/", "web_or_app", "Scores en direct; ne pas présumer d'un flux API autorisé."),
    ("sofascore", "Sofascore", "sports_horse_betting", "live_scores", "https://www.sofascore.com/", "web_or_app", "Scores et statistiques; respecter les conditions d'accès."),
    ("oddsportal", "OddsPortal", "sports_horse_betting", "odds_comparison", "https://www.oddsportal.com/", "web", "Historique et comparaison des cotes selon disponibilité."),
    ("betexplorer", "BetExplorer", "sports_horse_betting", "odds_comparison", "https://www.betexplorer.com/", "web", "Résultats et historiques de cotes."),
    ("geny", "Geny", "sports_horse_betting", "horse_racing", "https://www.geny.com/", "web", "Courses hippiques, partants et pronostics."),
    ("equidia", "Equidia", "sports_horse_betting", "horse_racing_stream", "https://www.equidia.fr/", "account_or_subscription", "Informations et diffusion hippiques selon droits."),
    ("letrot", "LeTROT", "sports_horse_betting", "official_horse_racing", "https://www.letrot.com/", "web", "Source officielle des courses au trot."),
    ("francegalop", "France Galop", "sports_horse_betting", "official_horse_racing", "https://www.france-galop.com/", "web", "Source officielle des courses au galop."),
    ("1xbet", "1xBet", "sports_horse_betting", "operator", "https://1xbet.com/", "account", "Opérateur; disponibilité, conformité locale et conditions à vérifier. Aucune mise effectuée par ce registre."),
    ("unibet", "Unibet", "sports_horse_betting", "operator", "https://www.unibet.com/", "account", "Opérateur; URL et disponibilité peuvent varier selon le pays."),
    ("winamax", "Winamax", "sports_horse_betting", "operator", "https://www.winamax.fr/", "account", "Opérateur; accès et conformité locale à vérifier."),
    ("bet365", "Bet365", "sports_horse_betting", "operator", "https://www.bet365.com/", "account", "Opérateur; disponibilité et restrictions territoriales à vérifier."),
    ("pmu", "PMU", "sports_horse_betting", "operator", "https://www.pmu.fr/", "account", "Opérateur de paris; aucune mise n'est effectuée par ce registre."),
    ("betclic", "Betclic", "sports_horse_betting", "operator", "https://www.betclic.fr/", "account", "Opérateur soumis aux règles locales."),
    ("betfair", "Betfair", "sports_horse_betting", "exchange", "https://www.betfair.com/", "account", "Exchange; vérifier disponibilité et restrictions territoriales."),
    ("tradingview", "TradingView", "trading", "charts_alerts", "https://www.tradingview.com/", "web_or_account", "Graphiques et alertes; les alertes ne constituent pas des ordres."),
    ("forexfactory", "Forex Factory", "trading", "economic_calendar", "https://www.forexfactory.com/", "web", "Calendrier économique."),
    ("investing", "Investing.com", "trading", "market_news_calendar", "https://www.investing.com/", "web_or_account", "Actualités, calendriers et données de marché."),
    ("tradingeconomics", "Trading Economics", "trading", "macro_data", "https://tradingeconomics.com/", "web_or_api", "Données macro; API et quotas à vérifier."),
    ("coinglass", "CoinGlass", "trading", "crypto_derivatives", "https://www.coinglass.com/", "web_or_api", "Liquidations, funding et open interest."),
    ("dexscreener", "DEX Screener", "trading", "onchain_market_data", "https://dexscreener.com/", "web_or_api", "Données publiques de paires DEX; vérifier l'API officielle."),
    ("coingecko", "CoinGecko", "trading", "crypto_market_data", "https://www.coingecko.com/", "web_or_api", "Données crypto et API avec limites selon le plan."),
    ("kraken", "Kraken", "trading", "exchange", "https://www.kraken.com/", "account_api", "API de marché; aucune transaction n'est déclenchée par le registre."),
    ("coinbase", "Coinbase", "trading", "exchange", "https://www.coinbase.com/", "account_api", "API de marché; clés de trading désactivées par défaut."),
    ("amazon", "Amazon", "ecommerce", "marketplace", "https://www.amazon.com/", "account_or_api", "Place de marché; accès vendeur/API selon autorisation."),
    ("ebay", "eBay", "ecommerce", "marketplace", "https://www.ebay.com/", "account_or_api", "Place de marché et API selon autorisation."),
    ("aliexpress", "AliExpress", "ecommerce", "sourcing_marketplace", "https://www.aliexpress.com/", "account", "Prix, livraison et règles fournisseurs à vérifier."),
    ("alibaba", "Alibaba.com", "ecommerce", "b2b_sourcing", "https://www.alibaba.com/", "account", "Sourcing B2B; demander échantillons et contrôler vendeurs."),
    ("globalsources", "Global Sources", "ecommerce", "b2b_sourcing", "https://www.globalsources.com/", "web_or_account", "Sourcing fournisseurs."),
    ("cjdropshipping", "CJdropshipping", "ecommerce", "dropshipping", "https://cjdropshipping.com/", "account_or_api", "Intégration et frais à vérifier."),
    ("dsers", "DSers", "ecommerce", "dropshipping", "https://www.dsers.com/", "account", "Outil de gestion des commandes; aucune commande automatique."),
    ("meta_ads_library", "Meta Ad Library", "ecommerce", "ad_research", "https://www.facebook.com/ads/library/", "public_web", "Recherche de publicités publiques."),
    ("tiktok_creative", "TikTok Creative Center", "ecommerce", "ad_research", "https://ads.tiktok.com/business/creativecenter/", "public_web", "Créations et tendances publicitaires selon région."),
    ("google_trends", "Google Trends", "ecommerce", "trend_research", "https://trends.google.com/", "public_web", "Tendances de recherche, indicatives et non ventes confirmées."),
    ("similarweb", "Similarweb", "ecommerce", "traffic_research", "https://www.similarweb.com/", "web_or_account", "Estimations de trafic; distinguer estimations des données réelles."),
    ("tgstat", "TGStat", "communities", "public_channel_index", "https://tgstat.com/", "public_web", "Index de canaux publics; qualité et activité à vérifier."),
    ("telemetr", "Telemetr", "communities", "public_channel_index", "https://telemetr.io/", "public_web", "Index de canaux publics; ne garantit pas la fiabilité des pronostics."),
    ("whalealert", "Whale Alert", "communities", "public_market_alerts", "https://whale-alert.io/", "public_web_or_account", "Alertes on-chain publiques; ne prouvent pas l'intention de marché."),
    ("wublockchain", "Wu Blockchain", "communities", "market_news", "https://wublockchain.com/", "public_web", "Actualités crypto; recouper les informations."),
]

def list_sources(*, domain: str | None = None, query: str | None = None) -> dict[str, Any]:
    """Return a safe catalogue with explicit catalog-only connection status."""
    clean_domain = (domain or "").strip().lower()
    if clean_domain and clean_domain not in DOMAINS:
        raise ValueError("unknown_domain")
    needle = (query or "").strip().casefold()[:100]
    rows = []
    for row in _ROWS:
        source = dict(zip(("id", "name", "domain", "kind", "url", "access", "notes"), row))
        if clean_domain and source["domain"] != clean_domain:
            continue
        if needle and needle not in " ".join(str(source[k]) for k in ("name", "kind", "notes", "domain")).casefold():
            continue
        source["connection_status"] = "NON_CONFIGURÉE"
        source["integration_mode"] = "catalog_only"
        source["verified_live"] = False
        rows.append(source)
    return {
        "count": len(rows),
        "domains": [{"id": key, "label": label} for key, label in DOMAINS.items()],
        "sources": rows,
        "disclaimer": "Catalogue de sources candidates. Aucun connecteur, compte, flux temps réel ou paiement n'est réputé actif sans test distinct.",
    }


def get_source(source_id: str) -> dict[str, Any] | None:
    """Return one registered source; never accept caller-supplied URLs."""
    needle = (source_id or "").strip().casefold()
    for row in _ROWS:
        if row[0] == needle:
            source = dict(zip(("id", "name", "domain", "kind", "url", "access", "notes"), row))
            source["connection_status"] = "NON_CONFIGURÉE"
            source["integration_mode"] = "catalog_only"
            source["verified_live"] = False
            return source
    return None
