"""Shared helpers for the GDAL-backed rasterio shim (internal)."""

from __future__ import annotations

from typing import Any

import numpy as np
from osgeo import gdal, gdal_array

from .crs import CRS, CRSLike
from .errors import RasterioError


def np_to_gdal_dtype(dtype: Any) -> int:
    """Map a numpy dtype to the closest GDAL data type code.

    Falls back to ``Float64`` for dtypes GDAL cannot represent directly
    (e.g. on older GDAL builds lacking Int64 support).
    """
    np_dtype = np.dtype(dtype)
    code = gdal_array.NumericTypeCodeToGDALTypeCode(np_dtype.type)
    if code is None:
        code = gdal.GDT_Float64
    return int(code)


def wkt_of(crs: CRSLike) -> str:
    """Return the WKT for any CRS-like value, or ``""`` for ``None``."""
    if crs is None:
        return ""
    return CRS.from_user_input(crs).to_wkt()


def mem_dataset_from_array(
    array: np.ndarray[Any, np.dtype[Any]],
    transform: Any,
    crs: CRSLike,
    nodata: float | None,
) -> "gdal.Dataset":
    """Create a single-band in-memory GDAL dataset wrapping ``array``.

    Args:
        array: A 2-D numpy array (rows, cols).
        transform: The array's affine transform (shim ``Affine``).
        crs: The array's CRS (any CRS-like value or ``None``).
        nodata: Nodata sentinel to record on the band, or ``None``.

    Returns:
        An open in-memory ``gdal.Dataset`` with the array written to band 1.
    """
    arr = np.ascontiguousarray(array)
    rows, cols = arr.shape
    gtype = np_to_gdal_dtype(arr.dtype)
    dataset = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gtype)
    dataset.SetGeoTransform(transform.to_gdal())
    wkt = wkt_of(crs)
    if wkt:
        dataset.SetProjection(wkt)
    band = dataset.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(float(nodata))
    band.WriteArray(arr)
    band.FlushCache()
    return dataset


def warp_source_to_grid(
    source: str,
    *,
    width: int,
    height: int,
    output_bounds: tuple[float, float, float, float],
    dst_wkt: str,
    resample_alg: int,
    src_nodata: float | None = None,
    dst_nodata: float | None = None,
) -> np.ndarray[Any, np.dtype[Any]]:
    """Warp band 1 of a GDAL source onto a target grid, reading only the window.

    Unlike :func:`~burnt_area_toolbox.bacore._rio.warp.reproject`, which
    reprojects an in-memory array, this drives ``gdal.Warp`` directly against a
    source path or descriptor. GDAL performs windowed reads, so a small
    area-of-interest can be pulled from a large (possibly remote ``/vsicurl/``)
    Cloud-Optimised GeoTIFF without materialising the whole scene.

    Args:
        source: A GDAL-openable path or descriptor (e.g. ``/vsicurl/https://...``).
        width: The target grid width in pixels.
        height: The target grid height in pixels.
        output_bounds: ``(minx, miny, maxx, maxy)`` of the target grid.
        dst_wkt: The target CRS as WKT (``""`` leaves the source CRS unchanged).
        resample_alg: A GDAL resampling code (e.g. ``gdalconst.GRA_*``).
        src_nodata: The source nodata value to honour, or ``None``.
        dst_nodata: The nodata value to initialise/fill the output with, or ``None``.

    Returns:
        The warped band as a 2-D numpy array of shape ``(height, width)``.

    Raises:
        RasterioError: If the warp fails or returns an empty result.
    """
    options: dict[str, Any] = {
        "format": "MEM",
        "width": int(width),
        "height": int(height),
        "outputBounds": tuple(float(v) for v in output_bounds),
        "resampleAlg": int(resample_alg),
    }
    if dst_wkt:
        options["dstSRS"] = dst_wkt
    if src_nodata is not None:
        options["srcNodata"] = float(src_nodata)
    if dst_nodata is not None:
        options["dstNodata"] = float(dst_nodata)

    warped = gdal.Warp("", source, **options)
    if warped is None:
        raise RasterioError(f"gdal.Warp failed for source {source!r}.")
    try:
        array = warped.GetRasterBand(1).ReadAsArray()
    finally:
        warped = None
    if array is None:
        raise RasterioError(f"gdal.Warp produced an empty result for {source!r}.")
    return np.asarray(array)
