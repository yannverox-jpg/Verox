"""Conservative source assessment with explicit provenance."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceAssessment:
    source: str
    source_type: str
    baseline_weight: float
    verification_status: str
    notes: str


class SourceEvaluator:
    """A starting heuristic, not a truth detector or automatic reputation system."""
    DEFAULT_WEIGHTS = {
        "regulatory": 0.95,
        "scientific_paper": 0.90,
        "financial_report": 0.85,
        "reputable_media": 0.65,
        "social_media": 0.30,
        "forum_unverified": 0.15,
        "unspecified": 0.25,
    }

    def assess(self, source: str, source_type: str, independently_verified: bool = False) -> SourceAssessment:
        weight = self.DEFAULT_WEIGHTS.get(source_type, self.DEFAULT_WEIGHTS["unspecified"])
        return SourceAssessment(
            source=source,
            source_type=source_type,
            baseline_weight=weight,
            verification_status="independently_verified" if independently_verified else "unverified",
            notes=("Corroboration recorded; inspect the underlying evidence."
                   if independently_verified else
                   "Prior weight is only a heuristic; corroborate important claims independently."),
        )
