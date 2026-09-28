"""Write a run's rasters, summary table, vectors and manifest to disk.

Given an :class:`~burnt_area_toolbox.bacore.analysis.AnalysisResult` and an
output directory, this module writes the five GeoTIFFs (baseline/post-fire
NBR, dNBR, the severity classification and the burnt mask), a one-row
``summary.csv``, the optional burnt/severity GeoJSON polygons, and a
``run_manifest.json`` capturing the configuration and scene provenance. It
mirrors the upstream ``burnt_area_mapping`` outputs, minus the boundary
summaries/clips that are deferred in the plugin, and uses only the standard
library plus the GDAL-free grid writer.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import vector
from .raster import float_to_coded, write_raster

if TYPE_CHECKING:
    from .analysis import AnalysisResult
    from .config import BurntAreaConfig

_NAN = float("nan")


def _slugify(value: str) -> str:
    """Return a filesystem-safe lowercase slug for ``value``."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "area"


def write_result_rasters(
    config: BurntAreaConfig,
    result: AnalysisResult,
    outdir: Path,
) -> list[Path]:
    """Write the five result GeoTIFFs and return their paths.

    Args:
        config: The run configuration (its ``area_name`` prefixes filenames).
        result: The analysis result to write.
        outdir: The output directory (created if needed).

    Returns:
        The paths of the written rasters, in a stable order.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(config.area_name)
    grid = result.grid

    paths = [
        write_raster(
            outdir / f"{slug}_baseline_nbr.tif",
            result.baseline_nbr,
            grid,
            dtype="float32",
            nodata=_NAN,
        ),
        write_raster(
            outdir / f"{slug}_postfire_nbr.tif",
            result.postfire_nbr,
            grid,
            dtype="float32",
            nodata=_NAN,
        ),
        write_raster(
            outdir / f"{slug}_delta_nbr.tif", result.delta_nbr, grid, dtype="float32", nodata=_NAN
        ),
        write_raster(
            outdir / f"{slug}_dnbr_severity.tif",
            result.severity_class,
            grid,
            dtype="uint8",
            nodata=0,
        ),
        write_raster(
            outdir / f"{slug}_burnt_mask.tif",
            float_to_coded(result.burnt_mask, fill=0),
            grid,
            dtype="uint8",
            nodata=None,
        ),
    ]
    return paths


def write_key_rasters(
    config: BurntAreaConfig,
    result: AnalysisResult,
    outdir: Path,
) -> tuple[Path, Path, Path]:
    """Write only the three exportable rasters and return their paths.

    Writes the dNBR, seven-class severity and burnt-mask GeoTIFFs -- the only
    rasters an algorithm can surface as named Processing outputs -- and skips
    the baseline / post-fire NBR, the summary CSV, the polygons and the
    manifest. Used when the user did not ask to keep the full result set (no
    output folder), so a quick run does not spend I/O writing files that would
    only be thrown away.

    Args:
        config: The run configuration (its ``area_name`` prefixes filenames).
        result: The analysis result to write.
        outdir: The output directory (created if needed).

    Returns:
        ``(dnbr, severity, burnt_mask)`` paths, matching the names
        :func:`write_result_rasters` would give those three rasters.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(config.area_name)
    grid = result.grid
    dnbr = write_raster(
        outdir / f"{slug}_delta_nbr.tif", result.delta_nbr, grid, dtype="float32", nodata=_NAN
    )
    severity = write_raster(
        outdir / f"{slug}_dnbr_severity.tif",
        result.severity_class,
        grid,
        dtype="uint8",
        nodata=0,
    )
    burnt_mask = write_raster(
        outdir / f"{slug}_burnt_mask.tif",
        float_to_coded(result.burnt_mask, fill=0),
        grid,
        dtype="uint8",
        nodata=None,
    )
    return dnbr, severity, burnt_mask


def _summary_row(config: BurntAreaConfig, result: AnalysisResult) -> dict[str, object]:
    """Return the single flat summary row (mirrors the upstream schema)."""
    summary: dict[str, object] = {
        "area_name": config.area_name,
        "fire_date": config.fire_date_str,
        "baseline_start": config.baseline_range[0],
        "baseline_end": config.baseline_range[1],
        "post_start": config.postfire_range[0],
        "post_end": config.postfire_range[1],
        "threshold": config.threshold,
        "postfire_strategy": config.postfire_strategy,
        "baseline_scenes_found": len(result.baseline_scene_audit),
        "baseline_scenes_used": len(result.baseline_scene_ids_used),
        "postfire_scenes_found": len(result.postfire_scene_audit),
        "postfire_scenes_used": len(result.postfire_scene_ids_used),
        "selected_postfire_scene_id": result.selected_postfire_scene_id,
        "postfire_image_date": result.postfire_observation_date,
        "postfire_valid_fraction": result.postfire_valid_fraction,
        "burnt_area_km2": result.burnt_area_km2,
        "unburnt_area_km2": result.unburnt_area_km2,
        "valid_area_km2": result.valid_area_km2,
        "nodata_area_km2": result.nodata_area_km2,
    }
    for label, area_km2 in result.severity_area_km2.items():
        summary[f"severity_{_slugify(label)}_km2"] = area_km2
    return summary


def write_summary_csv(config: BurntAreaConfig, result: AnalysisResult, outdir: Path) -> Path:
    """Write the one-row ``summary.csv`` and return its path."""
    outdir.mkdir(parents=True, exist_ok=True)
    row = _summary_row(config, result)
    path = outdir / "summary.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)
    return path


def write_vectors(config: BurntAreaConfig, result: AnalysisResult, outdir: Path) -> list[Path]:
    """Write the optional burnt/severity polygon GeoJSONs.

    Honours ``config.export_polygons`` (nothing written when false) and
    ``config.export_raw_polygons`` (also write the unfiltered polygons). The
    ``vector_min_area_km2`` threshold filters the cleaned outputs.

    Args:
        config: The run configuration.
        result: The analysis result.
        outdir: The output directory.

    Returns:
        The paths written (empty when ``export_polygons`` is false).
    """
    if not config.export_polygons:
        return []

    outdir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(config.area_name)
    grid = result.grid
    written: list[Path] = []

    raw_burnt = vector.burnt_polygons(result.burnt_mask, grid)
    raw_severity = vector.severity_polygons(result.severity_class, grid)
    clean_burnt = vector.filter_min_area(raw_burnt, config.vector_min_area_km2)
    clean_severity = vector.filter_min_area(raw_severity, config.vector_min_area_km2)

    if config.export_raw_polygons:
        written.append(
            vector.write_geojson(raw_burnt, outdir / f"{slug}_burnt_polygons_raw.geojson", grid)
        )
        written.append(
            vector.write_geojson(
                raw_severity, outdir / f"{slug}_dnbr_severity_polygons_raw.geojson", grid
            )
        )
    written.append(
        vector.write_geojson(clean_burnt, outdir / f"{slug}_burnt_polygons.geojson", grid)
    )
    written.append(
        vector.write_geojson(
            clean_severity, outdir / f"{slug}_dnbr_severity_polygons.geojson", grid
        )
    )
    return written


def write_manifest(
    config: BurntAreaConfig,
    result: AnalysisResult,
    outdir: Path,
    written: Iterable[Path] | None = None,
) -> Path:
    """Write ``run_manifest.json`` capturing config, ranges and provenance.

    Args:
        config: The run configuration.
        result: The analysis result.
        outdir: The output directory.
        written: The files produced by *this* run, to record under
            ``"outputs"``. When ``None`` (legacy callers) the directory is
            globbed instead -- which, in a non-empty user folder, would also
            list unrelated pre-existing files as if they were run outputs, so
            callers should pass the explicit list.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    if written is None:
        listed = sorted(
            str(path.relative_to(outdir)) for path in outdir.rglob("*") if path.is_file()
        )
    else:
        listed = sorted(_relative_output_name(path, outdir) for path in written)
    manifest: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_name": config.project_name,
        "area_name": config.area_name,
        "config": config.to_dict(),
        "baseline_range": {"start": config.baseline_range[0], "end": config.baseline_range[1]},
        "postfire_range": {"start": config.postfire_range[0], "end": config.postfire_range[1]},
        "baseline_scene_ids_used": result.baseline_scene_ids_used,
        "baseline_scene_audit": result.baseline_scene_audit,
        "postfire_scene_ids_used": result.postfire_scene_ids_used,
        "postfire_scene_audit": result.postfire_scene_audit,
        "selected_postfire_scene_id": result.selected_postfire_scene_id,
        "selected_postfire_scene_date": result.postfire_observation_date,
        "selected_postfire_valid_fraction": result.postfire_valid_fraction,
        "severity_area_km2": result.severity_area_km2,
        "outputs": sorted([*listed, "run_manifest.json"]),
    }
    path = outdir / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _relative_output_name(path: Path, outdir: Path) -> str:
    """Return ``path`` relative to ``outdir`` (or its bare name if unrelated)."""
    try:
        return str(path.relative_to(outdir))
    except ValueError:
        return path.name


def export_outputs(
    config: BurntAreaConfig, result: AnalysisResult, outdir: str | Path
) -> list[Path]:
    """Write all outputs for a run (rasters, summary, vectors, manifest).

    Args:
        config: The run configuration.
        result: The analysis result to serialise.
        outdir: The output directory.

    Returns:
        The paths of every file written, including the manifest last.
    """
    out = Path(outdir)
    written: list[Path] = []
    written.extend(write_result_rasters(config, result, out))
    written.append(write_summary_csv(config, result, out))
    written.extend(write_vectors(config, result, out))
    # Record only the files this run produced (not any pre-existing directory
    # contents), then append the manifest itself.
    written.append(write_manifest(config, result, out, written=list(written)))
    return written
