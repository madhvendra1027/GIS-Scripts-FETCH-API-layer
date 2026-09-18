import pytest

from gis_fetcher.core.base import BBox, Feature, GISDataProvider
from gis_fetcher.core.cache import Cache
from gis_fetcher.core.fetcher import GISDataFetcher
from gis_fetcher.core.registry import _reset_registry_for_tests, register_provider

BBOX = BBox(-1, -1, 1, 1)


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def make_feature(source: str) -> Feature:
    return Feature(geometry={"type": "Point", "coordinates": [0, 0]}, properties={}, source=source)


def test_fetch_many_runs_providers_concurrently_and_merges_results():
    @register_provider("alpha")
    class AlphaProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return [make_feature("alpha")]

    @register_provider("beta")
    class BetaProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return [make_feature("beta"), make_feature("beta")]

    fetcher = GISDataFetcher(config={"providers": {}})
    results = {r.provider: r for r in fetcher.fetch(["alpha", "beta"], BBOX)}

    assert results["alpha"].ok and len(results["alpha"].features) == 1
    assert results["beta"].ok and len(results["beta"].features) == 2


def test_unknown_provider_name_fails_gracefully_without_crashing_others():
    @register_provider("alpha")
    class AlphaProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return [make_feature("alpha")]

    fetcher = GISDataFetcher(config={"providers": {}})
    results = {r.provider: r for r in fetcher.fetch(["alpha", "does_not_exist"], BBOX)}

    assert results["alpha"].ok
    assert not results["does_not_exist"].ok
    assert "No provider registered" in results["does_not_exist"].error


def test_provider_retries_then_succeeds():
    call_count = {"n": 0}

    @register_provider("flaky")
    class FlakyProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("simulated transient failure")
            return [make_feature("flaky")]

    fetcher = GISDataFetcher(config={"providers": {"flaky": {"max_retries": 3}}})
    [result] = fetcher.fetch(["flaky"], BBOX)

    assert result.ok
    assert call_count["n"] == 2


def test_provider_gives_up_after_max_retries():
    @register_provider("always_fails")
    class AlwaysFailsProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            raise RuntimeError("nope")

    fetcher = GISDataFetcher(config={"providers": {"always_fails": {"max_retries": 2}}})
    [result] = fetcher.fetch(["always_fails"], BBOX)

    assert not result.ok
    assert "nope" in result.error


def test_cache_avoids_a_second_call(tmp_path):
    call_count = {"n": 0}

    @register_provider("counted")
    class CountedProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            call_count["n"] += 1
            return [make_feature("counted")]

    cache = Cache(enabled=True, directory=str(tmp_path))
    fetcher = GISDataFetcher(config={"providers": {}}, cache=cache)

    fetcher.fetch(["counted"], BBOX)
    fetcher.fetch(["counted"], BBOX)  # should hit cache, not call fetch() again

    assert call_count["n"] == 1
