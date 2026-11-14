"""Executable CLI for Verox CognitiveBrain; does not start or modify the dashboard.

Examples:
  python cognitive_brain_main.py --inspect
  python cognitive_brain_main.py --investigate "distributed system latency"
  python cognitive_brain_main.py --cycle --objective "Reduce latency" --observation "p95 is high"
  python cognitive_brain_main.py --synthesize-tool text_length --tool-spec "Return string length" --tool-tests '[{"args":["Verox"],"expected":5}]'

Persistent store: COGNITIVE_BRAIN_DB (default data/cognitive_brain.sqlite3).
Optional planner: COGNITIVE_BRAIN_PLANNER_BASE_URL / MODEL / API_KEY, or shared
COGNITIVE_BRAIN_LLM_* settings. Optional code generator uses COGNITIVE_BRAIN_LLM_*.
No endpoint is contacted unless its related operation is explicitly invoked.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from cognitive_brain import CognitiveBrain, Objective, Observation


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verox CognitiveBrain command-line interface")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--inspect", action="store_true", help="Audit runtime, libraries, tools, and providers")
    actions.add_argument("--investigate", metavar="QUERY", help="Search configured public research providers")
    actions.add_argument("--cycle", action="store_true", help="Run one objective-directed planning cycle")
    actions.add_argument("--synthesize-tool", metavar="NAME", help="Generate and validate a pure Python tool")
    parser.add_argument("--objective", default="Investigate and reduce data-processing latency")
    parser.add_argument("--observation", default="Observed possible latency; measurement is not verified.")
    parser.add_argument("--source", default="cli")
    parser.add_argument("--source-type", default="unspecified")
    parser.add_argument("--library-query", default="")
    parser.add_argument("--library-limit", type=int, default=100)
    parser.add_argument("--tool-spec", default="")
    parser.add_argument("--tool-tests", default="",
                        help="JSON array of explicit test cases, e.g. '[{\"args\":[\"abc\"],\"expected\":3}]'")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    brain = CognitiveBrain(os.getenv("COGNITIVE_BRAIN_DB", "data/cognitive_brain.sqlite3"))

    if args.inspect:
        _print_json(brain.inspect_capabilities(
            library_query=args.library_query, library_limit=args.library_limit
        ))
        return 0

    if args.investigate is not None:
        providers = brain.enable_public_research()
        if not providers:
            print("Aucun fournisseur de recherche n'est configuré.", file=sys.stderr)
            return 2
        _print_json(asyncio.run(brain.investigate(args.investigate)))
        return 0

    if args.cycle:
        objective = Objective(
            description=args.objective,
            success_criteria={"metric": "processing_latency_ms", "direction": "lower"},
            constraints={"max_experiment_cost": 0.3, "max_steps": 5,
                         "external_side_effects": False},
        )
        observation = Observation(
            content=args.observation, source=args.source, source_type=args.source_type,
        )
        _print_json(brain.cognitive_cycle(objective, observation))
        return 0

    if args.synthesize_tool:
        if not args.tool_spec.strip():
            print("--tool-spec est obligatoire avec --synthesize-tool.", file=sys.stderr)
            return 2
        test_cases = None
        if args.tool_tests:
            try:
                test_cases = json.loads(args.tool_tests)
            except json.JSONDecodeError as exc:
                print(f"--tool-tests JSON invalide : {exc}", file=sys.stderr)
                return 2
            if not isinstance(test_cases, list):
                print("--tool-tests doit être un tableau JSON.", file=sys.stderr)
                return 2
        result = brain.ensure_capability_or_design(
            args.synthesize_tool, args.tool_spec, test_cases=test_cases
        )
        _print_json(result)
        return 0 if result.get("status") == "available" else 2

    # Default safe demo: no network call and no tool execution.
    objective = Objective(
        description=args.objective,
        success_criteria={"metric": "processing_latency_ms", "direction": "lower"},
        constraints={"max_experiment_cost": 0.3, "max_steps": 5, "external_side_effects": False},
    )
    observation = Observation(
        content=args.observation, source=args.source, source_type=args.source_type,
    )
    result = brain.cognitive_cycle(objective, observation)
    print(f"Cycle enregistré (événement #{result['event_id']}).")
    print(f"Hypothèses proposées : {len(result['hypotheses'])}")
    if result["selected_hypothesis"]:
        print(f"Stratégie prioritaire proposée : {result['selected_hypothesis']['statement']}")
    print(f"Moteur de planification : {result['planner_status']}")
    print("Aucune expérience n'est déclarée réussie sans mesure réelle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
