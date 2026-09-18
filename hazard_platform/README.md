# Hazard Platform — Cleaning, ML Scoring, Zone Classification, Prioritization

Code for the pipeline stages downstream of `gis_fetcher` (see
`gis_fetcher.zip`, built separately). This package implements
everything from raw-feature normalization through to the FastAPI +
Leaflet dashboard.

Scoped strictly to the four hazards in the SIH 2026 problem statement
(Problem Statement ID 26191): **flood, landslide, coastal erosion,
cloudburst**. No seismic/earthquake scoring is included, even though
`gis_fetcher` can fetch earthquake data as a bonus source — it isn't
one of the named hazards.

## What changed in this update

The whole data-fetching-through-feature-engineering layer is now
end-to-end automated for a fresh zone, with exactly two fields that
still need a one-time human download (explained below, not glossed
over) and one field that needs a one-time human *signup* instead.
Concretely:

- **`sediment_type_code`** and all four **`historical_*_count`**
  fields — previously manual CSV ingestion — are fetched **live**
  by two scripts (`fetch_sediment_type.py`, `fetch_historical_events.py`)
  and refreshed automatically by `auto_refresh.py`, which
  `pipeline_runner.py` calls on every run with zero setup.
- **`distance_to_river_m`** (FLOOD) and **`distance_to_coast_m`**
  (EROSION) are now live too: `fetch_river_distance.py` is a new
  OSM-Overpass fetcher for the former, and the latter's existing fetch
  path had two bugs fixed this update (wrong Overpass tag, and a
  node-only query against a way-tagged feature — see
  `STATIC_DATASETS.md` for the full story).
- **`river_level_m`** (FLOOD, absolute gauge stage) is now live via a
  new `fetch_river_level.py` against Google's Flood Forecasting API —
  the one field in this repo that needs a one-time signup
  (`GOOGLE_FLOOD_API_KEY`) rather than a download or nothing at all. No
  command to run either way — once the key is set, `auto_refresh.py`
  picks it up automatically on the next run.
- **`shoreline_change_rate_m_per_yr`** and **`mangrove_cover_pct`**
  still require a one-time human download (no live API exists for
  either — re-confirmed by search this update, see `STATIC_DATASETS.md`
  for the two near-misses found), but the two manual commands that used
  to follow that download (`spatial_join.py` then a `load_*.py` script)
  are one (`ingest_dataset.py`), with automatic zone-boundary fetching,
  value-column detection, and sign-convention handling.

See `STATIC_DATASETS.md` for the full breakdown of which fields are
live vs. still manual, and exactly what to do for each.

## Layout

```
data_pipeline/
  models.py                       # HazardType, DataQuality, HazardReading
  normalize.py                    # raw gis_fetcher Features -> locked field names
  cleaning.py                     # validate, impute, flag staleness
  hazard_reading_store.py         # SQLite persistence boundary
  static_datasets/
    store.py                      # StaticDatasetStore -- separate SQLite table for slow-changing facts
    fetch_sediment_type.py         # LIVE: sediment_type_code (SoilGrids proxy)
    fetch_historical_events.py     # LIVE: historical_*_count (GDACS + NASA COOLR)
    fetch_river_distance.py        # LIVE: distance_to_river_m (OSM Overpass waterway ways)
    fetch_river_level.py           # LIVE (after signup): river_level_m (Google Flood Forecasting API)
    fetch_flood_status.py          # LIVE (after signup, same key): flood_status_severity_code (same API, FloodStatus data)
    auto_refresh.py                # orchestrates all fetchers above, staleness-aware
    zone_boundaries.py             # cached OSM boundary fetch -- called automatically
    fetch_zone_boundaries.py       # optional CLI over zone_boundaries.py, for pre-warming
    spatial_join.py                # dataset + zone polygons -> zone_id,value CSV
    ingest_dataset.py              # ONE command: join + ingest, for shoreline/mangrove
    load_from_csv.py               # generic CSV -> StaticDatasetStore writer
    load_shoreline_change.py       # manual-override entry point (shoreline)
    load_mangrove_cover.py         # manual-override entry point (mangrove)
    load_sediment_type.py          # manual-override entry point (sediment, if you get real GSI data)
    load_historical_counts.py      # manual-override entry point (historical counts, if you get real NDMA/SDMA/EM-DAT data)
ml_service/
  features/feature_engineering.py # per-hazard feature dicts/vectors
  inference/predictor.py          # weighted-formula scorers, 4 hazards
backend/
  zone_classifier.py               # worst hazard score -> RED/YELLOW/GREEN
  prioritization.py                 # + population vulnerability + history -> priority tier
  api.py                            # FastAPI app
frontend/
  dashboard.html                    # Leaflet demo: click map or zone marker
tests/
  test_predictor.py                 # scoring + classification tests
zones.py                            # zone_id -> bbox registry (5 demo zones)
pipeline_runner.py                  # the live end-to-end glue: fetch -> normalize -> clean -> store
example_run.py                      # end-to-end demo with sample data, no network needed
STATIC_DATASETS.md                  # which fields are live vs. still need a download, and why
```

## Pipeline order

```
gis_fetcher  ->  normalize.py  ->  cleaning.py  ->  HazardReadingStore
    ->  feature_engineering.py  ->  predictor.py (4 hazard scores)
    ->  zone_classifier.py (worst score -> color)
    ->  prioritization.py (+ vulnerability, history -> priority tier)
    ->  api.py  ->  frontend/dashboard.html
```

`auto_refresh.py` sits just before `HazardReadingStore` in this chain —
it's what populates `StaticDatasetStore` automatically so
`pipeline_runner.py`'s merge step always has something to read, even
for a zone that's never been touched before.

## Run the sanity demo (no network required)

```bash
cd hazard_platform
python3 example_run.py
```

## Run the tests

```bash
pip install pytest --break-system-packages
PYTHONPATH=. python3 -m pytest tests/ -v
```

## Run it once, live — genuinely one command now

```bash
PYTHONPATH=. python3 pipeline_runner.py Z-ODISHA-PURI-01
```

No prior setup, no CSV to prepare, no separate refresh command. Every
field that has *any* automatable source — live weather/soil/slope/etc.
from `gis_fetcher`, plus the newly-automated `sediment_type_code` and
`historical_*_count` — gets filled in on this one call. Only
`shoreline_change_rate_m_per_yr` and `mangrove_cover_pct` will still
show up as `None` until you run the one-time `ingest_dataset.py` step
described in `STATIC_DATASETS.md` — and even then, only for zones you
haven't ingested a download for yet.

## Run the API + dashboard

```bash
pip install fastapi "uvicorn[standard]"
PYTHONPATH=. uvicorn backend.api:app --reload
# then open frontend/dashboard.html in a browser
```

Open `frontend/dashboard.html` and click anywhere on the map: this hits
`GET /api/analyze-point?lat=..&lon=..` (no zone_id, no pre-defined
region needed), which derives a zone around that exact point
(`zones.zone_from_point()`), runs the full live
fetch → normalize → clean → save → score pipeline for it, and drops a
colored RED/YELLOW/GREEN marker as soon as it resolves. This is the
"no hardcoded regions" path — `zones.py`'s 5 named towns are just a
demo seed list; a clicked point never needs an entry there first.

`/api/analyze-point` and `/api/place-data` both require `gis_fetcher`
(from `gis_fetcher.zip`) installed/importable alongside this package.
A click takes a few seconds to resolve (it's making real network calls
to every provider each hazard needs), so the popup shows a loading
state until the scored result comes back.

---

## Full workflow walkthrough, function by function

Below is every script in the live path, in the order data actually
flows through them, each with what it does and why. Running example
throughout: a fictional zone **`Z-TN-CHENNAI-NORTH-01`**, "North
Chennai Coastal Belt, Tamil Nadu" — a made-up low-lying, cyclone- and
flood-prone coastal ward, invented purely to make the data concrete.
None of the numbers below are real measurements.

### 1. `zones.py` — what a "zone" is

```python
@dataclass(frozen=True)
class Zone:
    zone_id: str; name: str
    min_lon: float; min_lat: float; max_lon: float; max_lat: float

def get_zone(zone_id: str) -> Zone: ...
def list_zones() -> list[Zone]: ...
```

The registry pipeline_runner.py needs a bbox for. For our example,
imagine an entry `("Z-TN-CHENNAI-NORTH-01", "North Chennai Coastal
Belt, Tamil Nadu", 80.28, 13.13)` — a ~5km box around a fictional
coastal ward center. `get_zone()` looks one up by id; `list_zones()`
returns all of them (used by every "run for every zone" script below).

### 2. `pipeline_runner.py` — the entry point

`main()` parses `zone_id` from the command line, opens
`HazardReadingStore` and `StaticDatasetStore`, and calls `ingest_zone()`.

`seed_history_from_store()` loads each hazard's last saved reading for
this zone into a fresh `ZoneHistory`, so if (say) yesterday's rainfall
reading exists but today's weather API call fails, `cleaning.py` can
fall back to yesterday's real number instead of a crude regional
default.

`ingest_zone()` is the actual orchestration, run once per hazard:

1. Calls `auto_refresh.refresh_static_fields([zone_id], static_store)`
   — see step 3.
2. Calls `gis_fetcher.hazard_map.fetch_for_hazard(hazard_name, bbox)` —
   this fans out to every live provider that hazard needs (e.g. for
   FLOOD: `weather`, `elevation`, `river_discharge`, `land_hydrology`,
   `historical_events`).
3. Feeds each provider's result through the matching function in
   `_NORMALIZERS` (see step 5) — a failed/empty provider still gets
   normalized via `_EmptyFeature`, so every field surfaces as an
   explicit `None` rather than silently vanishing.
4. Merges the normalized dicts with `normalize.merge_shared_fields()`,
   static fields going **last** so a real ingested value can override a
   live `None`, but never override a live *real* value.
5. Runs the merged dict through `cleaning.clean_reading()`.
6. Saves the result via `HazardReadingStore.save()`.

For `Z-TN-CHENNAI-NORTH-01`, one `FLOOD` pass might merge: live 24h
rainfall from `weather`, live river-discharge trend, and a
`historical_flood_count` of, say, 4 (auto-fetched by step 3 below) — no
human touched any of that for this zone's first-ever run.

### 3. `data_pipeline/static_datasets/auto_refresh.py` — the static-layer orchestrator

`DEFAULT_MAX_AGE_DAYS = 180`. `refresh_static_fields(zone_ids, store,
max_age_days)` is the function `pipeline_runner.py` calls:

- `_zones_needing_refresh()` checks `store.needs_refresh()` (see step
  4) for `sediment_type_code` and all four `historical_*_count` fields,
  per zone.
- If nothing is stale, returns immediately (`{"refreshed_zones": []}`)
  — a zone refreshed an hour ago costs nothing on the next run.
- If something is stale, calls `ingest_sediment_codes()` (step 5) and
  `ingest_historical_counts()` (step 6) for exactly those zones.

`refresh_all_zones()` is the batch convenience version — every zone in
`zones.py` at once, e.g. to pre-warm before a demo. Both are also
exposed as a standalone CLI (`main()`): `python3 -m
data_pipeline.static_datasets.auto_refresh`.

For `Z-TN-CHENNAI-NORTH-01`'s very first run, every one of the five
fields is "missing" → all five get fetched together in this one batch.

### 4. `data_pipeline/static_datasets/store.py` — where static facts live

`StaticDatasetStore` wraps one SQLite table, `static_zone_fields`
(`zone_id, field_name, value, source, ingested_at`).

- `upsert(zone_id, field_name, value, source)` — insert or overwrite.
  Re-ingesting next year's mangrove data just calls this again.
- `get(zone_id, field_name)` — one field's stored value + metadata.
- `get_all_for_zone(zone_id)` — what `pipeline_runner.py` actually
  reads: every non-`None` field for a zone, as a plain dict.
- `needs_refresh(zone_id, field_name, max_age_days)` — **new**: true if
  the field has never been ingested, or its `ingested_at` is older than
  `max_age_days`. This is the one piece of logic `auto_refresh.py`
  needed and didn't have anywhere else to live.
- `coverage_report()` — `{field_name: [zone_ids...]}`, a quick "what
  have I actually got" sanity check across every zone.

### 5. `data_pipeline/static_datasets/fetch_sediment_type.py` — live sediment proxy

`fetch_sediment_codes_for_zones(zone_ids)` queries ISRIC SoilGrids
(`rest.isric.org`) at each zone's bbox center for sand/silt/clay % at
0–5cm depth, and `_classify_usda_texture()` buckets those into the same
1–12 code `gis_fetcher`'s `soil.py` already uses for LANDSLIDE (1=clay
… 12=sand). `ingest_sediment_codes()` upserts the result into
`StaticDatasetStore` with `source="SoilGrids_texture_proxy_auto"`.

For our fictional coastal ward — imagine SoilGrids reports ~78% sand,
12% silt, 10% clay at that point — `_classify_usda_texture()` returns
`(12, "sand")`. That gets stored as `sediment_type_code=12.0`, labeled
as a proxy, meaning "sandy coastal sediment (fast-draining, but prone
to wave-driven erosion)" rather than a real GSI coastal-geomorphology
classification.

### 6. `data_pipeline/static_datasets/fetch_historical_events.py` — live historical counts

`fetch_historical_counts_for_zones()` fires two requests concurrently:

- `_fetch_gdacs_features()` — one call to GDACS's recent-events feed,
  filtered client-side (not via GDACS's own broken query params — see
  the module docstring) to India-affecting "FL" (flood) and "TC"
  (cyclone) events within `days_back` days, matched to a zone if the
  event's one centroid point falls within `flood_buffer_deg` /
  `erosion_buffer_deg` of that zone's bbox.
- `_fetch_coolr_features()` — one bbox query against NASA COOLR's
  landslide point catalog for the whole India region, matched to a zone
  within `landslide_buffer_deg`.

`_gather_counts()` combines both into `{zone_id: {field_name: count}}`.
`ingest_historical_counts()` upserts every field for every zone
(including real zeros — "no tracked event nearby" is a meaningful
value, not a missing one), labeling erosion/cloudburst counts as
`_proxy` since GDACS has no dedicated category for either.

For `Z-TN-CHENNAI-NORTH-01`: imagine one GDACS "TC" event (a cyclone
making landfall near the Tamil Nadu coast last season) whose centroid
falls within `erosion_buffer_deg` of the zone — that's
`historical_erosion_events += 1`, labeled `GDACS_recent_events_proxy`.
A COOLR point 15km inland from a different, hillier zone would **not**
match this coastal zone's tight `landslide_buffer_deg` — correctly
zero, since this ward isn't landslide-prone terrain.

### 7. `data_pipeline/static_datasets/ingest_dataset.py` — the one-command manual step

For the two fields that stay manual: `main()` takes `--dataset-type
{shoreline,mangrove}` and a downloaded `--file`. It calls
`load_zones_gdf()` (auto-fetches zone polygons from OSM if you didn't
pass `--zones-file`), then either `join_mean_value()` (shoreline) or
`join_area_pct()` (mangrove) from `spatial_join.py`, auto-detecting the
value column for shoreline via `_autodetect_value_column()` (checks
common NCCR column names) and auto-applying the accretion→erosion sign
flip. It writes an intermediate CSV for review, then calls
`load_from_csv.load_csv()` directly to ingest into `StaticDatasetStore`.

For our example: you'd download Tamil Nadu's NCCR shoreline shapefile
once, run `ingest_dataset.py --dataset-type shoreline --file
tn_shoreline.shp --source "ISRO_NCCR_shoreline_atlas_2023"`, and every
future `pipeline_runner.py Z-TN-CHENNAI-NORTH-01` run picks up that
zone's real erosion rate (say, `-1.8` m/yr — a genuinely eroding
stretch) automatically, forever, with zero further action.

### 8. `data_pipeline/normalize.py` — renaming to the locked field contract

Each `*_to_*_fields()` function takes one `gis_fetcher.Feature` and
returns a dict using this project's locked field names (not whatever
name the upstream API happened to use) — e.g. `weather_to_flood_fields()`
pulls `rainfall_mm_24h`/`rainfall_mm_72h` off a weather feature;
`soil_to_landslide_fields()` pulls `soil_type_code`/`soil_moisture_pct`;
`historical_to_fields(feature, hazard_type)` maps whichever
`historical_*_count` field matches that hazard. `osm_to_erosion_fields()`
is the one that takes a **list** of features (it scans for a coastline
tag to compute `distance_to_coast_m`). `merge_shared_fields(*dicts)`
combines every hazard's field dicts (live sources first, static last),
letting a later non-`None` value win.

### 9. `data_pipeline/cleaning.py` — validate, impute, flag

`clean_reading(zone_id, raw_parameters, recorded_at, history)`:

- Drops any value outside `VALID_RANGES` (e.g. a corrupted
  `rainfall_mm_24h` of 5000mm gets dropped, not trusted).
- For each `None`/dropped field, tries `ZoneHistory.get_last_known()`
  first (this zone's own last real reading), then falls back to a
  coarse `REGIONAL_DEFAULTS` placeholder only if there's no history
  either.
- Flags the whole reading `is_stale` if the underlying data is older
  than `STALE_AFTER` (3 hours).

Returns a `CleaningResult` with `.parameters`, `.imputed_fields`,
`.dropped_fields`, `.is_stale` — `pipeline_runner.py` uses these to set
`DataQuality.RAW` / `IMPUTED` / `STALE` on the saved reading.

### 10. `data_pipeline/hazard_reading_store.py` — persistence

`HazardReadingStore.save()` writes one timestamped `HazardReading` row
per hazard per run (unlike `StaticDatasetStore`, this is an
append-only observation log, not an overwrite-in-place fact table).
`latest_for_zone()` and `history_for_zone()` read it back —
`latest_for_zone()` is what `seed_history_from_store()` uses to
rebuild `ZoneHistory` at the start of the next run.

### 11. `ml_service/features/feature_engineering.py` — shaping for the scorer

`build_feature_dict(hazard_type, parameters)` and
`build_feature_vector(...)` take a cleaned `HazardReading.parameters`
dict and produce exactly the feature set each hazard's scorer expects
— nothing more, nothing implicitly reordered.

### 12. `ml_service/inference/predictor.py` — the four scorers

`_normalize(value, reference_max, invert)` rescales a raw value to
0–1. `FloodScorer`, `LandslideScorer`, `ErosionScorer`,
`CloudburstScorer` each implement `.score(features) -> ScoreResult`
using a fixed weighted formula (not a trained model — see the project
design notes on why). `predict(features_by_hazard)` runs all four and
returns `{HazardType: ScoreResult}`.

For our example zone, imagine `ErosionScorer.score()` weighs a negative
shoreline-change rate, a sandy `sediment_type_code`, and a nonzero
`historical_erosion_events` proxy count together into a single 0–1
erosion risk score — every one of those three inputs traced back
through this whole chain from a live/proxy/one-time-downloaded source.
(As of this update this is no longer just a hypothetical — `sediment_type_code`
went from fetched-but-unused to actually weighted in `ErosionScorer`,
same for `soil_type_code` in `LandslideScorer` and the new
`flood_status_severity_code` in `FloodScorer`; see
`ml_service/inference/predictor.py`'s class docstrings for the exact
weights and the reasoning behind each.)

### 13. `backend/zone_classifier.py` — worst score wins

`classify_zone(zone_id, scores)` takes all four `ScoreResult`s and
returns a `ZoneClassification` colored by whichever hazard scored
worst — `ZoneColor.RED/YELLOW/GREEN`. A zone can be GREEN on flood but
RED overall because of erosion; the classifier doesn't average, it
takes the max risk.

### 14. `backend/prioritization.py` — folding in vulnerability

`prioritize(classification, vulnerability: VulnerabilityInputs, ...)`
combines the zone's color with population/vulnerability inputs and
historical severity into a `PrioritizationResult` with a
`PriorityTier`. (`VulnerabilityInputs`' population/census fields are
still the one documented placeholder in this repo — see the README's
"what's a placeholder" section below.)

### 15. `backend/api.py` + `frontend/dashboard.html` — serving it

`zone_status(zone_id)` is the FastAPI endpoint that ties steps 11–14
together on demand for `GET /api/zone-status/{zone_id}`.
`_bbox_from_point(lon, lat, radius_km)` supports the map's "click
anywhere" mode by building an ad-hoc bbox instead of requiring a known
`zone_id`. `dashboard.html` is the Leaflet frontend that calls these.

---

## What's a documented placeholder vs. what's real

- **Real and tested:** `cleaning.py`, `predictor.py`'s 4 weighted-formula
  scorers, `zone_classifier.py`, `prioritization.py`, the SQLite stores.
- **Real, live, automated (this update):** `sediment_type_code`
  (SoilGrids proxy, via `fetch_sediment_type.py`), all four
  `historical_*_count` fields (GDACS + NASA COOLR, via
  `fetch_historical_events.py`), `distance_to_river_m` (OSM Overpass,
  via `fetch_river_distance.py`), `distance_to_coast_m` (OSM Overpass,
  bug-fixed this update — see `STATIC_DATASETS.md`) — all refreshed
  automatically and staleness-aware via `auto_refresh.py`, zero manual
  steps for a new zone.
- **Real, live, automated once signed up (this update):** `river_level_m`
  (Google Flood Forecasting API, via `fetch_river_level.py`) — the one
  field that needs a one-time signup (`GOOGLE_FLOOD_API_KEY`) rather
  than nothing or a download; see "Getting `river_level_m` live" in
  `STATIC_DATASETS.md`. Zero manual steps after the key is set.
- **Real, one-time download + one command:** `shoreline_change_rate_m_per_yr`,
  `mangrove_cover_pct` — via `ingest_dataset.py`, once you've downloaded
  the source file (see `STATIC_DATASETS.md`).
- **Real, previously added:** `soil_type_code` (ISRIC SoilGrids),
  `soil_saturation_pct`/`soil_moisture_pct` (NASA POWER), `slope_deg`
  (derived), `river_level_change_rate_m_per_hr` (Open-Meteo/GloFAS —
  a discharge trend, not the same field as `river_level_m`, see
  `STATIC_DATASETS.md`), `wave_energy_index` (Open-Meteo Marine),
  `vegetation_index` (Agromonitoring NDVI, needs a free API key).
- **Placeholder, clearly marked in code:** `_PLACEHOLDER_VULNERABILITY`
  in `api.py` (no population/census ingestion exists yet).
- **Not built:** a trained-ML path for any hazard (all four currently
  use the weighted formula, by design).

### Field coverage summary (as of this update)

| Hazard | Now fetched live/automated | Still needs one-time download | Live, but needs one-time signup |
|---|---|---|---|
| FLOOD | rainfall, elevation, river discharge trend, soil saturation, **historical_flood_count (live)**, **distance_to_river_m (live)** | — (fully live/signup-covered) | **river_level_m** (Google Flood Forecasting API) |
| LANDSLIDE | slope, rainfall, soil moisture, soil type, NDVI, **historical_landslide_count (live)** | — (fully live) | — |
| EROSION | wave energy index, **distance_to_coast_m (live, bug-fixed)**, **sediment_type_code (live proxy)**, **historical_erosion_events (live proxy)** | shoreline_change_rate_m_per_yr, mangrove_cover_pct | — |
| CLOUDBURST | rainfall intensity, humidity, temperature, wind, elevation, **historical_cloudburst_count (live proxy)** | — (fully live) | — |

Every field in the entire pipeline is now either fetched live with zero
setup, fetched live after one one-time signup (`river_level_m` only), or
requires one one-time download plus a single ingestion command
(`shoreline_change_rate_m_per_yr` and `mangrove_cover_pct` only). Nothing
is a silent placeholder or a fabricated number anywhere in this repo.
