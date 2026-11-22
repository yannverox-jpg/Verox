from __future__ import annotations

import io
from typing import Any

import pytest

from verox_brain import opportunity_engine
from verox_brain.server_proof_recorder import ProofIntegrityError, VerifiedServerEvidence
from verox_brain.opportunity_engine import Feed


RSS_XML = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title><item>
<title>Automation contract opportunity</title>
<link>https://example.org/items/101</link>
<description>Public integration and automation notice.</description>
</item></channel></rss>"""


class FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self._url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _limit: int = -1) -> bytes:
        return self._body


class FakeDataPlane:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def save_observation(self, **kwargs: Any) -> dict[str, Any]:
        self.saved.append(kwargs)
        # The scanner requires a non-null proof result from the data plane.
        return {"verified_evidence": object(), "verification_status": "unverified"}


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    url = "https://example.org/feed.xml"

    def fake_urlopen(request: Any, timeout: int = 0) -> FakeResponse:
        requested_url = request.full_url
        assert requested_url == url
        return FakeResponse(RSS_XML, requested_url)

    monkeypatch.setattr(opportunity_engine.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        opportunity_engine,
        "scan_world_bank_procurement",
        lambda timeout=opportunity_engine.DEFAULT_TIMEOUT_SECONDS: ([], None),
    )
    return Feed(
        name="Test feed",
        url=url,
        department="microtasks_services",
        keywords=("automation", "contract"),
    )


def test_scan_feeds_proof_mode_passes_real_run_and_registered_source(
    monkeypatch: pytest.MonkeyPatch, feed: Feed
) -> None:
    monkeypatch.setenv("VEROX_WORLD_BANK_PROCUREMENT_ENABLED", "false")
    plane = FakeDataPlane()
    source_id = "source-test-001"

    result = opportunity_engine.scan_feeds(
        feeds=[feed],
        run_id="run-test-001",
        data_plane=plane,
        source_ids_by_feed_url={feed.url: source_id},
    )

    assert result["proof_mode"] is True
    assert result["proofs_recorded"] == 1
    assert result["leads_detected"] == 1
    assert len(plane.saved) == 1
    saved = plane.saved[0]
    assert saved["source_id"] == source_id
    assert saved["proof_context"].run_id == "run-test-001"
    assert saved["proof_context"].evidence_type == "rss_feed"
    assert [(c.check_name, c.passed) for c in saved["proof_context"].executed_checks] == [
        ("rss_xml_parsed", True)
    ]


def test_scan_feeds_proof_mode_fails_closed_for_unmapped_source(
    monkeypatch: pytest.MonkeyPatch, feed: Feed
) -> None:
    monkeypatch.setenv("VEROX_WORLD_BANK_PROCUREMENT_ENABLED", "false")
    plane = FakeDataPlane()

    with pytest.raises(ProofIntegrityError, match="No registered source_id"):
        opportunity_engine.scan_feeds(
            feeds=[feed],
            run_id="run-test-002",
            data_plane=plane,
            source_ids_by_feed_url={},
        )

    # No observation or proof may be fabricated when the source is unmapped.
    assert plane.saved == []


def test_scan_feeds_legacy_mode_remains_available(
    monkeypatch: pytest.MonkeyPatch, feed: Feed
) -> None:
    monkeypatch.setenv("VEROX_WORLD_BANK_PROCUREMENT_ENABLED", "false")

    result = opportunity_engine.scan_feeds(feeds=[feed])

    assert "proof_mode" not in result
    assert result["leads_detected"] == 1
