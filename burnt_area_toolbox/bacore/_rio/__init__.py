"""A minimal, GDAL-backed re-implementation of the rasterio API surface.

The burnt-area compute core (:mod:`burnt_area_toolbox.bacore`) does its
raster/vector I/O against the rasterio API. QGIS ships GDAL
(``osgeo.gdal/ogr/osr``) with every install but not rasterio, so this
package reproduces *exactly* the subset of the rasterio API the core
touches, backed entirely by GDAL. The core imports it as
``from burnt_area_toolbox.bacore import _rio as rasterio``.

Because the shim mirrors the real API, an equivalence test can drive both
this package and genuine rasterio through the same calls and assert
identical results -- see ``tests/test_rio_shim_equivalence.py``.

Exposed to match rasterio:

* ``rasterio.open`` and ``rasterio.Affine`` at the top level;
* submodules ``crs``, ``enums``, ``errors``, ``features``, ``io``,
  ``transform``, ``warp`` and ``windows``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .affine import Affine

if TYPE_CHECKING:
    from . import crs, enums, errors, features, io, transform, warp, windows
    from .io import open

#: Osgeo-backed submodules resolved lazily so importing the pure-Python
#: :class:`Affine` (or any other pure member) never triggers ``osgeo``.
_LAZY_SUBMODULES = frozenset(
    {"crs", "enums", "errors", "features", "io", "transform", "warp", "windows"}
)

__all__ = [
    "Affine",
    "open",
    "crs",
    "enums",
    "errors",
    "features",
    "io",
    "transform",
    "warp",
    "windows",
]


def __getattr__(name: str) -> Any:
    """Lazily import the GDAL-backed members (PEP 562).

    Keeping these off the eager import path means ``osgeo`` is only imported
    when the shim is actually used, so pure-Python consumers (and the test
    sandbox) can import :class:`Affine` without a GDAL install.
    """
    if name in _LAZY_SUBMODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    if name == "open":
        from .io import open as _open

        globals()["open"] = _open
        return _open
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
