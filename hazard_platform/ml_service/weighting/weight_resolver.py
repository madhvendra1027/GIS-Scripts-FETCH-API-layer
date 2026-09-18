"""weight_resolver.py — turns each hazard's pairwise-judgment file into
the live weights predictor.py's scorers use.

This is the one place the project's "no fabricated data, no manual step
that could instead be automated" principle meets AHP's actual requirement
for human pairwise judgments, so it's worth being explicit about the
split:

  * judgments/<hazard>.json  — the ONE manual, one-time human input. Same
    category as mangrove_cover_pct.csv or the NCCR shoreline download:
    sourced by people once, versioned in the repo, and re-used forever
    after with zero further manual work. See judgments/README.md for how
    to (re)produce one for real with a live panel.

  * everything in this file and ahp.py — fully automated. Every process
    that imports predictor.py re-derives weights from the judgment files,
    re-checks the Consistency Ratio, and refuses to serve weights that
    fail that check (see get_weights()). Nothing here is hand-set.

This mirrors auto_refresh.py's shape exactly: a slow-changing fact
(mangrove cover, or here, expert pairwise judgments) gets ingested once by
a human, then every live run picks it up automatically.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import ahp

_JUDGMENTS_DIR = Path(__file__).parent / "judgments"

# Every hazard MUST have a judgment file — there is no silent fallback to
# a hand-set default. A hazard with no file, or a file whose CR fails the
# threshold, fails loudly at import time (via predictor.py's module-level
# WEIGHTS = get_weights(...) calls) rather than serving unvalidated
# numbers. This is the same "an honest gap beats a false positive"
# philosophy the rest of the pipeline already applies to missing sensor
# fields, just applied to the weights themselves.
#
# "prioritization" is not a hazard — it's backend/prioritization.py's
# hazard/population/socioeconomic/history relocation-priority weights,
# added here because they needed the exact same "one-time human pairwise
# judgment, CR-checked, no hand-set decimals" treatment as the four real
# hazards (see judgments/prioritization.json and decisions-and-learnings.md
# item 6). Kept in this one tuple rather than a second parallel constant
# since every consumer (get_weights, get_ahp_result, coverage_report)
# already generalizes over "any key with a judgment file".
KNOWN_HAZARDS = ("flood", "landslide", "erosion", "cloudburst", "prioritization")


class JudgmentFileError(ValueError):
    """The judgment file for a hazard is missing, malformed, or its
    comparisons don't parse into a valid AHP matrix. Distinct from
    ahp.InconsistentJudgmentsError, which means the file is well-formed
    but the judgments inside it are self-contradictory."""


def _load_judgment_file(hazard_key: str) -> dict:
    path = _JUDGMENTS_DIR / f"{hazard_key}.json"
    if not path.exists():
        raise JudgmentFileError(
            f"No AHP judgment file for hazard {hazard_key!r} at {path}. "
            f"Every hazard in {KNOWN_HAZARDS} must have one — see judgments/README.md."
        )
    try:
        with path.open() as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise JudgmentFileError(f"{path} is not valid JSON: {exc}") from exc

    for required_key in ("factors", "comparisons"):
        if required_key not in data:
            raise JudgmentFileError(f"{path} is missing required key {required_key!r}")
    return data


@lru_cache(maxsize=None)
def get_ahp_result(hazard_key: str) -> ahp.AHPResult:
    """The full AHP result (weights + CR + lambda_max) for one hazard,
    computed once per process and cached — pairwise judgments are a
    build-time input, not something that changes mid-run, so recomputing
    per score() call would be pure waste. Raises JudgmentFileError if the
    file is missing/malformed, or ahp.InconsistentJudgmentsError if CR is
    too high — both propagate to the caller rather than being swallowed,
    per this module's docstring.
    """
    if hazard_key not in KNOWN_HAZARDS:
        raise JudgmentFileError(f"Unknown hazard {hazard_key!r}; expected one of {KNOWN_HAZARDS}")
    data = _load_judgment_file(hazard_key)
    comparisons = {k: float(v) for k, v in data["comparisons"].items()}
    return ahp.solve(data["factors"], comparisons)


def get_weights(hazard_key: str) -> dict[str, float]:
    """What predictor.py actually calls: {field_name: weight}, summing to
    1.0, for the given hazard's WEIGHTS dict."""
    return get_ahp_result(hazard_key).weights


def coverage_report() -> dict[str, dict]:
    """Every known hazard's resolved CR and lambda_max in one call — a
    quick end-to-end sanity check (e.g. for a CLI or a test) that every
    judgment file is present, parses, and clears the consistency bar,
    without having to know predictor.py's internals."""
    report = {}
    for hazard_key in KNOWN_HAZARDS:
        result = get_ahp_result(hazard_key)
        report[hazard_key] = {
            "weights": result.weights,
            "consistency_ratio": result.consistency_ratio,
            "lambda_max": result.lambda_max,
            "n_factors": result.n,
        }
    return report
