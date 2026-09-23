"""GDAL-backed AOI/grid helper tests (skip where ``osgeo`` is unavailable).

These drive the coordinate transforms in
:mod:`burnt_area_toolbox.bacore.spatial`, which need GDAL (``osgeo.osr``).
They are skipped in the numpy-only sandbox and in CI; they run inside QGIS or
any GDAL-enabled interpreter.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("osgeo")

from burnt_area_toolbox.bacore import spatial
from burnt_area_toolbox.bacore.config import BBox, BurntAreaConfig


def _bbox_config(**overrides: object) -> BurntAreaConfig:
    params: dict[str, object] = {
        "project_name": "demo",
        "catalog_url": "https://example.test",
        "collection": "sentinel-2-l2a",
        "area_name": "Test Area",
        "fire_date": date(2020, 1, 5),
        "baseline_length_months": 6,
        "post_window_days": 30,
        "threshold": 0.2,
        "max_cloud_cover": 30,
        "min_valid_fraction": 0.4,
        "output_crs": "EPSG:3577",
        "resolution_m": 100,
        "bbox": BBox(150.0, -33.0, 150.5, -32.5),
    }
    params.update(overrides)
    return BurntAreaConfig(**params)  # type: ignore[arg-type]


def test_aoi_lonlat_bounds_passes_bbox_through() -> None:
    config = _bbox_config()
    assert spatial.aoi_lonlat_bounds(config) == (150.0, -33.0, 150.5, -32.5)


def test_build_analysis_grid_is_projected_and_covers_bbox() -> None:
    config = _bbox_config()
    grid = spatial.build_analysis_grid(config)

    assert grid.crs is not None and grid.crs.is_projected
    assert grid.width > 0 and grid.height > 0
    assert grid.pixel_width == pytest.approx(100.0)
    # ~0.5 deg spans well over 10 km, so at 100 m we expect a few hundred cells.
    assert grid.width > 100
    assert grid.height > 100


def test_aoi_mask_for_grid_is_none_for_plain_bbox() -> None:
    config = _bbox_config()
    grid = spatial.build_analysis_grid(config)
    assert spatial.aoi_mask_for_grid(config, grid) is None
    assert spatial.aoi_geometries(config, grid.crs) is None
