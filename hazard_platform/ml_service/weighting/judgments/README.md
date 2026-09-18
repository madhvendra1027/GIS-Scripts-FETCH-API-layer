# AHP judgment files

One JSON file per hazard (`flood.json`, `landslide.json`, `erosion.json`,
`cloudburst.json`). Each file is the **one manual, one-time human input**
the whole weighting system needs — not the weights themselves, which are
always derived, but the pairwise comparisons a person made to produce
them. Same category as `mangrove_cover_pct.csv` or the NCCR shoreline
download elsewhere in this project: sourced by a human once, versioned in
the repo, consumed automatically forever after by `weight_resolver.py`.

## Current status — placeholder judgments, not a real elicitation

The comparisons currently in these four files were seeded by Claude from
this project's own `WEIGHT_JUSTIFICATION.md` (i.e. reverse-engineered to
reproduce the literature-backed weights already documented there), **not**
elicited from an actual panel. They pass the CR < 0.10 check, which proves
they are *internally arithmetically consistent* — it proves nothing about
whether they reflect real expert judgment. Both `_meta.elicited_by` and
`_meta.elicited_on` in each file are literal `PLACEHOLDER` strings for
this reason. Replace them (and the comparisons) the first time a real
elicitation is run.

## File format

```json
{
  "_meta": { "hazard": "...", "elicited_by": "...", "elicited_on": "...", ... },
  "factors": ["field_a", "field_b", "field_c"],
  "comparisons": {
    "field_a|field_b": 3,
    "field_a|field_c": 0.5,
    "field_b|field_c": 2
  }
}
```

- `factors` — the exact field names `predictor.py`'s scorer for this
  hazard expects, in any order. Must match exactly, or `weight_resolver`
  raises `JudgmentFileError` naming the mismatch.
- `comparisons` — every one of the `n*(n-1)/2` pairs, **each judged in
  exactly one direction**. `"field_a|field_b": 3` means *field_a is 3×
  as important as field_b* on Saaty's 1-9 scale; use a fraction (e.g.
  `0.5` = 1/2) for the reverse judgment. Do not also supply
  `"field_b|field_a"` — the reciprocal is filled in automatically, and
  supplying both raises an error (that's `ahp.build_matrix()` catching a
  contradiction before it can hide in the matrix).

Saaty scale reference:

| Value | Meaning |
|---|---|
| 1 | Equal importance |
| 3 | Moderately more important |
| 5 | Strongly more important |
| 7 | Very strongly more important |
| 9 | Extremely more important |
| 2, 4, 6, 8 | Intermediate judgments |

## How to run a real elicitation

1. Get 2-3 people who actually know the domain (team members who've read
   the cited literature is a reasonable bar for a hackathon; a real
   deployment should use actual hazard-domain experts).
2. Each person independently fills in every pair for one hazard using the
   scale above — don't let them see each other's answers first, or you
   lose the independence that makes averaging meaningful.
3. Average corresponding cells across panelists (or take the geometric
   mean, the more standard AHP aggregation for multiple judges) to get
   one merged `comparisons` dict.
4. Run it through `ahp.solve()` (see `weight_resolver.get_ahp_result()`
   for the exact call). If `InconsistentJudgmentsError` is raised, the
   panel's judgments contradict each other too much (CR ≥ 0.10) —
   identify the most surprising pairwise entries and ask the panel to
   revisit them, then re-run.
5. Once CR < 0.10, replace this file's `comparisons`, and fill in the
   real `_meta.elicited_by` / `_meta.elicited_on`.

No code changes are needed anywhere else — `predictor.py` re-reads
whatever is in these files on every import.
