import pytest

from gis_fetcher.core.base import GISDataProvider
from gis_fetcher.core.registry import (
    _reset_registry_for_tests,
    available_providers,
    get_provider,
    register_provider,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test gets an empty registry so tests don't leak into each other."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_register_and_get_provider():
    @register_provider("dummy")
    class DummyProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return []

    assert get_provider("dummy") is DummyProvider
    assert "dummy" in available_providers()


def test_get_unknown_provider_raises_helpful_error():
    with pytest.raises(KeyError, match="No provider registered under 'nope'"):
        get_provider("nope")


def test_available_providers_is_sorted():
    @register_provider("zebra")
    class ZebraProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return []

    @register_provider("alpha")
    class AlphaProvider(GISDataProvider):
        async def fetch(self, bbox, **params):
            return []

    assert available_providers() == ["alpha", "zebra"]
