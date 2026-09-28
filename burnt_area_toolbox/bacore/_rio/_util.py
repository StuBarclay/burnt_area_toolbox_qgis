"""Shared helpers for the GDAL-backed rasterio shim (internal)."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import numpy as np
from osgeo import gdal, gdal_array

from .crs import CRS, CRSLike
from .errors import RasterioError

#: GDAL configuration applied while reading remote (``/vsicurl`` etc.) Cloud-
#: Optimised GeoTIFFs. These make transfers resilient to transient network
#: failures (GDAL's own bounded retry), bound how long a stalled request may
#: hang, and speed up opens by not listing the remote directory. They are set
#: only for the duration of a remote read and then restored, so QGIS's own GDAL
#: configuration is never permanently changed.
_REMOTE_GDAL_CONFIG: dict[str, str] = {
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_CONNECTTIMEOUT": "30",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_USE_HEAD": "NO",
    "VSI_CACHE": "TRUE",
}

#: How many times a remote warp is attempted before giving up. GDAL retries at
#: the HTTP layer (``GDAL_HTTP_MAX_RETRY``); this outer loop additionally covers
#: errors GDAL surfaces without retrying, so a single blip mid-download does not
#: abort a whole multi-scene run.
_MAX_REMOTE_ATTEMPTS = 3

#: Base back-off (seconds) between remote warp attempts; grows linearly.
_REMOTE_RETRY_BACKOFF_S = 2.0


def _is_remote_source(source: str) -> bool:
    """Return whether ``source`` is a network dataset worth retrying/tuning.

    Recognises GDAL virtual network filesystems (``/vsicurl/``, ``/vsis3/``,
    ``/vsigs/``, ``/vsiaz/``, ``/vsioss/``, ``/vsiswift/``) and bare
    ``http(s)://`` URLs. Local paths return ``False`` so their reads stay fast
    and any failure surfaces immediately instead of being retried.
    """
    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        return True
    return any(
        token in lowered
        for token in ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsioss", "/vsiswift")
    )


@contextmanager
def _gdal_config(options: Mapping[str, str]) -> Iterator[None]:
    """Temporarily set GDAL config options, restoring the prior values after.

    Uses ``gdal.GetConfigOption`` / ``gdal.SetConfigOption`` directly (rather
    than :func:`gdal.config_options`) so it behaves identically across the GDAL
    versions QGIS ships. Each key's previous value is captured and restored,
    including keys that were previously unset (restored to ``None``).
    """
    previous: dict[str, str | None] = {}
    try:
        for key, value in options.items():
            previous[key] = gdal.GetConfigOption(key, None)
            gdal.SetConfigOption(key, value)
        yield
    finally:
        for key, prior in previous.items():
            gdal.SetConfigOption(key, prior)


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

    if not _is_remote_source(source):
        # Local source: a failure is deterministic, so read once and surface it.
        return _warp_once(source, options)

    # Remote source: tune GDAL for /vsicurl and retry a bounded number of times
    # so a transient network blip does not abort a whole multi-scene run.
    with _gdal_config(_REMOTE_GDAL_CONFIG):
        last_error: Exception | None = None
        for attempt in range(1, _MAX_REMOTE_ATTEMPTS + 1):
            try:
                return _warp_once(source, options)
            except (RasterioError, RuntimeError) as error:
                last_error = error
                if attempt < _MAX_REMOTE_ATTEMPTS:
                    time.sleep(_REMOTE_RETRY_BACKOFF_S * attempt)
    raise RasterioError(
        f"gdal.Warp failed for remote source {source!r} after "
        f"{_MAX_REMOTE_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def _warp_once(source: str, options: dict[str, Any]) -> np.ndarray[Any, np.dtype[Any]]:
    """Run a single ``gdal.Warp`` into memory and return band 1 as an array.

    Raises:
        RasterioError: If the warp fails or returns an empty result.
    """
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
