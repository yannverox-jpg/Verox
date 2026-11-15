"""Read-only inventory of local reverse-engineering utilities; installs nothing."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "integrations" / "readonly_tools.json"


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    results = []
    for item in data.get("tools", []):
        found = next((shutil.which(name) for name in item.get("executable_candidates", []) if shutil.which(name)), None)
        version = None
        if found:
            try:
                proc = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=3, check=False)
                version = (proc.stdout or proc.stderr).strip().splitlines()[:1]
            except (OSError, subprocess.TimeoutExpired):
                version = ["version probe unavailable"]
        results.append({
            "id": item["id"], "label": item["label"],
            "installed": bool(found), "executable": found,
            "version": version[0] if version else None,
            "connection_status": "not_connected",
            "mode": "read_only",
        })
    print(json.dumps({"policy": "read_only", "tools": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
