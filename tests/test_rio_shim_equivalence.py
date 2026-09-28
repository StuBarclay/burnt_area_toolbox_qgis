"""Equivalence tests: the vendored ``_rio`` GDAL shim vs. real ``rasterio``.

The compute core does all of its raster/vector I/O against the rasterio API,
but ships a GDAL-backed re-implementation (:mod:`burnt_area_toolbox.bacore._rio`)
because QGIS bundles GDAL, not rasterio. These tests drive *both* the shim and
genuine rasterio through the same calls and assert the results agree, which is
the guarantee that swapping one for the other inside QGIS is safe.

They need three optional packages that are dev/CI-only -- ``osgeo`` (GDAL),
``rasterio`` and ``affine`` -- and skip cleanly when any is missing, so the
plain numpy-only sandbox and the pure-Python CI matrix skip them while the
dedicated GDAL CI job (and any GDAL-enabled interpreter) runs them in full.

Both stacks are backed by GDAL, so tolerances are tight; the small absolute
allowances on reprojected coordinates only cover last-bit floating-point drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("osgeo", reason="GDAL Python bindings (osgeo) required")
pytest.importorskip("rasterio", reason="real rasterio required for the parity check")
pytest.importorskip("affine", reason="real affine required for the parity check")

import numpy as np
import rasterio
from affine import Affine as RioAffine
from burnt_area_toolbox.bacore._rio import affine as shim_affine
from burnt_area_toolbox.bacore._rio import crs as shim_crs
from burnt_area_toolbox.bacore._rio import enums as shim_enums
from burnt_area_toolbox.bacore._rio import features as shim_features
from burnt_area_toolbox.bacore._rio import io as shim_io
from burnt_area_toolbox.bacore._rio import transform as shim_transform
from burnt_area_toolbox.bacore._rio import warp as shim_warp
from burnt_area_toolbox.bacore._rio import windows as shim_windows
from rasterio import features as rio_features
from rasterio import transform as rio_transform
from rasterio import warp as rio_warp
from rasterio import windows as rio_windows
from rasterio.crs import CRS as RioCRS  # noqa: N811 - rasterio's public class name
from rasterio.enums import Resampling as RioResampling

# A north-up grid with a 10 m pixel used across several window/transform checks.
_A = (10.0, 0.0, 1000.0, 0.0, -10.0, 2000.0)
# A unit grid whose origin is top-left (0, 4) for the small rasterise tests.
_UNIT = (1.0, 0.0, 0.0, 0.0, -1.0, 4.0)
# A square polygon covering the lower-left 2x2 of the unit grid.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
}
# A lon/lat square around Sydney used for the geometry-reprojection check.
_SQUARE_LONLAT = {
    "type": "Polygon",
    "coordinates": [
        [[150.0, -34.0], [151.0, -34.0], [151.0, -33.0], [150.0, -33.0], [150.0, -34.0]]
    ],
}


def test_affine_matches_real_affine() -> None:
    """Point application, composition, inversion and GDAL ordering all agree."""
    coeffs = (2.0, 0.0, 100.0, 0.0, -3.0, 200.0)
    shim = shim_affine.Affine(*coeffs)
    real = RioAffine(*coeffs)

    assert tuple(shim * (4.0, 5.0)) == pytest.approx(tuple(real * (4.0, 5.0)))

    shim_shift = shim_affine.Affine.translation(10.0, -7.0)
    real_shift = RioAffine.translation(10.0, -7.0)
    assert tuple(shim * shim_shift)[:6] == pytest.approx(tuple(real * real_shift)[:6])

    assert tuple(~shim)[:6] == pytest.approx(tuple(~real)[:6])
    assert shim.to_gdal() == pytest.approx(real.to_gdal())
    assert tuple(shim_affine.Affine.from_gdal(*shim.to_gdal()))[:6] == pytest.approx(
        tuple(RioAffine.from_gdal(*real.to_gdal()))[:6]
    )


def test_window_from_bounds_and_transform_match() -> None:
    """``windows.from_bounds`` and ``windows.transform`` track rasterio."""
    shim_t = shim_affine.Affine(*_A)
    real_t = RioAffine(*_A)
    left, bottom, right, top = 1020.0, 1900.0, 1080.0, 1980.0

    shim_win = shim_windows.from_bounds(left, bottom, right, top, transform=shim_t)
    real_win = rio_windows.from_bounds(left, bottom, right, top, transform=real_t)
    assert (shim_win.col_off, shim_win.row_off, shim_win.width, shim_win.height) == pytest.approx(
        (real_win.col_off, real_win.row_off, real_win.width, real_win.height)
    )

    shim_wt = shim_windows.transform(shim_win, shim_t)
    real_wt = rio_windows.transform(real_win, real_t)
    assert tuple(shim_wt)[:6] == pytest.approx(tuple(real_wt)[:6])


def test_array_bounds_matches_rasterio() -> None:
    """``transform.array_bounds`` returns the same outer extent as rasterio."""
    shim_t = shim_affine.Affine(*_A)
    real_t = RioAffine(*_A)
    assert shim_transform.array_bounds(5, 7, shim_t) == pytest.approx(
        rio_transform.array_bounds(5, 7, real_t)
    )


def test_warp_transform_points_match() -> None:
    """Coordinate reprojection agrees with rasterio to sub-millimetre."""
    xs = [150.0, 151.0, 152.0]
    ys = [-33.0, -34.0, -35.0]
    shim_xs, shim_ys = shim_warp.transform("EPSG:4326", "EPSG:3577", xs, ys)
    real_xs, real_ys = rio_warp.transform(RioCRS.from_epsg(4326), RioCRS.from_epsg(3577), xs, ys)
    assert shim_xs == pytest.approx(real_xs, abs=1e-3)
    assert shim_ys == pytest.approx(real_ys, abs=1e-3)


def test_warp_transform_bounds_match() -> None:
    """A reprojected, edge-densified bounding box agrees with rasterio."""
    shim_b = shim_warp.transform_bounds("EPSG:4326", "EPSG:3577", 150.0, -34.0, 151.0, -33.0)
    real_b = rio_warp.transform_bounds(
        RioCRS.from_epsg(4326), RioCRS.from_epsg(3577), 150.0, -34.0, 151.0, -33.0
    )
    assert shim_b == pytest.approx(real_b, abs=1.0)


def test_warp_transform_geom_matches() -> None:
    """A reprojected polygon's vertices agree with rasterio (no densification)."""
    shim_geom = shim_warp.transform_geom("EPSG:4326", "EPSG:3577", _SQUARE_LONLAT)
    real_geom = rio_warp.transform_geom(
        RioCRS.from_epsg(4326), RioCRS.from_epsg(3577), _SQUARE_LONLAT
    )

    shim_ring = shim_geom["coordinates"][0]
    real_ring = list(real_geom["coordinates"][0])
    assert len(shim_ring) == len(real_ring)
    for shim_xy, real_xy in zip(shim_ring, real_ring, strict=True):
        assert shim_xy[0] == pytest.approx(real_xy[0], abs=1e-3)
        assert shim_xy[1] == pytest.approx(real_xy[1], abs=1e-3)


def test_reproject_matches_rasterio() -> None:
    """Warping an array onto a projected grid matches rasterio pixel-for-pixel."""
    source = np.arange(64, dtype=np.float32).reshape(8, 8)
    shim_src_t = shim_affine.Affine(0.01, 0.0, 150.0, 0.0, -0.01, -33.0)
    real_src_t = RioAffine(*tuple(shim_src_t)[:6])

    left, bottom, right, top = shim_transform.array_bounds(8, 8, shim_src_t)
    dst_t, dst_w, dst_h = shim_warp.calculate_default_transform(
        "EPSG:4326", "EPSG:3857", 8, 8, left, bottom, right, top
    )
    real_dst_t = RioAffine(*tuple(dst_t)[:6])

    shim_dest = np.zeros((dst_h, dst_w), dtype=np.float32)
    shim_warp.reproject(
        source,
        shim_dest,
        src_transform=shim_src_t,
        src_crs="EPSG:4326",
        dst_transform=dst_t,
        dst_crs="EPSG:3857",
        resampling=shim_enums.Resampling.nearest,
    )

    real_dest = np.zeros((dst_h, dst_w), dtype=np.float32)
    rio_warp.reproject(
        source,
        real_dest,
        src_transform=real_src_t,
        src_crs=RioCRS.from_epsg(4326),
        dst_transform=real_dst_t,
        dst_crs=RioCRS.from_epsg(3857),
        resampling=RioResampling.nearest,
    )

    np.testing.assert_allclose(shim_dest, real_dest, atol=1e-4)


def test_io_write_read_roundtrips_with_rasterio(tmp_path: Path) -> None:
    """A raster the shim writes reads back through rasterio, and vice versa."""
    data = np.arange(12, dtype=np.float32).reshape(3, 4)
    shim_t = shim_affine.Affine(30.0, 0.0, 300000.0, 0.0, -30.0, 6000000.0)

    shim_path = tmp_path / "shim_out.tif"
    profile = {
        "driver": "GTiff",
        "width": 4,
        "height": 3,
        "count": 1,
        "dtype": "float32",
        "transform": shim_t,
        "crs": "EPSG:32755",
        "nodata": None,
    }
    with shim_io.open(shim_path, "w", **profile) as dst:
        dst.write(data, 1)

    with rasterio.open(shim_path) as src:
        np.testing.assert_allclose(src.read(1), data)
        assert src.width == 4
        assert src.height == 3
        assert tuple(src.transform)[:6] == pytest.approx(tuple(shim_t)[:6])
        assert src.crs.to_epsg() == 32755

    rio_path = tmp_path / "rio_out.tif"
    with rasterio.open(
        rio_path,
        "w",
        driver="GTiff",
        width=4,
        height=3,
        count=1,
        dtype="float32",
        transform=RioAffine(*tuple(shim_t)[:6]),
        crs=RioCRS.from_epsg(32755),
    ) as dst:
        dst.write(data, 1)

    with shim_io.open(rio_path) as src:
        np.testing.assert_allclose(src.read(1), data)
        assert src.crs is not None
        assert src.crs.to_epsg() == 32755
        assert tuple(src.transform)[:6] == pytest.approx(tuple(shim_t)[:6])


def test_rasterize_matches_rasterio() -> None:
    """Burning a polygon into a grid matches rasterio cell-for-cell."""
    shim_t = shim_affine.Affine(*_UNIT)
    real_t = RioAffine(*_UNIT)
    shapes = [(_SQUARE, 5)]

    shim_out = shim_features.rasterize(
        shapes, out_shape=(4, 4), fill=0, transform=shim_t, all_touched=False, dtype="int32"
    )
    real_out = rio_features.rasterize(
        shapes, out_shape=(4, 4), fill=0, transform=real_t, all_touched=False, dtype="int32"
    )
    np.testing.assert_array_equal(shim_out, real_out)


def test_geometry_mask_matches_rasterio() -> None:
    """The inside/outside boolean mask matches rasterio."""
    shim_t = shim_affine.Affine(*_UNIT)
    real_t = RioAffine(*_UNIT)

    shim_mask = shim_features.geometry_mask([_SQUARE], out_shape=(4, 4), transform=shim_t)
    real_mask = rio_features.geometry_mask([_SQUARE], out_shape=(4, 4), transform=real_t)
    np.testing.assert_array_equal(shim_mask, real_mask)


def test_shapes_matches_rasterio() -> None:
    """Polygonising a labelled array yields the same regions and corners."""
    shim_t = shim_affine.Affine(*_UNIT)
    real_t = RioAffine(*_UNIT)
    arr = np.zeros((4, 4), dtype=np.int32)
    arr[:, :2] = 1
    arr[:, 2:] = 2

    shim_pairs = list(shim_features.shapes(arr, transform=shim_t, connectivity=4))
    real_pairs = list(rio_features.shapes(arr, transform=real_t, connectivity=4))

    assert sorted(value for _, value in shim_pairs) == sorted(value for _, value in real_pairs)
    assert _corner_sets(shim_pairs) == _corner_sets(real_pairs)


def test_crs_matches_rasterio() -> None:
    """CRS identification, kind and canonical string agree with rasterio."""
    shim_geo = shim_crs.CRS.from_epsg(4326)
    real_geo = RioCRS.from_epsg(4326)
    assert shim_geo.to_epsg() == real_geo.to_epsg() == 4326
    assert shim_geo.is_geographic is bool(real_geo.is_geographic) is True
    assert shim_geo.to_string() == "EPSG:4326"

    shim_proj = shim_crs.CRS.from_epsg(3577)
    assert shim_proj.is_projected is True
    assert shim_proj.to_epsg() == 3577

    # Equality is semantic: a WKT round-trip still compares equal to the EPSG form.
    assert shim_crs.CRS.from_wkt(shim_geo.to_wkt()) == shim_geo


def _corner_sets(
    pairs: list[tuple[dict[str, Any], float]],
) -> dict[float, set[tuple[float, float]]]:
    """Collapse polygonisation output to a set of exterior-ring corners per value.

    Winding order and the ring's start vertex are implementation details that
    can differ between GDAL builds, so comparing the *set* of corners keeps the
    check meaningful without being brittle.
    """
    out: dict[float, set[tuple[float, float]]] = {}
    for geom, value in pairs:
        ring = geom["coordinates"][0]
        corners = {(round(float(x), 6), round(float(y), 6)) for x, y in ring}
        out.setdefault(float(value), set()).update(corners)
    return out
