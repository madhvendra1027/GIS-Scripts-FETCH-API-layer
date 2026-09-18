"""
Vector features (points-of-interest, roads, buildings, coastline, ...)
from OpenStreetMap via the public Overpass API
(https://overpass-api.de/api/interpreter). Free, no API key, but
please be polite -- this shared instance rate-limits aggressively,
which is exactly why `min_interval_seconds` exists in the fetcher.

Handles both node-tagged features (e.g. amenity=hospital -> points)
and way-tagged features (e.g. natural=coastline -> lines). For ways,
computes the minimum distance in meters from the query bbox's center
to the nearest segment of the returned geometry, attached as
`properties["distance_m"]` -- this is what
hazard_platform's normalize.osm_to_erosion_fields() reads for
`distance_to_coast_m`.
"""

from __future__ import annotations

import math

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider

_EARTH_RADIUS_M = 6_371_000.0


def _to_local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """Flat-earth projection in meters, centered on (origin_lat,
    origin_lon) -- fine at the tens-of-km scale this provider's
    distance fields ever need (matches fetch_river_distance.py's
    identical approach for the same reason)."""
    lat_rad = math.radians(origin_lat)
    x = math.radians(lon - origin_lon) * math.cos(lat_rad) * _EARTH_RADIUS_M
    y = math.radians(lat - origin_lat) * _EARTH_RADIUS_M
    return x, y


def _point_to_segment_distance_m(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_x, nearest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def _min_distance_to_way_m(center_lat: float, center_lon: float, nodes: list[tuple[float, float]]) -> float:
    points = [_to_local_xy(lat, lon, center_lat, center_lon) for lat, lon in nodes]
    best = math.inf
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        d = _point_to_segment_distance_m(0.0, 0.0, ax, ay, bx, by)
        best = min(best, d)
    return best


@register_provider("osm")
class OverpassProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://overpass-api.de/api/interpreter"

    async def fetch(self, bbox: BBox, **params) -> list:
        # e.g. tag="amenity=hospital" or tag="natural=coastline"
        tag = params.get("tag") or self.config.get("default_tag", "amenity=hospital")
        key, _, value = tag.partition("=")

        south, west, north, east = bbox.min_lat, bbox.min_lon, bbox.max_lat, bbox.max_lon
        overpass_query = f"""
        [out:json][timeout:25];
        (
          node[{key!r}={value!r}]({south},{west},{north},{east});
          way[{key!r}={value!r}]({south},{west},{north},{east});
        );
        out geom;
        """.replace("'", '"')

        # overpass-api.de's usage policy asks clients to self-identify;
        # the default aiohttp User-Agent gets a 406 from their WAF.
        headers = {
            "User-Agent": "hazard-platform-sih2026/1.0 (SIH26191 Rescue Arc)",
        }
        async with self.session.post(
            self.BASE_URL, data={"data": overpass_query}, headers=headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        center_lat, center_lon = (south + north) / 2, (west + east) / 2

        features = []
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            el_type = el.get("type")

            if el_type == "node" and "lat" in el and "lon" in el:
                geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
                features.append(Feature(geometry=geometry, properties=tags, source=self.name))
                continue

            if el_type == "way" and el.get("geometry"):
                nodes = [(pt["lat"], pt["lon"]) for pt in el["geometry"] if "lat" in pt and "lon" in pt]
                if len(nodes) < 2:
                    continue
                props = dict(tags)
                props["distance_m"] = _min_distance_to_way_m(center_lat, center_lon, nodes)
                geometry = {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in nodes],
                }
                features.append(Feature(geometry=geometry, properties=props, source=self.name))

        return features
