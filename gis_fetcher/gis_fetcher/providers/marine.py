"""
Wave conditions from the Open-Meteo Marine Weather API
(https://open-meteo.com/en/docs/marine-weather-api). Free, no API key,
global coastal coverage (updated every 6h).

Fills `wave_energy_index` for EROSION. There's no free/cheap Indian-
specific wave-energy API with global uptime as good as this one (INCOIS
publishes wave data but as portal downloads/FTP, not a lightweight REST
endpoint) -- flagged for a future swap if INCOIS opens one.

wave_energy_index here is a simple proxy, not a physically calibrated
energy flux: (significant_wave_height_m ** 2) * wave_period_s, which
tracks the standard deep-water wave power formula's shape (power ~ H^2 * T)
closely enough to rank zones, without pretending to be a validated
kW/m figure. DYNAMIC provider, cache ~3-6h.
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


@register_provider("marine")
class OpenMeteoMarineProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://marine-api.open-meteo.com/v1/marine"

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        query = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wave_height,wave_period",
            "models": "best_match",
            "forecast_days": 1,
        }

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        hourly = data.get("hourly", {})
        heights = [v for v in hourly.get("wave_height", []) if v is not None]
        periods = [v for v in hourly.get("wave_period", []) if v is not None]

        wave_energy_index = None
        latest_height = heights[-1] if heights else None
        latest_period = periods[-1] if periods else None
        if latest_height is not None and latest_period is not None:
            wave_energy_index = round((latest_height ** 2) * latest_period, 2)

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        properties = {
            "wave_energy_index": wave_energy_index,
            "wave_height_m": latest_height,
            "wave_period_s": latest_period,
        }
        return [Feature(geometry=geometry, properties=properties, source=self.name)]
