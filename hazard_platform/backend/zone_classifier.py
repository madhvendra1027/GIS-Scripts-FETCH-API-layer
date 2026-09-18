"""zone_classifier.py — turns the 4 hazard scores into a single zone color.

Zone color is the WORST of the 4 scores, not an average: a zone with one
severe hazard is still unsafe even if the other three are low.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from data_pipeline.models import HazardType
from ml_service.inference.predictor import ScoreResult

RED_THRESHOLD = 0.7
YELLOW_THRESHOLD = 0.4


class ZoneColor(str, Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


@dataclass
class ZoneClassification:
    zone_id: str
    color: ZoneColor
    worst_hazard: HazardType
    worst_score: float
    scores: dict[HazardType, float]


def classify_zone(zone_id: str, scores: dict[HazardType, ScoreResult]) -> ZoneClassification:
    worst_hazard, worst_result = max(scores.items(), key=lambda kv: kv[1].score)
    worst_score = worst_result.score

    if worst_score >= RED_THRESHOLD:
        color = ZoneColor.RED
    elif worst_score >= YELLOW_THRESHOLD:
        color = ZoneColor.YELLOW
    else:
        color = ZoneColor.GREEN

    return ZoneClassification(
        zone_id=zone_id,
        color=color,
        worst_hazard=worst_hazard,
        worst_score=worst_score,
        scores={h: r.score for h, r in scores.items()},
    )
