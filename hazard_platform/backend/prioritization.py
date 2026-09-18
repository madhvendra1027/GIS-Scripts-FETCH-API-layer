"""prioritization.py — combines zone classification with population
vulnerability and disaster history to produce a relocation priority tier.

This is where the problem statement's "population vulnerability" and
"disaster history" factors are actually used — deliberately kept out of the
hazard scores themselves (see ml_service/inference/predictor.py), so a
zone's color reflects pure hazard risk, and prioritization reflects who's
most urgently affected by that risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.zone_classifier import ZoneClassification, ZoneColor
from ml_service.weighting import weight_resolver


class PriorityTier(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    SHORT_TERM = "SHORT_TERM"
    MEDIUM_TERM = "MEDIUM_TERM"
    NONE = "NONE"  # not in a red/yellow zone; no relocation need identified


@dataclass
class VulnerabilityInputs:
    """Population vulnerability and disaster history, per zone.

    These are placeholders for whatever population/census ingestion is
    built later (not yet part of gis_fetcher) — kept as simple 0-1 inputs
    here so this module doesn't need to change once that data source exists.
    """

    population_density_score: float  # 0-1, already normalized upstream
    socioeconomic_vulnerability_score: float  # 0-1, higher = more vulnerable
    disaster_history_score: float  # 0-1, frequency/severity of past events


@dataclass
class PrioritizationResult:
    zone_id: str
    priority: PriorityTier
    priority_score: float
    zone_color: ZoneColor
    worst_hazard_score: float


# Was a hand-set dict ({"hazard": 0.5, "population": 0.2, "socioeconomic":
# 0.15, "history": 0.15}, carried over from an earlier session — see
# decisions-and-learnings.md item 6). Now resolved the same way every
# hazard scorer's WEIGHTS already are: from a CR-checked AHP pairwise
# judgment file (ml_service/weighting/judgments/prioritization.json), via
# weight_resolver.py. "prioritization" isn't a hazard, but it needed the
# identical "no fabricated decimals, judgments-in/weights-out" treatment,
# so weight_resolver.KNOWN_HAZARDS now includes it alongside the four real
# hazards. The resolved weights (~0.49/0.23/0.14/0.14) approximate the old
# 0.50/0.20/0.15/0.15 split without being a re-typed copy of it — see the
# judgment file's _meta.note for why an exact match isn't the point.
WEIGHTS = weight_resolver.get_weights("prioritization")


def prioritize(
    classification: ZoneClassification,
    vulnerability: VulnerabilityInputs,
) -> PrioritizationResult:
    if classification.color == ZoneColor.GREEN:
        return PrioritizationResult(
            zone_id=classification.zone_id,
            priority=PriorityTier.NONE,
            priority_score=0.0,
            zone_color=classification.color,
            worst_hazard_score=classification.worst_score,
        )

    priority_score = min(max(
        WEIGHTS["hazard"] * classification.worst_score
        + WEIGHTS["population"] * vulnerability.population_density_score
        + WEIGHTS["socioeconomic"] * vulnerability.socioeconomic_vulnerability_score
        + WEIGHTS["history"] * vulnerability.disaster_history_score,
        0.0,
    ), 1.0)

    if classification.color == ZoneColor.RED and priority_score >= 0.7:
        tier = PriorityTier.IMMEDIATE
    elif priority_score >= 0.45:
        tier = PriorityTier.SHORT_TERM
    else:
        tier = PriorityTier.MEDIUM_TERM

    return PrioritizationResult(
        zone_id=classification.zone_id,
        priority=tier,
        priority_score=priority_score,
        zone_color=classification.color,
        worst_hazard_score=classification.worst_score,
    )
