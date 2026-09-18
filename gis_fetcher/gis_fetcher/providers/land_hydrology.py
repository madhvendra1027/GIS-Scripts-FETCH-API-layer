"""
Root-zone/surface soil wetness from NASA POWER
(https://power.larc.nasa.gov). Free, no API key, daily global grid
(~0.5deg). Fills `soil_saturation_pct` (FLOOD) and `soil_moisture_pct`
(LANDSLIDE) -- the two fields normalize.py previously left as None because
neither weather nor OSM exposes soil wetness.

NASA POWER's GWETROOT/GWETTOP are a 0-1 wetness fraction relative to that
cell's field capacity, not an absolute volumetric %, but they are exactly
the "is the ground already saturated" signal both flood and landslide
scoring need, and there is no free alternative with better latency for
India-wide coverage. Treated as DYNAMIC (changes over days) -- short-ish
TTL (~6h) is appropriate, not the 90-day static tier.
"""

from __future__ import annotations

import datetime as dt

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("land_hydrology")
class NasaPowerProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        # NASA POWER lags by ~2-3 days; ask for the last 3 days and use
        # the most recent day that actually has a value.
        end = dt.date.today()
        start = end - dt.timedelta(days=4)
        query = {
            "parameters": "GWETROOT,GWETTOP,GWETPROF",
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        params_out = data.get("properties", {}).get("parameter", {})

        def _latest(series: dict) -> float | None:
            if not series:
                return None
            for day in sorted(series.keys(), reverse=True):
                value = series[day]
                if value is not None and value != -999:
                    return value
            return None

        root_wetness = _latest(params_out.get("GWETROOT", {}))
        top_wetness = _latest(params_out.get("GWETTOP", {}))

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        properties = {
            # 0-1 fraction -> % , rounded; None passes through untouched.
            "soil_saturation_pct": round(root_wetness * 100, 1) if root_wetness is not None else None,
            "soil_moisture_pct": round(top_wetness * 100, 1) if top_wetness is not None else None,
        }
        return [Feature(geometry=geometry, properties=properties, source=self.name)]
