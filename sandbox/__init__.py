"""Verox Sandbox: isolated, offline-first research and paper-simulation tools."""

from .backtesting import HistoricalForecast, evaluate_forecasts, walk_forward_evaluate
from .history_db import HistoricalLibraryDB
from .ingestion import DataIngestionEngine
from .orchestrator import VeroxOrchestrator
from .engine import (
    MarketObservation,
    OddsObservation,
    PaperLedger,
    compare_platform_odds,
    evaluate_binary_market,
    summarize_binary_forecasts,
)

__all__ = [
    "DataIngestionEngine",
    "HistoricalLibraryDB",
    "HistoricalForecast",
    "MarketObservation",
    "OddsObservation",
    "PaperLedger",
    "VeroxOrchestrator",
    "compare_platform_odds",
    "evaluate_binary_market",
    "evaluate_forecasts",
    "summarize_binary_forecasts",
    "walk_forward_evaluate",
]
