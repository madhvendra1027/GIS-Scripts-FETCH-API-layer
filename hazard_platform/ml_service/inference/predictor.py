"""predictor.py — the ML/rule scoring service's single entry point.

    predict(features_by_hazard: dict[HazardType, dict]) -> dict[HazardType, ScoreResult]

Every hazard here uses the weighted-formula path, not a trained model — a
deliberate choice, since none of the four hazards currently has defensible
zone-level historical outcome labels to train a real classifier on. Every
score is a documented, inspectable weighted overlay rather than an
unvalidated black box.

Swapping any single hazard to a trained model later means implementing the
same `.score(features) -> ScoreResult` interface on a new class — nothing
else in this file, feature_engineering.py, or anything downstream in
backend/ needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from data_pipeline.models import HazardType
from ml_service.weighting import weight_resolver


def _normalize(value: Optional[float], reference_max: float, invert: bool = False) -> float:
    """Scale a raw value into [0, 1] against a documented reference max.

    invert=True means a HIGHER raw value means LOWER risk (a protective
    factor, e.g. distance from a hazard source). Missing values (None) fall
    back to a neutral 0.5 and are recorded in ScoreResult.missing_fields, so
    a score built on gaps never looks more confident than the data supports.
    """
    if value is None:
        return 0.5
    ratio = min(max(value / reference_max, 0.0), 1.0)
    return (1.0 - ratio) if invert else ratio


@dataclass
class ScoreResult:
    score: float
    missing_fields: list[str]


class FloodScorer:
    """Weighted formula — originally matched the worked FLOOD example
    from the project README exactly; as of this update, `flood_status_severity_code`
    is included (see the module's changelog note below), so every
    original weight has been scaled down by 10% to make room for it
    rather than left as an unrelated add-on. See README.md's worked
    FLOOD example for the recomputed reference numbers.
    """

    # Weights are no longer hand-set here. They're derived automatically
    # from ml_service/weighting/judgments/flood.json via an AHP pairwise
    # comparison (see weight_resolver.py and ahp.py) — resolved once at
    # import time and cached. The comment that used to hard-code each
    # decimal now lives as the *reasoning behind the judgment file's
    # pairwise comparisons* instead: flood_status_severity_code is judged
    # roughly on par with rainfall/river_level_m in that file because
    # Google's model already folds in that river reach's own local
    # flood-stage thresholds, arguably a more direct signal than a bare
    # meters reading with no context. See judgments/flood.json and
    # WEIGHT_JUSTIFICATION.md for the full reasoning and literature
    # comparison; see judgments/README.md to redo this with a real panel.
    WEIGHTS = weight_resolver.get_weights("flood")
    REFERENCE_MAX = {
        "rainfall_mm_24h": 150.0,
        "rainfall_mm_72h": 300.0,
        "river_level_m": 5.0,
        "river_level_change_rate_m_per_hr": 0.5,
        "soil_saturation_pct": 100.0,
        "elevation_m": 50.0,
        "distance_to_river_m": 1000.0,
        "historical_flood_count": 10.0,
    }
    PROTECTIVE = {"elevation_m", "distance_to_river_m"}

    def score(self, features: dict[str, Any]) -> ScoreResult:
        missing = [f for f, v in features.items() if v is None]
        total = sum(
            weight * _normalize(features.get(field), self.REFERENCE_MAX[field], field in self.PROTECTIVE)
            for field, weight in self.WEIGHTS.items()
        )
        return ScoreResult(score=min(max(total, 0.0), 1.0), missing_fields=missing)


class LandslideScorer:
    # All 6 landslide factors — including the categorical soil_type_code,
    # see below — are judged together as one AHP pairwise set in
    # judgments/landslide.json, since AHP weighs "how important is this
    # factor overall", which is unaffected by whether the factor happens
    # to be scaled linearly (_normalize()) or via a lookup table.
    # soil_type_code's share is split out into SOIL_TYPE_WEIGHT below;
    # the remaining 5 stay in WEIGHTS for the linear-normalize path in
    # score(). Both still come from the same resolved, CR-checked vector,
    # so "0.255 for slope" is never re-typed anywhere — see
    # weight_resolver.py.
    _RESOLVED = weight_resolver.get_weights("landslide")
    WEIGHTS = {f: w for f, w in _RESOLVED.items() if f != "soil_type_code"}
    REFERENCE_MAX = {
        "slope_deg": 45.0,
        "rainfall_mm_72h": 300.0,
        "soil_moisture_pct": 100.0,
        "vegetation_index": 1.0,
        "historical_landslide_count": 10.0,
    }
    PROTECTIVE = {"vegetation_index"}
    # soil_type_code is categorical (a USDA texture class 1-12 from
    # gis_fetcher/providers/soil.py — 1=clay ... 12=sand, see that
    # file's `_TEXTURE_ORDER`), not a magnitude, so it can't be
    # min-max normalized like the fields above — it needs a lookup
    # table instead. Turned on this update: soil_type_code has been a
    # reliably live field (ISRIC SoilGrids, no key, global coverage)
    # for a while now, not the sparse/unpopulated case the old
    # SOIL_TYPE_WEIGHT=0.0 comment was guarding against — that
    # reasoning was simply stale.
    #
    # Risk-by-texture below is ranked on landslide-relevant grounds
    # (drainage + cohesion-under-saturation), not measured coefficients
    # — same "coarsely bucketed, good enough to rank" standard soil.py
    # itself claims, stated honestly rather than presented as
    # validated science: clay-rich textures drain poorly and lose shear
    # strength when saturated (highest risk); pure silt is notoriously
    # unstable when wet despite being "coarse" (loess-type failures are
    # well documented, so it is NOT ranked low just because it isn't
    # clay); sand/loamy sand drain well and carry the lowest risk here.
    # This REPLACES the previous SOIL_RISK_BY_CODE, whose comment
    # ("sandy/clay/rocky/loamy/laterite") didn't actually match
    # soil.py's real 1-12 texture codes at all — "rocky" and "laterite"
    # aren't in that scheme, so the old table was labeling the wrong
    # thing regardless of its weight.
    SOIL_RISK_BY_CODE = {
        1: 0.85,   # clay
        2: 0.80,   # silty_clay
        3: 0.65,   # sandy_clay
        4: 0.70,   # clay_loam
        5: 0.65,   # silty_clay_loam
        6: 0.55,   # sandy_clay_loam
        7: 0.45,   # loam
        8: 0.55,   # silty_loam
        9: 0.35,   # sandy_loam
        10: 0.70,  # silt — deliberately NOT low; see note above
        11: 0.25,  # loamy_sand
        12: 0.15,  # sand
    }
    SOIL_TYPE_WEIGHT = _RESOLVED["soil_type_code"]

    def score(self, features: dict[str, Any]) -> ScoreResult:
        missing = [f for f, v in features.items() if v is None]
        total = sum(
            weight * _normalize(features.get(field), self.REFERENCE_MAX[field], field in self.PROTECTIVE)
            for field, weight in self.WEIGHTS.items()
        )
        # A missing soil_type_code now contributes weight * 0.5 (neutral),
        # matching how every other missing field is treated by
        # _normalize()'s own None-handling — previously this branch was
        # skipped entirely on a miss, silently contributing 0 instead of
        # a neutral assumption, which quietly biased the score DOWN
        # whenever soil data happened to be missing. Fixed as part of
        # turning this field on for real.
        soil_code = features.get("soil_type_code")
        soil_risk = self.SOIL_RISK_BY_CODE.get(int(soil_code), 0.5) if soil_code is not None else 0.5
        total += self.SOIL_TYPE_WEIGHT * soil_risk
        return ScoreResult(score=min(max(total, 0.0), 1.0), missing_fields=missing)


class ErosionScorer:
    """Extends the worked EROSION example from the README with
    sediment_type_code (see below) — every original weight scaled down
    by 15% to make room, same approach as FloodScorer. The
    mangrove-cover protective discount is unchanged.
    """

    # Same split as LandslideScorer: sediment_type_code is judged in the
    # same AHP pairwise set (judgments/erosion.json) as the other 4
    # linearly-normalized factors, then pulled out into
    # SEDIMENT_TYPE_WEIGHT below for its lookup-table path.
    # mangrove_cover_pct is NOT part of this AHP set at all — it was
    # never a weighted-sum term to begin with (see §4.3 of the project
    # summary), it's a multiplicative discount applied after raw_score is
    # computed, so "how important is it relative to wave_energy_index"
    # isn't a meaningful AHP question for it.
    _RESOLVED = weight_resolver.get_weights("erosion")
    WEIGHTS = {f: w for f, w in _RESOLVED.items() if f != "sediment_type_code"}
    REFERENCE_MAX = {
        "shoreline_change_rate_m_per_yr": 5.0,
        # NOT a 0-1 index -- gis_fetcher's `marine` provider computes this
        # as (wave_height_m ** 2) * wave_period_s (routinely 10-50+ for an
        # ordinary sea state). A reference_max of 1.0 here meant norm_wave
        # saturated to 1.0 for every zone, all the time -- every zone
        # scored max wave-energy risk regardless of actual conditions.
        # 100.0 puts a rough storm-scale sea state (e.g. 3m/12s -> 108) at
        # full normalized risk, matching cleaning.py's corrected
        # VALID_RANGES/REGIONAL_DEFAULTS for this same field.
        "wave_energy_index": 100.0,
        "distance_to_coast_m": 2000.0,
        "historical_erosion_events": 10.0,
    }
    MANGROVE_PROTECTION_FACTOR = 0.3  # up to 30% risk reduction at 100% cover
    # sediment_type_code was being fetched live (fetch_sediment_type.py,
    # same SoilGrids-texture proxy soil_type_code uses for LANDSLIDE)
    # but was never read anywhere in this scorer at all — not even at
    # zero weight like soil_type_code was. Turned on this update, with
    # its own EROSION-specific risk-by-texture table: the ranking
    # criterion here is grain-size erodibility under wave action, not
    # landslide drainage/cohesion, so it is NOT the same table as
    # LandslideScorer's — sand erodes/transports easily (highest risk
    # here, opposite of its landslide ranking), clay is cohesive and
    # erosion-resistant (lowest risk here). Same "informed ranking, not
    # measured coefficients" honesty standard as LandslideScorer's table.
    SEDIMENT_RISK_BY_CODE = {
        1: 0.20,   # clay
        2: 0.25,   # silty_clay
        3: 0.35,   # sandy_clay
        4: 0.30,   # clay_loam
        5: 0.35,   # silty_clay_loam
        6: 0.45,   # sandy_clay_loam
        7: 0.50,   # loam
        8: 0.55,   # silty_loam
        9: 0.65,   # sandy_loam
        10: 0.60,  # silt — loose, easily suspended/transported
        11: 0.80,  # loamy_sand
        12: 0.90,  # sand — classic easily-eroded beach/dune sediment
    }
    SEDIMENT_TYPE_WEIGHT = _RESOLVED["sediment_type_code"]

    def score(self, features: dict[str, Any]) -> ScoreResult:
        missing = [f for f, v in features.items() if v is None]
        raw_change = features.get("shoreline_change_rate_m_per_yr")
        norm_change = _normalize(
            abs(raw_change) if raw_change is not None else None,
            self.REFERENCE_MAX["shoreline_change_rate_m_per_yr"],
        )
        norm_wave = _normalize(features.get("wave_energy_index"), self.REFERENCE_MAX["wave_energy_index"])
        norm_dist = _normalize(
            features.get("distance_to_coast_m"), self.REFERENCE_MAX["distance_to_coast_m"], invert=True
        )
        norm_hist = _normalize(
            features.get("historical_erosion_events"), self.REFERENCE_MAX["historical_erosion_events"]
        )

        raw_score = (
            self.WEIGHTS["shoreline_change_rate_m_per_yr"] * norm_change
            + self.WEIGHTS["wave_energy_index"] * norm_wave
            + self.WEIGHTS["distance_to_coast_m"] * norm_dist
            + self.WEIGHTS["historical_erosion_events"] * norm_hist
        )
        sediment_code = features.get("sediment_type_code")
        sediment_risk = self.SEDIMENT_RISK_BY_CODE.get(int(sediment_code), 0.5) if sediment_code is not None else 0.5
        raw_score += self.SEDIMENT_TYPE_WEIGHT * sediment_risk

        mangrove_cover = features.get("mangrove_cover_pct") or 0.0
        protection = self.MANGROVE_PROTECTION_FACTOR * (mangrove_cover / 100.0)
        final = raw_score * (1 - protection)
        return ScoreResult(score=min(max(final, 0.0), 1.0), missing_fields=missing)


class CloudburstScorer:
    # temperature_c is excluded from this AHP set entirely, not just left
    # at weight 0 — it isn't in judgments/cloudburst.json's factor list at
    # all, so there is no pairwise comparison to make for it. Reason is
    # the same non-monotonic-relationship argument in the comment below:
    # AHP still assumes a monotonic "more of this factor = more risk (or
    # less)" relationship per factor, which doesn't hold for temperature
    # here either.
    WEIGHTS = weight_resolver.get_weights("cloudburst")
    REFERENCE_MAX = {
        "rainfall_intensity_mm_per_hr": 100.0,
        "humidity_pct": 100.0,
        "elevation_m": 2500.0,
        "wind_speed_kmph": 60.0,
        "historical_cloudburst_count": 10.0,
    }
    # temperature_c is tracked in the feature contract but STILL not
    # weighted here, reassessed and reconfirmed this update rather than
    # left unexamined: convective/cloudburst intensity does relate to
    # temperature (via atmospheric moisture-holding capacity), but not
    # monotonically — risk generally peaks in a mid-to-high temperature
    # band tied to regional CAPE/instability rather than rising
    # indefinitely with temperature the way, say, rainfall_intensity
    # does. `_normalize()` is a straight linear min-max scaler; feeding
    # a non-monotonic relationship through it would silently encode
    # either "hotter is always worse" or "hotter is always better",
    # both wrong, without a validated peak-risk band for this
    # regime to build the correct (non-linear) curve from. Left off
    # until that band is sourced from real Indian-cloudburst
    # meteorological research, not invented here.

    def score(self, features: dict[str, Any]) -> ScoreResult:
        missing = [f for f, v in features.items() if v is None]
        total = sum(
            weight * _normalize(features.get(field), self.REFERENCE_MAX[field])
            for field, weight in self.WEIGHTS.items()
        )
        return ScoreResult(score=min(max(total, 0.0), 1.0), missing_fields=missing)


_SCORERS = {
    HazardType.FLOOD: FloodScorer(),
    HazardType.LANDSLIDE: LandslideScorer(),
    HazardType.EROSION: ErosionScorer(),
    HazardType.CLOUDBURST: CloudburstScorer(),
}


def predict(features_by_hazard: dict[HazardType, dict[str, Any]]) -> dict[HazardType, ScoreResult]:
    """The one function backend/zone_classifier.py calls."""
    return {
        hazard: _SCORERS[hazard].score(features)
        for hazard, features in features_by_hazard.items()
    }
