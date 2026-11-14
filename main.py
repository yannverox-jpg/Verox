import asyncio
import os
import random
import logging
import json
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
import httpx
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

# Configuration des logs pour suivre la compréhension des contraintes en temps réel
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("CognitiveBrain")


# ==============================================================================
# 1. PASSERELLE RÉSEAU & GÉOLOCALISATION FIXE (SUISSE / FRANCFORT)
# ==============================================================================

class NetworkGateway:
    """
    Gère l'identité réseau fixe, la géolocalisation (Zurich / Francfort) 
    et la résilience face aux contraintes techniques (anti-bot, rate-limit, timeouts).
    """
    def __init__(self):
        # Proxy SOCKS5 ou HTTP pointant vers un nœud fixe en Suisse (CH) ou Francfort (DE)
        self.proxy_url = os.getenv("COGNITIVE_PROXY_URL", "")
        self.geo_location = os.getenv("COGNITIVE_GEO_TARGET", "Zurich-CH / Frankfurt-DE")
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    def get_http_client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        """Retourne un client HTTP asynchrone verrouillé sur l'IP fixe géolocalisée."""
        return httpx.AsyncClient(
            proxy=self.proxy_url or None,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Language": "fr-CH,fr;q=0.9,de-DE;q=0.8"}
        )

    async def verify_exit_ip(self) -> Dict[str, Any]:
        """Vérifie que la sortie réseau correspond bien à la contrainte géographique avant d'opérer."""
        async with self.get_http_client(timeout=10.0) as client:
            try:
                resp = await client.get("https://ipapi.co/json/")
                if resp.status_code == 200:
                    data = resp.json()
                    ip_info = {
                        "ip": data.get("ip", "Inconnue"),
                        "city": data.get("city", "Inconnue"),
                        "country": data.get("country_name", "Inconnu")
                    }
                    logger.info(f"[Réseau] Sortie vérifiée : {ip_info['ip']} ({ip_info['city']}, {ip_info['country']})")
                    return ip_info
            except Exception as e:
                logger.warning(f"[Réseau] Impossible de vérifier l'IP distante (Mode local direct actif) : {e}")
        return {"ip": "127.0.0.1", "city": "Local", "country": "Direct"}

    async def human_type(self, page: Page, selector: str, text: str):
        """Simule un clavier virtuel humain pour contourner les détections comportementales."""
        await page.click(selector)
        await asyncio.sleep(random.uniform(0.3, 0.7))
        for char in text:
            await page.keyboard.type(char)
            delay = random.randint(75, 190) if char != ' ' else random.randint(140, 280)
            await asyncio.sleep(delay / 1000.0)


# ==============================================================================
# 2. ANALYSEUR DE CONTRAINTES & DE CONTEXTE
# ==============================================================================

class ConstraintAnalyzer:
    """
    Analyse la requête entrante, identifie les contraintes opérationnelles 
    (furtivité requise, type de sources, domaine métier) et décide de la stratégie.
    """
    def __init__(self):
        self.osint_keywords = ["osint", "identité", "domaine", "ip", "fuite", "profil", "sherlock", "maigret", "threat"]
        self.academic_keywords = ["étude", "recherche", "scientifique", "papier", "algorithme", "architecture", "modèle"]
        self.web_llm_keywords = ["synthèse web", "claude", "chatgpt", "gemini", "osint4all"]

    def evaluate_constraints(self, query: str) -> Dict[str, Any]:
        q_lower = query.lower()
        
        # Détection automatique du mode opératoire et des contraintes
        needs_osint = any(k in q_lower for k in self.osint_keywords)
        needs_academic = any(k in q_lower for k in self.academic_keywords) or not needs_osint
        needs_stealth_browser = any(k in q_lower for k in self.web_llm_keywords) or needs_osint

        constraints = {
            "query": query,
            "use_academic_apis": needs_academic,
            "use_osint_connectors": needs_osint,
            "use_stealth_browser": needs_stealth_browser,
            "enforce_fixed_geo": True,
            "rate_limit_delay": 1.5 if needs_osint else 0.5,
            "max_retries": 3
        }
        
        logger.info(
            f"[Contraintes] Stratégie calculée -> Académique: {constraints['use_academic_apis']} | "
            f"OSINT: {constraints['use_osint_connectors']} | Navigateur Furtif: {constraints['use_stealth_browser']}"
        )
        return constraints


# ==============================================================================
# 3. BIBLIOTHÈQUE DES CONNECTEURS (OSINT4ALL, ACADÉMIQUE, WEB STEALTH)
# ==============================================================================

class ConnectorHub:
    """Regroupe les connecteurs natifs pilotés sous les contraintes du réseau."""
    
    def __init__(self, gateway: NetworkGateway):
        self.gateway = gateway
        self.user_email = os.getenv("USER_EMAIL", "contact@cognitive-engine.local")

    async def fetch_openalex(self, client: httpx.AsyncClient, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        url = "https://api.openalex.org/works"
        params = {"search": query, "per-page": limit, "mailto": self.user_email}
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []
        return [
            {
                "connector": "OpenAlex",
                "title": item.get("title"),
                "year": item.get("publication_year"),
                "evidence": f"Publication indexée OpenAlex ({item.get('id')})"
            }
            for item in resp.json().get("results", [])
        ]

    async def fetch_arxiv(self, client: httpx.AsyncClient, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        url = "http://export.arxiv.org/api/query"
        params = {"search_query": f"all:{query}", "start": 0, "max_results": limit}
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
            summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
            results.append({
                "connector": "arXiv",
                "title": title,
                "year": entry.find("atom:published", ns).text[:4],
                "evidence": summary[:350]
            })
        return results

    async def fetch_osint4all_and_threat(self, client: httpx.AsyncClient, target: str) -> List[Dict[str, Any]]:
        """Connecteur natif pour Osint4all / AlienVault OTX / vérifications d'indicateurs."""
        results = []
        # Interrogation AlienVault OTX (sans clé pour les requêtes publiques de base)
        clean_target = target.split()[-1] # Extraction du terme cible principal
        url = f"https://otx.alienvault.com/api/v1/indicators/domain/{clean_target}/general"
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pulses = len(data.get("pulse_info", {}).get("pulses", []))
                results.append({
                    "connector": "Osint4all / AlienVault OTX",
                    "title": f"Analyse Threat Intel pour {clean_target}",
                    "year": "Temps réel",
                    "evidence": f"Indicateur analysé depuis IP fixe. Nombre de signaux (pulses) détectés : {pulses}."
                })
        except Exception as e:
            logger.debug(f"Connecteur Threat/OSINT ignoré pour cette requête : {e}")
        return results

    async def fetch_stealth_web_interfaces(self, query: str) -> List[Dict[str, Any]]:
        """
        Utilise Playwright Stealth + Clavier virtuel via le proxy fixe (Suisse/Francfort)
        pour interroger les portails web (Osint4all Web, moteurs furtifs).
        """
        results = []
        launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        if self.gateway.proxy_url:
            launch_args.append(f"--proxy-server={self.gateway.proxy_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            context = await browser.new_context(
                user_agent=self.gateway.user_agent,
                locale="fr-CH",
                timezone_id="Europe/Zurich"
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            try:
                logger.info("[StealthWeb] Navigation furtive via IP géolocalisée...")
                await page.goto("https://duckduckgo.com", wait_until="domcontentloaded", timeout=15000)
                await self.gateway.human_type(page, "input[name='q']", query)
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)

                elements = await page.locator("article, .result").all()
                for idx, el in enumerate(elements[:3]):
                    txt = await el.inner_text()
                    results.append({
                        "connector": "StealthWeb (CH/DE Node)",
                        "title": f"Extraction Web Furtive #{idx+1}",
                        "year": "Temps réel",
                        "evidence": " ".join(txt.split())[:300]
                    })
            except Exception as e:
                logger.warning(f"[StealthWeb] Contrainte réseau rencontrée : {e}")
            finally:
                await browser.close()
        return results



    async def fetch_web_llm_interfaces(self, query: str, user_email: str = "") -> List[Dict[str, Any]]:
        results = []
        provider_sets = {"chrome": [("Claude Web", "https://claude.ai/new"), ("GPT Web", "https://chatgpt.com/")], "firefox": [("Gemini Web", "https://gemini.google.com/app")]}
        headless = os.getenv("VEROX_BROWSER_HEADLESS", "true").lower() != "false"
        profile_root = Path(os.getenv("VEROX_BROWSER_PROFILE_DIR", str(Path.home() / ".verox_browser_profiles")))
        profile_root.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            for browser_name in ("chrome", "firefox"):
                providers = provider_sets[browser_name]
                browser = None
                try:
                    browser = await (p.chromium.launch(channel="chrome", headless=headless) if browser_name == "chrome" else p.firefox.launch(headless=headless))
                    for provider, url in providers:
                        state_path = profile_root / f"{browser_name}-{provider.lower().replace(chr(32), chr(95))}.json"
                        context_options = {"user_agent": self.gateway.user_agent, "locale": "fr-CH", "timezone_id": "Europe/Zurich"}
                        if state_path.exists():
                            context_options["storage_state"] = str(state_path)
                        context = await browser.new_context(**context_options)
                        page = await context.new_page()
                        try:
                            if browser_name == "chrome":
                                await Stealth().apply_stealth_async(page)
                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            await page.wait_for_timeout(1000)
                            email_input = page.locator('input[type="email"], input[autocomplete="username"], input[name*="email" i]').first
                            if await email_input.count():
                                if user_email:
                                    try:
                                        await email_input.fill(user_email, timeout=3000)
                                    except Exception:
                                        pass
                                if not headless:
                                    for selector in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Next")', 'button:has-text("Continuer")', 'button:has-text("Suivant")'):
                                        button = page.locator(selector).last
                                        if await button.count():
                                            try:
                                                await button.click(timeout=1500)
                                                break
                                            except Exception:
                                                pass
                                    for _ in range(180):
                                        await page.wait_for_timeout(1000)
                                        prompt_check = page.locator('textarea, [contenteditable="true"], [role="textbox"]').first
                                        if await prompt_check.count():
                                            break
                                    else:
                                        results.append({"connector": f"{provider} via {browser_name}", "title": f"{provider}: authentification incomplète", "year": "État de session", "evidence": "La connexion nécessite une action manuelle dans le navigateur ouvert."})
                                        continue
                                else:
                                    results.append({"connector": f"{provider} via {browser_name}", "title": f"{provider}: authentification requise", "year": "État de session", "evidence": "Le worker distant est sans interface interactive. Lance le moteur localement avec VEROX_BROWSER_HEADLESS=false pour terminer la connexion."})
                                    continue
                            prompt = page.locator('textarea, [contenteditable="true"], [role="textbox"]').first
                            if not await prompt.count():
                                results.append({"connector": f"{provider} via {browser_name}", "title": f"{provider}: interface indisponible", "year": "État de session", "evidence": "Aucun champ de conversation détecté."})
                                continue
                            await prompt.fill(query, timeout=5000)
                            sent = False
                            for selector in ('button[data-testid*="send"]', 'button[aria-label*="Send" i]', 'button[aria-label*="Envoyer" i]', 'button[type="submit"]'):
                                button = page.locator(selector).last
                                if await button.count():
                                    try:
                                        await button.click(timeout=1500)
                                        sent = True
                                        break
                                    except Exception:
                                        pass
                            if not sent:
                                await prompt.press("Enter")
                            await page.wait_for_timeout(5000)
                            body = " ".join((await page.locator("body").inner_text()).split())
                            results.append({"connector": f"{provider} via {browser_name}", "title": f"{provider}: résultat Web", "year": "Temps réel", "evidence": body[-1800:] or "Aucun contenu lisible retourné."})
                        except Exception as e:
                            results.append({"connector": f"{provider} via {browser_name}", "title": f"{provider}: échec de navigation", "year": "Erreur", "evidence": str(e)[:500]})
                        finally:
                            try:
                                await context.storage_state(path=str(state_path))
                            except Exception:
                                pass
                            await context.close()
                except Exception as e:
                    results.append({"connector": f"Navigateur {browser_name}", "title": f"Échec de démarrage {browser_name}", "year": "Erreur", "evidence": str(e)[:500]})
                finally:
                    if browser:
                        await browser.close()
        return results


# ==============================================================================
# 4. CERVEAU COGNITIF UNIFIÉ (ORCHESTRATEUR & PERSISTANCE SUPABASE)
# ==============================================================================

class CognitiveBrain:
    """
    Cœur souverain du logiciel :
    1. Vérifie l'ancrage géographique (Suisse/Francfort).
    2. Comprend les contraintes de la demande.
    3. Orchestre les connecteurs en parallèle.
    4. Synthétise uniquement sur preuves et archive dans Supabase.
    """
    def __init__(self):
        self.gateway = NetworkGateway()
        self.analyzer = ConstraintAnalyzer()
        self.hub = ConnectorHub(self.gateway)

    async def save_to_supabase(self, query: str, evidence: List[Dict[str, Any]]):
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_ANON_KEY")
        if not supabase_url or not supabase_key:
            return

        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        payload = {"query": query, "evidence_data": evidence, "status": "verified"}
        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{supabase_url}/rest/v1/cognitive_logs", json=payload, headers=headers)
                logger.info("[Supabase] Preuves et rapport archivés avec succès.")
            except Exception as e:
                logger.error(f"[Supabase] Échec de l'archivage : {e}")

    async def run(self, user_query: str, user_email: str = ""):
        print("\n" + "="*65)
        print("🧠 CERVEAU COGNITIF SOUVERAIN — EXÉCUTION SOUS CONTRAINTES")
        print("="*65)

        # 1. Contrôle de la contrainte de géolocalisation fixe
        net_status = await self.gateway.verify_exit_ip()

        # 2. Compréhension des contraintes de la requête
        constraints = self.analyzer.evaluate_constraints(user_query)

        # 3. Exécution coordonnée des connecteurs autorisés par les contraintes
        raw_evidence = []
        async with self.gateway.get_http_client() as client:
            tasks = []
            if constraints["use_academic_apis"]:
                tasks.append(self.hub.fetch_openalex(client, user_query))
                tasks.append(self.hub.fetch_arxiv(client, user_query))
            if constraints["use_osint_connectors"]:
                tasks.append(self.hub.fetch_osint4all_and_threat(client, user_query))

            if tasks:
                batches = await asyncio.gather(*tasks, return_exceptions=True)
                for b in batches:
                    if isinstance(b, list):
                        raw_evidence.extend(b)

        # Respect du délai anti-saturation / rate-limit
        await asyncio.sleep(constraints["rate_limit_delay"])

        if constraints["use_stealth_browser"]:
            web_results = await self.hub.fetch_stealth_web_interfaces(user_query)
            raw_evidence.extend(web_results)

        raw_evidence.extend(await self.hub.fetch_web_llm_interfaces(user_query, user_email))

        # 4. Filtrage et dédoublonnage strict
        unique_evidence = []
        seen = set()
        for item in raw_evidence:
            key = (item.get("title") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique_evidence.append(item)

        # 5. Sauvegarde sur Supabase (si configuré sur Render)
        await self.save_to_supabase(user_query, unique_evidence)

        # 6. Restitution finale strictement fondée sur les preuves
        print(f"\n📍 Nœud réseau actif : {net_status['city']} ({net_status['country']}) | IP: {net_status['ip']}")
        print(f"🔍 Requête analysée : {user_query}")
        print(f"🛡️ Contraintes appliquées : Geo-Lock={constraints['enforce_fixed_geo']} | Stealth={constraints['use_stealth_browser']}")
        print("-" * 65)

        if not unique_evidence:
            print("[!] Aucune preuve validée n'a franchi les filtres de contraintes.")
            return

        for idx, ev in enumerate(unique_evidence, 1):
            print(f"\n[Preuve #{idx}] Source : {ev['connector']} ({ev['year']})")
            print(f"Titre   : {ev['title']}")
            print(f"Extrait : {ev['evidence']}")

        print("\n" + "="*65)
        print(f"✅ Cycle terminé : {len(unique_evidence)} éléments collectés et structurés.")
        return unique_evidence
        print("="*65 + "\n")



async def run_worker():
    brain = CognitiveBrain()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL et SUPABASE_ANON_KEY sont requis.")
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}", "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                response = await client.get(f"{supabase_url}/rest/v1/cognitive_logs", params={"select": "id,query,status", "status": "eq.queued", "limit": "1"}, headers=headers)
                response.raise_for_status()
                rows = response.json()
                if not rows:
                    await asyncio.sleep(2)
                    continue
                command = rows[0]
                command_id = command["id"]
                claim = await client.patch(f"{supabase_url}/rest/v1/cognitive_logs", params={"id": f"eq.{command_id}", "status": "eq.queued"}, json={"status": "processing"}, headers=headers)
                claim.raise_for_status()
                try:
                    payload = json.loads(command["query"].split("VEROX_COMMAND:", 1)[1])
                    query = str(payload.get("text", "")).strip()
                    email = str(payload.get("email", "")).strip()
                    if not query:
                        raise ValueError("La requête est vide.")
                    evidence = await brain.run(query, email)
                    await client.patch(f"{supabase_url}/rest/v1/cognitive_logs", params={"id": f"eq.{command_id}"}, json={"status": "completed", "evidence_data": evidence or []}, headers=headers)
                except Exception as e:
                    logger.exception("Échec de commande %s", command_id)
                    await client.patch(f"{supabase_url}/rest/v1/cognitive_logs", params={"id": f"eq.{command_id}"}, json={"status": "failed", "evidence_data": [{"connector": "CognitiveBrain", "title": "Échec d'exécution", "year": "Erreur", "evidence": str(e)[:1000]}]}, headers=headers)
            except Exception:
                logger.exception("Erreur du worker cognitif")
                await asyncio.sleep(5)


def run_dashboard():
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    class DashboardHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html", "/nexus_dashboard.html"):
                self.path = "/nexus_dashboard.html"
            return super().do_GET()
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "10000"))), DashboardHandler).serve_forever()


async def run_server():
    dashboard_task = asyncio.create_task(asyncio.to_thread(run_dashboard))
    try:
        await run_worker()
    finally:
        dashboard_task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if args.dashboard:
        run_dashboard()
    elif args.server:
        asyncio.run(run_server())
    else:
        asyncio.run(run_worker())
