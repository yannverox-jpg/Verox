from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_financial_bypass.py"
SPEC = importlib.util.spec_from_file_location("financial_bypass_audit", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_direct_unguarded_side_effect_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "broker.py"
    source.write_text(
        "def submit():\n    broker.place_order(symbol='EURUSD')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(AUDIT, "ROOT", tmp_path)
    findings = AUDIT.audit_file(source)
    assert len(findings) == 1
    assert "place_order" in findings[0]
    assert "submit" in findings[0]


def test_guard_in_nested_function_does_not_hide_unguarded_parent(tmp_path, monkeypatch):
    source = tmp_path / "broker.py"
    source.write_text(
        "def submit():\n"
        "    def safe_helper():\n"
        "        execute_financial_action_guarded()\n"
        "    broker.place_order(symbol='EURUSD')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(AUDIT, "ROOT", tmp_path)
    findings = AUDIT.audit_file(source)
    assert any("submit" in item and "place_order" in item for item in findings)


def test_guard_and_side_effect_in_same_scope_is_not_flagged(tmp_path, monkeypatch):
    source = tmp_path / "broker.py"
    source.write_text(
        "def submit():\n"
        "    execute_financial_action_guarded()\n"
        "    broker.place_order(symbol='EURUSD')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(AUDIT, "ROOT", tmp_path)
    assert AUDIT.audit_file(source) == []


def test_parse_error_is_audit_failure_not_silent_pass(tmp_path):
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    findings = AUDIT.audit_file(source)
    assert findings and "unable to audit" in findings[0]
