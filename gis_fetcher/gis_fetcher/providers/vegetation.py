"""
NDVI (vegetation index) from Agromonitoring (https://agromonitoring.com).
Fills `vegetation_index` for LANDSLIDE (bare/sparse vegetation on a slope
raises landslide risk; dense cover lowers it).

NOT free-keyless like the other new providers: Agromonitoring requires a
free registration but its free tier (1,000 calls/day) comfortably covers
a hackathon demo or a small pilot deployment at zero cost -- it only
becomes a paid concern (~$0 base + per-call after quota) at real
production scale. Put the key in providers.yaml as
`${AGROMONITORING_API_KEY}` (see config.py's ${VAR} expansion).

Agromonitoring's NDVI endpoint needs a *polygon* registered first (it's
built for farm fields, not arbitrary points), which is a second round
trip. We approximate a small square polygon around the bbox center so
one zone = one polygon, then request the latest available NDVI image
stats for it. STATIC-ish/slow-changing (satellite revisit is ~5-10 days)
-- cache ~3-5 days.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("vegetation")
class AgromonitoringNDVIProvider(GISDataProvider):
    requires_api_key = True
    POLYGON_URL = "https://api.agromonitoring.com/agro/1.0/polygons"
    NDVI_URL = "https://api.agromonitoring.com/agro/1.0/ndvi/history"

    async def fetch(self, bbox: BBox, **params) -> list:
        api_key = self.config.get("api_key")
        if not api_key:
            raise RuntimeError(
                "vegetation provider requires AGROMONITORING_API_KEY "
                "(free tier at https://agromonitoring.com/) set in providers.yaml"
            )

        lon, lat = bbox.center
        delta = 0.001  # ~100m square, small enough to stay "one point"
        square = {
            "name": f"zone-{lat:.4f}-{lon:.4f}",
            "geo_json": {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [lon - delta, lat - delta], [lon + delta, lat - delta],
                        [lon + delta, lat + delta], [lon - delta, lat + delta],
                        [lon - delta, lat - delta],
                    ]],
                },
            },
        }

        async with self.session.post(
            self.POLYGON_URL, params={"appid": api_key, "duplicated": "true"}, json=square
        ) as resp:
            resp.raise_for_status()
            polygon = await resp.json()
        polygon_id = polygon.get("id")

        import time
        end = int(time.time())
        start = end - 30 * 24 * 3600 to start = end - 90 * 24 * 3600  # last 30 days, satellite revisit is slow

        async with self.session.get(
            self.NDVI_URL,
            params={"polyid": polygon_id, "start": start, "end": end, "appid": api_key},
        ) as resp:
            resp.raise_for_status()
            history = await resp.json()

        vegetation_index = None
        if history:
            latest = max(history, key=lambda h: h.get("dt", 0))
            vegetation_index = latest.get("data", {}).get("mean")

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        return [Feature(
            geometry=geometry,
            properties={"vegetation_index": vegetation_index},
            source=self.name,
        )]
