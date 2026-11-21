"""Read-only structural profiling of a rendered public page.

This is a reverse-engineering inventory of visible/public structure, not an
attempt to defeat access controls. It does not execute JavaScript, submit forms,
click controls, inspect authenticated sessions, or infer hidden APIs.
"""
from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

PRICE_RE = re.compile(r"(?:[$€£]\s?\d[\d,.]*|\d[\d,.]*\s?(?:EUR|USD|GBP|€|\$|£))", re.I)
CTA_TERMS = ("start", "join", "apply", "earn", "reward", "price", "pricing", "subscribe",
             "register", "sign up", "get started", "commission", "fee", "payout", "withdraw")


def _clean(value: str, limit: int = 180) -> str:
    return " ".join((value or "").split())[:limit]


class _VisibleStructure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.counts = Counter()
        self.labels: list[str] = []
        self.text_parts: list[str] = []
        self._capture_depth = 0
        self._capture_tags: list[str] = []
        self.form_methods: list[str] = []
        self.link_hosts: set[str] = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"a", "button", "form", "input", "select", "textarea", "table", "tr", "th", "td"} or re.fullmatch(r"h[1-6]", tag):
            self.counts[tag] += 1
        if tag == "form":
            self.form_methods.append(str(attrs.get("method", "get")).lower())
        if tag == "a" and attrs.get("href"):
            parsed = urlparse(str(attrs["href"]))
            if parsed.hostname:
                self.link_hosts.add(parsed.hostname.lower())
        if tag in {"a", "button", "label", "option"} or re.fullmatch(r"h[1-6]", tag):
            self._capture_depth += 1
            self._capture_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self._capture_tags:
            index = len(self._capture_tags) - 1 - self._capture_tags[::-1].index(tag)
            self._capture_tags = self._capture_tags[:index]
            self._capture_depth = len(self._capture_tags)

    def handle_data(self, data):
        value = _clean(data)
        if not value:
            return
        self.text_parts.append(value)
        if self._capture_depth and len(self.labels) < 500:
            self.labels.append(value)


def profile_public_page(*, url: str, title: str, html: str, text: str,
                        http_status: int | None, captured_at: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only credential-free HTTPS page URLs can be profiled")
    parser = _VisibleStructure()
    parser.feed(str(html)[:500_000])
    visible_text = _clean(str(text), 18_000)
    labels = list(dict.fromkeys(_clean(x) for x in parser.labels if _clean(x)))[:200]
    lower_labels = [label.lower() for label in labels]
    cta_signals = sorted({term for term in CTA_TERMS if any(term in label for label in lower_labels)})
    prices = list(dict.fromkeys(PRICE_RE.findall(visible_text)))[:100]
    return {
        "schema_version": 1,
        "profile_type": "public_read_only_structural_probe",
        "url": url[:2000],
        "host": parsed.hostname.lower(),
        "title": _clean(title, 300),
        "http_status": http_status,
        "captured_at": captured_at,
        "structure_counts": dict(sorted(parser.counts.items())),
        "form_methods": parser.form_methods[:100],
        "link_hosts": sorted(parser.link_hosts)[:200],
        "visible_labels": labels,
        "commercial_signals": {
            "price_strings_observed": prices,
            "cta_terms_observed": cta_signals,
            "cta_label_count": len(labels),
        },
        "evidence_quality": "direct_public_observation",
        "limitations": [
            "JavaScript is disabled and this probe does not click or submit controls.",
            "Visible prices or calls to action do not establish actual conversion, earnings or profitability.",
            "This profile is a snapshot; compare repeated captures and independent sources before inferring behavior.",
        ],
        "external_actions_performed": False,
    }
