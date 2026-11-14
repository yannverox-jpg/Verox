"""Standalone demonstration entry point for the CognitiveBrain core.

Run: python cognitive_brain_main.py
Optional persistent store path: set COGNITIVE_BRAIN_DB.
This does not alter or start the existing Verox dashboard/server.
"""
import os

from cognitive_brain import CognitiveBrain, Objective, Observation


def main() -> None:
    brain = CognitiveBrain(os.getenv("COGNITIVE_BRAIN_DB", "data/cognitive_brain.sqlite3"))
    objective = Objective(
        description="Investigate and reduce data-processing latency",
        success_criteria={"metric": "processing_latency_ms", "direction": "lower"},
        constraints={"max_experiment_cost": 0.3, "external_side_effects": False},
    )
    observation = Observation(
        content="Observed possible latency in data processing; measurement is not yet verified.",
        source="initial_demo",
        source_type="unspecified",
    )
    result = brain.cognitive_cycle(objective, observation)
    print(f"Cycle enregistré (événement #{result['event_id']}).")
    print(f"Hypothèses proposées : {len(result['hypotheses'])}")
    print("Aucune expérience ni capacité n'est déclarée réussie sans mesure réelle.")


if __name__ == "__main__":
    main()
