"""Smoke-test de production Verox. Le jeton est lu depuis l'environnement, jamais affiché."""
import argparse
import os
import sys
import time
import uuid
from typing import Any, Dict

import httpx

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
HOLD_STATES = {"needs_approval"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Verox aligné sur le contrat de main.py")
    parser.add_argument("--base-url", default=os.getenv("VEROX_BASE_URL", "https://verox-juqt.onrender.com").rstrip("/"))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    token = os.getenv("VEROX_MARKET_API_TOKEN", "").strip()

    if not args.base_url:
        print("Erreur : définir VEROX_BASE_URL ou fournir --base-url.", file=sys.stderr)
        return 2
    if not token:
        print("Erreur : définir VEROX_MARKET_API_TOKEN dans l'environnement; ne pas coller le jeton dans le code.", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.poll_interval <= 0:
        print("Erreur : timeout et poll-interval doivent être positifs.", file=sys.stderr)
        return 2

    auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    run_id = uuid.uuid4().hex[:10]
    idempotency_key = f"smoke-{run_id}"
    mission_id = f"mission-{run_id}"
    submit_payload: Dict[str, Any] = {
        "query": f"Smoke test Verox : vérifier le circuit asynchrone ({run_id})",
        "task_type": "cognitive_chat",
        "idempotency_key": idempotency_key,
        "mission_id": mission_id,
    }
    spawn_payload = {
        "query": f"Vérifier le routage éphémère ({run_id})",
        "mission_id": mission_id,
        "count": 2,
        "ttl_seconds": 60,
    }
    retire_payload = {"mission_id": mission_id}
    spawned = False
    task_id = None
    failures = []

    with httpx.Client(base_url=args.base_url, timeout=15.0) as client:
        print("[1/6] Vérification du catalogue public et des instances authentifiées.")
        products = client.get("/api/market/products")
        products.raise_for_status()
        print(f"  products: HTTP {products.status_code}")
        instances = client.get("/api/market/instances", headers=auth_headers)
        instances.raise_for_status()
        print(f"  instances: HTTP {instances.status_code}")

        print("[2/6] Vérification du refus sans jeton.")
        unauth = client.post("/api/market/submit", json=submit_payload)
        if unauth.status_code not in (401, 403):
            failures.append(f"auth sans jeton : HTTP {unauth.status_code}, attendu 401/403")
            print("  ÉCHEC : " + failures[-1], file=sys.stderr)
        else:
            print(f"  refus attendu : HTTP {unauth.status_code}")

        print("[3/6] Soumission authentifiée et mesure de latence HTTP.")
        start = time.perf_counter()
        first = client.post("/api/market/submit", headers=auth_headers, json=submit_payload)
        submit_latency_ms = (time.perf_counter() - start) * 1000
        first.raise_for_status()
        body1 = first.json()
        task_id = body1.get("id") or body1.get("task_id")
        if not task_id:
            print(f"Erreur : identifiant absent de la réponse (champs affichés sans secret) : {body1}", file=sys.stderr)
            return 1
        print(f"  id={task_id}; latence soumission={submit_latency_ms:.1f} ms; status={body1.get('status')}")

        print("[4/6] Rejeu avec la même idempotency_key.")
        start = time.perf_counter()
        replay = client.post("/api/market/submit", headers=auth_headers, json=submit_payload)
        replay_latency_ms = (time.perf_counter() - start) * 1000
        replay.raise_for_status()
        body2 = replay.json()
        task_id2 = body2.get("id") or body2.get("task_id")
        if task_id2 != task_id:
            failures.append(f"idempotence : identifiants différents ({task_id} vs {task_id2})")
            print("  ÉCHEC : " + failures[-1], file=sys.stderr)
        else:
            print(f"  même id confirmé; latence du rejeu={replay_latency_ms:.1f} ms; deduplicated={body2.get('deduplicated')}")

        print(f"[5/6] Suivi de GET /api/tasks/{task_id}.")
        poll_start = time.perf_counter()
        last_status = None
        final_body = None
        while time.perf_counter() - poll_start < args.timeout:
            task_response = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
            task_response.raise_for_status()
            task_data = task_response.json()
            status = str(task_data.get("status") or "").lower()
            if status != last_status:
                print(f"  status={status or 'inconnu'}")
                last_status = status
            if status in HOLD_STATES:
                # This is a hold state, not a successful terminal state.
                pass
            if status in TERMINAL_STATES:
                final_body = task_data
                break
            time.sleep(args.poll_interval)

        observed_elapsed = time.perf_counter() - poll_start
        if final_body is None:
            failures.append(f"tâche non terminale après {args.timeout:.1f}s")
            print("  TIMEOUT : " + failures[-1], file=sys.stderr)
        else:
            final_status = str(final_body.get("status", "")).lower()
            print(f"  état final={final_status}; temps total observé={observed_elapsed:.2f}s; error={final_body.get('error')}")
            if final_status != "succeeded":
                failures.append(f"exécution non réussie : état final {final_status}")

        print(f"[6/6] Cycle de vie éphémère pour {mission_id}.")
        try:
            spawned_response = client.post("/api/market/spawn", headers=auth_headers, json=spawn_payload)
            spawned_response.raise_for_status()
            spawned = True
            spawned_body = spawned_response.json()
            print(f"  spawn: HTTP {spawned_response.status_code}; instances créées={spawned_body.get('count', 'inconnu')}")
            active_response = client.get("/api/market/instances", headers=auth_headers)
            active_response.raise_for_status()
            active_body = active_response.json()
            print(f"  instances actives retournées={active_body.get('count', 'inconnu')}")
            if not any(item.get("mission_id") == mission_id for item in active_body.get("instances", [])):
                failures.append("instances créées non visibles dans la liste active")
        except Exception as exc:
            failures.append(f"cycle spawn/instances : {type(exc).__name__}")
            print("  ÉCHEC du cycle spawn/instances.", file=sys.stderr)
        finally:
            if spawned:
                try:
                    retired = client.post("/api/market/retire", headers=auth_headers, json=retire_payload)
                    retired.raise_for_status()
                    print(f"  retire: HTTP {retired.status_code}; {retired.json().get('retired_count', 'inconnu')} instance(s) retirée(s)")
                except Exception as exc:
                    failures.append(f"nettoyage des agents éphémères : {type(exc).__name__}")
                    print("  AVERTISSEMENT : nettoyage explicite non confirmé; les instances expirent au plus tard après leur TTL.", file=sys.stderr)

    if failures:
        print("\nSMOKE-TEST EN ÉCHEC :")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nSMOKE-TEST RÉUSSI : authentification, idempotence, état terminal et cycle éphémère validés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
