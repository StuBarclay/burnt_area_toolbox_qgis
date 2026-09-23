"""Sandbox tests for the "from my rasters" config builder.

:mod:`burnt_area_toolbox.algorithms._config_build` imports only the GDAL-free
config layer and reaches ``spatial.grid_lonlat_bounds`` lazily. For a grid with
no CRS that helper returns the bounds unchanged (no coordinate transform, so no
GDAL), which lets the whole builder be exercised here in the plain sandbox.
"""

from __future__ import annotations

from datetime import date

import pytest
from burnt_area_toolbox.algorithms._config_build import (
    config_for_rasters,
    resolution_m_for_grid,
)
from burnt_area_toolbox.bacore._rio.affine import Affine
from burnt_area_toolbox.bacore.raster import RasterGrid


def _metric_grid(resolution_m: float = 20.0) -> RasterGrid:
    """A north-up grid with no CRS (treated as already metric / lon-lat)."""
    transform = Affine(resolution_m, 0.0, 0.0, 0.0, -resolution_m, 0.0)
    return RasterGrid(transform=transform, crs=None, width=10, height=10)


def _lonlat_grid() -> RasterGrid:
    """A small ~0.01-degree grid with no CRS, positioned over SE Australia."""
    transform = Affine(0.01, 0.0, 150.0, 0.0, -0.01, -33.0)
    return RasterGrid(transform=transform, crs=None, width=10, height=10)


def test_resolution_metric_grid_returns_pixel_width() -> None:
    """A CRS-less grid is treated as metric: the pixel width rounds through."""
    assert resolution_m_for_grid(_metric_grid(20.0)) == 20


def test_resolution_is_floored_at_one_metre() -> None:
    """A sub-metre pixel is floored at 1 so ``resolution_m`` stays a valid int."""
    assert resolution_m_for_grid(_metric_grid(0.3)) == 1


def test_config_for_rasters_fills_stac_placeholders() -> None:
    """STAC-only fields get harmless, clearly-labelled placeholder values."""
    config = config_for_rasters(
        _lonlat_grid(),
        output_crs="EPSG:4326",
        area_name="Test Fire",
        fire_date="2020-01-15",
        threshold=0.12,
        min_burnt_patch_pixels=5,
        fill_holes_pixels=3,
        export_polygons=False,
        vector_min_area_km2=0.0,
    )
    assert config.catalog_url == "n/a (from rasters)"
    assert config.collection == "n/a (from rasters)"
    assert config.baseline_length_months == 1
    assert config.post_window_days == 1
    assert config.max_cloud_cover == 100.0
    assert config.min_valid_fraction == 0.0
    assert config.postfire_strategy == "best_single"


def test_config_for_rasters_carries_user_values_and_bbox() -> None:
    """The user's threshold / cleaning / naming choices and the grid bbox pass through."""
    config = config_for_rasters(
        _lonlat_grid(),
        output_crs="EPSG:3577",
        area_name="Test Fire",
        fire_date="2020-01-15",
        threshold=0.27,
        min_burnt_patch_pixels=9,
        fill_holes_pixels=4,
        export_polygons=True,
        vector_min_area_km2=0.5,
    )
    assert config.threshold == pytest.approx(0.27)
    assert config.min_burnt_patch_pixels == 9
    assert config.fill_holes_pixels == 4
    assert config.export_polygons is True
    assert config.vector_min_area_km2 == pytest.approx(0.5)
    assert config.area_name == "Test Fire"
    assert config.fire_date == "2020-01-15"
    assert config.output_crs == "EPSG:3577"
    # A CRS-less grid's bounds are recorded as-is (assumed lon/lat).
    bbox = config.bbox
    assert bbox is not None
    assert bbox.min_lon == pytest.approx(150.0)
    assert bbox.max_lon == pytest.approx(150.1)
    assert bbox.min_lat == pytest.approx(-33.1)
    assert bbox.max_lat == pytest.approx(-33.0)


def test_config_for_rasters_defaults_fire_date_to_today() -> None:
    """A ``None`` fire date falls back to today's ISO date."""
    config = config_for_rasters(
        _lonlat_grid(),
        output_crs="EPSG:4326",
        area_name="",
        fire_date=None,
        threshold=0.10,
        min_burnt_patch_pixels=0,
        fill_holes_pixels=0,
        export_polygons=False,
        vector_min_area_km2=0.0,
    )
    assert config.fire_date == date.today().isoformat()
    # An empty area name falls back to the default slug base.
    assert config.area_name == "burnt_area"
