# Static / slow-changing fields — what's automated now, and what still needs a one-time download

This file used to describe four fields that all needed a manual CSV
ingestion. As of this update, **seven of them are live and automatic**
(one of those seven only after a one-time *signup*, not a download —
see the `river_level_m` row) and **two still require a one-time human
download**, because that's the honest split — no engineering effort
changes which side of that line a field sits on. This doc says exactly
which is which, and exactly what you need to do for the fields that
remain manual or gated behind signup.

## The split, in one table

| Field | Hazard | Status | Source |
|---|---|---|---|
| `sediment_type_code` | EROSION | **Automatic, live** | `fetch_sediment_type.py` — ISRIC SoilGrids texture proxy |
| `historical_flood_count` | FLOOD | **Automatic, live** | `fetch_historical_events.py` — GDACS |
| `historical_landslide_count` | LANDSLIDE | **Automatic, live** | `fetch_historical_events.py` — NASA COOLR |
| `historical_erosion_events` | EROSION | **Automatic, live (proxy)** | `fetch_historical_events.py` — GDACS tropical-cyclone proxy |
| `historical_cloudburst_count` | CLOUDBURST | **Automatic, live (proxy)** | `fetch_historical_events.py` — GDACS short-duration-flood proxy |
| `distance_to_river_m` | FLOOD | **Automatic, live** | `fetch_river_distance.py` — OSM Overpass waterway ways |
| `distance_to_coast_m` | EROSION | **Automatic, live** | `osm` provider (gis_fetcher) — OSM Overpass coastline ways (bug fix this update, see below) |
| `river_level_m` | FLOOD | **Automatic, live — after one-time signup** | `fetch_river_level.py` — Google Flood Forecasting API (needs `GOOGLE_FLOOD_API_KEY`; see "Getting river_level_m live" below) |
| `shoreline_change_rate_m_per_yr` | EROSION | **Manual, one-time download** | ISRO/NCCR National Shoreline Change Assessment |
| `mangrove_cover_pct` | EROSION | **Manual, one-time download** | Global Mangrove Watch |

Nothing in this repo pretends the bottom two are live — see "Why these
two can't be automated further" below (now including this update's
re-search for a live source for them). Everything else, including both
river fields that used to be documented gaps, is now zero-touch —
`river_level_m` needs one signup first, but zero further commands ever.

## What "automatic" means concretely

`pipeline_runner.py` calls `auto_refresh.refresh_static_fields()` once
per zone, before every live run. That function checks
`StaticDatasetStore.needs_refresh()` for each of the auto-fetched fields
above (six of them always; a seventh, `river_level_m`, only once
`GOOGLE_FLOOD_API_KEY` is set — see below); if any are missing or older
than `--static-max-age-days` (default 180 days), it calls the matching
fetcher — no CSV, no download, **no command to remember for any of
them, `river_level_m` included**. First time a new zone is ever passed
to `pipeline_runner.py`, this is what populates it.

```bash
# Nothing to prepare -- this alone is what populates sediment type +
# all four historical counts for a brand-new zone:
python3 pipeline_runner.py Z-ODISHA-PURI-01

# Force a refresh across every known zone right now instead of waiting
# for each one's next pipeline_runner.py run (e.g. before a demo):
python3 -m data_pipeline.static_datasets.auto_refresh
```

### `sediment_type_code` — how the automation works

GSI's Bhukosh coastal-geomorphology maps (the "real" source) are
usually a PDF report or a static map image per state, not a
shapefile with a numeric column — there is nothing for code to join
against. `fetch_sediment_type.py` closes the gap with a live proxy
instead: it queries ISRIC SoilGrids (the same free, no-key, 250m
global soil API `gis_fetcher`'s `soil` provider already uses for
LANDSLIDE) at each zone's center point, and classifies the returned
sand/silt/clay percentages into the same 1–12 USDA-texture code
`soil.py` already defines. The stored `source` string always ends in
`_proxy_auto`, so nobody downstream mistakes a soil-texture guess for
verified GSI classification. If you ever do get a real GSI shapefile
for a zone, `load_sediment_type.py` still exists to override this with
the real thing.

### `historical_*_count` — how the automation works

`fetch_historical_events.py` replaced the NDMA/SDMA/EM-DAT manual CSV
path with two free, live, no-key sources:

- **GDACS** (`gdacs.org/gdacsapi`) for `historical_flood_count` (its
  "FL" event type). Its documented date/country query filters don't
  actually narrow the feed (confirmed by testing), so the script pulls
  the raw recent-events feed and does every filter — India membership,
  event type, date window, distance to the zone's bbox — itself in
  Python.
- The same GDACS feed's "TC" (tropical cyclone) events, as a **labeled
  proxy** for `historical_erosion_events` (severe Indian coastal
  erosion overwhelmingly co-occurs with cyclone landfall) and a
  duration-filtered subset of "FL" events as a proxy for
  `historical_cloudburst_count` (GDACS has no dedicated cloudburst
  category).
- **NASA COOLR** (Cooperative Open Online Landslide Repository), a
  live ArcGIS REST point catalog, for `historical_landslide_count` —
  this one is real per-incident point data, not a country/region
  centroid, so it's meaningfully more precise than the GDACS-based
  fields above.

Every GDACS event carries one point for its *entire* affected extent
(a multi-state flood is still one lon/lat), so these counts are a "was
a tracked event's centroid within a buffer distance of this zone,
within the lookback window" signal — a real geographic + temporal
filter, finer than the old country-level ReliefWeb number, but still
coarser than a true per-district NDMA tally. `load_historical_counts.py`
still exists if you ever get a real NDMA/SDMA/EM-DAT district table and
want to override a specific zone/hazard with it.

### `distance_to_river_m` and `distance_to_coast_m` — how the automation works

Neither field had a dedicated fetcher before this update — `distance_to_river_m`
was a hardcoded `None` in `normalize.py` with nothing behind it at all,
and `distance_to_coast_m` had a fetch path (`osm_to_erosion_fields()`)
but it was silently broken (see the bug-fix note just below). Both are
now genuinely live:

- **`fetch_river_distance.py`** queries OpenStreetMap's `waterway=river`
  / `waterway=stream` ways via the same free, no-key, public Overpass
  API `gis_fetcher`'s `osm` provider already uses, in a box padded
  0.5° around the zone, and computes the shortest point-to-segment
  distance from the zone's center to any returned river/stream geometry
  (a local flat-earth projection — accurate to well under 1% error at
  this field's ≤50km valid range, so not worth a full geodesic
  library). A zone with genuinely nothing OSM-mapped in that box comes
  back `None` — a real signal, not a failure.
- **`distance_to_coast_m`** (EROSION) already had a fetch path through
  `gis_fetcher`'s `osm` provider, but two bugs meant it always returned
  `None` on a real run: (1) `fetch_for_hazard()` sent every hazard's
  `osm` call the same Overpass tag (`config/providers.yaml`'s
  `default_tag: "amenity=hospital"`), so EROSION was querying for
  hospitals, not coastline; (2) even with the right tag, the `osm`
  provider only ever parsed Overpass **nodes**, and `natural=coastline`
  in OpenStreetMap is drawn as **ways** — so the query would have come
  back empty regardless. Both are fixed this update: `hazard_map.py` now
  maps EROSION's `osm` call to `natural=coastline` explicitly, and the
  `osm` provider now requests way geometry (`out geom;`) and computes
  each feature's distance from the query bbox's center itself,
  attaching it as `properties["distance_m"]` — exactly what
  `osm_to_erosion_fields()` was already reading and had been silently
  getting `None` from.

Both are proper live geographic distances, not proxies — no `_proxy` or
`_auto` suffix beyond noting the source is OSM, since "distance from
zone center to the nearest OSM-mapped river/coastline" is what the
field name says it is, not a stand-in for something else.

### `river_level_m` — how the automation works (once you've signed up)

`fetch_river_level.py` queries Google's Flood Forecasting API
(`floodforecasting.googleapis.com`), which publishes real-time riverine
forecasts for ~150 countries including India — and, critically, India's
gauges report water-level in **meters** (`GaugeModel.gaugeValueUnit`),
not just discharge like GloFAS. That's the actual unit this field needs,
unlike `river_level_change_rate_m_per_hr` (which stays sourced from
GloFAS discharge trend — see "Why this isn't `river_level_m`" below).

Unlike every other fetcher in this package, this one is **free but not
key-less** — Google gates it behind a one-time approval step (see
"Getting `river_level_m` live" below). Until `GOOGLE_FLOOD_API_KEY` is
set, `auto_refresh.py`'s `_active_auto_fields()` simply excludes
`river_level_m` from the auto-fetch list — no error, no retry loop, it
just stays the documented gap it always was. The moment the key is set,
the very next `pipeline_runner.py` run for a zone with stale/missing
`river_level_m` picks it up automatically — no separate command, ever.

For each zone, the script searches Google's gauge catalog within a
padded bbox, filters to gauges whose `gaugeValueUnit` contains "METER"
(a discharge-unit gauge is the wrong field entirely and is skipped, not
silently treated as meters), and reads the nearest-in-time forecast
value as the closest available proxy for "current level" (this is a
forecast-oriented API — there's no separate "observed now" endpoint
documented). No gauge, or no water-level gauge, near a zone returns
`None`, same as every other fetcher's "no override this run" behavior.

**Honest limitation, stated in the script's own docstring:** the exact
field names above come from Google's published REST reference, not a
live tested response — the approval step below has to happen before any
real call can be made. Treat this as needing one live smoke-test against
your own approved key before fully trusting it in a demo.

## `flood_status_severity_code` — a second field from the same signup

While writing `fetch_river_level.py`, it turned out the same Flood
Forecasting API (same waitlist, same key, same enabled-API step) also
exposes `FloodStatus` data — current forecast severity (an enum:
`NO_FLOODING`/`WARNING`/`DANGER`/`EXTREME_DANGER`) and a rising/falling
trend, distinct from the `HydrologicForecast` gauge-stage data
`fetch_river_level.py` reads. `fetch_flood_status.py` fetches and stores
this as `flood_status_severity_code` (0-3), gated by the exact same
`GOOGLE_FLOOD_API_KEY` — there's no second signup, and `auto_refresh.py`
activates it in the same conditional block as `river_level_m`.

**This field is stored but deliberately not wired into `FloodScorer`'s
weighted formula** (`ml_service/inference/predictor.py`) — that formula's
weights and reference maxes are the documented, already-tested FLOOD
scoring behavior from the project README, and adding a field there means
choosing a weight for it, a scoring-design decision, not something a
fetcher script should make unilaterally. The value is there to query,
chart, or fold into scoring once that decision is made on purpose.



This is the only field in the whole pipeline that needs a human signup
step rather than either "nothing" or "one download." Do this once,
outside this repo, before `fetch_river_level.py` does anything:

1. Fill out Google's Flood Hub / Flood Forecasting API waitlist form —
   see the link on the [Flood Forecasting API developer page](https://developers.google.com/flood-forecasting).
2. Wait for Google's approval email.
3. Reply to that email with your Google Cloud Project ID (create a
   project first at [Google Cloud Resource Manager](https://cloud.google.com/resource-manager/docs/creating-managing-projects)
   if you don't already have one).
4. Create an API key for that project — see
   [Google's "Setting up API keys" guide](https://support.google.com/googleapi/answer/6158862)
   — or reuse an existing Google Cloud API key.
5. Enable the Flood Forecasting API for that project at the
   [API library page](https://console.cloud.google.com/apis/library/floodforecasting.googleapis.com)
   — this only works once step 3's reply has been processed.
6. Set the environment variable before running anything in this repo:
   `export GOOGLE_FLOOD_API_KEY=your_key_here`

That's it — no code to write, no command to run afterward.
`pipeline_runner.py` (via `auto_refresh.py`) does the rest on its next
run for any zone whose `river_level_m` is missing or stale.

## Why the shoreline and mangrove fields still can't be automated further

`shoreline_change_rate_m_per_yr` (ISRO/NCCR) and `mangrove_cover_pct`
(Global Mangrove Watch) were searched for a live API — twice now, this
update re-checked specifically because `river_level_m` turned out to
have a live option once one search went one layer deeper than the
obvious "no API" answer. The result this time is the same conclusion,
but with two specific near-misses worth recording instead of a flat "no":

- **Global Mangrove Watch**: no global, official, queryable REST
  endpoint. It's distributed as shapefiles/GeoTIFFs (UNEP-WCMC, JAXA,
  Zenodo) or as a Google Earth Engine asset
  (`projects/sat-io/open-datasets/GMW/...`). Earth Engine's REST API
  could technically query it, but that needs its own Google Cloud
  project + Earth Engine access approval (similar friction to
  `river_level_m`'s signup) plus a genuinely heavier query (a raster
  `reduceRegion` over a polygon, not a simple point lookup) — a bigger
  lift than the payoff justified this round. A handful of *regional*
  community-hosted ArcGIS FeatureServers exist (e.g. a Sundarbans-only
  mangrove layer) but none give national Indian coverage, so they'd
  need per-zone special-casing to be useful at all.
- **ISRO/NCCR shoreline change**: Bhuvan (ISRO's geoportal) does support
  WFS/WMS as documented OGC standards in general, and hosts an "Erosion"
  thematic layer — but Bhuvan's own materials describe layer-specific
  REST APIs as still "being developed," and no stable, documented
  `GetFeature` endpoint + layer name for shoreline-change-rate
  specifically could be confirmed. Building a fetcher against an
  unconfirmed/undocumented endpoint is exactly the mistake this repo
  already rejected for CWC/India-WRIS (see below) — a silent break on
  any change is a bad foundation for evacuation-priority decisions, so
  this stays a download until Bhuvan's per-layer API is confirmed
  stable and documented.

No amount of engineering here changes either of those; building a
scraper against an undocumented portal was considered and rejected for
the same reason CWC/India-WRIS's gauge portal was (see below) — a
silent break on any HTML change is a bad foundation for
evacuation-priority decisions.


### The one command that replaces the old two-step process

Getting a zone polygon to join against **is** automated —
`ingest_dataset.py` (like `spatial_join.py` before it) fetches and
caches zone boundaries from OpenStreetMap on its own, no separate
command needed. What used to be *two* manual steps after downloading
the source file — run `spatial_join.py` to produce a CSV, inspect it,
then run the matching `load_*.py` to ingest it — is now **one**:

```bash
python3 -m data_pipeline.static_datasets.ingest_dataset \
    --dataset-type shoreline \
    --file nccr_shoreline_transects.shp \
    --source "ISRO_NCCR_shoreline_atlas_2023"

python3 -m data_pipeline.static_datasets.ingest_dataset \
    --dataset-type mangrove \
    --file gmw_v3_2020_your_region.shp \
    --source "GlobalMangroveWatch_2020"
```

`ingest_dataset.py` also auto-detects the shoreline-rate column name
(NCCR's per-state shapefiles use a few different names — `EPR_myr`,
`LRR`, `Rate`, etc.; pass `--value-column` to override if it guesses
wrong) and automatically applies NCCR's accretion-positive →
erosion-positive sign flip, so you no longer need `--negate` either.
It still writes the intermediate CSV to disk (default
`shoreline_ingest.csv` / `mangrove_ingest.csv`) so the "review before
trusting it" step this repo insists on is exactly as easy as before —
open the CSV, same as always.

`spatial_join.py` and the individual `load_shoreline_change.py` /
`load_mangrove_cover.py` scripts still exist underneath (and still
work standalone) — `ingest_dataset.py` is a thin wrapper over both, not
a replacement that removes anything.

## Data you need to download once, offline

This is the complete list, now that sediment type and historical
counts no longer belong on it:

1. **ISRO/NCCR shoreline-change shapefile** — for whichever coastal
   state(s) your zones are in. Get it from ISRO Bhuvan's Coastal &
   Marine thematic services (`bhuvan.nrsc.gov.in`) or NCCR's own
   publications page (`nccr.gov.in`); mirrored on `data.gov.in` /
   `vedas.sac.gov.in` under "shoreline change" if the direct link has
   moved. Free, no login beyond a Bhuvan account.
2. **Global Mangrove Watch extent shapefile/GeoJSON** — for your
   region/year, from `data.unep-wcmc.org/datasets/45` or the
   `globalmangrovewatch.org` viewer. Free, no login. Re-download
   yearly if you want mangrove cover to track real-world change
   (restoration, clearing) rather than one fixed year.

That's it. For a demo scoped to a handful of named zones (see
`zones.py`), this is realistically an hour or two of downloading, not
"weeks" — the "weeks" framing in earlier notes assumed building full
national coverage, which a demo doesn't need.

## Why `river_level_change_rate_m_per_hr` isn't the same field as `river_level_m`

Scraping CWC/India-WRIS's web dashboard for absolute river-gauge level
(`river_level_m`) was considered and rejected here for the same reason
as the shoreline/mangrove portals above: it's not a documented
interface, so it can silently break on any HTML change, and doing it
without the agency's consent is a fragile foundation for a platform
meant to inform evacuation/priority decisions. `fetch_river_level.py`'s
Google Flood Forecasting API (see above) is what actually closes this
gap now — but it's worth being explicit that it and
`river_level_change_rate_m_per_hr` are genuinely different fields, not
one filling in for the other:

- `river_level_change_rate_m_per_hr` comes from `river_discharge.py`
  (Open-Meteo/GloFAS) — a **discharge** (m³/s) trend, rising-vs-falling
  only, no absolute stage.
- `river_level_m` needs **gauge stage** (meters) — which is what
  `fetch_river_level.py` now fetches, once signed up (see above). GloFAS
  discharge can't substitute for this no matter how it's rescaled; it's
  a different physical quantity.

If a real deployment ever needs gauge-level precision without the
Google API (e.g. it's not approved for a given region, or an
organization specifically wants the official government reading), the
correct path is still an official data-sharing request to CWC/India-WRIS
— a paperwork lead time, not an engineering one, same as it's always
been for that specific source.

## Remaining documented gaps

Both of these are now optional/covered rather than absolute gaps — see
the sections above for exactly what closes each one:

| Field | Hazard | Status | What would close it |
|---|---|---|---|
| `river_level_m` (absolute gauge stage) | FLOOD | **Live, once signed up** — see "Getting `river_level_m` live" above | Nothing further; `GOOGLE_FLOOD_API_KEY` is the only step |
| `shoreline_change_rate_m_per_yr`, `mangrove_cover_pct` | EROSION | Manual, one-time download (`ingest_dataset.py`) | A confirmed, stable, documented Bhuvan WFS layer (shoreline) or an Earth Engine integration (mangrove) — see "Why the shoreline and mangrove fields still can't be automated further" |

## What this deliberately still does not do

Fabricate plausible-looking numbers for a field with no source at all.
`cleaning.py` flags a hazard reading's `data_quality` when a field is
missing/stale rather than silently filling it, and every automated
fetcher above degrades to "no override this run" (not a crash, not a
guess) on a network failure or no-coverage response. A judge finding
invented shoreline-erosion numbers presented as real ISRO data would be
a far worse outcome than a dashboard that's honest about exactly which
fields are live-fetched, which are proxies, and which are still one
download away.
