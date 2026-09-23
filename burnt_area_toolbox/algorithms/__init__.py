"""QGIS Processing algorithms for the burnt-area toolbox.

Each algorithm is a thin adapter: it collects native QGIS parameters,
turns any layers into the plain file paths the compute core expects
(GeoTIFF rasters), calls into :mod:`burnt_area_toolbox.bacore`, and loads
the results back as styled QGIS layers. All numerical dNBR work happens in
the untouched, GDAL-native core; nothing here re-implements the science.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DnbrFromRastersAlgorithm",
    "DnbrFromStacAlgorithm",
]


def _algorithms() -> list[Any]:
    """Return fresh instances of every algorithm (imported lazily).

    Imported inside the function so that merely importing this package does
    not require the ``qgis`` runtime, keeping the compute core importable in
    plain Python for testing.
    """
    from burnt_area_toolbox.algorithms.dnbr_from_rasters import DnbrFromRastersAlgorithm
    from burnt_area_toolbox.algorithms.dnbr_from_stac import DnbrFromStacAlgorithm

    return [
        DnbrFromRastersAlgorithm(),
        DnbrFromStacAlgorithm(),
    ]
