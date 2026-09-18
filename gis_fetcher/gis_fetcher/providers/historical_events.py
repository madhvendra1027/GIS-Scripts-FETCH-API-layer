"""
Historical disaster counts from ReliefWeb (https://reliefweb.int/help/api).
Free, no API key, run by UN OCHA. Fills `historical_flood_count`,
`historical_landslide_count`, `historical_erosion_events`, and
`historical_cloudburst_count`.

Caveat, stated plainly: ReliefWeb indexes *reported disaster events*
(country/state-level, sometimes district), not per-zone counts at the
granularity a small hazard-mapping "zone" needs. Treat this as a coarse
regional prior (e.g. "this state has had 12 reported flood events since
2015"), not a precise per-zone historical count -- a better long-term
source is NDMA/SDMA district disaster databases or EM-DAT, neither of
which expose a free live API (both are static downloadable datasets;
see STATIC_DATASETS.md for how to seed them once instead of live-fetching).

STATIC provider (event history doesn't change minute to minute) --
cache ~7 days.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider

_HAZARD_TO_RELIEFWEB_TYPE = {
    "FLOOD": "Flood",
    "LANDSLIDE": "Landslide",
    "EROSION": "Land Slide",  # ReliefWeb has no "coastal erosion" type;
    # closest coastal-hazard tags are Storm Surge / Tropical Cyclone.
    "CLOUDBURST": "Flash Flood",
}


@register_provider("historical_events")
class ReliefWebHistoryProvider(GISDataProvider):
    requires_api_key = False
    # ReliefWeb decommissioned v1 (see apidoc.reliefweb.int -- "the previous
    # version, V1, is decommissioned and no longer available"). V2 is a
    # documented drop-in replacement, same params/response shape, hence no
    # other changes here. Separately, per the same docs: "from 1 November
    # 2025, you need to use a pre-approved appname" -- "hazard-platform-
    # sih2026-x7k2" (registered 2026-09-17) still got 403'd, so a second
    # appname, "rescue-disaster-x7k2", was submitted and approved
    # 2026-09-18. If this ever 403s again, don't assume the fix is "try a
    # new made-up name" a third time -- confirm the exact approved string
    # with whoever registered it before changing this.
    BASE_URL = "https://api.reliefweb.int/v2/disasters"

    async def fetch(self, bbox: BBox, **params) -> list:
        hazard_type = params.get("hazard_type", "FLOOD")
        event_type = _HAZARD_TO_RELIEFWEB_TYPE.get(hazard_type, "Flood")

        query = {
            "appname": "rescue-disaster-x7k2",
            "filter[field]": "primary_type.name",
            "filter[value]": event_type,
            "filter[operator]": "AND",
            "query[value]": "India",
            "query[fields][]": "country.name",
            "limit": 1,
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        count = data.get("totalCount", 0)
        lon, lat = bbox.center
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        field_name = {
            "FLOOD": "historical_flood_count",
            "LANDSLIDE": "historical_landslide_count",
            "EROSION": "historical_erosion_events",
            "CLOUDBURST": "historical_cloudburst_count",
        }[hazard_type]

        return [Feature(
            geometry=geometry,
            properties={field_name: count, "_scope": "country-level (India), not per-zone"},
            source=self.name,
        )]
