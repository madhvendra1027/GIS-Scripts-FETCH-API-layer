"""zones.py — the zone_id -> bbox registry pipeline_runner.py needs.

Nothing else in this repo defines what a "zone" is in lon/lat terms
(HazardReading just stores a zone_id string). This started as a minimal,
hand-seeded registry of a few named towns -- it now also supports
registering a zone *dynamically* from any arbitrary lat/lon (e.g. a map
click), which is what makes "click anywhere and fetch that place" work
without pre-listing every place someone might click.

Each bbox is a small box (radius configurable, ~5km by default) around a
center point -- small enough that bbox.center (what every provider
actually queries) stays representative of the clicked/named place.

Two ways a Zone ends up in the registry:
  1. Hand-seeded at import time (`_SEED_ZONES` below) -- a few named
     towns kept around for the CLI/demo/tests.
  2. Registered at runtime via `zone_from_point(lat, lon)` -- this is
     what backend/api.py's /api/analyze-point calls for a map click. It
     derives a deterministic zone_id from the (rounded) coordinates,
     builds the bbox, and registers it so every existing zone_id-keyed
     piece of code (get_zone, ingest_zone, HazardReadingStore, the
     static-dataset fetchers) works on it exactly like a seeded zone --
     nothing downstream needs to know the difference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lon + self.max_lon) / 2, (self.min_lat + self.max_lat) / 2)


# lon/lat centers taken from public sources for each town; box is
# center +/- 0.025 degrees (~2.5km) in each direction.
_HALF_WIDTH_DEG = 0.025

_SEED_ZONES = [
    ("Z-BIHAR-PATNA-01", "Patna, Bihar", 85.1376, 25.5941),
    ("Z-KERALA-WAYANAD-01", "Wayanad, Kerala", 76.1319, 11.6854),
    ("Z-ASSAM-GUWAHATI-01", "Guwahati, Assam", 91.7362, 26.1445),
    ("Z-ODISHA-PURI-01", "Puri, Odisha", 85.8312, 19.8135),
    ("Z-UTTARAKHAND-JOSHIMATH-01", "Joshimath, Uttarakhand", 79.5641, 30.5551),
]

# Mutable at runtime -- zone_from_point() adds to this dict, which is
# exactly what makes dynamic/clicked zones visible to every other
# zone_id-keyed lookup in the codebase (get_zone, list_zones, and the
# static-dataset fetchers' `_load_zones` helpers, which all import
# `list_zones`/`get_zone` fresh rather than caching a copy at import time).
_ZONES: dict[str, Zone] = {
    zone_id: Zone(
        zone_id=zone_id,
        name=name,
        min_lon=round(lon - _HALF_WIDTH_DEG, 4),
        min_lat=round(lat - _HALF_WIDTH_DEG, 4),
        max_lon=round(lon + _HALF_WIDTH_DEG, 4),
        max_lat=round(lat + _HALF_WIDTH_DEG, 4),
    )
    for zone_id, name, lon, lat in _SEED_ZONES
}


def get_zone(zone_id: str) -> Zone:
    try:
        return _ZONES[zone_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown zone_id '{zone_id}'. Known zones: {sorted(_ZONES)}. "
            "If this was meant to be a map-click point rather than a "
            "pre-seeded zone, call zone_from_point(lat, lon) first -- "
            "that registers it under a derived zone_id."
        ) from exc


def list_zones() -> list[Zone]:
    return list(_ZONES.values())


def register_zone(zone: Zone) -> Zone:
    """Add (or overwrite) a zone in the registry. Idempotent: registering
    the same zone_id twice just replaces it, which is what we want when
    the same point is clicked again later -- callers don't need to check
    "does this already exist" first."""
    _ZONES[zone.zone_id] = zone
    return zone


def _point_zone_id(lat: float, lon: float, precision: int = 3) -> str:
    """Deterministic zone_id for a raw lat/lon, e.g. 'Z-PT-25.594N-85.138E'.
    Rounding to `precision` decimal places (~110m at 3dp) means two clicks
    on essentially the same spot reuse one zone_id -- so HazardReadingStore
    history, ZoneHistory imputation fallback, and the static-dataset
    freshness cache all actually accumulate for that point instead of
    minting a fresh, historyless zone on every click.
    """
    lat_r, lon_r = round(lat, precision), round(lon, precision)
    ns = "N" if lat_r >= 0 else "S"
    ew = "E" if lon_r >= 0 else "W"
    return f"Z-PT-{abs(lat_r):.{precision}f}{ns}-{abs(lon_r):.{precision}f}{ew}"


def zone_from_point(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    name: Optional[str] = None,
) -> Zone:
    """Build a Zone bbox around an arbitrary clicked point and register it,
    so it's immediately usable by zone_id everywhere else in the codebase.
    This is the "no hardcoded regions" entry point: the frontend sends
    whatever lat/lon the user clicked, and this turns it into a real zone
    on the fly -- no prior entry in `_SEED_ZONES` required.

    `radius_km` controls the bbox half-width in km (converted to degrees
    the same way backend/api.py's `_bbox_from_point` already does for
    /api/place-data, so the two stay consistent). Latitude degrees are a
    constant ~111km; longitude degrees shrink with cos(latitude), which
    matters more the further a click is from the equator.
    """
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.1, abs(math.cos(math.radians(lat)))))
    zone_id = _point_zone_id(lat, lon)
    zone = Zone(
        zone_id=zone_id,
        name=name or f"Point ({lat:.4f}, {lon:.4f})",
        min_lon=round(lon - dlon, 6),
        min_lat=round(lat - dlat, 6),
        max_lon=round(lon + dlon, 6),
        max_lat=round(lat + dlat, 6),
    )
    return register_zone(zone)
