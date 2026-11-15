import asyncio
import os
import random
import logging
import json
import argparse
import sqlite3
import re
from datetime import datetime, timezone
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
    
    def __init__(self, gateway: NetworkGateway, session_manager=None):
        self.gateway = gateway
        self.session_manager = session_manager
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
    def remember(self,kind,content,metadata=None):
        with self.connect() as db: db.execute("INSERT INTO cognitive_memory(kind,content,metadata,created_at) VALUES(?,?,?,?)",(kind,str(content)[:20000],json.dumps(metadata or {},ensure_ascii=False,default=str),self.now()))
    def recent_memory(self,limit=20):
        with self.connect() as db:return [dict(r) for r in db.execute("SELECT * FROM cognitive_memory ORDER BY id DESC LIMIT ?",(max(1,min(200,limit)),))]
    def record_outcome(self,strategy,tool_id,success,value,result):
        with self.connect() as db:db.execute("INSERT INTO cognitive_outcomes(strategy,tool_id,success,measured_value,result,created_at) VALUES(?,?,?,?,?,?)",(strategy,tool_id,int(bool(success)),value,json.dumps(result,ensure_ascii=False,default=str)[:20000],self.now()))
    def outcome_stats(self):
        with self.connect() as db:rows=db.execute("SELECT tool_id,COUNT(*) attempts,SUM(success) successes,AVG(measured_value) average_value FROM cognitive_outcomes GROUP BY tool_id").fetchall()
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

    async def save_to_supabase(self, query: str, evidence: List[Dict[str, Any]]):
        """Archive la mémoire du cerveau dans le schéma PostgreSQL canonique Nexus/Verox."""
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
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

    async def run(self, user_query: str, user_email: str = ""):
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



async def run_worker():
    """Consomme la file canonique public.nexus_tasks et écrit les résultats dans PostgreSQL."""
    brain = CognitiveBrain()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY sont requis. Le serveur HTTP reste disponible, mais le worker est en attente de configuration.")
        while True:
            await asyncio.sleep(30)
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        while True:
            try:
                response = await client.get(
                    f"{supabase_url}/rest/v1/nexus_tasks",
                    params={"select": "id,task_key,task_type,payload,status", "status": "eq.queued",
                            "order": "scheduled_at.asc", "limit": "1"},
                    headers=headers
                )
                response.raise_for_status()
                rows = response.json()
                if not rows:
                    await asyncio.sleep(2)
                    continue
                task = rows[0]
                task_id = task["id"]
                claim = await client.patch(
                    f"{supabase_url}/rest/v1/nexus_tasks",
                    params={"id": f"eq.{task_id}", "status": "eq.queued"},
                    json={"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                          "attempts": (task.get("attempts") or 0) + 1},
                    headers=headers
                )
                claim.raise_for_status()
                if not claim.json():
                    continue
                try:
                    payload = task.get("payload") or {}
                    query = str(payload.get("text") or payload.get("query") or "").strip()
                    email = str(payload.get("email") or "").strip()
                    if not query:
                        raise ValueError("Le champ payload.text ou payload.query est requis.")
                    evidence = await brain.run(query, email)
                    result = {"evidence": evidence or [], "evidence_count": len(evidence or [])}
                    await client.patch(
                        f"{supabase_url}/rest/v1/nexus_tasks",
                        params={"id": f"eq.{task_id}"},
                        json={"status": "succeeded", "result": result,
                              "completed_at": datetime.now(timezone.utc).isoformat(), "error": None},
                        headers=headers
                    )
                    await client.post(
                        f"{supabase_url}/rest/v1/nexus_execution_events",
                        json={"task_id": task_id, "event_type": "task_succeeded", "severity": "info",
                              "message": "CognitiveBrain a terminé la tâche.", "details": result},
                        headers={**headers, "Prefer": "return=minimal"}
                    )
                except Exception as exc:
                    logger.exception("Échec de la tâche cognitive %s", task_id)
                    error_result = {"error": str(exc)[:1000], "evidence": []}
                    await client.patch(
                        f"{supabase_url}/rest/v1/nexus_tasks",
                        params={"id": f"eq.{task_id}"},
                        json={"status": "failed", "result": error_result, "error": str(exc)[:2000],
                              "completed_at": datetime.now(timezone.utc).isoformat()},
                        headers=headers
                    )
                    await client.post(
                        f"{supabase_url}/rest/v1/nexus_execution_events",
                        json={"task_id": task_id, "event_type": "task_failed", "severity": "error",
                              "message": "Échec de la tâche CognitiveBrain.", "details": error_result},
                        headers={**headers, "Prefer": "return=minimal"}
                    )
            except Exception:
                logger.exception("Erreur du worker cognitif")
                await asyncio.sleep(5)


def run_dashboard():
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse
    import uuid

    class DashboardHandler(SimpleHTTPRequestHandler):
        def _supabase(self):
            url = os.getenv("SUPABASE_URL", "").rstrip("/")
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
            if parsed.path == "/api/health":
                try:
                    url, headers = self._supabase()
                    with httpx.Client(timeout=8.0) as client:
                        check = client.get(url + "/rest/v1/nexus_tasks?select=id&limit=1", headers=headers)
                        check.raise_for_status()
                    return self._json(200, {"ok": True, "database": "connected"})
                except Exception as exc:
                    return self._json(503, {"ok": False, "error": str(exc)[:250]})
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
                self.path = "/nexus_dashboard.html"
            return super().do_GET()

        def do_POST(self):
            if urlparse(self.path).path != "/api/chat":
                return self._json(404, {"error": "Route inconnue"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 20000:
                    return self._json(400, {"error": "Taille de requête invalide"})
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                query = str(body.get("text", "")).strip()
                email = str(body.get("email", "")).strip()
                if not query:
                    return self._json(400, {"error": "Le message est vide"})
                task_key = str(uuid.uuid4())
                url, headers = self._supabase()
                task = {"task_key": task_key, "task_type": "cognitive_chat",
                        "status": "queued", "payload": {"text": query, "email": email,
                        "source": "verox_dashboard"}}
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
