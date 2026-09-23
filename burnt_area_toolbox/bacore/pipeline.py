"""End-to-end run orchestration for the two burnt-area flows.

This ties the pieces of :mod:`burnt_area_toolbox.bacore` together into two
high-level entry points so the QGIS algorithm classes stay thin adapters:

* :func:`run_stac` -- search a STAC catalogue for baseline and post-fire
  Sentinel-2 imagery over the configured area, load the NBR bands (and the SCL
  quality band) onto the analysis grid, and run the full dNBR analysis.
* :func:`run_from_rasters` -- compute the same dNBR analysis from two rasters
  the user already has (a pre-fire and a post-fire image), reading their NIR
  and SWIR bands (or a single band already holding NBR) onto a shared grid.

The heavy lifting stays in the modules this imports (``stac``, ``spatial``,
``raster``, ``severity`` and ``analysis``); every one of them reaches GDAL only
lazily, so importing this module needs no GDAL install and it can be exercised,
where GDAL is present, without QGIS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import analysis, raster, severity, spatial, stac

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from .analysis import AnalysisResult
    from .config import BurntAreaConfig
    from .raster import RasterGrid

#: A progress callback receiving ``(scenes_done, scenes_total)``.
SceneProgress = Callable[[int, int], None]

#: A status-message callback receiving a single human-readable line.
Message = Callable[[str], None]

#: A cancellation predicate; when it returns ``True`` a run stops early.
Cancelled = Callable[[], bool]


def _note(message: Message | None, text: str) -> None:
    """Send ``text`` to the optional message callback."""
    if message is not None:
        message(text)


def _cap_scenes(
    items: list[stac.StacItem],
    limit: int | None,
    window: str,
    message: Message | None,
) -> list[stac.StacItem]:
    """Apply the optional per-window scene cap, noting what was kept."""
    if limit is None or limit >= len(items):
        return items
    kept = stac.select_scenes(items, limit)
    _note(message, f"  keeping the {len(kept)} least-cloudy {window} scenes (cap {limit})")
    return kept


def _offset_progress(
    scene_progress: SceneProgress | None, offset: int, total: int
) -> SceneProgress | None:
    """Re-base a per-window ``(done, total)`` callback onto a combined total.

    Returns a callback that reports ``(offset + done, total)`` so a single
    progress bar advances monotonically across the baseline and post-fire
    loading phases instead of resetting between them. ``None`` in yields
    ``None`` out (no progress reporting).
    """
    if scene_progress is None:
        return None

    def report(done: int, _window_total: int) -> None:
        scene_progress(offset + done, total)

    return report


def run_stac(
    config: BurntAreaConfig,
    *,
    message: Message | None = None,
    scene_progress: SceneProgress | None = None,
    is_cancelled: Cancelled | None = None,
) -> AnalysisResult | None:
    """Run the STAC-driven flow end to end.

    Args:
        config: The run configuration (catalogue, area, dates and thresholds).
        message: Optional status-line callback.
        scene_progress: Optional ``(done, total)`` callback for scene loading.
        is_cancelled: Optional predicate polled between stages; when it returns
            ``True`` the run stops and ``None`` is returned.

    Returns:
        The populated :class:`AnalysisResult`, or ``None`` if cancelled.

    Raises:
        BurntAreaError: If either the baseline or post-fire search finds no
            imagery (raised by :func:`~burnt_area_toolbox.bacore.stac.search_items`).
    """
    grid = spatial.build_analysis_grid(config)
    aoi_mask = spatial.aoi_mask_for_grid(config, grid)
    bbox = spatial.aoi_lonlat_bounds(config)

    baseline_start, baseline_end = config.baseline_range
    postfire_start, postfire_end = config.postfire_range

    limit = config.max_scenes_per_window

    _note(message, f"Searching baseline imagery {baseline_start}..{baseline_end}")
    baseline_items = stac.search_items(
        config.catalog_url,
        config.collection,
        baseline_start,
        baseline_end,
        bbox=bbox,
        max_cloud_cover=config.max_cloud_cover,
    )
    _note(message, f"  found {len(baseline_items)} baseline scenes")
    baseline_items = _cap_scenes(baseline_items, limit, "baseline", message)
    if is_cancelled is not None and is_cancelled():
        return None

    _note(message, f"Searching post-fire imagery {postfire_start}..{postfire_end}")
    postfire_items = stac.search_items(
        config.catalog_url,
        config.collection,
        postfire_start,
        postfire_end,
        bbox=bbox,
        max_cloud_cover=config.max_cloud_cover,
    )
    _note(message, f"  found {len(postfire_items)} post-fire scenes")
    postfire_items = _cap_scenes(postfire_items, limit, "post-fire", message)
    if is_cancelled is not None and is_cancelled():
        return None

    # Drive one monotonic progress sweep across both loading phases: the scene
    # loader reports ``(done, total)`` per window, so wrap it to report against
    # the combined total with the post-fire phase offset past the baseline.
    total_scenes = len(baseline_items) + len(postfire_items)

    _note(message, "Loading baseline scenes")
    baseline_scenes = stac.load_scenes(
        baseline_items, grid, progress=_offset_progress(scene_progress, 0, total_scenes)
    )
    if is_cancelled is not None and is_cancelled():
        return None

    _note(message, "Loading post-fire scenes")
    postfire_scenes = stac.load_scenes(
        postfire_items,
        grid,
        progress=_offset_progress(scene_progress, len(baseline_items), total_scenes),
    )
    if is_cancelled is not None and is_cancelled():
        return None

    _note(message, "Computing dNBR and classifying severity")
    return analysis.run_from_scenes(
        baseline_scenes,
        postfire_scenes,
        grid,
        config,
        fire_date=config.fire_date_obj,
        aoi_mask=aoi_mask,
    )


def nbr_from_source(
    source: str,
    grid: RasterGrid,
    *,
    nir_band: int,
    swir_band: int | None,
    resampling: str = "bilinear",
) -> NDArray[np.floating]:
    """Return an NBR array read from ``source`` onto ``grid``.

    When ``swir_band`` is ``None`` the raster is assumed to already hold NBR and
    ``nir_band`` is read straight through; otherwise NBR is computed from the
    NIR and SWIR bands via :func:`~burnt_area_toolbox.bacore.severity.compute_nbr`.

    Args:
        source: A GDAL-openable raster path or descriptor.
        grid: The destination analysis grid.
        nir_band: The 1-based NIR band index (or the NBR band when
            ``swir_band`` is ``None``).
        swir_band: The 1-based SWIR band index, or ``None`` for direct NBR.
        resampling: The resampling method for the reads.

    Returns:
        A float64 NBR array of shape ``grid.shape``.
    """
    if swir_band is None:
        return raster.read_band_on_grid(source, grid, nir_band, resampling)
    nir = raster.read_band_on_grid(source, grid, nir_band, resampling)
    swir = raster.read_band_on_grid(source, grid, swir_band, resampling)
    return severity.compute_nbr(nir, swir)


def run_from_rasters(
    config: BurntAreaConfig,
    grid: RasterGrid,
    *,
    pre_source: str,
    post_source: str,
    nir_band: int = 1,
    swir_band: int | None = 2,
    resampling: str = "bilinear",
    aoi_mask: NDArray[np.bool_] | None = None,
) -> AnalysisResult:
    """Run the dNBR analysis from two user-supplied rasters.

    Both rasters are read onto the same ``grid`` (the post-fire raster's own
    grid, typically), so pre- and post-fire NBR are directly differenced.

    Args:
        config: The run configuration (only ``threshold``, the cleanup sizes
            and ``resolution_m`` are consulted here).
        grid: The shared analysis grid both rasters are sampled onto.
        pre_source: The pre-fire (baseline) raster path or descriptor.
        post_source: The post-fire raster path or descriptor.
        nir_band: The 1-based NIR band index (or NBR band when ``swir_band``
            is ``None``).
        swir_band: The 1-based SWIR band index, or ``None`` when the rasters
            already hold NBR.
        resampling: The resampling method for the reads.
        aoi_mask: Optional boolean AOI mask (``True`` inside the AOI).

    Returns:
        The populated :class:`AnalysisResult`.
    """
    baseline_nbr = nbr_from_source(
        pre_source, grid, nir_band=nir_band, swir_band=swir_band, resampling=resampling
    )
    postfire_nbr = nbr_from_source(
        post_source, grid, nir_band=nir_band, swir_band=swir_band, resampling=resampling
    )
    return analysis.compute_burnt_area(baseline_nbr, postfire_nbr, grid, config, aoi_mask=aoi_mask)
