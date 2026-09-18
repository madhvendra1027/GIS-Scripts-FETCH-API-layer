"""ahp.py — Analytic Hierarchy Process core math.

Pure, deterministic functions: pairwise comparison judgments in, weights +
a Consistency Ratio out. No file I/O, no network, no project-specific
knowledge of hazards or field names — that belongs to weight_resolver.py.
Kept separate so this half (the math) can be trusted and unit-tested on
its own, independent of where the judgments come from.

Method, in order:
  1. build_matrix()   — expand a sparse {"A|B": saaty_value} judgment dict
                         into a full reciprocal pairwise matrix.
  2. compute_weights() — column-normalize, then row-average. This is the
                         standard AHP approximation to the matrix's principal
                         eigenvector; exact eigenvector decomposition gives
                         near-identical results for the matrix sizes here
                         (5-9 factors) and this version has no numpy
                         dependency.
  3. consistency()    — lambda_max, Consistency Index (CI), Consistency
                         Ratio (CR) via Saaty's tabulated Random Index (RI).
  4. solve()          — runs all three and raises InconsistentJudgmentsError
                         if CR is at or above threshold (default 0.10, the
                         standard AHP cutoff) rather than silently returning
                         weights built on self-contradictory judgments.

See WEIGHT_JUSTIFICATION.md for why this method was chosen over ad-hoc
weight-setting, and judgments/README.md for how the input files that feed
this module are produced.
"""
from __future__ import annotations

from dataclasses import dataclass

# Saaty's Random Index: the average CI produced by a completely random
# reciprocal matrix of size n. CR = CI / RI(n) is only meaningful for the
# n this table covers; n=1 and n=2 can never be inconsistent (there's at
# most one independent comparison), so RI is 0 and CR is defined as 0.
_RANDOM_INDEX: dict[int, float] = {
    1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12,
    6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49,
}

DEFAULT_CR_THRESHOLD = 0.10


class InconsistentJudgmentsError(ValueError):
    """Raised when a pairwise comparison matrix's CR is at or above the
    threshold — the judgments contain a logical contradiction (e.g. A > B,
    B > C, but C > A) too large to trust. This is AHP's whole point: it
    is a deliberate hard failure, not a warning, so an inconsistent
    judgment file can never silently become live scoring weights."""


@dataclass(frozen=True)
class AHPResult:
    weights: dict[str, float]
    lambda_max: float
    consistency_index: float
    consistency_ratio: float
    n: int


def build_matrix(factors: list[str], comparisons: dict[str, float]) -> list[list[float]]:
    """Expand a sparse upper-triangle judgment dict into a full n x n
    reciprocal matrix. `comparisons` keys are "A|B" meaning 'A is
    comparisons["A|B"] times as important as B' (Saaty 1-9 scale, or a
    fraction 1/2..1/9 for the reverse judgment). Only one direction per
    pair needs to be supplied — the reciprocal and the diagonal are
    filled in automatically, exactly as a formally elicited AHP matrix
    requires.

    Raises if any comparison references an unknown factor, uses a
    non-positive value, or if the judgment set is incomplete (every one
    of the n*(n-1)/2 pairs must be judged exactly once).
    """
    n = len(factors)
    if n < 2:
        raise ValueError("AHP needs at least 2 factors to compare")
    if len(set(factors)) != n:
        raise ValueError(f"Duplicate factor names in {factors}")

    index = {f: i for i, f in enumerate(factors)}
    matrix = [[1.0] * n for _ in range(n)]
    judged_pairs: set[tuple[int, int]] = set()

    for key, value in comparisons.items():
        try:
            a, b = key.split("|")
        except ValueError:
            raise ValueError(f"Comparison key {key!r} must be of the form 'factorA|factorB'")
        if a not in index or b not in index:
            raise ValueError(f"Comparison key {key!r} references a factor not in {factors}")
        if a == b:
            raise ValueError(f"Comparison key {key!r} compares a factor to itself")
        if value <= 0:
            raise ValueError(f"Saaty comparison values must be positive, got {value} for {key!r}")
        i, j = index[a], index[b]
        pair = (min(i, j), max(i, j))
        if pair in judged_pairs:
            raise ValueError(f"Pair ({factors[pair[0]]}, {factors[pair[1]]}) judged more than once")
        matrix[i][j] = float(value)
        matrix[j][i] = 1.0 / float(value)
        judged_pairs.add(pair)

    expected = n * (n - 1) // 2
    if len(judged_pairs) != expected:
        missing_pairs = [
            (factors[i], factors[j])
            for i in range(n) for j in range(i + 1, n)
            if (i, j) not in judged_pairs
        ]
        raise ValueError(
            f"Incomplete pairwise judgment set for {factors}: "
            f"{len(missing_pairs)} of {expected} pair(s) missing: {missing_pairs}"
        )
    return matrix


def compute_weights(matrix: list[list[float]]) -> list[float]:
    """Column-normalize the matrix, then average each row. This is the
    standard closed-form approximation to the matrix's principal
    eigenvector used throughout the AHP literature when a full eigenvalue
    solver isn't available or needed at this matrix size."""
    n = len(matrix)
    col_sums = [sum(matrix[i][j] for i in range(n)) for j in range(n)]
    normalized = [[matrix[i][j] / col_sums[j] for j in range(n)] for i in range(n)]
    return [sum(row) / n for row in normalized]


def consistency(matrix: list[list[float]], weights: list[float]) -> tuple[float, float, float]:
    """Returns (lambda_max, CI, CR) for a matrix and its derived weights.
    lambda_max == n exactly only for a perfectly consistent matrix; CI and
    CR quantify how far off that ideal the actual judgments are."""
    n = len(matrix)
    if n < 3:
        # With only 2 factors there is exactly one independent judgment;
        # it cannot be self-contradictory, so treat as perfectly consistent.
        return float(n), 0.0, 0.0
    weighted_sums = [sum(matrix[i][j] * weights[j] for j in range(n)) for i in range(n)]
    ratios = [weighted_sums[i] / weights[i] for i in range(n)]
    lambda_max = sum(ratios) / n
    ci = (lambda_max - n) / (n - 1)
    ri = _RANDOM_INDEX.get(n)
    if ri is None:
        raise ValueError(
            f"No tabulated Random Index for n={n} factors (supported: 1-{max(_RANDOM_INDEX)}). "
            "Split this hazard's factors into a smaller comparison set."
        )
    cr = 0.0 if ri == 0 else ci / ri
    return lambda_max, ci, cr


def solve(
    factors: list[str],
    comparisons: dict[str, float],
    *,
    cr_threshold: float = DEFAULT_CR_THRESHOLD,
) -> AHPResult:
    """End-to-end: judgments -> matrix -> weights -> consistency check.
    Raises InconsistentJudgmentsError if CR >= cr_threshold — callers
    (weight_resolver.py) must not catch this and fall back to a guessed
    weight set, per this project's no-fabricated-data principle: an
    honest failure beats a false positive here exactly as much as it
    does for a missing sensor reading.
    """
    matrix = build_matrix(factors, comparisons)
    weights = compute_weights(matrix)
    lambda_max, ci, cr = consistency(matrix, weights)
    if cr >= cr_threshold:
        raise InconsistentJudgmentsError(
            f"Pairwise judgments for {factors} are inconsistent: "
            f"CR={cr:.4f} >= threshold {cr_threshold:.2f} (lambda_max={lambda_max:.4f}, CI={ci:.4f}). "
            "Re-examine the comparisons in this hazard's judgment file — "
            "AHP requires CR < 0.10 before its weights can be trusted."
        )
    return AHPResult(
        weights=dict(zip(factors, weights)),
        lambda_max=lambda_max,
        consistency_index=ci,
        consistency_ratio=cr,
        n=len(factors),
    )
