# Hazard Platform — Project Chat Log

Running record of decisions, research, and open threads from conversations with Claude about
`gis_fetcher` and `hazard_platform` (SIH 2026, PS 26191). Newest entry at the top. Each entry
should capture: what was decided, why, and what's still open — not full transcripts.

Guiding principle carried through every session: **full automation wherever technically
possible; manual steps are defects to be closed, not accepted tradeoffs.** No fabricated data
for fields that can't yet be automatically sourced — an honest gap beats a false positive.

---

## 2026-09-14 — AHP weight integration (end-to-end, automated)

**Context:** Follow-on from the same session's methodology explainer, below. Walked through the
AHP math step-by-step for landslide by hand first (matrix → column-normalize → row-average →
λmax → CI → CR), then asked to integrate real AHP scoring into the codebase end-to-end,
matching the project's "full automation, no manual steps that can be avoided" philosophy.

**Important tension surfaced and resolved:** AHP fundamentally requires human pairwise
judgments as input — that part can't be automated without becoming fabricated data, which would
violate this project's own core principle. Resolved by mirroring the pattern the project
already uses for `mangrove_cover_pct.csv` / NCCR shoreline data: **one** manual, one-time,
versioned human input (the pairwise comparison file), with everything downstream (matrix build,
weight derivation, Consistency Ratio gate, injection into `predictor.py`) fully automated and
re-run on every import.

**What was built:**
- `ml_service/weighting/ahp.py` — pure AHP math (`build_matrix`, `compute_weights`,
  `consistency`, `solve`). No I/O, no project-specific knowledge. Raises
  `InconsistentJudgmentsError` if CR ≥ 0.10 — a hard gate, not a warning.
- `ml_service/weighting/judgments/{flood,landslide,erosion,cloudburst}.json` — one pairwise
  comparison matrix per hazard, Saaty 1–9 scale. **Honest caveat:** these were reverse-derived
  by Claude from the already-documented literature weights (`WEIGHT_JUSTIFICATION.md`), not
  elicited from a real panel — `_meta.elicited_by`/`elicited_on` are literal `PLACEHOLDER`
  strings for this reason. CR per hazard: flood 0.010, landslide 0.018, erosion 0.013,
  cloudburst 0.002 — all comfortably under threshold, but that only proves internal arithmetic
  consistency, not real expert judgment.
- `ml_service/weighting/judgments/README.md` — how to redo the elicitation for real with an
  actual 2–3-person panel; no code changes needed elsewhere once that's done.
- `ml_service/weighting/weight_resolver.py` — loads a judgment file, calls `ahp.solve()`,
  caches the result (`lru_cache`), exposes `get_weights(hazard)` and `coverage_report()`. Fails
  loudly (`JudgmentFileError`) on a missing/malformed file rather than falling back to a
  hand-set default — same "honest gap beats false positive" rule as everywhere else.
- `predictor.py` — all four scorers' `WEIGHTS` (plus `SOIL_TYPE_WEIGHT` / `SEDIMENT_TYPE_WEIGHT`
  for landslide/erosion) now resolve from `weight_resolver` at import time instead of being
  hand-typed decimals. `mangrove_cover_pct`'s multiplicative discount stays outside the AHP set
  entirely, as before — it was never a weighted-sum term.
- `tests/test_ahp_weighting.py` — new test module: pure-math checks (perfectly consistent
  matrix → CR≈0, contradictory matrix → raises, missing/duplicate/unknown comparisons → raises),
  plus integration checks against the real shipped judgment files (CR < 0.10, weights sum to 1,
  factor sets match `predictor.py`'s scorers exactly, caching, missing/bad-file failure modes).

**Verified break-proof:** all 7 pre-existing `test_predictor.py` assertions pass unchanged
against the new AHP-derived weights (they were range-based, and the derived weights landed
inside every existing tolerance band) — no test rewrites were forced, only three inline comments
updated where they cited now-stale exact percentages. `requirements.txt` needed no changes;
the new modules use only the stdlib.

**Open threads carried forward:**
- Real AHP elicitation (2–3 team members, live panel) still needed to replace the placeholder
  judgment files — mechanically ready, just needs the actual people.
- `backend/prioritization.py` has its own separate hand-set `WEIGHTS` (0.50 hazard / 0.20
  population / 0.15 socioeconomic / 0.15 history) — same hand-set-weight pattern, not yet
  brought into the AHP system; flagged as a natural next step, not done this session since it
  wasn't in scope of what was asked.
- All open threads from the prior entry (Bhukosh raster-to-vector, EM-DAT ingestion, ML
  training-data assembly for erosion/cloudburst, full pipeline documentation) remain open.

---

## 2026-09-14 — ML training data research + scoring methodology explainer

**Context:** Uploaded project summary PDF, `mangrove_cover_pct.csv`, and both zipped repos
(`gis_fetcher`, `hazard_platform_updated`) for the first time in this conversation.

**Asked for:** historical datasets to train real ML models per hazard (to eventually replace
the weighted-formula scorers), an explanation of how the current weights are calculated, and
this running log.

**Weight methodology (explained, not changed):**
- Current scoring is a hand-set weighted-overlay / multi-criteria model, not learned. Documented
  reason in the code: none of the 4 hazards has defensible zone-level historical outcome labels
  yet to train a real model on.
- Mechanics: `_normalize(value, reference_max, invert)` → 0–1 sub-score; missing field → neutral
  0.5 (never biases in either direction); final score = Σ(weight × sub-score), weights sum to 1
  per hazard.
- Categorical fields (`soil_type_code`, `sediment_type_code`) use hand-ranked lookup tables
  instead of linear scaling — same soil types rank in *opposite* order across the two tables
  (landslide vs. erosion), because the physical failure mechanism differs.
- `mangrove_cover_pct` is a multiplicative discount, not an additive weighted term.
- Every scorer shares one `.score(features) → ScoreResult` interface specifically so any hazard
  can be swapped for a trained model later without touching the rest of the pipeline.

**Datasets surfaced (see full chat turn for citations/detail):**

| Hazard | Best national source(s) | Readiness |
|---|---|---|
| Flood | INDOFLOODS (AMS 2025, 214 CWC/India-WRIS gauge stations); India Flood Inventory (1985–2016 fatality/damage records) | Strong, close to ML-ready |
| Landslide | GSI Bhukosh (105k+ polygons, 49k+ points); NASA COOLR raw point records; regional inventories (Kerala, Karnataka) | Strong, close to ML-ready |
| Coastal Erosion | NCCR National Shoreline Change Assessment (raw rate data, not just the summary report) | Needs assembly — join NCCR rates to zones, same pattern as the mangrove ingestion |
| Cloudburst | IMD gridded daily rainfall (0.25°) for 95th-percentile extreme-event labels; IPED ensemble dataset (0.1°/0.25°, 1991–2020); ERA5 CAPE/vertical-velocity as physical predictors | Needs assembly — raw grids, not a labeled table |

**Open threads carried forward:**
- Completing implementation of the two automation gaps from the prior session: Bhukosh
  PDF/raster-to-vector conversion (sediment type), EM-DAT historical event count ingestion.
- Erosion and cloudburst ML training data will require real data-engineering work (gridded/rate
  data → zone-level joins) before any model can be trained — flagged as an honest gap, not
  glossed over.
- Full pipeline documentation in workflow order, grounded with a concrete Indian
  disaster-prone location, still outstanding.
- `requirements.txt` consistency check against any new dependencies still outstanding.

---

## How to keep this file updated

At the end of each future session, ask Claude to append a new dated entry above this section
(newest on top) summarizing: what was decided, why, and what's still open. Keep entries short —
this is a decision log, not a transcript.
