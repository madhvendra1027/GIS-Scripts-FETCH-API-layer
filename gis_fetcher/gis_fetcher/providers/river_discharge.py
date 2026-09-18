"""
River discharge from the Open-Meteo Flood API
(https://open-meteo.com/en/docs/flood-api), backed by the Copernicus
GloFAS model. Free, no API key, worldwide coverage including India.

There is no free/cheap real-time river-GAUGE-LEVEL (meters) API covering
India -- India-WRIS and CWC's flood-forecast portal
(aff.india-water.gov.in) are agency web portals without a documented
public JSON API (confirmed by search; see the README gap notes). GloFAS
discharge (m3/s) is used as the closest available substitute: we derive
`river_level_change_rate_m_per_hr` from the day-over-day discharge trend
and leave `river_level_m` as None (discharge and stage are not
interchangeable units -- do not silently coerce one into the other).

IMPORTANT: GloFAS is a 5km-grid river-routing model. Coordinates that
don't sit on a mapped river channel return near-zero/no data -- see
open-meteo docs. DYNAMIC provider: cache ~1-3h.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("river_discharge")
class OpenMeteoFloodProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://flood-api.open-meteo.com/v1/flood"

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        query = {
            "latitude": lat,
            "longitude": lon,
            "daily": "river_discharge",
            "past_days": 2,
            "forecast_days": 1,
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        daily = data.get("daily", {})
        values = daily.get("river_discharge", []) or []
        dates = daily.get("time", []) or []

        change_rate = None
        latest_discharge = None
        if len(values) >= 2 and values[-1] is not None and values[-2] is not None:
            latest_discharge = values[-1]
            # crude m3/s-per-day -> per-hour trend, used only as a rising/
            # falling signal for predictor.py, not an absolute gauge level.
            change_rate = round((values[-1] - values[-2]) / 24, 4)
        elif values:
            latest_discharge = values[-1]

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        properties = {
            "river_discharge_m3s": latest_discharge,
            "river_level_change_rate_m_per_hr": change_rate,
            "river_level_m": None,  # no free gauge-level source for India yet
            "_dates": dates,
        }
        return [Feature(geometry=geometry, properties=properties, source=self.name)]
