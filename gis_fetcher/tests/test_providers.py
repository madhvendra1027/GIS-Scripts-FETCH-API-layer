import pytest

from gis_fetcher.core.base import BBox
from gis_fetcher.providers.earthquakes import USGSEarthquakeProvider
from gis_fetcher.providers.elevation import OpenElevationProvider
from gis_fetcher.providers.osm_vector import OverpassProvider
from gis_fetcher.providers.weather import OpenMeteoProvider
from tests.fakes import FakeSession

BBOX = BBox(-122.6, 37.6, -122.3, 37.9)


@pytest.mark.asyncio
async def test_weather_provider_parses_current_weather():
    payload = {"current_weather": {"temperature": 18.4, "windspeed": 12.1, "weathercode": 1}}
    session = FakeSession(payload)
    provider = OpenMeteoProvider(session=session, config={})

    features = await provider.fetch(BBOX)

    assert len(features) == 1
    assert features[0].properties["temperature"] == 18.4
    assert features[0].source == "weather"
    assert features[0].geometry == {"type": "Point", "coordinates": list(BBOX.center)}
    assert session.last_call[0] == "GET"


@pytest.mark.asyncio
async def test_earthquake_provider_parses_geojson_features():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.4, 37.7, 8.2]},
                "properties": {"mag": 3.4, "place": "5km NW of Somewhere"},
            }
        ],
    }
    session = FakeSession(payload)
    provider = USGSEarthquakeProvider(session=session, config={})

    features = await provider.fetch(BBOX)

    assert len(features) == 1
    assert features[0].properties["mag"] == 3.4
    assert features[0].source == "earthquakes"


@pytest.mark.asyncio
async def test_osm_provider_parses_overpass_elements():
    payload = {
        "elements": [
            {"lat": 37.75, "lon": -122.45, "tags": {"amenity": "hospital", "name": "Test Hospital"}}
        ]
    }
    session = FakeSession(payload)
    provider = OverpassProvider(session=session, config={})

    features = await provider.fetch(BBOX, tag="amenity=hospital")

    assert len(features) == 1
    assert features[0].properties["name"] == "Test Hospital"
    assert features[0].geometry["coordinates"] == [-122.45, 37.75]
    assert session.last_call[0] == "POST"


@pytest.mark.asyncio
async def test_osm_provider_skips_elements_without_coordinates():
    payload = {"elements": [{"type": "way", "id": 123, "tags": {"amenity": "hospital"}}]}
    session = FakeSession(payload)
    provider = OverpassProvider(session=session, config={})

    features = await provider.fetch(BBOX)

    assert features == []


@pytest.mark.asyncio
async def test_elevation_provider_parses_results():
    payload = {"results": [{"latitude": 37.75, "longitude": -122.45, "elevation": 52}]}
    session = FakeSession(payload)
    provider = OpenElevationProvider(session=session, config={})

    features = await provider.fetch(BBOX)

    assert len(features) == 1
    assert features[0].properties["elevation_m"] == 52
