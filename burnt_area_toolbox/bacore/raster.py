"""Raster grids and GDAL-backed raster I/O for the burnt-area core.

The pure-numpy science in :mod:`~burnt_area_toolbox.bacore.severity` works on
arrays; this module is the bridge to disk. It reads bands (mapping the source
nodata to ``numpy.nan`` so the NBR/dNBR arithmetic propagates missing data),
optionally reprojects a band onto a common analysis grid, and writes results
back out as GeoTIFFs.

All GDAL access goes through the vendored :mod:`~burnt_area_toolbox.bacore._rio`
shim, so QGIS's bundled GDAL is the only requirement. The :class:`RasterGrid`
dataclass and :func:`grid_from_bounds` are deliberately import-light (only the
pure-Python ``Affine`` is imported at module load), so grid geometry can be
constructed and unit-tested without GDAL present; the read/write helpers import
the shim lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from ._rio.affine import Affine

if TYPE_CHECKING:
    from ._rio.crs import CRS

#: Read guardrail: refuse to slurp absurdly large single reads into memory.
_MAX_FULL_READ_PIXELS = 250_000_000


@dataclass(frozen=True, slots=True)
class RasterGrid:
    """The geometry of a raster: its transform, CRS and pixel dimensions.

    Attributes:
        transform: The affine transform mapping pixel (col, row) to world (x, y).
        crs: The coordinate reference system (a shim ``CRS``), or ``None``.
        width: The number of columns.
        height: The number of rows.
    """

    transform: Affine
    crs: CRS | None
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        """Return ``(rows, cols)`` for numpy array allocation."""
        return (self.height, self.width)

    @property
    def pixel_width(self) -> float:
        """Return the absolute pixel width in CRS units."""
        return abs(self.transform.a)

    @property
    def pixel_height(self) -> float:
        """Return the absolute pixel height in CRS units."""
        return abs(self.transform.e)

    @property
    def resolution_m(self) -> float:
        """Return the pixel size in metres (assuming a projected CRS)."""
        return self.pixel_width

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return ``(minx, miny, maxx, maxy)`` of the grid extent."""
        left, top = self.transform * (0.0, 0.0)
        right, bottom = self.transform * (float(self.width), float(self.height))
        return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def grid_from_bounds(
    bounds: tuple[float, float, float, float],
    crs: CRS | None,
    resolution_m: float,
) -> RasterGrid:
    """Build a north-up :class:`RasterGrid` covering ``bounds`` at a resolution.

    Args:
        bounds: ``(minx, miny, maxx, maxy)`` in the target CRS.
        crs: The target CRS.
        resolution_m: The pixel size in CRS units (metres for a projected CRS).

    Returns:
        A grid whose origin is the top-left corner of ``bounds``.
    """
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    if resolution_m <= 0:
        raise ValueError("resolution_m must be greater than 0.")
    width = max(1, round((maxx - minx) / resolution_m))
    height = max(1, round((maxy - miny) / resolution_m))
    transform = Affine(resolution_m, 0.0, minx, 0.0, -resolution_m, maxy)
    return RasterGrid(transform=transform, crs=crs, width=width, height=height)


def open_grid(source: str | Path) -> RasterGrid:
    """Return the :class:`RasterGrid` of a raster source (no pixel data read).

    Args:
        source: A file path or GDAL dataset descriptor.

    Returns:
        The source's grid geometry.
    """
    from . import _rio as rasterio

    with rasterio.open(str(source)) as dataset:
        return RasterGrid(
            transform=dataset.transform,
            crs=dataset.crs,
            width=dataset.width,
            height=dataset.height,
        )


def read_band(source: str | Path, index: int = 1) -> NDArray[np.float64]:
    """Read one band as float64 with the source nodata mapped to ``nan``.

    Args:
        source: A file path or GDAL dataset descriptor.
        index: The 1-based band index to read.

    Returns:
        The band as a float64 array with nodata cells set to ``nan``.

    Raises:
        ValueError: If the band exceeds the full-read pixel guardrail.
    """
    from . import _rio as rasterio

    with rasterio.open(str(source)) as dataset:
        if dataset.width * dataset.height > _MAX_FULL_READ_PIXELS:
            raise ValueError(
                f"Raster {source} has {dataset.width * dataset.height} pixels, "
                f"exceeding the {_MAX_FULL_READ_PIXELS} full-read limit."
            )
        raw = dataset.read(int(index))
        nodata = dataset.nodata
    values = np.asarray(raw, dtype=np.float64)
    if nodata is not None and not np.isnan(nodata):
        values = np.where(raw == nodata, np.nan, values)
    return np.asarray(values, dtype=np.float64)


def read_band_on_grid(
    source: str | Path,
    grid: RasterGrid,
    index: int = 1,
    resampling: str = "bilinear",
) -> NDArray[np.float64]:
    """Read a band and warp it onto ``grid``, with nodata as ``nan``.

    Args:
        source: A file path or GDAL dataset descriptor.
        grid: The destination analysis grid.
        index: The 1-based band index to read.
        resampling: The resampling method (``"bilinear"`` or ``"nearest"``).

    Returns:
        A float64 array of shape ``grid.shape`` in the grid's CRS, with
        uncovered/nodata cells set to ``nan``.
    """
    from . import _rio as rasterio
    from ._rio.enums import Resampling

    source_grid = open_grid(source)
    source_band = read_band(source, index)
    destination: NDArray[np.float64] = np.full(grid.shape, np.nan, dtype=np.float64)
    alg = Resampling.nearest if resampling == "nearest" else Resampling.bilinear
    rasterio.warp.reproject(
        source_band,
        destination,
        src_transform=source_grid.transform,
        src_crs=source_grid.crs,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=alg,
    )
    return destination


def read_source_on_grid(
    source: str | Path,
    grid: RasterGrid,
    *,
    resampling: str = "bilinear",
    src_nodata: float | None = None,
) -> NDArray[np.float64]:
    """Warp band 1 of a source directly onto ``grid`` using windowed reads.

    This is the loader used for the STAC path: it lets GDAL pull only the
    grid's window out of a (typically remote ``/vsicurl/``) Cloud-Optimised
    GeoTIFF, rather than reading the whole scene into memory first as
    :func:`read_band_on_grid` does.

    Args:
        source: A GDAL-openable path or descriptor (e.g. ``/vsicurl/https://...``).
        grid: The destination analysis grid.
        resampling: The resampling method (``"bilinear"`` or ``"nearest"``).
        src_nodata: A source nodata value to map to ``nan`` after warping. When
            ``None``, values are returned as-is (gaps read as ``0`` and, for
            reflectance, fall out of the NBR ratio as ``nan``).

    Returns:
        A float64 array of shape ``grid.shape`` in the grid's CRS.
    """
    from ._rio._util import warp_source_to_grid, wkt_of
    from ._rio.enums import Resampling

    alg = Resampling.nearest if resampling == "nearest" else Resampling.bilinear
    warped = warp_source_to_grid(
        str(source),
        width=grid.width,
        height=grid.height,
        output_bounds=grid.bounds,
        dst_wkt=wkt_of(grid.crs),
        resample_alg=int(alg.value),
        src_nodata=src_nodata,
        dst_nodata=src_nodata,
    )
    values = np.asarray(warped, dtype=np.float64)
    if src_nodata is not None and not np.isnan(src_nodata):
        values = np.where(warped == src_nodata, np.nan, values)
    return np.asarray(values, dtype=np.float64)


def write_raster(
    path: str | Path,
    array: NDArray[Any],
    grid: RasterGrid,
    *,
    dtype: str = "float32",
    nodata: float | None = None,
    compress: str = "deflate",
) -> Path:
    """Write a single-band GeoTIFF for ``array`` on ``grid``.

    Args:
        path: The output path.
        array: The 2-D array to write (its shape must match ``grid``).
        grid: The grid providing the transform and CRS.
        dtype: The output numpy dtype name (e.g. ``"float32"``, ``"uint8"``).
        nodata: The nodata value to record, or ``None``.
        compress: The GeoTIFF compression (``"deflate"`` by default).

    Returns:
        The output path.
    """
    from . import _rio as rasterio

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": dtype,
        "transform": grid.transform,
        "crs": grid.crs,
        "nodata": nodata,
        "compress": compress,
    }
    data = np.asarray(array).astype(np.dtype(dtype))
    with rasterio.open(str(out), "w", **profile) as dataset:
        dataset.write(data, 1)
    return out


def float_to_coded(
    array: NDArray[np.floating],
    *,
    fill: int = 0,
) -> NDArray[np.uint8]:
    """Convert a float class/mask array (``nan`` nodata) to ``uint8``.

    Args:
        array: A float array whose ``nan`` cells are nodata.
        fill: The integer value to write where the input is ``nan``.

    Returns:
        A ``uint8`` array with ``nan`` replaced by ``fill``.
    """
    values = np.asarray(array, dtype=np.float64)
    coded = np.where(np.isnan(values), float(fill), values)
    return np.asarray(coded, dtype=np.uint8)
