from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from verox_brain.recon import inspect_repository

class RepositoryReconTests(unittest.TestCase):
    def test_inventory_finds_imports_routes_env_and_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api").mkdir()
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / "requirements.txt").write_text("httpx>=0.27\n", encoding="utf-8")
            (root / "api" / "service.py").write_text(
                "import httpx\nimport missing_distribution\nimport os\n"
                "TOKEN = os.getenv('VEROX_TOKEN')\n"
                "@app.get('/health')\ndef health(): return {'ok': True}\n",
                encoding="utf-8",
            )
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "steps:\n  - run: python -m api.service\n", encoding="utf-8"
            )
            report = inspect_repository(root)
            self.assertEqual(report["counts"]["python_files"], 1)
            self.assertIn({"file": "api/service.py", "method": "GET", "path": "/health"}, report["routes"])
            self.assertIn("VEROX_TOKEN", report["environment_variables"])
            self.assertIn("missing_distribution", report["possibly_undeclared_dependencies"])
            self.assertEqual(report["parse_errors"], [])

    def test_parse_errors_are_reported_without_importing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            report = inspect_repository(root)
            self.assertEqual(report["counts"]["parse_errors"], 1)
            self.assertEqual(report["parse_errors"][0]["file"], "bad.py")

if __name__ == "__main__":
    unittest.main()
