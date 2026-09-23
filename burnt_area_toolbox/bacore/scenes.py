"""Per-scene quality control and compositing, in pure :mod:`numpy`.

The STAC loader turns each Sentinel-2 item into a :class:`Scene`: a set of
reflectance bands and (optionally) the Scene Classification Layer, all
already resampled onto one common grid. This module then reproduces the
upstream project's scene handling with no GDAL dependency:

* :func:`scene_valid_fraction` -- the clear-pixel fraction from the SCL;
* :func:`build_scene_audit` / :func:`retained_scene_ids` -- the per-scene
  audit trail and the list of scenes that pass the quality gate;
* :func:`apply_quality_mask` -- drop scenes below the valid-fraction
  threshold and mask cloudy pixels to ``nan`` within those kept;
* :func:`composite_median` -- the median composite used for the baseline
  (and the ``median`` post-fire strategy);
* :func:`select_best_postfire` -- pick the clearest, then closest, post-fire
  scene.

Nodata and masked-out cells are ``numpy.nan`` so compositing skips them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from numpy.typing import NDArray

from .constants import CLEAR_SCL_CLASSES


class BurntAreaError(RuntimeError):
    """Raised when a run cannot proceed (e.g. every scene fails QC)."""


@dataclass(slots=True)
class Scene:
    """One acquisition resampled onto the common analysis grid.

    Attributes:
        bands: Logical band name -> 2-D float array (``nan`` = nodata).
        scl: The Scene Classification Layer as a 2-D array, or ``None`` when
            the catalogue/item exposes no SCL.
        scene_id: The STAC item id, if known.
        observation_date: The acquisition date as ``"YYYY-MM-DD"``, if known.
        cloud_cover: The item's ``eo:cloud_cover`` percentage, if known.
    """

    bands: dict[str, NDArray[np.float64]]
    scl: NDArray[np.float64] | None = None
    scene_id: str | None = None
    observation_date: str | None = None
    cloud_cover: float | None = None


def scene_valid_fraction(scl: NDArray[np.floating] | None) -> float:
    """Return the fraction of clear pixels in a scene's SCL.

    Args:
        scl: The Scene Classification Layer, or ``None`` (treated as fully
            clear, matching the upstream behaviour when no SCL is present).

    Returns:
        The mean over the SCL of membership in :data:`CLEAR_SCL_CLASSES`, in
        ``[0, 1]``; ``1.0`` when ``scl`` is ``None`` or empty.
    """
    if scl is None:
        return 1.0
    values = np.asarray(scl)
    if values.size == 0:
        return 1.0
    clear = np.isin(values, list(CLEAR_SCL_CLASSES))
    return float(clear.mean())


def build_scene_audit(
    scenes: list[Scene],
    min_valid_fraction: float,
) -> list[dict[str, object]]:
    """Return a per-scene audit record with the clear fraction and keep flag.

    Args:
        scenes: The scenes to audit.
        min_valid_fraction: The clear-fraction threshold to keep a scene.

    Returns:
        One mapping per scene with ``scene_id``, ``observation_date``,
        ``cloud_cover``, ``valid_fraction`` and ``kept``.
    """
    audit: list[dict[str, object]] = []
    for scene in scenes:
        fraction = scene_valid_fraction(scene.scl)
        audit.append(
            {
                "scene_id": scene.scene_id,
                "observation_date": scene.observation_date,
                "cloud_cover": scene.cloud_cover,
                "valid_fraction": fraction,
                "kept": fraction >= min_valid_fraction,
            }
        )
    return audit


def retained_scene_ids(scene_audit: list[dict[str, object]]) -> list[str]:
    """Return the ids of scenes the audit marked as kept."""
    return [
        str(record["scene_id"])
        for record in scene_audit
        if record.get("kept") and record.get("scene_id") is not None
    ]


def apply_quality_mask(
    scenes: list[Scene],
    min_valid_fraction: float,
) -> list[Scene]:
    """Drop poor scenes and mask cloudy pixels within the rest.

    Scenes whose clear fraction is below ``min_valid_fraction`` are removed
    entirely; in each kept scene every band pixel whose SCL value is not a
    clear class is set to ``nan``. Scenes without an SCL are kept unchanged.

    Args:
        scenes: The candidate scenes.
        min_valid_fraction: The clear-fraction threshold to keep a scene.

    Returns:
        The kept, pixel-masked scenes.

    Raises:
        BurntAreaError: If every scene fails the quality gate.
    """
    kept: list[Scene] = []
    for scene in scenes:
        if scene_valid_fraction(scene.scl) < min_valid_fraction:
            continue
        if scene.scl is None:
            kept.append(scene)
            continue
        clear = np.isin(np.asarray(scene.scl), list(CLEAR_SCL_CLASSES))
        masked_bands = {
            name: np.where(clear, band, np.nan).astype(np.float64)
            for name, band in scene.bands.items()
        }
        kept.append(
            Scene(
                bands=masked_bands,
                scl=scene.scl,
                scene_id=scene.scene_id,
                observation_date=scene.observation_date,
                cloud_cover=scene.cloud_cover,
            )
        )
    if not kept:
        raise BurntAreaError(
            "All scenes were removed by the quality mask. Relax max_cloud_cover "
            "or min_valid_fraction, or widen the date window."
        )
    return kept


def composite_median(scenes: list[Scene]) -> dict[str, NDArray[np.float64]]:
    """Return the per-band median composite over a set of scenes.

    Args:
        scenes: The scenes to composite (must share a grid and band set).

    Returns:
        A mapping of band name -> the ``nan``-skipping median across scenes.

    Raises:
        BurntAreaError: If ``scenes`` is empty.
    """
    if not scenes:
        raise BurntAreaError("Cannot composite an empty set of scenes.")
    band_names = list(scenes[0].bands.keys())
    composite: dict[str, NDArray[np.float64]] = {}
    for name in band_names:
        stack = np.stack([np.asarray(scene.bands[name], dtype=np.float64) for scene in scenes])
        with np.errstate(invalid="ignore"):
            all_nan = np.all(np.isnan(stack), axis=0)
            median = np.where(all_nan, np.nan, np.nanmedian(np.where(all_nan, 0.0, stack), axis=0))
        composite[name] = np.asarray(median, dtype=np.float64)
    return composite


def _observation_offset_days(observation_date: str | None, fire_date: date) -> float:
    """Return the absolute day offset of a scene from the fire date.

    Args:
        observation_date: ``"YYYY-MM-DD"`` or ``None``.
        fire_date: The fire date.

    Returns:
        The absolute number of days between the two, or ``inf`` when the
        scene has no parseable date (so it sorts last on the tie-break).
    """
    if not observation_date:
        return float("inf")
    try:
        observed = datetime.strptime(observation_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return float("inf")
    return float(abs((observed - fire_date).days))


def select_best_postfire(
    scenes: list[Scene],
    fire_date: date,
) -> tuple[dict[str, NDArray[np.float64]], str | None, float | None, str | None]:
    """Pick the best single post-fire scene.

    Scenes are ranked by descending clear fraction, then by ascending
    distance (in days) from the fire date, then by ascending observation
    date -- exactly the upstream ordering. The winner's bands are returned.

    Args:
        scenes: The candidate (already quality-masked) post-fire scenes.
        fire_date: The fire date used to measure temporal distance.

    Returns:
        A tuple of the chosen scene's ``bands``, its ``scene_id``, its
        ``valid_fraction`` and its ``observation_date`` (any of which may be
        ``None`` when unknown).

    Raises:
        BurntAreaError: If ``scenes`` is empty.
    """
    if not scenes:
        raise BurntAreaError("No post-fire scenes available to select from.")
    if len(scenes) == 1:
        scene = scenes[0]
        return (
            scene.bands,
            scene.scene_id,
            scene_valid_fraction(scene.scl),
            scene.observation_date,
        )

    def sort_key(index: int) -> tuple[float, float, str]:
        scene = scenes[index]
        fraction = scene_valid_fraction(scene.scl)
        offset = _observation_offset_days(scene.observation_date, fire_date)
        return (-fraction, offset, scene.observation_date or "")

    best = sorted(range(len(scenes)), key=sort_key)[0]
    scene = scenes[best]
    return (
        scene.bands,
        scene.scene_id,
        scene_valid_fraction(scene.scl),
        scene.observation_date,
    )


@dataclass(slots=True)
class CompositeResult:
    """The bands and provenance produced by compositing a scene set."""

    bands: dict[str, NDArray[np.float64]]
    scene_ids_used: list[str] = field(default_factory=list)
    selected_scene_id: str | None = None
    observation_date: str | None = None
    valid_fraction: float | None = None
