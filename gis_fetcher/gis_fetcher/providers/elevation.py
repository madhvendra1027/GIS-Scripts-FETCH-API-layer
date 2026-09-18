"""
Terrain elevation from the Open-Meteo Elevation API
(https://open-meteo.com/en/docs/elevation-api). Free, no API key.

Replaces the previous Open-Elevation (api.open-elevation.com) backend,
which was timing out with a 504 on every call in practice (see
STATIC_DATASETS.md / project chat-log for 2026-09-17), blocking both
`elevation_m` here and `slope_deg` in slope.py (which sampled the same
dead endpoint independently for its 5-point grid -- see that file).
Open-Meteo's elevation endpoint is the same vendor already proven
reliable by weather.py, is a GET (not POST) taking comma-separated
lat/lon lists, and returns a bare `{"elevation": [...]}` array aligned
positionally with the input lists -- no per-point object wrapper like
Open-Elevation's response shape, hence the parsing below looks
different even though the provider's public interface (fetch() ->
list[Feature]) is unchanged.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("elevation")
class OpenMeteoElevationProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://api.open-meteo.com/v1/elevation"

    async def fetch(self, bbox: BBox, **params) -> list:
        # Default: sample the bbox center. Callers can pass explicit
        # `locations=[(lat, lon), ...]` for a denser grid (see slope.py).
        locations = params.get("locations")
        if not locations:
            lon, lat = bbox.center
            locations = [(lat, lon)]

        query = {
            "latitude": ",".join(str(lat) for lat, _lon in locations),
            "longitude": ",".join(str(lon) for _lat, lon in locations),
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        elevations = data.get("elevation", [])
        features = []
        for (lat, lon), elev in zip(locations, elevations):
            geometry = {"type": "Point", "coordinates": [lon, lat]}
            features.append(
                Feature(
                    geometry=geometry,
                    properties={"elevation_m": elev},
                    source=self.name,
                )
            )
        return features
