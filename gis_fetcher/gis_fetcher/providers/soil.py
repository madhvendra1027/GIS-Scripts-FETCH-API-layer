"""
Static soil texture, sourced via Google Earth Engine's hosted mirror of
ISRIC SoilGrids (asset family `projects/soilgrids-isric/*`). Fills
`soil_type_code` for LANDSLIDE (and gives a sanity check on NASA POWER's
soil-wetness numbers).

CHANGED 2026-09-18: previously queried ISRIC's own REST API
(rest.isric.org) directly. ISRIC has since paused that REST API
indefinitely ("we are currently experiencing issues... and have decided
to temporarily pause the service", no ETA given) -- and even before the
pause, ISRIC's own docs describe it as a best-effort beta convenience
layer, not something to depend on in production. Google Earth Engine
hosts the same underlying SoilGrids prediction rasters as a stable,
Google-run asset, so this now queries GEE instead. See
STATIC_DATASETS.md / SESSIONS_README.md for the one-time GEE project
setup this requires (Cloud project, Earth Engine registration, a
service account + JSON key) -- once done, `GEE_SERVICE_ACCOUNT_EMAIL`
and `GEE_SERVICE_ACCOUNT_KEY_PATH` (read via providers.yaml's `soil:`
block, same `${VAR}` env-expansion as every other provider's api_key)
are all this needs.

Queries the sand/silt/clay mean-prediction images at the 0-5cm band and
classifies the USDA texture class ourselves (1=clay ... 12=sand), rather
than trusting a single upstream label field, because SoilGrids'
classification layers report WRB soil groups (things like "Acrisols"),
not the texture classes ml_service actually keys `soil_type_code` on --
this reasoning is unchanged from the old ISRIC-REST version, only the
transport underneath it changed.

Queries a *region* (the zone's own bbox, not a single lon/lat point):
SoilGrids' 250m grid can put a raw point query on a no-data pixel
(water, bare rock, a masked urban cell) even when the surrounding area
has real values -- confirmed hitting this during setup testing on a
coastal zone (Puri, Odisha). Averaging over the bbox with
`reduceRegion(reducer=ee.Reducer.mean())` instead of a point query
avoids betting the whole result on one pixel.

STATIC provider: soil texture at a point does not change on human
timescales. Give this a long cache_ttl_seconds (e.g. 7776000 / 90 days)
in providers.yaml instead of re-fetching per hazard check -- see
hazard_map.py for the tiered static/dynamic cache split.
"""

from __future__ import annotations

import asyncio
import threading

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider

# SoilGrids images on GEE are one image per property (not per depth+
# property like the old REST API's response shape), with one band per
# standard depth interval -- we only need the shallowest (0-5cm) band,
# consistent with what the old ISRIC-REST version queried.
_GEE_ASSET_BAND = {
    "sand": ("projects/soilgrids-isric/sand_mean", "sand_0-5cm_mean"),
    "silt": ("projects/soilgrids-isric/silt_mean", "silt_0-5cm_mean"),
    "clay": ("projects/soilgrids-isric/clay_mean", "clay_0-5cm_mean"),
}
# SoilGrids reports these bands as g/kg scaled by a d_factor of 10 --
# same convention (and same /10 conversion to percent) as the old
# ISRIC-REST response used, just hardcoded here since GEE's image
# metadata doesn't expose d_factor the way the REST JSON did.
_D_FACTOR = 10

# ee.Initialize() is a one-time, process-wide call -- doing it inside
# fetch() (which gets a *new* provider instance per call, see
# core/fetcher.py's `provider = provider_cls(...)`) would either
# reinitialize needlessly or race across concurrent providers. Guarded
# module-level instead, the same shape as a lazy singleton.
_ee_lock = threading.Lock()
_ee_ready = False


def _ensure_ee_initialized(service_account: str, key_path: str) -> None:
    global _ee_ready
    if _ee_ready:
        return
    with _ee_lock:
        if _ee_ready:
            return
        if not service_account or not key_path:
            raise RuntimeError(
                "soil provider: GEE_SERVICE_ACCOUNT_EMAIL / GEE_SERVICE_ACCOUNT_KEY_PATH "
                "not configured -- see providers.yaml's soil: block and .env.example. "
                "(ISRIC's own REST API is paused indefinitely, so this provider no longer "
                "has a no-setup fallback.)"
            )
        import ee

        credentials = ee.ServiceAccountCredentials(service_account, key_path)
        ee.Initialize(credentials)
        _ee_ready = True


def _query_sand_silt_clay_pct(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    service_account: str, key_path: str,
) -> dict:
    """Blocking (non-async) GEE call -- run this via asyncio.to_thread
    from fetch() below, since the earthengine-api client makes its own
    synchronous HTTP calls under the hood and would otherwise stall the
    event loop every other provider shares."""
    _ensure_ee_initialized(service_account, key_path)
    import ee

    region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
    image = None
    for prop, (asset_id, band) in _GEE_ASSET_BAND.items():
        band_image = ee.Image(asset_id).select(band).rename(prop)
        image = band_image if image is None else image.addBands(band_image)

    stats = image.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=250, bestEffort=True
    ).getInfo()

    fractions = {}
    for prop in _GEE_ASSET_BAND:
        raw = stats.get(prop)
        if raw is not None:
            fractions[prop] = raw / _D_FACTOR
    return fractions

# USDA texture triangle, coarsely bucketed. Good enough to rank landslide
# permeability (sand=free-draining/low risk .. clay=poor-draining/high risk)
# without needing a full soil-science implementation.
_TEXTURE_ORDER = [
    (1, "clay"), (2, "silty_clay"), (3, "sandy_clay"), (4, "clay_loam"),
    (5, "silty_clay_loam"), (6, "sandy_clay_loam"), (7, "loam"),
    (8, "silty_loam"), (9, "sandy_loam"), (10, "silt"),
    (11, "loamy_sand"), (12, "sand"),
]


def _classify_usda_texture(sand_pct: float, silt_pct: float, clay_pct: float) -> tuple[int, str]:
    """Simplified USDA texture classification from sand/silt/clay percent."""
    if clay_pct >= 40:
        code, name = (1, "clay") if silt_pct < 40 else (2, "silty_clay")
        if sand_pct >= 45 and clay_pct < 55:
            code, name = (3, "sandy_clay")
        return code, name
    if clay_pct >= 27:
        if sand_pct >= 45:
            return 6, "sandy_clay_loam"
        if silt_pct >= 40:
            return 5, "silty_clay_loam"
        return 4, "clay_loam"
    if silt_pct >= 80:
        return 10, "silt"
    if silt_pct >= 50:
        return 8, "silty_loam"
    if sand_pct >= 85:
        return 12, "sand"
    if sand_pct >= 70:
        return 11, "loamy_sand"
    if sand_pct >= 43 and clay_pct < 20:
        return 9, "sandy_loam"
    return 7, "loam"


@register_provider("soil")
class SoilGridsProvider(GISDataProvider):
    # No longer an upstream API key in the traditional sense, but this
    # provider *does* require one-time GEE credentials to be configured
    # (see the module docstring) -- requires_api_key stays False because
    # that flag gates gis_fetcher's generic "is this provider usable at
    # all" checks, which are about upstream-vendor API keys specifically,
    # not this repo-local GEE setup.
    requires_api_key = False

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        service_account = self.config.get("gee_service_account", "")
        key_path = self.config.get("gee_key_path", "")

        fractions = await asyncio.to_thread(
            _query_sand_silt_clay_pct,
            bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat,
            service_account, key_path,
        )

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        if {"sand", "silt", "clay"} <= fractions.keys():
            code, name = _classify_usda_texture(
                fractions["sand"], fractions["silt"], fractions["clay"]
            )
            properties = {
                "soil_type_code": code,
                "soil_type_name": name,
                "sand_pct": fractions["sand"],
                "silt_pct": fractions["silt"],
                "clay_pct": fractions["clay"],
            }
        else:
            # SoilGrids has no coverage at some coordinates (e.g. open ocean)
            # -- but this branch also fires on a genuine response-shape
            # mismatch (e.g. the GEE asset/band names above going stale if
            # ISRIC ever republishes under different ids), which would look
            # identical downstream (soil_type_code stays None) without this
            # log line. Printing what actually came back lets a real
            # no-coverage case be told apart from a parsing bug on the next
            # run, instead of guessing at a fix blind.
            print(
                f"soil provider: no usable sand/silt/clay at bbox around ({lat:.4f},{lon:.4f}) -- "
                f"found fractions: {fractions or 'none'}"
            )
            properties = {"soil_type_code": None}

        return [Feature(geometry=geometry, properties=properties, source=self.name)]
