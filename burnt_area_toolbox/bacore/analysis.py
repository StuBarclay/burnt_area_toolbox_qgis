"""dNBR orchestration on numpy arrays, free of GDAL and ``qgis``.

This module joins the pure-numpy science in
:mod:`~burnt_area_toolbox.bacore.severity` and the scene handling in
:mod:`~burnt_area_toolbox.bacore.scenes` into the two run flows the plugin
offers, and packages the result in :class:`AnalysisResult`:

* :func:`compute_burnt_area` -- the shared dNBR pipeline that turns a baseline
  and a post-fire NBR array into the delta, the severity classification, the
  cleaned burnt mask and the area tallies. Both algorithms funnel through it.
* :func:`run_from_scenes` -- the STAC flow: quality-mask and composite the
  baseline scenes, pick or composite the post-fire scene(s), compute NBR for
  each and hand off to :func:`compute_burnt_area`.

It mirrors the upstream ``burnt_area_mapping`` ``run_analysis`` exactly (same
thresholds, the same ``best_single`` selection key, the same burnt/valid/
unburnt/nodata bookkeeping) but works on in-memory arrays so it can be unit
tested without any geospatial stack present. GDAL only enters when the caller
builds the :class:`~burnt_area_toolbox.bacore.raster.RasterGrid` and reads the
imagery; the numerics here are GDAL-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from . import scenes, severity

if TYPE_CHECKING:
    from datetime import date

    from .config import BurntAreaConfig
    from .raster import RasterGrid
    from .scenes import Scene


@dataclass(slots=True)
class AnalysisResult:
    """The full output of a burnt-area run, held as numpy arrays on a grid.

    Attributes:
        grid: The analysis grid the arrays are defined on.
        baseline_nbr: The composited pre-fire NBR.
        postfire_nbr: The selected/composited post-fire NBR.
        delta_nbr: ``baseline_nbr - postfire_nbr`` (``nan`` = nodata).
        severity_class: The seven-class dNBR severity code raster (``0`` nodata).
        burnt_mask: The cleaned burnt mask (``1.0``/``0.0``, ``nan`` nodata).
        severity_area_km2: Area per severity class label, in km^2.
        baseline_scene_audit: Per-scene QC records for the baseline search.
        postfire_scene_audit: Per-scene QC records for the post-fire search.
        baseline_scene_ids_used: Scene ids retained in the baseline composite.
        postfire_scene_ids_used: Scene ids retained for the post-fire image.
        selected_postfire_scene_id: The chosen scene id (``best_single`` only).
        postfire_observation_date: The chosen post-fire date (``best_single``).
        postfire_valid_fraction: The chosen scene's clear fraction.
        burnt_area_km2: Total cleaned burnt area, in km^2.
        unburnt_area_km2: Total finite dNBR area at or below the threshold.
        valid_area_km2: Total finite dNBR area, in km^2.
        nodata_area_km2: Total nodata (``nan`` dNBR) area, in km^2.
    """

    grid: RasterGrid
    baseline_nbr: NDArray[np.float64]
    postfire_nbr: NDArray[np.float64]
    delta_nbr: NDArray[np.float64]
    severity_class: NDArray[np.uint8]
    burnt_mask: NDArray[np.float64]
    severity_area_km2: dict[str, float]
    baseline_scene_audit: list[dict[str, object]] = field(default_factory=list)
    postfire_scene_audit: list[dict[str, object]] = field(default_factory=list)
    baseline_scene_ids_used: list[str] = field(default_factory=list)
    postfire_scene_ids_used: list[str] = field(default_factory=list)
    selected_postfire_scene_id: str | None = None
    postfire_observation_date: str | None = None
    postfire_valid_fraction: float | None = None
    burnt_area_km2: float = 0.0
    unburnt_area_km2: float = 0.0
    valid_area_km2: float = 0.0
    nodata_area_km2: float = 0.0


def compute_burnt_area(
    baseline_nbr: NDArray[np.floating],
    postfire_nbr: NDArray[np.floating],
    grid: RasterGrid,
    config: BurntAreaConfig,
    *,
    baseline_scene_audit: list[dict[str, object]] | None = None,
    postfire_scene_audit: list[dict[str, object]] | None = None,
    baseline_scene_ids_used: list[str] | None = None,
    postfire_scene_ids_used: list[str] | None = None,
    selected_postfire_scene_id: str | None = None,
    postfire_observation_date: str | None = None,
    postfire_valid_fraction: float | None = None,
    aoi_mask: NDArray[np.bool_] | None = None,
) -> AnalysisResult:
    """Turn a baseline and post-fire NBR into a full :class:`AnalysisResult`.

    This is the shared tail of both run flows. It reproduces the upstream
    ``run_analysis`` arithmetic: ``delta = baseline - postfire``; the burnt
    mask is ``delta > threshold`` over finite cells (then morphologically
    cleaned); the severity classification and per-class areas come from the
    raw delta; and burnt/valid/unburnt/nodata areas are tallied from the delta
    and the cleaned mask.

    Args:
        baseline_nbr: The pre-fire NBR array.
        postfire_nbr: The post-fire NBR array (same shape as ``baseline_nbr``).
        grid: The analysis grid the arrays live on.
        config: The run configuration (threshold, cleanup sizes, resolution).
        baseline_scene_audit: Optional baseline QC records for provenance.
        postfire_scene_audit: Optional post-fire QC records for provenance.
        baseline_scene_ids_used: Optional baseline scene ids for provenance.
        postfire_scene_ids_used: Optional post-fire scene ids for provenance.
        selected_postfire_scene_id: Optional chosen post-fire scene id.
        postfire_observation_date: Optional chosen post-fire date.
        postfire_valid_fraction: Optional chosen post-fire clear fraction.
        aoi_mask: Optional boolean mask (``True`` inside the AOI); cells outside
            it are forced to nodata before anything is derived.

    Returns:
        The populated :class:`AnalysisResult`.
    """
    baseline = np.asarray(baseline_nbr, dtype=np.float64)
    postfire = np.asarray(postfire_nbr, dtype=np.float64)
    if aoi_mask is not None:
        outside = ~np.asarray(aoi_mask, dtype=bool)
        baseline = np.where(outside, np.nan, baseline)
        postfire = np.where(outside, np.nan, postfire)

    delta_nbr = baseline - postfire
    severity_class = severity.classify_dnbr(delta_nbr)
    severity_area_km2 = severity.summarize_severity_areas(severity_class, config.resolution_m)

    finite = ~np.isnan(delta_nbr)
    raw_burnt = np.where(finite, np.where(delta_nbr > config.threshold, 1.0, 0.0), np.nan)
    burnt_mask = severity.cleanup_burnt_mask(
        raw_burnt,
        min_patch_pixels=config.min_burnt_patch_pixels,
        fill_holes_pixels=config.fill_holes_pixels,
    )
    areas = severity.area_summary(delta_nbr, burnt_mask, config.threshold, config.resolution_m)

    return AnalysisResult(
        grid=grid,
        baseline_nbr=baseline,
        postfire_nbr=postfire,
        delta_nbr=delta_nbr,
        severity_class=severity_class,
        burnt_mask=burnt_mask,
        severity_area_km2=severity_area_km2,
        baseline_scene_audit=baseline_scene_audit or [],
        postfire_scene_audit=postfire_scene_audit or [],
        baseline_scene_ids_used=baseline_scene_ids_used or [],
        postfire_scene_ids_used=postfire_scene_ids_used or [],
        selected_postfire_scene_id=selected_postfire_scene_id,
        postfire_observation_date=postfire_observation_date,
        postfire_valid_fraction=postfire_valid_fraction,
        burnt_area_km2=areas["burnt_area_km2"],
        unburnt_area_km2=areas["unburnt_area_km2"],
        valid_area_km2=areas["valid_area_km2"],
        nodata_area_km2=areas["nodata_area_km2"],
    )


def _composite_nbr(band_scenes: list[Scene]) -> NDArray[np.float64]:
    """Median-composite ``band_scenes`` and return the NBR of the result."""
    bands = scenes.composite_median(band_scenes)
    return severity.compute_nbr(bands["nir"], bands["swir22"])


def run_from_scenes(
    baseline_scenes: list[Scene],
    postfire_scenes: list[Scene],
    grid: RasterGrid,
    config: BurntAreaConfig,
    *,
    fire_date: date | None = None,
    aoi_mask: NDArray[np.bool_] | None = None,
) -> AnalysisResult:
    """Run the full STAC flow over already-loaded :class:`Scene` objects.

    The scenes are audited and quality-masked exactly as upstream: the
    baseline is median-composited from the scenes that pass
    ``min_valid_fraction``; the post-fire image is either the single clearest,
    closest scene (``postfire_strategy == "best_single"``) or a median
    composite (``"median"``). NBR is computed for each and passed to
    :func:`compute_burnt_area`.

    Args:
        baseline_scenes: The pre-fire scenes (raw; masking happens here).
        postfire_scenes: The post-fire scenes (raw; masking happens here).
        grid: The analysis grid all scene bands are already sampled onto.
        config: The run configuration.
        fire_date: The fire date used to rank post-fire scenes by recency;
            defaults to ``config.fire_date_obj``.
        aoi_mask: Optional boolean AOI mask forwarded to
            :func:`compute_burnt_area`.

    Returns:
        The populated :class:`AnalysisResult`.
    """
    resolved_fire_date = fire_date or config.fire_date_obj

    baseline_audit = scenes.build_scene_audit(baseline_scenes, config.min_valid_fraction)
    baseline_ids_used = scenes.retained_scene_ids(baseline_audit)
    kept_baseline = scenes.apply_quality_mask(baseline_scenes, config.min_valid_fraction)
    baseline_nbr = _composite_nbr(kept_baseline)

    postfire_audit = scenes.build_scene_audit(postfire_scenes, config.min_valid_fraction)
    kept_postfire = scenes.apply_quality_mask(postfire_scenes, config.min_valid_fraction)

    selected_scene_id: str | None = None
    observation_date: str | None = None
    valid_fraction: float | None = None
    if config.postfire_strategy == "best_single":
        bands, selected_scene_id, valid_fraction, observation_date = scenes.select_best_postfire(
            kept_postfire, fire_date=resolved_fire_date
        )
        postfire_nbr = severity.compute_nbr(bands["nir"], bands["swir22"])
        postfire_ids_used = [selected_scene_id] if selected_scene_id else []
    else:
        postfire_nbr = _composite_nbr(kept_postfire)
        postfire_ids_used = scenes.retained_scene_ids(postfire_audit)

    return compute_burnt_area(
        baseline_nbr,
        postfire_nbr,
        grid,
        config,
        baseline_scene_audit=baseline_audit,
        postfire_scene_audit=postfire_audit,
        baseline_scene_ids_used=baseline_ids_used,
        postfire_scene_ids_used=postfire_ids_used,
        selected_postfire_scene_id=selected_scene_id,
        postfire_observation_date=observation_date,
        postfire_valid_fraction=valid_fraction,
        aoi_mask=aoi_mask,
    )
