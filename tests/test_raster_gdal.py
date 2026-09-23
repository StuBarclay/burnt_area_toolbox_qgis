"""GDAL-backed raster I/O tests (skip where ``osgeo`` is unavailable).

These exercise the real GDAL code paths in :mod:`burnt_area_toolbox.bacore.raster`
and the vendored ``_rio`` shim: writing a GeoTIFF, reading a band back onto a
grid, and the windowed :func:`read_source_on_grid` loader used by the STAC flow.
They are skipped in the numpy-only sandbox and in CI (no ``osgeo``); they run
inside QGIS or any GDAL-enabled interpreter.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("osgeo")

from burnt_area_toolbox.bacore import raster
from burnt_area_toolbox.bacore._rio.affine import Affine
from burnt_area_toolbox.bacore._rio.crs import CRS
from burnt_area_toolbox.bacore.raster import RasterGrid


def _projected_grid(height: int = 4, width: int = 4, resolution_m: float = 100.0) -> RasterGrid:
    # A small north-up grid anchored somewhere sensible in EPSG:3577 (metres).
    transform = Affine(resolution_m, 0.0, 1_000_000.0, 0.0, -resolution_m, 2_000_000.0)
    return RasterGrid(
        transform=transform,
        crs=CRS.from_user_input("EPSG:3577"),
        width=width,
        height=height,
    )


def test_write_then_read_band_on_grid_roundtrips(tmp_path: Path) -> None:
    grid = _projected_grid()
    values = np.arange(16, dtype=np.float64).reshape(4, 4)

    path = raster.write_raster(tmp_path / "band.tif", values, grid, dtype="float32", nodata=None)
    assert path.exists()

    read_back = raster.read_band_on_grid(path, grid, resampling="nearest")
    np.testing.assert_allclose(read_back, values, atol=1e-4)


def test_read_source_on_grid_matches_native_pixels(tmp_path: Path) -> None:
    grid = _projected_grid()
    values = (np.arange(16, dtype=np.float64) / 10.0).reshape(4, 4)
    path = raster.write_raster(tmp_path / "src.tif", values, grid, dtype="float32", nodata=None)

    warped = raster.read_source_on_grid(path, grid, resampling="nearest")
    assert warped.shape == grid.shape
    np.testing.assert_allclose(warped, values, atol=1e-4)


def test_read_source_on_grid_maps_src_nodata_to_nan(tmp_path: Path) -> None:
    grid = _projected_grid(height=2, width=2)
    values = np.array([[1.0, 2.0], [3.0, -9999.0]], dtype=np.float64)
    path = raster.write_raster(tmp_path / "nd.tif", values, grid, dtype="float32", nodata=-9999.0)

    warped = raster.read_source_on_grid(path, grid, resampling="nearest", src_nodata=-9999.0)
    assert np.isnan(warped[1, 1])
    np.testing.assert_allclose(warped[:1, :], values[:1, :], atol=1e-4)


def test_open_grid_recovers_geometry(tmp_path: Path) -> None:
    grid = _projected_grid()
    values = np.zeros((4, 4), dtype=np.float64)
    path = raster.write_raster(tmp_path / "geom.tif", values, grid, dtype="float32", nodata=None)

    recovered = raster.open_grid(path)
    assert recovered.width == grid.width
    assert recovered.height == grid.height
    assert recovered.pixel_width == pytest.approx(grid.pixel_width)
