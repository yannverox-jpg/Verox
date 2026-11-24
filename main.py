import asyncio
import os
import random
import logging
import json
import argparse
import sqlite3
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
import httpx
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth
from verox_brain.dispatch import resolve_route, UnknownTaskType, ROUTES, route_status
from verox_brain.market import market_catalog, resolve_market
from verox_brain.ephemeral_agents import active_mission_agents, retire_mission_agents, spawn_mission_agents
from verox_brain.runtime_guardrails import classify_task_envelope, execute_a2a_pipeline_step, guard_declared_financial_action, RuntimeGuardError

MARKET_PRODUCT_CATALOG = {
    "name": "Verox Agent Market",
    "version": "1.1.0",
    "products": [
        {"id": "capability-discovery", "name": "Capability Discovery", "delivery": "synchronous", "endpoint": "/api/market/catalog", "purpose": "Discover machine-readable specialist capabilities."},
        {"id": "low-latency-routing", "name": "Low-Latency Routing", "delivery": "synchronous", "endpoint": "/api/market/resolve", "purpose": "Rank local specialist candidates without external model calls."},
        {"id": "async-task-execution", "name": "Asynchronous Task Execution", "delivery": "asynchronous", "endpoint": "/api/market/submit", "result_endpoint": "/api/tasks/{task_id}", "purpose": "Submit an authenticated task to the persistent Supabase queue and poll its status."},
        {"id": "auditable-delivery", "name": "Auditable Delivery", "delivery": "asynchronous", "endpoint": "/api/tasks/{task_id}", "purpose": "Retrieve task status, result and error without holding the initial request open."}
    ],
    "protocols": ["HTTP+JSON", "agent-card"],
    "auth": "Bearer VEROX_MARKET_API_TOKEN required for task submission; never send provider secrets in task payloads."
}

# Cibles autorisées du pont navigateur : navigation publique en lecture seule.
BROWSER_TARGETS = {
    "monetly": "https://www.monetly.in/login",
    "mt5": "https://www.metatrader5.com/en/trading-platform/web-trading",
    "1xbet": "https://1xbet.com/",
    "tradingview": "https://www.tradingview.com/",
    "oanda": "https://www.oanda.com/",
    "fxpro": "https://www.fxpro.com/",
    "chrome": "https://www.google.com/chrome/",
    "aws": "https://console.aws.amazon.com/",
    "google": "https://myaccount.google.com/",
    "airtel_money": "https://www.airtel.africa/airtel-money",
    "youtube": "https://www.youtube.com/",
    "vscode": "https://code.visualstudio.com/",
    "github": "https://github.com/",
    "supabase": "https://supabase.com/dashboard",
    "vercel": "https://vercel.com/dashboard",
    "render": "https://dashboard.render.com/",
    "files_by_google": "https://play.google.com/store/apps/details?id=com.google.android.apps.nbu.files",
    "besoccer": "https://www.besoccer.com/",
}


# Configuration des logs pour suivre la compréhension des contraintes en temps réel
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("CognitiveBrain")
_research_cycle_lock = __import__("threading").Lock()


def _supabase_base_url(value: str) -> str:
    """Normalise SUPABASE_URL whether configured as project root or with /rest/v1."""
    url = (value or "").strip().rstrip("/")
    while url.lower().endswith("/rest/v1"):
        url = url[:-8].rstrip("/")
    return url



def _gemini_api_key() -> tuple[str, str | None]:
    """Read supported server-side Gemini key aliases without exposing their values."""
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "Gemini"):
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", None


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

        model_keywords = ["hugging face", "huggingface", "modèle local", "modèles locaux", "modèle ia", "modèle d'ia", "llm", "modèle gratuit", "modèles gratuits"]
        needs_model_catalog = any(k in q_lower for k in model_keywords)

        constraints = {
            "query": query,
            "use_model_catalog": needs_model_catalog,
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
    
    def __init__(self, gateway: NetworkGateway, session_manager=None):
        self.gateway = gateway
        self.session_manager = session_manager
        self.user_email = os.getenv("USER_EMAIL", "contact@cognitive-engine.local")

    async def fetch_huggingface_models(self, client: httpx.AsyncClient, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Recherche des modèles publics dans le Hub Hugging Face, sans clé pour les métadonnées publiques."""
        terms = query.strip()[:180] or "text-generation"
        try:
            response = await client.get(
                "https://huggingface.co/api/models",
                params={"search": terms, "sort": "downloads", "direction": "-1", "limit": limit},
                timeout=8.0
            )
            response.raise_for_status()
            models = response.json()
            results = []
            for model in models[:limit]:
                model_id = model.get("id") or model.get("modelId")
                if not model_id:
                    continue
                results.append({
                    "connector": "Hugging Face Hub",
                    "year": datetime.now(timezone.utc).year,
                    "title": model_id,
                    "evidence": (
                        f"Modèle public répertorié. Téléchargements : {model.get('downloads', 0)}; "
                        f"likes : {model.get('likes', 0)}; tâche : {model.get('pipeline_tag') or 'non précisée'}. "
                        "La compatibilité, la licence et les ressources matérielles doivent être vérifiées avant installation."
                    )
                })
            return results
        except Exception as exc:
            logger.warning("Hugging Face Hub indisponible (%s).", type(exc).__name__)
            return []

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



    async def fetch_web_llm_interfaces(self, query: str, user_email: str = "", provider_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        results = []
        provider_sets = {"chrome": [("Claude Web", "https://claude.ai/new"), ("GPT Web", "https://chatgpt.com/"), ("Grok Web", "https://grok.com/")], "firefox": [("Gemini Web", "https://gemini.google.com/app")]}
        provider_names = {"claude": "Claude Web", "gpt": "GPT Web", "chatgpt": "GPT Web", "gemini": "Gemini Web", "grok": "Grok Web"}
        if provider_filter:
            target = provider_names.get(provider_filter.strip().lower())
            if not target:
                raise ValueError(f"Fournisseur web non pris en charge: {provider_filter}")
            provider_sets = {name: [(label, url) for label, url in providers if label == target]
                             for name, providers in provider_sets.items()}
            provider_sets = {name: providers for name, providers in provider_sets.items() if providers}
        headless = os.getenv("VEROX_BROWSER_HEADLESS", "true").lower() != "false"
        profile_root = Path(os.getenv("VEROX_BROWSER_PROFILE_DIR", str(Path.home() / ".verox_browser_profiles")))
        profile_root.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            for browser_name in ("chrome", "firefox"):
                providers = provider_sets.get(browser_name, [])
                if not providers:
                    continue
                browser = None
                try:
                    browser = await (p.chromium.launch(headless=headless) if browser_name == "chrome" else p.firefox.launch(headless=headless))
                    for provider, url in providers:
                        if self.session_manager:
                            profile = self.session_manager.profile_for(provider)
                            state_path = Path(profile["storage_state_path"])
                        else:
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
                                if self.session_manager:
                                    await self.session_manager.save_state(context, provider)
                                else:
                                    await context.storage_state(path=str(state_path))
                                    try:
                                        state_path.chmod(0o600)
                                    except OSError:
                                        pass
                            except Exception as state_error:
                                logger.debug("État de session non sauvegardé pour %s: %s", provider, state_error)
                            await context.close()
                except Exception as e:
                    results.append({"connector": f"Navigateur {browser_name}", "title": f"Échec de démarrage {browser_name}", "year": "Erreur", "evidence": str(e)[:500]})
                finally:
                    if browser:
                        await browser.close()
        return results



# ÉTAT PERSISTANT, INVENTAIRE, SESSIONS ET EXTENSION DES OUTILS
class CognitiveStore:
    """Source persistante unique pour mémoire, inventaire et résultats mesurés."""
    def __init__(self, path=None):
        self.path=str(Path(path or os.getenv("VEROX_STATE_DB","verox_state.sqlite3")).expanduser())
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS app_inventory(app_id TEXT PRIMARY KEY,name TEXT NOT NULL,app_type TEXT NOT NULL,metadata TEXT NOT NULL,install_status TEXT NOT NULL DEFAULT 'unknown',connection_status TEXT NOT NULL DEFAULT 'unknown',updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cognitive_tools(tool_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,specification TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'proposed',updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cognitive_memory(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,content TEXT NOT NULL,metadata TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cognitive_outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT,strategy TEXT NOT NULL,tool_id TEXT NOT NULL,success INTEGER NOT NULL,measured_value REAL,result TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cognitive_sessions(app_id TEXT PRIMARY KEY,profile_path TEXT NOT NULL,state_path TEXT,updated_at TEXT NOT NULL);
            """)
    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat()
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15); db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000"); db.execute("PRAGMA journal_mode=WAL"); return db
    def _supabase(self, method, table, params=None, payload=None):
        url=os.getenv("SUPABASE_URL","").rstrip("/")
        key=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
        if not url or not key:return None
        headers={"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Prefer":"return=representation"}
        try:
            with httpx.Client(timeout=8.0) as client:
                response=client.request(method,url+"/rest/v1/"+table,params=params,json=payload,headers=headers)
                response.raise_for_status()
                return response.json() if response.content else []
        except Exception:
            logger.exception("[Supabase] Opération CognitiveStore impossible sur %s",table)
            return None

    def remember(self,kind,content,metadata=None):
        clean_content=str(content)[:20000]
        clean_metadata=metadata or {}
        with self.connect() as db:
            db.execute("INSERT INTO cognitive_memory(kind,content,metadata,created_at) VALUES(?,?,?,?)",(kind,clean_content,json.dumps(clean_metadata,ensure_ascii=False,default=str),self.now()))
        self._supabase("POST","nexus_memory",payload={
            "namespace":"verox.cognitive_brain",
            "memory_key":f"{kind}:{uuid.uuid4()}",
            "content":{"kind":kind,"content":clean_content,"metadata":clean_metadata},
            "source":"cognitive_brain",
            "importance":3
        })

    def recent_memory(self,limit=20):
        limit=max(1,min(200,limit))
        remote=self._supabase("GET","nexus_memory",params={
            "select":"id,memory_key,content,source,importance,created_at",
            "namespace":"eq.verox.cognitive_brain","order":"created_at.desc","limit":str(limit)
        })
        if remote is not None:
            return [{"id":r.get("id"),"kind":(r.get("content") or {}).get("kind","memory"),
                     "content":(r.get("content") or {}).get("content",r.get("content")),
                     "metadata":json.dumps((r.get("content") or {}).get("metadata",{}),ensure_ascii=False),
                     "created_at":r.get("created_at"),"source":r.get("source")} for r in remote]
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM cognitive_memory ORDER BY id DESC LIMIT ?",(limit,))]

    def record_outcome(self,strategy,tool_id,success,value,result):
        result_json=json.loads(json.dumps(result,ensure_ascii=False,default=str))
        with self.connect() as db:
            db.execute("INSERT INTO cognitive_outcomes(strategy,tool_id,success,measured_value,result,created_at) VALUES(?,?,?,?,?,?)",(strategy,tool_id,int(bool(success)),value,json.dumps(result_json,ensure_ascii=False),self.now()))
        self._supabase("POST","nexus_execution_events",payload={
            "task_id":None,"event_type":"outcome_recorded","severity":"info" if success else "warning",
            "message":f"Résultat mesuré pour {tool_id}",
            "details":{"strategy":strategy,"tool_id":tool_id,"success":bool(success),"measured_value":value,"result":result_json}
        })

    def outcome_stats(self):
        remote=self._supabase("GET","nexus_execution_events",params={
            "select":"details","event_type":"eq.outcome_recorded","order":"created_at.desc","limit":"1000"
        })
        if remote is not None:
            grouped={}
            for row in remote:
                d=row.get("details") or {}; tool=d.get("tool_id")
                if not tool:continue
                x=grouped.setdefault(tool,{"attempts":0,"successes":0,"_values":[]})
                x["attempts"]+=1
                x["successes"]+=int(bool(d.get("success")))
                if isinstance(d.get("measured_value"),(int,float)):x["_values"].append(d["measured_value"])
            for x in grouped.values():
                values=x.pop("_values");x["average_value"]=(sum(values)/len(values)) if values else None
            return grouped
        with self.connect() as db:
            rows=db.execute("SELECT tool_id,COUNT(*) attempts,SUM(success) successes,AVG(measured_value) average_value FROM cognitive_outcomes GROUP BY tool_id").fetchall()
        return {r["tool_id"]:dict(r) for r in rows}

class VeroxAppManager:
    """Inventaire d'applications en SQLite; un statut inconnu n'est pas présenté comme vérifié."""
    def __init__(self,store):self.store=store
    def register(self,app_id,name,app_type="unknown",**metadata):
        app_id=re.sub(r"[^a-zA-Z0-9_.:-]","-",str(app_id).strip())[:120]
        if not app_id or not str(name).strip():raise ValueError("app_id et name requis")
        rec={"app_id":app_id,"name":str(name).strip(),"type":app_type,"origin":metadata.get("origin","user_or_brain"),"access_point":metadata.get("access_point",""),"execution_env":metadata.get("execution_env","unknown"),"capabilities":metadata.get("capabilities",[]),"authorizations":metadata.get("authorizations",[]),"remember_me":bool(metadata.get("remember_me",False))}
        with self.store.connect() as db:
            old=db.execute("SELECT install_status,connection_status FROM app_inventory WHERE app_id=?",(app_id,)).fetchone()
            db.execute("""INSERT INTO app_inventory VALUES(?,?,?,?,?,?,?) ON CONFLICT(app_id) DO UPDATE SET name=excluded.name,app_type=excluded.app_type,metadata=excluded.metadata,updated_at=excluded.updated_at""",(app_id,rec["name"],str(app_type),json.dumps(rec,ensure_ascii=False),old["install_status"] if old else "unknown",old["connection_status"] if old else "unknown",self.store.now()))
        return self.get(app_id)
    def get(self,app_id):
        with self.store.connect() as db:r=db.execute("SELECT * FROM app_inventory WHERE app_id=?",(app_id,)).fetchone()
        if not r:return None
        x=dict(r);x["metadata"]=json.loads(x.pop("metadata"));return x
    def list(self):
        with self.store.connect() as db:rows=db.execute("SELECT * FROM app_inventory ORDER BY name").fetchall()
        out=[]
        for r in rows:x=dict(r);x["metadata"]=json.loads(x.pop("metadata"));out.append(x)
        return out
    def update_status(self,app_id,install_status=None,connection_status=None,evidence=""):
        if not self.get(app_id):raise KeyError("Application inconnue: "+app_id)
        if install_status is not None and install_status not in {"unknown","not_installed","installed","in_progress","error"}:raise ValueError("Statut d'installation invalide")
        if connection_status is not None and connection_status not in {"unknown","disconnected","connected","authentication_required","error"}:raise ValueError("Statut de connexion invalide")
        with self.store.connect() as db:db.execute("UPDATE app_inventory SET install_status=COALESCE(?,install_status),connection_status=COALESCE(?,connection_status),updated_at=? WHERE app_id=?",(install_status,connection_status,self.store.now(),app_id))
        if evidence:self.store.remember("app_status_evidence",evidence,{"app_id":app_id})
        return self.get(app_id)

class VeroxSessionManager:
    """Profil isolé par application; fichiers de session traités comme données sensibles."""
    def __init__(self,store):
        self.store=store;self.root=Path(os.getenv("VEROX_BROWSER_PROFILE_DIR",str(Path.home()/".verox_browser_profiles"))).expanduser();self.root.mkdir(parents=True,exist_ok=True)
    def profile_for(self,app_id):
        safe=re.sub(r"[^a-zA-Z0-9_.-]","_",app_id)[:100]
        if not safe:raise ValueError("Identifiant invalide")
        path=(self.root/safe).resolve()
        if self.root.resolve() not in path.parents:raise ValueError("Chemin de profil invalide")
        path.mkdir(parents=True,exist_ok=True);state=path/"storage_state.json"
        with self.store.connect() as db:db.execute("""INSERT INTO cognitive_sessions VALUES(?,?,?,?) ON CONFLICT(app_id) DO UPDATE SET profile_path=excluded.profile_path,state_path=excluded.state_path,updated_at=excluded.updated_at""",(app_id,str(path),str(state) if state.exists() else None,self.store.now()))
        return {"profile_path":str(path),"storage_state_path":str(state),"cookies_restored":state.exists()}
    async def save_state(self,context,app_id):
        p=self.profile_for(app_id);state=Path(p["storage_state_path"]);await context.storage_state(path=str(state))
        try:state.chmod(0o600)
        except OSError:pass
        with self.store.connect() as db:db.execute("UPDATE cognitive_sessions SET state_path=?,updated_at=? WHERE app_id=?",(str(state),self.store.now(),app_id))
        return str(state)

class VeroxExecutionManager:
    """Registre d'outils natifs; le code proposé n'est pas exécuté automatiquement."""
    def __init__(self,store):self.store=store;self.executors={}
    def register_executor(self,tool_id,executor):
        self.executors[tool_id]=executor;now=self.store.now()
        with self.store.connect() as db:db.execute("""INSERT INTO cognitive_tools VALUES(?,?,?,?,?,?) ON CONFLICT(tool_id) DO UPDATE SET name=excluded.name,description=excluded.description,status='active',updated_at=excluded.updated_at""",(tool_id,tool_id,getattr(executor,"__doc__","") or "Connecteur natif","{}","active",now))
    def propose_tool(self,tool_id,name,description,specification):
        safe=re.sub(r"[^a-zA-Z0-9_.:-]","-",tool_id)[:120]
        if not safe or not name.strip():raise ValueError("Identifiant et nom requis")
        now=self.store.now()
        with self.store.connect() as db:db.execute("""INSERT INTO cognitive_tools VALUES(?,?,?,?,?,?) ON CONFLICT(tool_id) DO UPDATE SET name=excluded.name,description=excluded.description,specification=excluded.specification,status=CASE WHEN cognitive_tools.status='active' THEN 'active' ELSE 'proposed' END,updated_at=excluded.updated_at""",(safe,name.strip(),description[:2000],json.dumps(specification,ensure_ascii=False)[:12000],"proposed",now))
        return {"tool_id":safe,"status":"active" if safe in self.executors else "proposed"}
    def list_tools(self):
        with self.store.connect() as db:return [dict(r) for r in db.execute("SELECT * FROM cognitive_tools ORDER BY name")]
    async def execute(self,tool_id,params):
        fn=self.executors.get(tool_id)
        if fn is None:raise RuntimeError("Outil proposé mais non activé: "+tool_id)
        result=fn(**params);return await result if asyncio.iscoroutine(result) else result

class VeroxAutonomousDrive:
    """Pulsion persistante de création de valeur, choix de stratégie adaptatif et apprentissage mesuré."""
    CORE_DIRECTIVE="Explorer les opportunités légales et autorisées de création de valeur et de revenus; inventer des stratégies librement et apprendre des résultats observés."
    def __init__(self,store,apps,execution):self.store=store;self.apps=apps;self.execution=execution
    def environment_snapshot(self):
        return {"directive":self.CORE_DIRECTIVE,"apps":self.apps.list(),"tools":self.execution.list_tools(),"memory":self.store.recent_memory(15),"outcomes":self.store.outcome_stats(),"environment":{"platform":os.name,"python":os.sys.version.split()[0],"proxy_configured":bool(os.getenv("COGNITIVE_PROXY_URL")),"supabase_configured":bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY"))}}
    def choose_strategy(self,objective):
        q=objective.lower();choices=[]
        if any(k in q for k in ("installer","application","connecteur","outil","navigateur","session")):choices.append(("inspect_capabilities","inspecter les capacités, sessions et inventaire"))
        if any(k in q for k in ("revenu","gagner","rentable","business","monétis","opportunité","arbitrage")):choices.append(("discover_opportunities","rechercher et comparer les opportunités légales"))
        if any(k in q for k in ("étude","recherche","scientifique","architecture","code","analyse")):choices.append(("evidence_research","collecter et confronter des preuves"))
        if not choices:choices=[("understand_and_research","comprendre l'objectif et choisir les connecteurs utiles")]
        stats=self.store.outcome_stats()
        def score(c):
            r=stats.get(c[0],{});n=r.get("attempts") or 0;w=r.get("successes") or 0
            return (w+1)/(n+2)+.25/(n+1)
        return max(choices,key=score)

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
        self.store = CognitiveStore()
        self.app_manager = VeroxAppManager(self.store)
        self.session_manager = VeroxSessionManager(self.store)
        self.execution_manager = VeroxExecutionManager(self.store)
        self.hub = ConnectorHub(self.gateway, self.session_manager)
        self.drive = VeroxAutonomousDrive(self.store, self.app_manager, self.execution_manager)

    def initialize_extensions(self) -> int:
        """Charge le registre versionné et l'inscrit dans l'inventaire local du cerveau."""
        catalog_path = Path(__file__).resolve().parent / "integrations" / "catalog.json"
        with catalog_path.open("r", encoding="utf-8") as catalog_file:
            catalog = json.load(catalog_file)
        items = catalog.get("items", [])
        for item in items:
            self.app_manager.register(
                item["id"],
                item["label"],
                item.get("category", "extension"),
                origin="repository_catalog",
                access_point=item.get("url", ""),
                execution_env=item.get("execution_mode", "unknown"),
                capabilities=item.get("integration_type", []),
                authorizations=["respect_service_auth", "explicit_approval_for_sensitive_actions"]
            )
        return len(items)

    async def probe_gemini(self) -> bool:
        """Vérifie réellement l'API de secours au démarrage sans bloquer le service."""
        if not _gemini_api_key()[0]:
            return False
        response = await self.ask_gemini("Test technique de démarrage. Réponds uniquement OK.", [])
        return bool(response)

    async def ask_gemini(self, user_query: str, evidence: List[Dict[str, Any]] = None) -> Optional[str]:
        """Modèle de secours via Gemini API; la clé reste exclusivement côté serveur."""
        api_key, key_source = _gemini_api_key()
        if not api_key:
            logger.warning("Gemini fallback indisponible: aucune clé Gemini n'est configurée dans Render.")
            return None
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        context = json.dumps((evidence or [])[:8], ensure_ascii=False)[:18000]
        prompt = (
            "Tu es le moteur de secours de CognitiveBrain dans Verox. "
            "Réponds directement et utilement à la demande, en français sauf demande contraire. "
            "Ne prétends pas avoir exécuté une action, connecté un compte, installé une application ou généré des revenus si ce n'est pas prouvé. "
            "Si les preuves fournies sont insuffisantes, indique les limites et propose la prochaine action sûre. "
            "Priorise les opportunités légales, à faible coût et testables rapidement, avec l'objectif de rechercher des premières recettes dans les 30 minutes sans garantir de revenu. "
            "N'engage pas d'argent, ne passe pas de transaction, ne crée pas de compte et ne publie pas de contenu sans autorisation explicite. "
            "Un revenu n'est confirmé que par une preuve de transaction ou un enregistrement financier fiable. "
            "Ne demande jamais à l'utilisateur de publier une clé secrète.\n\n"
            f"Demande : {user_query}\n\n"
            f"Preuves disponibles : {context or 'Aucune preuve exploitable retournée par les connecteurs.'}"
        )
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                response = await client.post(
                    endpoint,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200}}
                )
                response.raise_for_status()
                payload = response.json()
            parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            answer = "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
            return answer.strip() or None
        except Exception as exc:
            logger.warning("Gemini fallback indisponible (%s).", type(exc).__name__)
            return None

    async def save_to_supabase(self, query: str, evidence: List[Dict[str, Any]]):
        """Archive la mémoire du cerveau dans le schéma PostgreSQL canonique Nexus/Verox."""
        supabase_url = _supabase_base_url(os.getenv("SUPABASE_URL", ""))
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not supabase_url or not supabase_key:
            logger.warning("[Supabase] Mémoire distante ignorée: SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY manquants.")
            return
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        payload = {
            "namespace": "verox.cognitive_brain",
            "memory_key": f"research:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
            "content": {"query": query, "evidence": evidence},
            "source": "cognitive_brain",
            "importance": 3
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.post(f"{supabase_url}/rest/v1/nexus_memory", json=payload, headers=headers)
                response.raise_for_status()
                logger.info("[Supabase] Mémoire CognitiveBrain archivée dans nexus_memory.")
            except Exception:
                logger.exception("[Supabase] Échec de l'archivage dans nexus_memory.")

    async def run(self, user_query: str, user_email: str = "", provider_filter: Optional[str] = None):
        strategy_id, strategy_description = self.drive.choose_strategy(user_query)
        self.store.remember("user_request", user_query, {"strategy": strategy_id, "description": strategy_description})
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
            if constraints.get("use_model_catalog"):
                tasks.append(self.hub.fetch_huggingface_models(client, user_query))

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

        raw_evidence.extend(await self.hub.fetch_web_llm_interfaces(user_query, user_email, provider_filter=provider_filter))

        # 4. Filtrage et dédoublonnage strict
        unique_evidence = []
        seen = set()
        for item in raw_evidence:
            key = (item.get("title") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique_evidence.append(item)

        # 5. Secours Gemini : uniquement si les connecteurs n'ont fourni aucune preuve exploitable.
        # Gemini ne reçoit jamais le droit d'exécuter arbitrairement du code ou de manipuler des secrets.
        if not unique_evidence:
            fallback_answer = await self.ask_gemini(user_query, [])
            if fallback_answer:
                unique_evidence = [{
                    "connector": "Gemini fallback",
                    "year": datetime.now(timezone.utc).year,
                    "title": "Réponse de secours CognitiveBrain",
                    "evidence": fallback_answer
                }]

        # 6. Sauvegarde sur Supabase (si configuré sur Render)
        await self.save_to_supabase(user_query, unique_evidence)

        # 7. Restitution finale : preuves des connecteurs ou réponse de secours explicitement identifiée
        print(f"\n📍 Nœud réseau actif : {net_status['city']} ({net_status['country']}) | IP: {net_status['ip']}")
        print(f"🔍 Requête analysée : {user_query}")
        print(f"🛡️ Contraintes appliquées : Geo-Lock={constraints['enforce_fixed_geo']} | Stealth={constraints['use_stealth_browser']}")
        print("-" * 65)

        if not unique_evidence:
            print("[!] Aucune preuve validée n'a franchi les filtres de contraintes.")
            self.store.record_outcome(strategy_id, strategy_id, False, 0.0, {"evidence_count": 0, "network": net_status})
            self.store.remember("execution_result", "Aucune preuve retournée", {"strategy": strategy_id, "success": False})
            return []

        for idx, ev in enumerate(unique_evidence, 1):
            print(f"\n[Preuve #{idx}] Source : {ev['connector']} ({ev['year']})")
            print(f"Titre   : {ev['title']}")
            print(f"Extrait : {ev['evidence']}")

        print("\n" + "="*65)
        print(f"✅ Cycle terminé : {len(unique_evidence)} éléments collectés et structurés.")
        self.store.record_outcome(strategy_id, strategy_id, True, float(len(unique_evidence)), {"evidence_count": len(unique_evidence), "network": net_status})
        self.store.remember("execution_result", f"{len(unique_evidence)} éléments de preuve collectés", {"strategy": strategy_id, "success": True, "evidence_count": len(unique_evidence)})
        return unique_evidence
        print("="*65 + "\n")



async def _renew_task_lease(client, supabase_url, headers, task_id, worker_id, lease_seconds):
    now = datetime.now(timezone.utc)
    response = await client.patch(
        f"{supabase_url}/rest/v1/nexus_tasks",
        params={
            "id": f"eq.{task_id}",
            "status": "eq.running",
            "worker_id": f"eq.{worker_id}",
            "lease_expires_at": f"gt.{now.isoformat()}",
        },
        json={
            "heartbeat_at": now.isoformat(),
            "lease_expires_at": (now + __import__("datetime").timedelta(seconds=lease_seconds)).isoformat(),
            "updated_at": now.isoformat(),
        },
        headers=headers,
    )
    response.raise_for_status()
    return bool(response.json())


async def _task_heartbeat(client, supabase_url, headers, task_id, worker_id, lease_seconds, lost_lease):
    interval = max(5.0, lease_seconds / 3.0)
    while True:
        await asyncio.sleep(interval)
        try:
            if not await _renew_task_lease(
                client, supabase_url, headers, task_id, worker_id, lease_seconds
            ):
                lost_lease.set()
                logger.warning("Bail perdu: task_id=%s worker_id=%s", task_id, worker_id)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Heartbeat indisponible task_id=%s (%s)", task_id, type(exc).__name__)
            # On retente au prochain intervalle; la finalisation reste protégée par le bail.


async def _finalize_claimed_task(
    client, supabase_url, headers, task_id, worker_id, status, result, error=None
):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": status,
        "result": result,
        "error": error,
        "last_error": error,
        "worker_id": None,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "completed_at": now if status in {"succeeded", "failed", "cancelled"} else None,
        "updated_at": now,
    }
    response = await client.patch(
        f"{supabase_url}/rest/v1/nexus_tasks",
        params={
            "id": f"eq.{task_id}",
            "status": "eq.running",
            "worker_id": f"eq.{worker_id}",
            "lease_expires_at": f"gt.{now}",
        },
        json=payload,
        headers=headers,
    )
    response.raise_for_status()
    committed = response.json()
    if not committed:
        logger.warning(
            "Finalisation refusée: bail expiré ou tâche reprise task_id=%s worker_id=%s",
            task_id, worker_id
        )
        return False
    return True


async def _reclaim_expired_tasks(client, supabase_url, headers):
    response = await client.post(
        f"{supabase_url}/rest/v1/rpc/reclaim_expired_nexus_tasks",
        json={"p_batch_size": max(1, min(500, int(os.getenv("VEROX_RECLAIM_BATCH_SIZE", "50"))))},
        headers=headers,
    )
    response.raise_for_status()
    reclaimed = response.json()
    if reclaimed:
        logger.warning("Reprise de %d tâche(s) au bail expiré.", len(reclaimed))


async def _reclaim_loop(client, supabase_url, headers, interval_seconds):
    while True:
        try:
            await _reclaim_expired_tasks(client, supabase_url, headers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Balayage des baux expirés indisponible (%s).", type(exc).__name__)
        await asyncio.sleep(interval_seconds)


async def run_worker(worker_id: int = 0):
    """Consomme la file Supabase avec réservation atomique, leases et finalisation protégée."""
    brain = CognitiveBrain()
    if worker_id == 0:
        try:
            extension_count = brain.initialize_extensions()
            logger.info("Registre CognitiveBrain chargé : %s extensions classées.", extension_count)
        except Exception:
            logger.exception("Impossible de charger le registre des extensions")
        if _gemini_api_key()[0]:
            if await brain.probe_gemini():
                logger.info("Gemini API de secours vérifiée au démarrage.")
            else:
                logger.warning("Gemini API de secours indisponible; le cerveau continue avec les connecteurs disponibles.")
        else:
            logger.warning("Clé Gemini absente (GEMINI_API_KEY, GEMINI_API_KEY_2 ou Gemini).")

    supabase_url = _supabase_base_url(os.getenv("SUPABASE_URL", ""))
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY sont requis. Worker en attente de configuration.")
        while True:
            await asyncio.sleep(30)

    node_id = os.getenv("RENDER_INSTANCE_ID") or os.getenv("HOSTNAME") or "verox"
    worker_name = f"{node_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}-w{worker_id}"
    lease_seconds = max(15, min(900, int(os.getenv("VEROX_LEASE_SECONDS", "90"))))
    reclaim_interval = max(5, int(os.getenv("VEROX_RECLAIM_INTERVAL_S", "20")))
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    logger.info("Worker %s prêt; lease=%ss.", worker_name, lease_seconds)

    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=8.0)) as client:
        reclaim_task = (
            asyncio.create_task(
                _reclaim_loop(client, supabase_url, headers, reclaim_interval),
                name="verox-lease-reclaimer",
            )
            if worker_id == 0 else None
        )
        try:
            while True:
                task = None
                heartbeat = None
                lost_lease = asyncio.Event()
                try:
                    claim_response = await client.post(
                        f"{supabase_url}/rest/v1/rpc/claim_next_nexus_task",
                        json={"p_worker_id": worker_name, "p_lease_seconds": lease_seconds},
                        headers=headers,
                    )
                    claim_response.raise_for_status()
                    claimed = claim_response.json()
                    if isinstance(claimed, dict):
                        task = claimed
                    elif isinstance(claimed, list) and claimed:
                        task = claimed[0]

                    if not task:
                        await asyncio.sleep(0.75)
                        continue

                    task_id = task["id"]
                    heartbeat = asyncio.create_task(
                        _task_heartbeat(
                            client, supabase_url, headers, task_id, worker_name,
                            lease_seconds, lost_lease
                        ),
                        name=f"verox-heartbeat-{str(task_id)[:8]}",
                    )
                    try:
                        payload = task.get("payload") or {}
                        guard_declared_financial_action(task)
                        envelope_type = classify_task_envelope(task)
                        if envelope_type == "a2a_message":
                            # No generic CognitiveBrain fallback is permitted for A2A.
                            result = await execute_a2a_pipeline_step(
                                task=task, client=client, supabase_url=supabase_url, headers=headers
                            )
                            if not isinstance(result, dict):
                                raise RuntimeGuardError("a2a_pipeline_returned_invalid_result")
                            if lost_lease.is_set():
                                logger.warning("Résultat A2A non publié: bail perdu task_id=%s", task_id)
                                continue
                            committed = await _finalize_claimed_task(
                                client, supabase_url, headers, task_id, worker_name,
                                "succeeded", result, None
                            )
                            if not committed:
                                logger.error("Finalisation A2A non confirmée task_id=%s", task_id)
                            continue
                        query = str(payload.get("text") or payload.get("query") or "").strip()
                        email = str(payload.get("email") or "").strip()
                        task_type = str(task.get("task_type") or "cognitive_chat").strip().lower()
                        if not query:
                            raise ValueError("Le champ payload.text ou payload.query est requis.")

                        route = None
                        provider_filter = None
                        if task_type not in {"cognitive_chat", "chat", "general_chat"}:
                            route = resolve_route(task_type)
                            if route.connector in {"grok", "gemini", "claude", "gpt"}:
                                provider_filter = route.provider
                            elif route.role != "brain_coordination":
                                raise RuntimeError(
                                    f"Connecteur requis non intégré au worker: {route.connector}. "
                                    "La tâche est arrêtée plutôt que simulée."
                                )

                        evidence = await brain.run(query, email, provider_filter=provider_filter)
                        if route and provider_filter and not any(
                            provider_filter.lower() in str(item.get("connector", "")).lower()
                            and "résultat web" in str(item.get("title", "")).lower()
                            for item in (evidence or [])
                        ):
                            raise RuntimeError(
                                f"Le fournisseur {route.provider} n'a pas retourné de résultat vérifiable. "
                                "Vérifier la session Playwright et relancer la tâche."
                            )

                        requested_provider = str(payload.get("provider", "")).strip().lower()
                        gemini_fallback = next(
                            (item for item in (evidence or []) if item.get("connector") == "Gemini fallback"),
                            None,
                        )
                        if task_type in {"chat", "cognitive_chat", "general_chat"} and requested_provider == "gemini":
                            # Explicit dashboard provider choice: use the Gemini API key configured server-side.
                            answer = await brain.ask_gemini(query, evidence or [])
                        elif gemini_fallback:
                            answer = str(gemini_fallback.get("evidence") or "").strip()
                        elif provider_filter:
                            provider_result = next(
                                (item for item in (evidence or [])
                                 if provider_filter.lower() in str(item.get("connector", "")).lower()
                                 and "résultat web" in str(item.get("title", "")).lower()),
                                None,
                            )
                            answer = str(provider_result.get("evidence") or "").strip() if provider_result else ""
                        else:
                            answer = await brain.ask_gemini(query, evidence or [])

                        if not answer or not str(answer).strip():
                            raise RuntimeError("Aucun modèle IA n'a produit de réponse exploitable. Vérifier la clé Gemini et le modèle.")

                        result = {
                            "answer": answer or "",
                            "provider": ("gemini" if task_type in {"chat", "cognitive_chat", "general_chat"} and requested_provider == "gemini" else (route.provider if route and provider_filter else ("gemini" if answer else "connectors"))),
                            "model": (os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash") if requested_provider == "gemini" else None,
                            "task_type": task_type,
                            "department_id": task.get("department_id") or payload.get("department_id"),
                            "strategy_id": task.get("strategy_id") or payload.get("strategy_id"),
                            "mission_id": task.get("mission_id") or payload.get("mission_id"),
                            "routed_role": route.role if route else "general_cognitive_chat",
                            "route_connector": route.connector if route else None,
                            "evidence": evidence or [],
                            "evidence_count": len(evidence or []),
                        }
                        if lost_lease.is_set():
                            logger.warning("Résultat non publié: bail perdu task_id=%s", task_id)
                            continue
                        committed = await _finalize_claimed_task(
                            client, supabase_url, headers, task_id, worker_name,
                            "succeeded", result, None
                        )
                        if committed:
                            await client.post(
                                f"{supabase_url}/rest/v1/nexus_execution_events",
                                json={
                                    "task_id": task_id, "event_type": "task_succeeded", "severity": "info",
                                    "message": "CognitiveBrain a terminé la tâche.",
                                    "details": {
                                        "task_type": task_type,
                                        "department_id": result["department_id"],
                                        "strategy_id": result["strategy_id"],
                                        "mission_id": result["mission_id"],
                                        "evidence_count": result["evidence_count"],
                                    },
                                },
                                headers={**headers, "Prefer": "return=minimal"},
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("Échec de la tâche cognitive %s", task_id)
                        error_result = {"error": str(exc)[:1000], "evidence": []}
                        if not lost_lease.is_set():
                            committed = await _finalize_claimed_task(
                                client, supabase_url, headers, task_id, worker_name,
                                "failed", error_result, str(exc)[:2000]
                            )
                            if committed:
                                await client.post(
                                    f"{supabase_url}/rest/v1/nexus_execution_events",
                                    json={
                                        "task_id": task_id, "event_type": "task_failed", "severity": "error",
                                        "message": "Échec de la tâche CognitiveBrain.",
                                        "details": {"error": str(exc)[:1000]},
                                    },
                                    headers={**headers, "Prefer": "return=minimal"},
                                )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Erreur du worker cognitif %s (%s)", worker_name, type(exc).__name__)
                    await asyncio.sleep(2)
                finally:
                    if heartbeat is not None:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
        finally:
            if reclaim_task is not None:
                reclaim_task.cancel()
                await asyncio.gather(reclaim_task, return_exceptions=True)


def run_dashboard():
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse
    import uuid

    class DashboardHandler(SimpleHTTPRequestHandler):
        def _supabase(self):
            url = _supabase_base_url(os.getenv("SUPABASE_URL", ""))
            key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            if not url or not key:
                raise RuntimeError("Le backend Supabase n'est pas configuré côté serveur.")
            return url, {"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"}

        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/architecture/back":
                return self._json(200, {
                    "service": "verox-architecture-bridge",
                    "direction": "back",
                    "method": "POST",
                    "label": "Retour",
                    "note": "Cette route reçoit une commande de retour d'interface; elle ne pilote pas encore une session Playwright persistante."
                })
            if parsed.path == "/api/architecture":
                return self._json(200, {
                    "service": "verox-architecture-bridge",
                    "directions": {
                        "upstream": {"label": "Correspondance ascendante", "flow": "frontend -> backend", "endpoint": "/api/architecture/upstream"},
                        "downstream": {"label": "Correspondance descendante", "flow": "backend -> frontend", "endpoint": "/api/architecture/downstream"},
                        "horizontal": {"label": "Correspondance horizontale", "flow": "backend -> backend", "endpoint": "/api/architecture/horizontal"}
                    },
                    "horizontal_targets": ["brain", "playwright", "catalog", "market", "integrations"],
                    "playwright": {"configured": bool(os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()), "execution": "server-side only"},
                    "note": "Cette route décrit le contrat de communication. Les commandes Playwright restent sur leurs routes dédiées."
                })
            if parsed.path == "/api/integrations":
                # Return readiness booleans only; never expose secret values.
                groups = [
                    {"id": "monetly", "label": "Monetly", "env": ["MONETLY_API_KEY", "MONETLY_API_TOKEN", "MONETLY_ACCESS_TOKEN"]},
                    {"id": "mt5", "label": "MetaTrader 5", "env": ["MT5_API_URL", "MT5_API_TOKEN", "MT5_ACCOUNT_ID", "MT5_LOGIN"]},
                    {"id": "1xbet", "label": "1xBet", "env": [], "mode": "manual_only"},
                    {"id": "tradingview", "label": "TradingView", "env": ["TRADINGVIEW_API_KEY", "TRADINGVIEW_ACCESS_TOKEN"]},
                    {"id": "oanda", "label": "OANDA", "env": ["OANDA_API_TOKEN", "OANDA_ACCESS_TOKEN", "OANDA_ACCOUNT_ID"]},
                    {"id": "fxpro", "label": "FxPro", "env": ["FXPRO_API_KEY", "FXPRO_API_TOKEN", "FXPRO_ACCOUNT_ID"]},
                    {"id": "aws", "label": "AWS", "env": ["AWS_ACCESS_KEY_ID", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"]},
                    {"id": "airtel_money", "label": "Airtel Money", "env": ["AIRTEL_MONEY_CLIENT_ID", "AIRTEL_MONEY_CLIENT_SECRET", "AIRTEL_MONEY_API_KEY", "AIRTEL_MONEY_BASE_URL"]},
                    {"id": "youtube", "label": "YouTube", "env": ["YOUTUBE_API_KEY", "GOOGLE_API_KEY"]},
                    {"id": "vscode", "label": "Visual Studio Code", "env": [], "mode": "manual_only"},
                    {"id": "github", "label": "GitHub", "env": ["GITHUB_TOKEN", "GH_TOKEN"]},
                    {"id": "supabase", "label": "Supabase", "env": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"]},
                    {"id": "vercel", "label": "Vercel", "env": ["VERCEL_TOKEN"]},
                    {"id": "render", "label": "Render", "env": ["RENDER_API_KEY"]},
                    {"id": "chrome", "label": "Google Chrome", "env": [], "mode": "manual_only"},
                    {"id": "files_by_google", "label": "Files by Google", "env": [], "mode": "manual_only"},
                    {"id": "besoccer", "label": "BeSoccer", "env": [], "mode": "manual_only"},
                ]
                items = [{"id": g["id"], "label": g["label"], "credential_configured": any(os.getenv(n, "").strip() for n in g["env"]), "mode": g.get("mode", "api_candidate")} for g in groups]
                google_id = os.getenv("GOOGLE_CLIENT_ID", "").strip() or os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
                google_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip() or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
                return self._json(200, {"service": "verox-integration-readiness", "items": items, "google_oauth_configured": bool(google_id and google_secret), "browser_bridge_configured": bool(os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()), "note": "Une variable présente ne prouve ni la validité des identifiants ni une session connectée."})
            if parsed.path.startswith("/api/browser/"):
                platform = parsed.path.rsplit("/", 1)[-1].strip().lower()
                if platform in BROWSER_TARGETS:
                    return self._json(200, {
                        "service": "verox-playwright-platform-route",
                        "platform": platform,
                        "endpoint": "/api/browser/" + platform,
                        "method": "POST",
                        "target": BROWSER_TARGETS[platform],
                        "configured": bool(os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()),
                        "mode": "read_only_public_page_inspection",
                        "login_performed": False,
                        "actions_performed": False,
                    })
            if parsed.path == "/api/browser/vercel":
                return self._json(200, {
                    "service": "verox-vercel-playwright",
                    "endpoint": "/api/browser/vercel",
                    "method": "POST",
                    "configured": bool(os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()),
                    "mode": "read_only_public_page_inspection",
                    "target": "https://vercel.com/",
                    "note": "POST requiert le jeton serveur. Le dashboard privé Vercel ne sera pas authentifié par ce contrôle."
                })
            if parsed.path == "/api/browser/bridge":
                token_configured = bool(os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip())
                return self._json(200, {
                    "service": "verox-playwright-browser-bridge",
                    "configured": token_configured,
                    "mode": "server_side_headless_browser",
                    "device_chrome_control": False,
                    "platforms": ["monetly", "mt5", "1xbet", "tradingview", "oanda", "fxpro", "chrome", "aws", "airtel_money", "youtube", "vscode", "github", "supabase", "vercel", "render", "files_by_google", "besoccer"],
                    "message": "Le pont utilise Chromium côté serveur. Il ne contrôle pas le Chrome installé sur votre téléphone ou PC."
                })
            if parsed.path == "/api/health":
                try:
                    url, headers = self._supabase()
                    with httpx.Client(timeout=8.0) as client:
                        check = client.get(url + "/rest/v1/nexus_tasks?select=id&limit=1", headers=headers)
                        check.raise_for_status()
                    catalog_path = Path(__file__).resolve().parent / "integrations" / "catalog.json"
                    with catalog_path.open("r", encoding="utf-8") as catalog_file:
                        extension_count = len(json.load(catalog_file).get("items", []))
                    return self._json(200, {
                        "ok": True,
                        "database": "connected",
                        "gemini_fallback_configured": bool(_gemini_api_key()[0]),
                        "extension_count": extension_count
                    })
                except Exception as exc:
                    return self._json(503, {
                        "ok": False,
                        "gemini_fallback_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
                        "error": str(exc)[:250]
                    })
            if parsed.path == "/.well-known/agent-card.json":
                public_url = os.getenv("VEROX_PUBLIC_URL", "https://verox-juqt.onrender.com").rstrip("/")
                return self._json(200, {
                    "name": "Verox Agent Market",
                    "description": "Low-latency agent-to-agent capability discovery and specialist routing.",
                    "url": public_url + "/api/market/resolve",
                    "version": "1.0.0",
                    "capabilities": {"streaming": False, "pushNotifications": False},
                    "skills": [
                        {"id": "market-discovery", "name": "Market discovery", "description": "Search declared specialist capabilities and return ranked routing candidates."},
                        {"id": "agent-routing", "name": "Low-latency routing", "description": "Rank specialist agents locally without calling external providers."},
                        {"id": "async-task-execution", "name": "Asynchronous task execution", "description": "Submit a task and receive its identifier immediately; poll its result endpoint."},
                        {"id": "product-catalog", "name": "Verox products", "description": "Machine-readable catalog of Verox integration products and endpoints."}
                    ],
                    "interfaces": [
                        {"url": public_url + "/api/market/resolve", "protocol": "HTTP+JSON", "method": "POST"},
                        {"url": public_url + "/api/market/submit", "protocol": "HTTP+JSON", "method": "POST", "authentication": "Bearer"},
                        {"url": public_url + "/api/market/products", "protocol": "HTTP+JSON", "method": "GET"},
                        {"url": public_url + "/api/tasks/{task_id}", "protocol": "HTTP+JSON", "method": "GET"}
                    ],
                    "authentication": {"required": bool(os.getenv("VEROX_MARKET_API_TOKEN")), "type": "Bearer" if os.getenv("VEROX_MARKET_API_TOKEN") else "none"}
                })
            if parsed.path == "/api/market/instances":
                market_token = os.getenv("VEROX_MARKET_API_TOKEN", "").strip()
                if not market_token:
                    return self._json(503, {"error": "VEROX_MARKET_API_TOKEN doit être configuré pour accéder aux instances de mission."})
                import hmac
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + market_token):
                    return self._json(401, {"error": "Authentification agent requise."})
                return self._json(200, active_mission_agents())
            if parsed.path == "/api/market/products":
                catalog = dict(MARKET_PRODUCT_CATALOG)
                catalog["url"] = os.getenv("VEROX_PUBLIC_URL", "https://verox-juqt.onrender.com").rstrip("/")
                catalog["products"] = [dict(item, url=catalog["url"] + item["endpoint"]) for item in catalog["products"]]
                return self._json(200, catalog)
            if parsed.path == "/api/market/catalog":
                catalog = market_catalog()
                catalog["url"] = os.getenv("VEROX_PUBLIC_URL", "https://verox-juqt.onrender.com")
                return self._json(200, catalog)
            if parsed.path == "/api/market/resolve":
                return self._json(405, {"error": "Utiliser POST pour résoudre une requête d'agent."})
            if parsed.path == "/api/agents":
                agents = [route_status(task_type) for task_type in sorted(ROUTES)]
                return self._json(200, {"agents": agents, "count": len(agents)})
            if parsed.path == "/api/extensions":
                try:
                    catalog_path = Path(__file__).resolve().parent / "integrations" / "catalog.json"
                    with catalog_path.open("r", encoding="utf-8") as catalog_file:
                        catalog = json.load(catalog_file)
                    return self._json(200, catalog)
                except Exception as exc:
                    logger.exception("Impossible de charger le catalogue d'extensions")
                    return self._json(500, {"error": "Catalogue d'extensions indisponible"})
            if parsed.path == "/api/metrics":
                try:
                    url, headers = self._supabase()
                    with httpx.Client(timeout=12.0) as client:
                        task_response = client.get(
                            url + "/rest/v1/nexus_tasks",
                            params={"select": "id,status,task_type", "limit": "1000"},
                            headers=headers
                        )
                        task_response.raise_for_status()
                        event_response = client.get(
                            url + "/rest/v1/nexus_execution_events",
                            params={"select": "event_type,severity", "limit": "1000"},
                            headers=headers
                        )
                        event_response.raise_for_status()
                    tasks = task_response.json()
                    events = event_response.json()
                    counts = {"total": len(tasks), "succeeded": 0, "failed": 0, "pending": 0, "running": 0}
                    by_task_type = {}
                    for row in tasks:
                        status = str(row.get("status") or "").lower()
                        if status in counts and status != "total":
                            counts[status] += 1
                        elif status in ("queued", "pending", "created"):
                            counts["pending"] += 1
                        kind = str(row.get("task_type") or "unspecified")
                        by_task_type[kind] = by_task_type.get(kind, 0) + 1
                    by_event_type = {}
                    for row in events:
                        kind = str(row.get("event_type") or "unspecified")
                        by_event_type[kind] = by_event_type.get(kind, 0) + 1
                    return self._json(200, {
                        "tasks": counts,
                        "by_task_type": by_task_type,
                        "by_event_type": by_event_type,
                        "events_total": len(events),
                        "confirmed_revenue": None,
                        "currency": "EUR"
                    })
                except Exception as exc:
                    return self._json(503, {"error": "Métriques indisponibles", "detail": str(exc)[:200]})
            if parsed.path.startswith("/api/tasks/"):
                task_id = parsed.path.rsplit("/", 1)[-1]
                try:
                    uuid.UUID(task_id)
                    url, headers = self._supabase()
                    with httpx.Client(timeout=10.0) as client:
                        resp = client.get(url + "/rest/v1/nexus_tasks",
                            params={"id": f"eq.{task_id}", "select": "id,status,result,error"}, headers=headers)
                        resp.raise_for_status()
                    rows = resp.json()
                    return self._json(200 if rows else 404, rows[0] if rows else {"error": "Tâche introuvable"})
                except ValueError:
                    return self._json(400, {"error": "Identifiant de tâche invalide"})
                except Exception as exc:
                    return self._json(503, {"error": str(exc)[:250]})
            if parsed.path in ("/", "/index.html", "/nexus_dashboard.html"):
                self.path = "/index.html"
            return super().do_GET()

        def do_POST(self):
            request_path = urlparse(self.path).path.rstrip("/")
            if request_path == "/api/internal/read-only-research/run":
                import hmac
                token = os.getenv("VEROX_RENDER_WORKER_TOKEN", "").strip()
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if len(token) < 32 or not supplied or not hmac.compare_digest(token, supplied):
                    return self._json(401, {"error": "Authentification de service requise."})
                if not _research_cycle_lock.acquire(blocking=False):
                    return self._json(202, {"accepted": True, "status": "already_running"})
                def run_research_cycle():
                    try:
                        from verox_brain.opportunity_worker import run_scan
                        from verox_brain.browser_worker import BrowserWorker
                        scan = run_scan()
                        worker = BrowserWorker()
                        seeded = worker.seed_public_cycle(limit=5)
                        processed = worker.run_once()
                        logger.info("Cycle de recherche lecture seule terminé: leads=%s jobs=%s",
                                    scan.get("leads_persisted", 0), processed.get("processed", 0))
                    except Exception:
                        logger.exception("Cycle de recherche lecture seule échoué.")
                    finally:
                        _research_cycle_lock.release()
                import threading
                threading.Thread(target=run_research_cycle, name="verox-read-only-research", daemon=True).start()
                return self._json(202, {"accepted": True, "status": "started"})
            architecture_routes = {
                "/api/architecture/upstream": "upstream",
                "/api/architecture/downstream": "downstream",
                "/api/architecture/horizontal": "horizontal",
                "/api/architecture/back": "back",
            }
            if request_path in architecture_routes:
                # Same-origin browser bridge: accept bounded JSON envelopes only.
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                if origin and host:
                    from urllib.parse import urlparse as _urlparse
                    if _urlparse(origin).netloc != host:
                        return self._json(403, {"error": "Origine non autorisée."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 12000:
                        return self._json(400, {"error": "Taille de requête invalide."})
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        return self._json(400, {"error": "Le corps doit être un objet JSON."})
                    direction = architecture_routes[request_path]
                    event = str(body.get("event", "status")).strip()[:80]
                    payload = body.get("payload", {})
                    if not isinstance(payload, dict):
                        return self._json(400, {"error": "payload doit être un objet JSON."})
                    if direction == "upstream":
                        source, target = "frontend", "backend"
                    elif direction == "downstream":
                        source, target = "backend", "frontend"
                    elif direction == "back":
                        source, target = "frontend", "backend"
                        event = "back"
                    else:
                        source = "backend"
                        target = str(body.get("target", "")).strip().lower()
                        if target not in {"brain", "playwright", "catalog", "market", "integrations"}:
                            return self._json(400, {"error": "Cible horizontale non autorisée."})
                    # Correlation envelope confirms the route and preserves a traceable request ID.
                    correlation_id = str(body.get("correlation_id", "")).strip()[:80] or str(uuid.uuid4())
                    return self._json(200, {
                        "ok": True,
                        "service": "verox-architecture-bridge",
                        "direction": direction,
                        "source": source,
                        "target": target,
                        "event": event,
                        "correlation_id": correlation_id,
                        "payload": payload,
                        "delivery": "request_acknowledged",
                        "note": "Le pont a validé et accusé réception de l'enveloppe; cela ne déclenche pas à lui seul une action Playwright ou métier."
                    })
                except (ValueError, UnicodeDecodeError):
                    return self._json(400, {"error": "JSON invalide."})
                except Exception as exc:
                    logger.warning("Architecture bridge error: %s", type(exc).__name__)
                    return self._json(500, {"error": "Pont d'architecture indisponible."})
            if request_path == "/api/browser/vercel":
                token = os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()
                if not token:
                    return self._json(503, {"error": "VEROX_BROWSER_BRIDGE_TOKEN non configuré côté serveur."})
                import hmac
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    return self._json(401, {"error": "Authentification du pont navigateur requise."})
                try:
                    async def inspect_vercel_public_page():
                        async with async_playwright() as p:
                            browser = await p.chromium.launch(headless=True)
                            try:
                                page = await browser.new_page(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
                                response = await page.goto("https://vercel.com/", wait_until="domcontentloaded", timeout=15000)
                                return {"status_code": response.status if response else None, "title": (await page.title())[:200], "final_url": page.url[:1000]}
                            finally:
                                await browser.close()
                    result = asyncio.run(inspect_vercel_public_page())
                    return self._json(200, {"service": "verox-vercel-playwright", "checked": True, "inspection": result, "dashboard_authenticated": False, "actions_performed": False})
                except Exception as exc:
                    logger.warning("Vercel Playwright inspection failed: %s", type(exc).__name__)
                    return self._json(502, {"error": "Inspection Playwright de Vercel indisponible."})
            if request_path.startswith("/api/browser/") and request_path.rsplit("/", 1)[-1].lower() in BROWSER_TARGETS:
                platform = request_path.rsplit("/", 1)[-1].lower()
                token = os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()
                if not token:
                    return self._json(503, {"error": "VEROX_BROWSER_BRIDGE_TOKEN non configuré côté serveur."})
                import hmac
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    return self._json(401, {"error": "Authentification du pont navigateur requise."})
                try:
                    async def inspect_named_public_page():
                        async with async_playwright() as p:
                            browser = await p.chromium.launch(headless=True)
                            try:
                                page = await browser.new_page(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
                                response = await page.goto(BROWSER_TARGETS[platform], wait_until="domcontentloaded", timeout=15000)
                                return {"status_code": response.status if response else None, "title": (await page.title())[:200], "final_url": page.url[:1000]}
                            finally:
                                await browser.close()
                    result = asyncio.run(inspect_named_public_page())
                    return self._json(200, {"service": "verox-playwright-platform-route", "platform": platform, "checked": True, "inspection": result, "login_performed": False, "actions_performed": False})
                except Exception as exc:
                    logger.warning("Playwright inspection failed for %s: %s", platform, type(exc).__name__)
                    return self._json(502, {"error": "Inspection Playwright indisponible; aucune connexion ni action n'a été effectuée."})
            if request_path == "/api/browser/bridge":
                token = os.getenv("VEROX_BROWSER_BRIDGE_TOKEN", "").strip()
                if not token:
                    return self._json(503, {"error": "VEROX_BROWSER_BRIDGE_TOKEN n'est pas configuré côté serveur."})
                import hmac
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    return self._json(401, {"error": "Authentification du pont navigateur requise."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 4000:
                        return self._json(400, {"error": "Taille de requête invalide."})
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    platform = str(body.get("platform", "")).strip().lower()
                    targets = BROWSER_TARGETS
                    if platform not in targets:
                        return self._json(400, {"error": "Plateforme non autorisée."})
                    async def inspect_public_page():
                        async with async_playwright() as p:
                            browser = await p.chromium.launch(headless=True)
                            try:
                                page = await browser.new_page(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
                                response = await page.goto(targets[platform], wait_until="domcontentloaded", timeout=15000)
                                return {"status_code": response.status if response else None, "title": (await page.title())[:200], "final_url": page.url[:1000]}
                            finally:
                                await browser.close()
                    result = asyncio.run(inspect_public_page())
                    return self._json(200, {"platform": platform, "checked": True, "inspection": result, "login_performed": False, "actions_performed": False})
                except Exception:
                    logger.exception("Échec du pont navigateur Playwright")
                    return self._json(502, {"error": "Inspection Playwright indisponible; aucune connexion ni action n'a été effectuée."})
            if request_path not in {"/api/chat", "/api/market/resolve", "/api/market/spawn", "/api/market/retire", "/api/market/submit"}:
                return self._json(404, {"error": "Route inconnue"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 20000:
                    return self._json(400, {"error": "Taille de requête invalide"})
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if request_path in {"/api/market/resolve", "/api/market/spawn", "/api/market/retire", "/api/market/submit"}:
                    market_token = os.getenv("VEROX_MARKET_API_TOKEN", "").strip()
                    if request_path in {"/api/market/spawn", "/api/market/retire", "/api/market/submit"} and not market_token:
                        return self._json(503, {"error": "VEROX_MARKET_API_TOKEN doit être configuré avant de créer des agents ou soumettre une tâche."})
                    if market_token:
                        import hmac
                        supplied = self.headers.get("Authorization", "")
                        expected = "Bearer " + market_token
                        if not hmac.compare_digest(supplied, expected):
                            return self._json(401, {"error": "Authentification agent requise."})
                if request_path == "/api/market/submit":
                    query = str(body.get("query", body.get("text", ""))).strip()
                    task_type = str(body.get("task_type", "cognitive_chat")).strip().lower()
                    if not query or len(query) > 8000:
                        return self._json(400, {"error": "Le champ query est requis (maximum 8000 caractères)."})
                    try:
                        guard_declared_financial_action({"task_type": task_type, "payload": body})
                        classify_task_envelope({
                            "task_type": task_type,
                            "payload": {
                                **body,
                                "envelope_type": body.get("envelope_type", body.get("kind", "generic_task")),
                                "text": query,
                            },
                        })
                    except RuntimeGuardError as exc:
                        return self._json(403 if "financial_authorization" in str(exc) or "human_authorization" in str(exc) else 400, {"error": str(exc)})
                    if task_type not in {"cognitive_chat", "chat", "general_chat"}:
                        try:
                            resolve_route(task_type)
                        except UnknownTaskType as exc:
                            return self._json(400, {"error": str(exc), "available_task_types": sorted(ROUTES)})
                    idempotency_key = str(body.get("idempotency_key", "")).strip()
                    if idempotency_key and (len(idempotency_key) > 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", idempotency_key)):
                        return self._json(400, {"error": "idempotency_key doit contenir 1 à 120 caractères alphanumériques, . _ : ou -."})
                    try:
                        supabase_url, supabase_headers = self._supabase()
                    except RuntimeError:
                        return self._json(503, {"error": "La file persistante Supabase n’est pas configurée; aucune exécution synchrone de remplacement n’a été tentée."})
                    task_key = "agent-market:" + idempotency_key if idempotency_key else str(uuid.uuid4())
                    try:
                        with httpx.Client(timeout=4.0) as client:
                            if idempotency_key:
                                existing = client.get(supabase_url + "/rest/v1/nexus_tasks", params={"task_key": "eq." + task_key, "select": "id,status", "limit": "1"}, headers=supabase_headers)
                                existing.raise_for_status()
                                rows_existing = existing.json()
                                if rows_existing:
                                    return self._json(202, {"id": rows_existing[0]["id"], "status": rows_existing[0]["status"], "deduplicated": True, "result_url": "/api/tasks/" + rows_existing[0]["id"]})
                            task = {
                                "task_key": task_key,
                                "task_type": task_type,
                                "status": "queued",
                                "department_id": str(body.get("department_id", ""))[:80] or None,
                                "strategy_id": str(body.get("strategy_id", ""))[:120] or None,
                                "mission_id": str(body.get("mission_id", ""))[:100] or None,
                                "payload": {
                                    "envelope_type": str(body.get("envelope_type", body.get("kind", "generic_task"))).strip().lower(),
                                    "text": query,
                                    "source": "agent_market",
                                    "department_id": str(body.get("department_id", ""))[:80] or None,
                                    "strategy_id": str(body.get("strategy_id", ""))[:120] or None,
                                    "mission_id": str(body.get("mission_id", ""))[:100] or None,
                                    "product_id": str(body.get("product_id", "async-task-execution"))[:100],
                                },
                            }
                            response = client.post(supabase_url + "/rest/v1/nexus_tasks", json=task, headers={**supabase_headers, "Prefer": "return=representation"})
                            response.raise_for_status()
                            rows = response.json()
                        if not rows:
                            return self._json(502, {"error": "La file n’a retourné aucun identifiant de tâche."})
                        task_id = rows[0]["id"]
                        return self._json(202, {"id": task_id, "status": rows[0]["status"], "accepted": True, "deduplicated": False, "result_url": "/api/tasks/" + task_id, "delivery": "asynchronous", "note": "La tâche est persistée; son résultat dépend du worker et des connecteurs réellement configurés."})
                    except httpx.HTTPStatusError as exc:
                        # A concurrent request may insert the same unique task_key after our
                        # initial lookup. On PostgREST's uniqueness conflict, re-read and
                        # return the canonical task instead of surfacing a transient 503.
                        if exc.response.status_code == 409 and idempotency_key:
                            try:
                                with httpx.Client(timeout=4.0) as retry_client:
                                    existing = retry_client.get(
                                        supabase_url + "/rest/v1/nexus_tasks",
                                        params={"task_key": "eq." + task_key, "select": "id,status", "limit": "1"},
                                        headers=supabase_headers,
                                    )
                                    existing.raise_for_status()
                                    rows_existing = existing.json()
                                if rows_existing:
                                    existing_id = rows_existing[0]["id"]
                                    return self._json(202, {
                                        "id": existing_id,
                                        "status": rows_existing[0]["status"],
                                        "accepted": True,
                                        "deduplicated": True,
                                        "result_url": "/api/tasks/" + existing_id,
                                    })
                            except Exception as retry_exc:
                                logger.warning("Relecture après collision d'idempotence impossible (%s).", type(retry_exc).__name__)
                        logger.warning("Soumission agent-market impossible (HTTP %s).", exc.response.status_code)
                        return self._json(503, {"error": "Soumission persistante indisponible; réessayer avec la même idempotency_key.", "retryable": True})
                    except Exception as exc:
                        logger.warning("Soumission agent-market impossible (%s).", type(exc).__name__)
                        return self._json(503, {"error": "Soumission persistante indisponible; réessayer avec la même idempotency_key.", "retryable": True})
                if request_path == "/api/market/spawn":
                    query = str(body.get("query", body.get("text", ""))).strip()
                    try:
                        result = spawn_mission_agents(
                            query=query,
                            count=body.get("count", 3),
                            ttl_seconds=body.get("ttl_seconds", 180),
                            mission_id=body.get("mission_id"),
                        )
                    except ValueError as exc:
                        return self._json(400, {"error": str(exc)})
                    return self._json(202, result)
                if request_path == "/api/market/retire":
                    try:
                        result = retire_mission_agents(str(body.get("mission_id", "")))
                    except ValueError as exc:
                        return self._json(400, {"error": str(exc)})
                    return self._json(200, result)
                if request_path == "/api/market/resolve":
                    query = str(body.get("query", body.get("text", ""))).strip()
                    if not query or len(query) > 8000:
                        return self._json(400, {"error": "Le champ query est requis (maximum 8000 caractères)."})
                    started = __import__("time").perf_counter()
                    result = resolve_market(query, limit=body.get("limit", 3))
                    result["lookup_latency_ms"] = round((__import__("time").perf_counter() - started) * 1000, 3)
                    result["mode"] = "local_deterministic_registry"
                    result["execution_started"] = False
                    result["note"] = "La résolution classe les capacités déclarées; elle ne prouve pas qu'un fournisseur externe est connecté ni qu'une tâche a été exécutée."
                    return self._json(200, result)
                query = str(body.get("text", "")).strip()
                email = str(body.get("email", "")).strip()
                task_type = str(body.get("task_type", "cognitive_chat")).strip().lower()
                try:
                    guard_declared_financial_action({"task_type": task_type, "payload": body})
                    envelope_type = classify_task_envelope({
                        "task_type": task_type,
                        "payload": {
                            **body,
                            "envelope_type": body.get("envelope_type", body.get("kind", "generic_task")),
                            "text": query,
                        },
                    })
                except RuntimeGuardError as exc:
                    return self._json(403 if "financial_authorization" in str(exc) or "human_authorization" in str(exc) else 400, {"error": str(exc)})
                if envelope_type == "a2a_message":
                    return self._json(503, {
                        "error": "Le pipeline A2A canonique n'est pas disponible; aucun fallback générique n'a été exécuté.",
                        "envelope_type": "a2a_message",
                    })
                if not query:
                    return self._json(400, {"error": "Le message est vide"})
                if task_type not in {"cognitive_chat", "chat", "general_chat"}:
                    try:
                        resolve_route(task_type)
                    except UnknownTaskType as exc:
                        return self._json(400, {"error": str(exc), "available_task_types": sorted(ROUTES)})
                task_key = str(uuid.uuid4())
                try:
                    url, headers = self._supabase()
                except RuntimeError:
                    # Un type de tâche spécialisé ne doit jamais être exécuté par un autre fournisseur en mode dégradé.
                    if task_type not in {"cognitive_chat", "chat", "general_chat"}:
                        return self._json(503, {
                            "error": "Les tâches spécialisées nécessitent le worker Supabase; aucune substitution de fournisseur n'a été effectuée.",
                            "task_type": task_type,
                        })
                    fallback = asyncio.run(CognitiveBrain().ask_gemini(query, []))
                    if fallback:
                        return self._json(200, {"answer": fallback, "mode": "gemini_fallback"})
                    return self._json(503, {"error": "Supabase n'est pas configuré et Gemini n'est pas disponible."})
                task = {
                    "task_key": task_key,
                    "task_type": task_type,
                    "status": "queued",
                    "department_id": str(body.get("department_id", ""))[:80] or None,
                    "strategy_id": str(body.get("strategy_id", ""))[:120] or None,
                    "mission_id": str(body.get("mission_id", ""))[:100] or None,
                    "payload": {
                        "envelope_type": envelope_type,
                        "text": query,
                        "email": email,
                        "source": "verox_dashboard",
                        "department_id": str(body.get("department_id", ""))[:80] or None,
                        "strategy_id": str(body.get("strategy_id", ""))[:120] or None,
                        "mission_id": str(body.get("mission_id", ""))[:100] or None,
                    },
                }
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(url + "/rest/v1/nexus_tasks",
                        json=task, headers={**headers, "Prefer": "return=representation"})
                    response.raise_for_status()
                rows = response.json()
                if not rows:
                    return self._json(502, {"error": "Supabase n'a pas retourné la tâche"})
                return self._json(202, {"id": rows[0]["id"], "status": rows[0]["status"]})
            except Exception as exc:
                logger.exception("Impossible de transmettre la requête du dashboard")
                return self._json(503, {"error": str(exc)[:300]})

    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "10000"))), DashboardHandler).serve_forever()


def _worker_concurrency(value: str | None = None) -> int:
    """Bound worker parallelism to protect external services and database resources."""
    raw = os.getenv("VEROX_WORKER_CONCURRENCY", "3") if value is None else value
    try:
        return max(1, min(8, int(raw)))
    except (TypeError, ValueError):
        return 3


async def run_server():
    dashboard_task = asyncio.create_task(asyncio.to_thread(run_dashboard))
    worker_count = _worker_concurrency()
    logger.info("Démarrage de %s workers asynchrones bornés.", worker_count)
    workers = [asyncio.create_task(run_worker(worker_id=index)) for index in range(worker_count)]
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            worker.cancel()
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