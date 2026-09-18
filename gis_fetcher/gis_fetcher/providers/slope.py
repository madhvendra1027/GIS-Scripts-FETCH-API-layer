"""
Slope angle derived from the Open-Meteo Elevation API (the same source
elevation.py now uses -- see that file's docstring for why this
replaced Open-Elevation/api.open-elevation.com, which was timing out
with a 504 on every call for both providers). Fills `slope_deg` for
LANDSLIDE -- previously None because a single elevation point can't give
a gradient (see normalize.py's original comment on this exact gap).

Method: sample elevation at the zone center plus 4 points offset ~90m
north/south/east/west (0.0008deg at India's latitudes), then take the
steepest of the two axis gradients and convert to degrees. This is a
coarse single-cell finite-difference slope, not a real DEM-based slope
raster (SRTM/Bhuvan at 30m would be more accurate) -- good enough to rank
"flat" vs "steep" zones for the weighted-formula scorer, not for
engineering-grade terrain analysis.

One HTTP call (same endpoint, 5 points as comma-separated lat/lon lists
in one GET), so this doesn't add extra network round trips beyond what
`elevation` already does. STATIC provider (terrain doesn't change) --
cache ~90 days, same tier as soil/elevation.
"""

from __future__ import annotations

import math

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider

_OFFSET_DEG = 0.0008  # ~90m at India's latitudes
_METERS_PER_DEG_LAT = 111_320


@register_provider("slope")
class DerivedSlopeProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://api.open-meteo.com/v1/elevation"

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        points = {
            "center": (lat, lon),
            "north": (lat + _OFFSET_DEG, lon),
            "south": (lat - _OFFSET_DEG, lon),
            "east": (lat, lon + _OFFSET_DEG),
            "west": (lat, lon - _OFFSET_DEG),
        }
        query = {
            "latitude": ",".join(str(p_lat) for p_lat, _p_lon in points.values()),
            "longitude": ",".join(str(p_lon) for _p_lat, p_lon in points.values()),
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        elevations = data.get("elevation", [])
        if len(elevations) != 5:
            return [Feature(
                geometry={"type": "Point", "coordinates": [lon, lat]},
                properties={"slope_deg": None},
                source=self.name,
            )]

        elev = dict(zip(points.keys(), elevations))
        meters_per_deg_lon = _METERS_PER_DEG_LAT * math.cos(math.radians(lat))
        run_ns = 2 * _OFFSET_DEG * _METERS_PER_DEG_LAT
        run_ew = 2 * _OFFSET_DEG * meters_per_deg_lon

        rise_ns = elev["north"] - elev["south"]
        rise_ew = elev["east"] - elev["west"]
        grad_ns = rise_ns / run_ns if run_ns else 0
        grad_ew = rise_ew / run_ew if run_ew else 0

        steepest_grad = max(abs(grad_ns), abs(grad_ew))
        slope_deg = round(math.degrees(math.atan(steepest_grad)), 2)

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        return [Feature(
            geometry=geometry,
            properties={"slope_deg": slope_deg, "elevation_m": elev["center"]},
            source=self.name,
        )]
