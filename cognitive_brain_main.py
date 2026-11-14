"""Standalone demonstration entry point for the CognitiveBrain core.

Run: python cognitive_brain_main.py
Optional persistent store: COGNITIVE_BRAIN_DB.
Optional OpenAI-compatible code-generation endpoint:
COGNITIVE_BRAIN_LLM_BASE_URL, COGNITIVE_BRAIN_LLM_MODEL, and optionally
COGNITIVE_BRAIN_LLM_API_KEY. No endpoint is contacted unless synthesis is requested.
This does not alter or start the existing Verox dashboard/server.
"""
import os

from cognitive_brain import CognitiveBrain, Objective, Observation


def main() -> None:
    brain = CognitiveBrain(os.getenv("COGNITIVE_BRAIN_DB", "data/cognitive_brain.sqlite3"))
    objective = Objective(
        description="Investigate and reduce data-processing latency",
        success_criteria={"metric": "processing_latency_ms", "direction": "lower"},
        constraints={"max_experiment_cost": 0.3, "max_steps": 5, "external_side_effects": False},
    )
    observation = Observation(
        content="Observed possible latency in data processing; measurement is not yet verified.",
        source="initial_demo",
        source_type="unspecified",
    )
    result = brain.cognitive_cycle(objective, observation)
    print(f"Cycle enregistré (événement #{result['event_id']}).")
    print(f"Hypothèses proposées : {len(result['hypotheses'])}")
    if result["selected_hypothesis"]:
        print(f"Stratégie prioritaire proposée : {result['selected_hypothesis']['statement']}")
    print("Aucune expérience n'est déclarée réussie sans mesure réelle.")

    synthesis = brain.ensure_capability_or_design(
        "text_length",
        "Create a pure function text_length(value) returning the length of a string.",
    )
    print(f"Synthèse d'outil : {synthesis['status']}")
    if synthesis["status"] == "draft_validated":
        tests = brain.tool_synthesizer.validate_in_sandbox("text_length", [
            {"args": ["Verox"], "expected": 5},
            {"args": [""], "expected": 0},
        ])
        print(f"Validation isolée : {tests['tests_passed']}/{tests['tests_run']} tests réussis.")
    elif synthesis["status"] == "blocked_or_failed":
        print("Aucune capacité n'a été déclarée disponible : génération ou validation manquante.")


if __name__ == "__main__":
    main()
