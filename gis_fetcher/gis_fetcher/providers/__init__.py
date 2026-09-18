"""
gis_fetcher
===========

A small, extensible framework for pulling *live* geospatial data from
multiple public APIs and normalizing it into a common GeoJSON-like shape.

The only thing you need to do to add a new data source is drop a new
file in `gis_fetcher/providers/`, subclass `GISDataProvider`, and
register it with `@register_provider("name")`. Nothing else in the
codebase needs to change.
"""

__version__ = "0.1.0"

import importlib
import pkgutil

# Auto-import every submodule in this package so each one's
# @register_provider(...) decorator runs as an import-time side effect.
for _module_info in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module_info.name}")
del _module_info
