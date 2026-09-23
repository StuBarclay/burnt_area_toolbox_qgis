"""Build a :class:`BurntAreaConfig` for the "from my rasters" flow.

The from-rasters algorithm has no STAC catalogue, dates or cloud thresholds --
it differences two rasters the user already has. But the compute core and the
exporter still take a :class:`~burnt_area_toolbox.bacore.config.BurntAreaConfig`,
which carries those STAC fields and validates them. This module fills the
STAC-only fields with harmless, clearly-labelled placeholders and derives the
two fields that actually matter for a from-rasters run from the analysis grid:
the metric ``resolution_m`` (used for the area tallies) and a lon/lat ``bbox``
(recorded in the manifest).

It imports only the GDAL-free config layer at module load; the one call that
needs a coordinate transform (:func:`spatial.grid_lonlat_bounds`) reaches GDAL
lazily, so the resolution helper stays unit-testable in the plain sandbox.
"""

from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

from burnt_area_toolbox.bacore.config import BBox, BurntAreaConfig

if TYPE_CHECKING:
    from burnt_area_toolbox.bacore.raster import RasterGrid

_METRES_PER_DEGREE = 111_320.0
_PLACEHOLDER = "n/a (from rasters)"


def resolution_m_for_grid(grid: RasterGrid) -> int:
    """Return the grid's pixel size in metres for the area tallies.

    For a projected grid this is simply the (metre) pixel width. For a
    geographic grid the pixel size in degrees is converted to an approximate
    metre size at the grid's mean latitude, so the burnt-area km^2 figures stay
    sensible. A grid with no CRS is treated as already metric.

    Args:
        grid: The analysis grid.

    Returns:
        The pixel size in metres, floored at ``1``.
    """
    if grid.crs is not None and grid.crs.is_geographic:
        _minx, miny, _maxx, maxy = grid.bounds
        mean_lat = (miny + maxy) / 2.0
        metres_per_deg_lon = _METRES_PER_DEGREE * math.cos(math.radians(mean_lat))
        res_x = grid.pixel_width * metres_per_deg_lon
        res_y = grid.pixel_height * _METRES_PER_DEGREE
        estimate = (abs(res_x) + abs(res_y)) / 2.0
        return max(1, round(estimate))
    return max(1, round(grid.resolution_m))


def config_for_rasters(
    grid: RasterGrid,
    *,
    output_crs: str,
    area_name: str,
    fire_date: str | None,
    threshold: float,
    min_burnt_patch_pixels: int,
    fill_holes_pixels: int,
    export_polygons: bool,
    vector_min_area_km2: float,
) -> BurntAreaConfig:
    """Assemble a validated config for a from-rasters run.

    Args:
        grid: The shared analysis grid (typically the post-fire raster's grid).
        output_crs: The output CRS authority string (recorded in the manifest).
        area_name: A short name for the study area (prefixes output filenames).
        fire_date: The fire date as ``"YYYY-MM-DD"``, or ``None`` for today.
        threshold: The dNBR value above which a pixel counts as burnt.
        min_burnt_patch_pixels: Speckle-removal threshold for the burnt mask.
        fill_holes_pixels: Hole-fill threshold for the burnt mask.
        export_polygons: Whether to export burnt/severity polygons.
        vector_min_area_km2: Minimum exported-polygon area.

    Returns:
        A validated :class:`BurntAreaConfig`.
    """
    from burnt_area_toolbox.bacore import spatial

    min_lon, min_lat, max_lon, max_lat = spatial.grid_lonlat_bounds(grid)
    return BurntAreaConfig(
        project_name="Burnt area (from rasters)",
        catalog_url=_PLACEHOLDER,
        collection=_PLACEHOLDER,
        area_name=area_name or "burnt_area",
        fire_date=fire_date or date.today().isoformat(),
        baseline_length_months=1,
        post_window_days=1,
        threshold=threshold,
        max_cloud_cover=100.0,
        min_valid_fraction=0.0,
        output_crs=output_crs or "EPSG:4326",
        resolution_m=resolution_m_for_grid(grid),
        bbox=BBox(min_lon, min_lat, max_lon, max_lat),
        postfire_strategy="best_single",
        min_burnt_patch_pixels=min_burnt_patch_pixels,
        fill_holes_pixels=fill_holes_pixels,
        export_polygons=export_polygons,
        export_raw_polygons=False,
        vector_min_area_km2=vector_min_area_km2,
    )
