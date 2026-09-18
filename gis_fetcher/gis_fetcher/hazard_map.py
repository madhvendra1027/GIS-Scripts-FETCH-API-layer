"""
hazard_map.py -- the piece that keeps the pipeline fast as more providers
get added.

Problem this solves: gis_fetcher.fetch_many() already fans requests out
concurrently (asyncio.gather in core/fetcher.py) -- that part was never
slow. The actual latency risk is calling *every* provider for *every*
hazard check, including ones a given hazard doesn't use (e.g. asking for
`marine` wave data when scoring FLOOD). Every extra provider in the list
adds its own worst-case latency (rate limits, retries with backoff) to
the batch's total wall-clock time, because fetch_many() still has to wait
for the slowest one before returning.

The fix is scoping, not more parallelism: each hazard only ever calls the
providers whose fields it actually consumes (see HAZARD_PROVIDERS below),
and slow-changing "static" providers get cached for months (see
providers.yaml's `cache_ttl_seconds`) so they're a cache hit -- effectively
free -- on every check after the first for a given zone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .core.base import BBox, FetchResult
from .core.fetcher import GISDataFetcher
from .core.cache import Cache
from .core.config import load_config

# Only the providers each hazard's normalize.py functions actually read.
# Keep this in sync with FIELD_ORDER in hazard_platform's
# ml_service/features/feature_engineering.py -- that's the source of
# truth for which fields a hazard needs; this is which providers supply them.
HAZARD_PROVIDERS: dict[str, list[str]] = {
    "FLOOD": ["weather", "elevation", "river_discharge", "land_hydrology", "historical_events"],
    "LANDSLIDE": ["weather", "slope", "soil", "land_hydrology", "vegetation", "historical_events"],
    "EROSION": ["marine", "osm", "historical_events"],
    "CLOUDBURST": ["weather", "elevation", "historical_events"],
}

# The `osm` provider's default tag (config/providers.yaml's
# `default_tag: "amenity=hospital"`) is wrong for EROSION -- it needs
# coastline geometry, not hospitals, to compute distance_to_coast_m (see
# normalize.py's osm_to_erosion_fields()). Every other hazard using
# `osm` (currently none) would fall through to the config default.
_OSM_TAG_OVERRIDES: dict[str, str] = {
    "EROSION": "natural=coastline",
}

# STATIC providers barely change (terrain, soil texture) -> cache for
# months. DYNAMIC providers change hourly/daily -> cache briefly, just
# enough to survive a burst of requests for the same zone. This mirrors
# `cache_ttl_seconds` in config/providers.yaml -- defined here too so
# callers get sane defaults even without editing YAML.
_STATIC_PROVIDERS = {"elevation", "slope", "soil", "osm"}
_DEFAULT_STATIC_TTL = 60 * 60 * 24 * 90     # 90 days
_DEFAULT_DYNAMIC_TTL = 60 * 60 * 3          # 3 hours


def providers_for_hazard(hazard_type: str) -> list[str]:
    try:
        return HAZARD_PROVIDERS[hazard_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown hazard_type '{hazard_type}'. Expected one of {list(HAZARD_PROVIDERS)}"
        ) from exc


def _resolve_config_path(config_path: str) -> str:
    """config/providers.yaml is looked up relative to the CURRENT
    WORKING DIRECTORY by default (see core/config.py's load_config()),
    which silently returns an empty {"providers": {}} config -- no
    error -- whenever a caller (e.g. hazard_platform's pipeline_runner.py,
    always run from hazard_platform's own directory) doesn't happen to
    have its own copy of that file. Every provider that reads api_key,
    max_retries, or a default_tag from providers.yaml then silently gets
    none of that -- this is exactly what broke `vegetation`'s
    AGROMONITORING_API_KEY expansion.

    Fix: if the CWD-relative path doesn't exist, fall back to
    gis_fetcher's own bundled config/providers.yaml (this file's
    grandparent directory), so build_fetcher() works the same regardless
    of which directory a caller happens to run from."""
    if Path(config_path).exists():
        return config_path
    bundled = Path(__file__).resolve().parent.parent / config_path
    if bundled.exists():
        return str(bundled)
    return config_path  # neither exists -- let load_config()'s own "file not found -> {}" behavior stand


def build_fetcher(config_path: str = "config/providers.yaml", cache_dir: str = ".gis_cache") -> GISDataFetcher:
    """Loads providers.yaml and fills in a default cache_ttl_seconds per
    provider (static vs dynamic) for any provider that doesn't set one
    explicitly, then returns a ready-to-use fetcher."""
    config = load_config(_resolve_config_path(config_path))
    providers_cfg = config.setdefault("providers", {})
    for name in {p for plist in HAZARD_PROVIDERS.values() for p in plist}:
        entry = providers_cfg.setdefault(name, {})
        entry.setdefault(
            "cache_ttl_seconds",
            _DEFAULT_STATIC_TTL if name in _STATIC_PROVIDERS else _DEFAULT_DYNAMIC_TTL,
        )
    return GISDataFetcher(config=config, cache=Cache(enabled=True, directory=cache_dir))


def fetch_for_hazard(
    hazard_type: str,
    bbox: BBox,
    fetcher: Optional[GISDataFetcher] = None,
    extra_params: Optional[dict] = None,
) -> list[FetchResult]:
    """Fetch only the providers `hazard_type` needs, in parallel, with
    static providers served from a long-lived cache. This is the function
    hazard_platform's normalize.py / api.py should call instead of
    reaching into gis_fetcher provider-by-provider.
    """
    fetcher = fetcher or build_fetcher()
    providers = providers_for_hazard(hazard_type)
    params = dict(extra_params or {})
    params.setdefault("hazard_type", hazard_type)  # historical_events reads this
    if hazard_type in _OSM_TAG_OVERRIDES:
        params.setdefault("tag", _OSM_TAG_OVERRIDES[hazard_type])
    return fetcher.fetch(providers, bbox, params)


def fetch_all_hazards_for_zone(
    bbox: BBox,
    fetcher: Optional[GISDataFetcher] = None,
) -> dict[str, list[FetchResult]]:
    """Convenience for the dashboard's per-zone click: fetches all 4
    hazards' provider sets. The union of providers across all 4 hazards
    is still smaller than "everything gis_fetcher can do" (earthquakes
    is deliberately excluded -- out of scope per the problem statement),
    and every provider is still cached, so a second zone reusing a
    static provider's result (e.g. two zones drawing from the same
    elevation cache entry) never re-hits the network.
    """
    fetcher = fetcher or build_fetcher()
    return {hazard: fetch_for_hazard(hazard, bbox, fetcher=fetcher) for hazard in HAZARD_PROVIDERS}
