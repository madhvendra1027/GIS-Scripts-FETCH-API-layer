"""
Live current-weather data from Open-Meteo (https://open-meteo.com).
Free, no API key, no rate-limit headaches for reasonable use.

Originally only requested `current_weather=true`, which Open-Meteo scopes
to temperature/windspeed/winddirection/weathercode/is_day/time -- it has
no precipitation or humidity fields at all. That silently starved
normalize.py's rainfall_mm_24h, rainfall_mm_72h, rainfall_intensity_mm_per_hr
and humidity_pct (it looks for "rain_24h"/"rain_72h"/"rain_rate"/"humidity",
none of which `current_weather` ever contains), so those four fields
always fell through to cleaning.py's imputed fallback on every single run
-- not a data gap, a provider gap. Fixed by also requesting the `hourly`
precipitation and relative_humidity_2m series with `past_days=3` (Open-Meteo
only returns historical hours when explicitly asked), then deriving:
  - humidity: the hourly value at the same timestamp as current_weather
  - rain_rate: that same hour's precipitation (mm in that hour == mm/hr)
  - rain_24h / rain_72h: summed hourly precipitation over the preceding
    24 / 72 hours up to and including the current hour
"""

from __future__ import annotations

from ..core.base import BBox, Feature, GISDataProvider
from ..core.registry import register_provider


def _find_current_index(hourly_times: list, current_time: str) -> int | None:
    """Index into hourly arrays matching current_weather's timestamp.
    Falls back to the closest earlier timestamp if there's no exact match
    (Open-Meteo's hourly grid and current_weather's own timestamp can be
    off by rounding at the edges)."""
    if not hourly_times:
        return None
    if current_time in hourly_times:
        return hourly_times.index(current_time)
    earlier = [i for i, t in enumerate(hourly_times) if t <= current_time]
    return earlier[-1] if earlier else None


@register_provider("weather")
class OpenMeteoProvider(GISDataProvider):
    requires_api_key = False
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    async def fetch(self, bbox: BBox, **params) -> list:
        lon, lat = bbox.center
        query = {
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "hourly": "precipitation,relative_humidity_2m",
            "past_days": 3,
            "forecast_days": 1,
        }
        # Allow callers (or config defaults) to request additional hourly
        # fields on top of the two this provider always needs.
        extra_hourly = params.get("hourly") or self.config.get("default_hourly")
        if extra_hourly:
            query["hourly"] = query["hourly"] + "," + extra_hourly

        async with self.session.get(self.BASE_URL, params=query) as resp:
            resp.raise_for_status()
            data = await resp.json()

        current = dict(data.get("current_weather", {}))
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation", [])
        humidity = hourly.get("relative_humidity_2m", [])

        idx = _find_current_index(times, current.get("time", ""))
        if idx is not None:
            if idx < len(humidity):
                current["humidity"] = humidity[idx]
            if idx < len(precip):
                current["rain_rate"] = precip[idx]
                current["rain_24h"] = sum(precip[max(0, idx - 23):idx + 1])
                current["rain_72h"] = sum(precip[max(0, idx - 71):idx + 1])

        geometry = {"type": "Point", "coordinates": [lon, lat]}
        feature = Feature(geometry=geometry, properties=current, source=self.name)
        return [feature]
