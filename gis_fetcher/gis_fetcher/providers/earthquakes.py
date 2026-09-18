"""
Live earthquake events from the USGS FDSN event API
(https://earthquake.usgs.gov/fdsnws/event/1/). Free, no API key.
Returns real GeoJSON already, so this provider is mostly about mapping
USGS's fields onto our common Feature shape.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("earthquakes")
class USGSEarthquakeProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    async def fetch(self, bbox: BBox, **params) -> list:
        query = {
            "format": "geojson",
            "minlongitude": bbox.min_lon,
            "minlatitude": bbox.min_lat,
            "maxlongitude": bbox.max_lon,
            "maxlatitude": bbox.max_lat,
            "starttime": params.get("starttime", self.config.get("default_starttime", "2024-01-01")),
            "minmagnitude": params.get("minmagnitude", self.config.get("default_minmagnitude", 2.5)),
        }
        endtime = params.get("endtime")
        if endtime:
            query["endtime"] = endtime

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        features = []
        for raw in data.get("features", []):
            features.append(
                Feature(
                    geometry=raw["geometry"],
                    properties=raw["properties"],
                    source=self.name,
                )
            )
        return features
