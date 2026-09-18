# gis-fetcher

A small, extensible framework for pulling **live** geospatial data from
multiple public APIs and merging it into one normalized GeoJSON output.
Built to make adding the *next* API source a one-file change.

Ships with eleven providers. The original four are generic/no-API-key
demo providers; the other seven were added specifically to fill the
hazard-platform field gaps (flood/landslide/erosion/cloudburst) — see
`hazard_map.py` and the field-gap table below.

| Provider name | Source | What it returns | Key needed? | Cache tier |
|---|---|---|---|---|
| `weather` | [Open-Meteo](https://open-meteo.com) | Current weather at the bbox center | No | dynamic |
| `earthquakes` | [USGS](https://earthquake.usgs.gov) | Recent earthquake events in the bbox (not used by any of the 4 hazards — bonus/demo only) | No | dynamic |
| `osm` | [Overpass API](https://overpass-api.de) (OpenStreetMap) | Points of interest matching a tag; also distance-to-coastline for erosion | No | static |
| `elevation` | [Open-Elevation](https://www.open-elevation.com) | Terrain elevation at a point | No | static |
| `soil` | [ISRIC SoilGrids](https://rest.isric.org) | USDA soil texture class, from sand/silt/clay % | No | static |
| `slope` | Open-Elevation (5-point sample, no new API) | Terrain slope in degrees, derived not looked up | No | static |
| `land_hydrology` | [NASA POWER](https://power.larc.nasa.gov) | Root-zone/surface soil wetness (flood saturation + landslide moisture proxy) | No | dynamic |
| `river_discharge` | [Open-Meteo Flood API](https://open-meteo.com/en/docs/flood-api) (GloFAS) | River discharge (m³/s) + day-over-day trend | No | dynamic |
| `marine` | [Open-Meteo Marine API](https://open-meteo.com/en/docs/marine-weather-api) | Wave height/period → wave energy index for coastal erosion | No | dynamic |
| `vegetation` | [Agromonitoring](https://agromonitoring.com) | NDVI vegetation index (landslide bare-slope risk) | **Yes** — free tier, 1,000 calls/day | dynamic |
| `historical_events` | [ReliefWeb](https://reliefweb.int/help/api) | Country-level historical disaster counts by hazard type | No | static |

Nine of the eleven need no key or payment at all. Only `vegetation`
needs registration, and its free tier is enough for a pilot deployment.
See `hazard_platform/STATIC_DATASETS.md` for the handful of fields
(shoreline change rate, sediment type, mangrove cover, absolute river
gauge level) that have **no** live API anywhere, cheap or otherwise, and
need a one-time dataset ingestion instead.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# or, for an editable install with the `gis-fetch` command:
pip install -e .
```

`vegetation` additionally needs `AGROMONITORING_API_KEY` set in your
environment (config/providers.yaml reads it via `${AGROMONITORING_API_KEY}`).
Every other provider works with zero configuration.

## Run it

### Generic CLI — any provider list, any bbox

```bash
python -m gis_fetcher.cli \
    --providers weather earthquakes osm \
    --bbox -122.6 37.6 -122.3 37.9 \
    --out sf_bay.geojson \
    --cache --verbose
```

This queries every named provider **concurrently**, retries transient
failures with backoff, respects any per-provider rate limit set in
`config/providers.yaml`, and writes one merged `FeatureCollection` to
`sf_bay.geojson`. A provider failing doesn't stop the others -- check
stderr for `[warn]` lines.

### Hazard-scoped fetch — the one hazard_platform should use

```python
from gis_fetcher.core.base import BBox
from gis_fetcher.hazard_map import fetch_for_hazard

bbox = BBox(min_lon=77.55, min_lat=13.0, max_lon=77.65, max_lat=13.1)
results = fetch_for_hazard("FLOOD", bbox)
```

`fetch_for_hazard()` only calls the providers a given hazard actually
uses (see `HAZARD_PROVIDERS` in `hazard_map.py`) instead of every
registered provider, and applies a tiered on-disk cache automatically —
static facts (soil, slope, elevation, OSM) cache for 90 days, dynamic
ones (weather, river discharge, marine, NDVI) for a few hours. This is
what keeps a per-zone hazard check fast even as more providers get
added: extra providers only cost time when a hazard actually calls them,
and repeat calls for the same zone hit the cache instead of the network.

```python
from gis_fetcher.hazard_map import fetch_all_hazards_for_zone
all_results = fetch_all_hazards_for_zone(bbox)  # dict[hazard_name] -> results
```

Load either CLI or programmatic results anywhere that reads GeoJSON,
including geopandas:

```python
import geopandas as gpd
gdf = gpd.read_file("sf_bay.geojson")
```

See also `examples/quickstart.py` for the plain (non-hazard-scoped) API.

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Provider tests use a tiny fake `aiohttp`-like session (`tests/fakes.py`)
instead of hitting real APIs, so the whole suite runs offline and fast.
(The 7 new providers added for the hazard platform don't yet have
dedicated unit tests in `tests/` — they were validated by a live
provider-registration smoke test and manual endpoint checks; adding
`fakes.py`-style tests for them is a good next task, following
`tests/test_providers.py`'s existing pattern.)

## Project layout

```
gis_fetcher/
  core/
    base.py       # BBox / Feature / FetchResult data model + the GISDataProvider interface
    registry.py   # name -> provider class registry (the plugin mechanism)
    fetcher.py    # orchestrator: concurrency, retries, rate limiting, tiered caching
    cache.py      # simple disk cache with a per-call TTL override
    config.py     # loads config/providers.yaml (with ${ENV_VAR} expansion for secrets)
  providers/
    weather.py, earthquakes.py, osm_vector.py, elevation.py   # original 4 demo providers
    soil.py, slope.py, land_hydrology.py, river_discharge.py, # added for the 4-hazard
    marine.py, vegetation.py, historical_events.py            # field gaps -- see table above
  hazard_map.py   # HAZARD_PROVIDERS + fetch_for_hazard() / fetch_all_hazards_for_zone()
  cli.py          # `python -m gis_fetcher.cli` / `gis-fetch` command
config/
  providers.yaml  # per-provider retry counts, rate limits, cache TTLs, API keys
tests/            # unit tests per module, all offline (mocked HTTP)
examples/
  quickstart.py   # programmatic usage without the CLI
```

## Adding a new API source

This is the whole point of the architecture -- it should never require
touching `fetcher.py`, `cli.py`, or any existing provider.

1. **Create `gis_fetcher/providers/my_source.py`:**

    ```python
    from ..core.base import BBox, Feature, GISDataProvider
    from ..core.registry import register_provider

    @register_provider("my_source")
    class MySourceProvider(GISDataProvider):
        requires_api_key = True  # set False if none needed
        BASE_URL = "https://api.example.com/v1/data"

        async def fetch(self, bbox: BBox, **params) -> list:
            query = {
                "bbox": f"{bbox.min_lon},{bbox.min_lat},{bbox.max_lon},{bbox.max_lat}",
                "api_key": self.config.get("api_key"),
            }
            async with self.session.get(self.BASE_URL, params=query) as resp:
                resp.raise_for_status()
                data = await resp.json()

            return [
                Feature(
                    geometry=item["geometry"],       # must be a GeoJSON geometry dict
                    properties=item["properties"],
                    source=self.name,
                )
                for item in data["results"]
            ]
    ```

2. **Register it for import** by adding it to `gis_fetcher/providers/__init__.py`:

    ```python
    from . import weather, earthquakes, osm_vector, elevation, my_source
    ```

3. **Add config** in `config/providers.yaml`, including a cache tier:

    ```yaml
    providers:
      my_source:
        api_key: "${MY_SOURCE_API_KEY}"
        max_retries: 3
        min_interval_seconds: 1
        cache_ttl_seconds: 21600  # pick static (~7776000, 90 days) or dynamic (hours) based on how fast the data changes
    ```

4. **If it feeds one of the 4 hazards**, add its name to the right list
   in `hazard_map.py`'s `HAZARD_PROVIDERS`, and add a matching
   `..._to_<hazard>_fields()` function in `hazard_platform`'s
   `normalize.py` to map its output into the locked field names.

5. Use it immediately: `--providers my_source ...` or
   `fetcher.fetch(["my_source"], bbox)`.

That's it -- the fetcher, CLI, caching, retry/backoff, and rate limiting
all pick it up automatically because they only ever go through the
registry and the common `GISDataProvider` interface.

## Design notes

- **Concurrency**: providers are fetched in parallel with `asyncio` +
  a shared `aiohttp.ClientSession`, so requesting data from many APIs
  costs roughly as much wall-clock time as the slowest one, not the
  sum of all of them.
- **Scoping**: `hazard_map.py` narrows "many APIs" down to "only the
  ones this hazard needs" *before* that fan-out happens — the fan-out
  being parallel was never the bottleneck; calling irrelevant providers
  was.
- **Isolation**: one provider raising an exception (bad response,
  timeout, rate-limited) never takes down the others -- each is
  wrapped individually and reported back as `ok=False` with an error
  message.
- **Normalization**: every provider, regardless of upstream shape,
  returns a list of `Feature` objects (GeoJSON geometry + properties +
  source + timestamp). Downstream code never needs to special-case a
  provider's raw response format.
- **Rate limiting & retries**: configured per-provider in
  `providers.yaml` (`min_interval_seconds`, `max_retries`) rather than
  hardcoded, since different free APIs tolerate very different request
  rates.
- **Tiered caching**: a TTL'd disk cache keyed on
  `(provider, bbox, params)`, with the TTL settable per-provider-call
  (`Cache.get(key, ttl_seconds=...)`) rather than fixed for the whole
  cache. `hazard_map.py`'s `build_fetcher()` sets 90-day TTLs for static
  providers (soil, slope, elevation, OSM) and a few hours for dynamic
  ones (weather, river discharge, marine, vegetation), so slow-changing
  facts about a place are effectively free to re-check while
  fast-changing ones stay fresh. Swap `Cache` for a Redis/S3-backed
  version without touching the fetcher, which only calls
  `get`/`set`/`make_key`.
- **Honesty about gaps**: fields with no free/cheap live API anywhere
  (absolute river gauge level, shoreline change rate, sediment type,
  mangrove cover) are left as `None` rather than approximated with a
  provider that quietly returns fabricated numbers — see
  `hazard_platform/STATIC_DATASETS.md` for what those need instead.

