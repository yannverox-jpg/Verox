"""Deterministic quality gates for read-only browser observations."""
from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MIN_CHARS = 250
MAX_TEXT_CHARS = 10_000
BLOCK_PATTERNS = (
    r"access denied", r"\bforbidden\b", r"\bcaptcha\b",
    r"verify you are human", r"checking your browser", r"just a moment",
    r"attention required", r"unusual traffic", r"bot protection",
    r"cf-browser-verification", r"enable javascript and cookies to continue",
    r"request blocked", r"temporarily blocked",
)
BLOCK_RE = re.compile("|".join(BLOCK_PATTERNS), re.IGNORECASE)

# Thresholds are conservative defaults. Source-specific rules may be added without
# weakening the HTTP-status and anti-bot checks.
SOURCE_MIN_CHARS = {
    "coingecko": 400,
    "tradingeconomics": 350,
    "tiktok_creative": 300,
    "meta_ads_library": 300,
    "google_trends": 200,
    "soccerstats": 250,
    "forexfactory": 250,
    "geny": 250,
    "letrot": 250,
    "francegalop": 250,
}

@dataclass(frozen=True)
class ObservationValidation:
    status: str
    reason: str
    clean_text: str
    cooldown_minutes: int = 0

def validate_observation(
    source_id: str,
    http_status: int | None,
    title: str | None,
    raw_text: str | None,
) -> ObservationValidation:
    """Classify captured content before any model call."""
    clean_text = re.sub(r"\s+", " ", raw_text or "").strip()
    clean_title = re.sub(r"\s+", " ", title or "").strip()
    min_chars = SOURCE_MIN_CHARS.get(source_id, DEFAULT_MIN_CHARS)

    if http_status is not None and (
        http_status in {401, 403, 407, 429} or http_status >= 500
    ):
        cooldown = 240 if source_id in {"meta_ads_library", "tiktok_creative"} else (
            180 if source_id == "tradingeconomics" else
            120 if source_id == "coingecko" else 60
        )
        return ObservationValidation("blocked_http", f"http_status_{http_status}", clean_text[:MAX_TEXT_CHARS], cooldown)

    haystack = f"{clean_title} {clean_text[:2000]}"
    if BLOCK_RE.search(haystack) and len(clean_text) < 1500:
        cooldown = 240 if source_id in {"meta_ads_library", "tiktok_creative"} else (
            180 if source_id == "tradingeconomics" else
            120 if source_id == "coingecko" else 60
        )
        return ObservationValidation("blocked_anti_bot", "anti_bot_or_access_wall_signature", clean_text[:MAX_TEXT_CHARS], cooldown)

    if len(clean_text) < min_chars:
        return ObservationValidation("empty_content", f"text_too_short_{len(clean_text)}_chars_min_{min_chars}", clean_text[:MAX_TEXT_CHARS])

    return ObservationValidation("collected_valid", "passed_quality_checks", clean_text[:MAX_TEXT_CHARS])
