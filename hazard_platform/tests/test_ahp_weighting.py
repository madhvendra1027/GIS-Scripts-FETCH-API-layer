"""Tests for ml_service/weighting/ — both the pure math (ahp.py) and its
wiring into predictor.py's live scoring weights (weight_resolver.py).

Two layers get exercised separately, matching the module split:
  * ahp.py:            pure math, tested against hand-checkable matrices.
  * weight_resolver.py: file loading + caching + the CR gate, tested
                         against the real judgments/*.json files that ship
                         with the project, plus a synthetic bad file to
                         confirm the CR gate actually rejects something.
"""
import json

import pytest

from ml_service.weighting import ahp, weight_resolver


# ---------------------------------------------------------------------------
# ahp.py — pure math
# ---------------------------------------------------------------------------

def test_perfectly_consistent_matrix_has_cr_zero():
    """If comparisons are exact ratios of some true weight vector, CR must
    be ~0 -- this is AHP's own definition of 'perfectly consistent'."""
    factors = ["a", "b", "c"]
    # true weights 4:2:1 -> pairwise ratios are exact
    comparisons = {"a|b": 2, "a|c": 4, "b|c": 2}
    result = ahp.solve(factors, comparisons)
    assert result.consistency_ratio == pytest.approx(0.0, abs=1e-9)
    assert result.weights["a"] == pytest.approx(4 / 7, abs=1e-9)
    assert result.weights["b"] == pytest.approx(2 / 7, abs=1e-9)
    assert result.weights["c"] == pytest.approx(1 / 7, abs=1e-9)


def test_weights_always_sum_to_one():
    factors = ["slope", "rain", "soil", "veg", "hist"]
    comparisons = {
        "slope|rain": 2, "slope|soil": 3, "slope|veg": 4, "slope|hist": 5,
        "rain|soil": 2, "rain|veg": 3, "rain|hist": 4,
        "soil|veg": 2, "soil|hist": 3,
        "veg|hist": 2,
    }
    result = ahp.solve(factors, comparisons)
    assert sum(result.weights.values()) == pytest.approx(1.0, abs=1e-9)


def test_grossly_contradictory_matrix_raises_inconsistent_error():
    """a >> b, b >> c, but then c >> a (a full circular contradiction)
    must push CR over the 0.10 threshold and raise, not just warn."""
    factors = ["a", "b", "c"]
    comparisons = {"a|b": 9, "b|c": 9, "a|c": 1.0 / 9.0}  # a<<c despite a>>b>>c
    with pytest.raises(ahp.InconsistentJudgmentsError):
        ahp.solve(factors, comparisons)


def test_two_factor_matrix_is_always_consistent():
    """With only one independent judgment, CR is defined as 0 (RI(2) = 0
    in Saaty's table) -- there's no possible contradiction to detect."""
    result = ahp.solve(["a", "b"], {"a|b": 7})
    assert result.consistency_ratio == 0.0
    assert result.weights["a"] > result.weights["b"]


def test_missing_pairwise_comparison_raises():
    with pytest.raises(ValueError, match="Incomplete pairwise"):
        ahp.build_matrix(["a", "b", "c"], {"a|b": 2})  # a|c and b|c missing


def test_duplicate_pairwise_comparison_raises():
    with pytest.raises(ValueError, match="judged more than once"):
        ahp.build_matrix(["a", "b"], {"a|b": 2, "b|a": 0.5})


def test_unknown_factor_in_comparison_raises():
    with pytest.raises(ValueError, match="not in"):
        ahp.build_matrix(["a", "b"], {"a|z": 2})


def test_non_positive_saaty_value_raises():
    with pytest.raises(ValueError, match="positive"):
        ahp.build_matrix(["a", "b"], {"a|b": 0})


# ---------------------------------------------------------------------------
# weight_resolver.py — the real judgment files that ship with the project
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hazard_key", weight_resolver.KNOWN_HAZARDS)
def test_every_shipped_judgment_file_is_consistent(hazard_key):
    """Every judgments/<hazard>.json in the repo must clear the CR < 0.10
    bar on its own -- if this fails, the file itself needs fixing, not
    the test."""
    result = weight_resolver.get_ahp_result(hazard_key)
    assert result.consistency_ratio < ahp.DEFAULT_CR_THRESHOLD


@pytest.mark.parametrize("hazard_key", weight_resolver.KNOWN_HAZARDS)
def test_every_shipped_judgment_file_weights_sum_to_one(hazard_key):
    weights = weight_resolver.get_weights(hazard_key)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_resolved_weights_match_predictor_field_sets():
    """The factor list in each judgment file must exactly match the
    field set predictor.py's corresponding scorer actually reads --
    otherwise a scorer would silently use a subset of the AHP-derived
    weights (or KeyError). Landslide/erosion resolve one extra field
    (soil_type_code / sediment_type_code respectively) that predictor.py
    deliberately splits out for its categorical lookup-table path -- see
    LandslideScorer/ErosionScorer's WEIGHTS class-body comments -- so
    those two are checked as a superset, not an exact match.
    """
    from ml_service.inference.predictor import (
        CloudburstScorer, ErosionScorer, FloodScorer, LandslideScorer,
    )

    assert set(weight_resolver.get_weights("flood")) == set(FloodScorer.WEIGHTS)
    assert set(weight_resolver.get_weights("cloudburst")) == set(CloudburstScorer.WEIGHTS)

    landslide_all = set(weight_resolver.get_weights("landslide"))
    assert landslide_all == set(LandslideScorer.WEIGHTS) | {"soil_type_code"}

    erosion_all = set(weight_resolver.get_weights("erosion"))
    assert erosion_all == set(ErosionScorer.WEIGHTS) | {"sediment_type_code"}


def test_get_ahp_result_is_cached_not_recomputed():
    """lru_cache means repeated calls return the identical AHPResult
    object -- pairwise judgments are a load-time input, not something
    that should be re-derived (and re-checked for CR) on every score()
    call in a hot scoring loop."""
    a = weight_resolver.get_ahp_result("flood")
    b = weight_resolver.get_ahp_result("flood")
    assert a is b


def test_missing_judgment_file_fails_loudly(tmp_path, monkeypatch):
    """No fallback to a hand-set default when a judgment file is
    missing -- matches the project's 'an honest gap beats a false
    positive' rule applied to weights, not just sensor readings."""
    monkeypatch.setattr(weight_resolver, "_JUDGMENTS_DIR", tmp_path)
    weight_resolver.get_ahp_result.cache_clear()
    with pytest.raises(weight_resolver.JudgmentFileError, match="No AHP judgment file"):
        weight_resolver.get_ahp_result("flood")
    weight_resolver.get_ahp_result.cache_clear()  # don't leak into other tests


def test_inconsistent_judgment_file_fails_loudly(tmp_path, monkeypatch):
    """An on-disk file that parses fine but is logically self-contradictory
    (CR >= 0.10) must still be rejected -- well-formed JSON is not the
    same thing as trustworthy judgments."""
    bad_file = tmp_path / "flood.json"
    bad_file.write_text(json.dumps({
        "factors": ["a", "b", "c"],
        "comparisons": {"a|b": 9, "b|c": 9, "a|c": 1.0 / 9.0},
    }))
    monkeypatch.setattr(weight_resolver, "_JUDGMENTS_DIR", tmp_path)
    weight_resolver.get_ahp_result.cache_clear()
    with pytest.raises(ahp.InconsistentJudgmentsError):
        weight_resolver.get_ahp_result("flood")
    weight_resolver.get_ahp_result.cache_clear()


def test_coverage_report_covers_all_known_hazards():
    report = weight_resolver.coverage_report()
    assert set(report) == set(weight_resolver.KNOWN_HAZARDS)
    for hazard_key, entry in report.items():
        assert entry["consistency_ratio"] < ahp.DEFAULT_CR_THRESHOLD
        assert sum(entry["weights"].values()) == pytest.approx(1.0, abs=1e-6)
