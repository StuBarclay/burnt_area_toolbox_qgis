"""Area-of-interest geometry and analysis-grid helpers.

The STAC flow needs to know three geometric things: the lon/lat bounding box
to search the catalogue with, the projected :class:`RasterGrid` every scene is
resampled onto, and (when the AOI is a polygon rather than a plain box) a mask
that trims the results to that polygon. This module derives all three from a
:class:`~burnt_area_toolbox.bacore.config.BurntAreaConfig`.

Only the pure-Python grid constructors are imported at module load, so this
module imports cleanly without GDAL. Every function that actually needs a
coordinate transform, a vector read or a rasterised mask imports the vendored
:mod:`~burnt_area_toolbox.bacore._rio` shim lazily, keeping GDAL confined to
call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from .raster import RasterGrid, grid_from_bounds

if TYPE_CHECKING:
    from ._rio.crs import CRSLike
    from .config import BurntAreaConfig

_LONLAT_CRS = "EPSG:4326"


def aoi_lonlat_bounds(config: BurntAreaConfig) -> tuple[float, float, float, float]:
    """Return the AOI as a lon/lat ``(min_lon, min_lat, max_lon, max_lat)`` box.

    When the config carries a vector ``aoi_path`` its extent is read and
    reprojected to WGS84; otherwise the plain ``bbox`` (already lon/lat) is
    returned. This is the box handed to the STAC search.

    Args:
        config: The run configuration.

    Returns:
        The lon/lat bounding box.

    Raises:
        ValueError: If neither an AOI path nor a bbox is configured, or the
            AOI file is empty.
    """
    if config.aoi_path:
        from ._rio import _fiona
        from ._rio.warp import transform_bounds

        with _fiona.open(str(config.resolved_aoi_path)) as layer:
            raw_bounds = layer.bounds
            layer_crs = layer.crs
        if raw_bounds is None:
            raise ValueError(f"AOI file '{config.resolved_aoi_path}' contained no features.")
        source_crs = layer_crs.to_string() if layer_crs is not None else _LONLAT_CRS
        left, bottom, right, top = raw_bounds
        return transform_bounds(source_crs, _LONLAT_CRS, left, bottom, right, top)

    if config.bbox is None:
        raise ValueError("No AOI source is configured (need a bbox or an aoi_path).")
    return config.bbox.as_tuple()


def grid_lonlat_bounds(grid: RasterGrid) -> tuple[float, float, float, float]:
    """Return a grid's extent as a lon/lat ``(min_lon, min_lat, max_lon, max_lat)`` box.

    Used by the "from my rasters" flow to record an accurate geographic
    bounding box in the run manifest when no STAC bbox was supplied. A grid
    with no CRS is assumed to already be in lon/lat.

    Args:
        grid: The analysis grid.

    Returns:
        The grid extent reprojected to WGS84 (or returned unchanged when the
        grid has no CRS).
    """
    minx, miny, maxx, maxy = grid.bounds
    if grid.crs is None:
        return (minx, miny, maxx, maxy)

    from ._rio.warp import transform_bounds

    return transform_bounds(grid.crs, _LONLAT_CRS, minx, miny, maxx, maxy)


def build_analysis_grid(config: BurntAreaConfig) -> RasterGrid:
    """Build the projected analysis grid the run's rasters are computed on.

    The lon/lat AOI bounds are reprojected into ``config.output_crs`` and a
    north-up grid at ``config.resolution_m`` is laid over them.

    Args:
        config: The run configuration.

    Returns:
        The analysis :class:`RasterGrid`.
    """
    from ._rio.crs import CRS
    from ._rio.warp import transform_bounds

    min_lon, min_lat, max_lon, max_lat = aoi_lonlat_bounds(config)
    projected = transform_bounds(_LONLAT_CRS, config.output_crs, min_lon, min_lat, max_lon, max_lat)
    crs = CRS.from_user_input(config.output_crs)
    return grid_from_bounds(projected, crs, float(config.resolution_m))


def aoi_geometries(
    config: BurntAreaConfig,
    target_crs: CRSLike,
) -> list[dict[str, object]] | None:
    """Return the AOI polygon geometries reprojected to ``target_crs``.

    Args:
        config: The run configuration.
        target_crs: Any CRS-like value the polygons should be expressed in
            (e.g. the analysis grid's CRS).

    Returns:
        A list of GeoJSON geometry mappings, or ``None`` when the AOI is a
        plain bbox (no polygon to clip against).
    """
    if not config.aoi_path:
        return None

    from ._rio import _fiona
    from ._rio.warp import transform_geom

    geometries: list[dict[str, object]] = []
    with _fiona.open(str(config.resolved_aoi_path)) as layer:
        layer_crs = layer.crs
        source_crs = layer_crs.to_string() if layer_crs is not None else _LONLAT_CRS
        for feature in layer:
            geometry = feature.get("geometry")
            if geometry is None:
                continue
            geometries.append(transform_geom(source_crs, target_crs, geometry))
    return geometries or None


def aoi_mask_for_grid(
    config: BurntAreaConfig,
    grid: RasterGrid,
) -> NDArray[np.bool_] | None:
    """Return a boolean mask that is ``True`` inside the AOI polygon.

    Args:
        config: The run configuration.
        grid: The analysis grid the mask is rasterised onto.

    Returns:
        A boolean array of shape ``grid.shape`` (``True`` inside the AOI), or
        ``None`` when the AOI is a plain bbox and no trimming is needed.
    """
    geometries = aoi_geometries(config, grid.crs)
    if not geometries:
        return None

    from ._rio.features import geometry_mask

    mask = geometry_mask(geometries, out_shape=grid.shape, transform=grid.transform, invert=True)
    return np.asarray(mask, dtype=bool)
