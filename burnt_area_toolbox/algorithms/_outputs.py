"""Write a run's full output set and return the key raster paths.

Both algorithms funnel their :class:`~burnt_area_toolbox.bacore.analysis.AnalysisResult`
through here so the "write the five GeoTIFFs, the summary, the optional polygons
and the manifest, then tell me where the severity / dNBR / burnt-mask rasters
landed" logic lives in one tested place. It imports only the GDAL-free export
layer, so it stays importable and unit-testable outside QGIS.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from burnt_area_toolbox.bacore import export as export_mod

if TYPE_CHECKING:
    from burnt_area_toolbox.bacore.analysis import AnalysisResult
    from burnt_area_toolbox.bacore.config import BurntAreaConfig


@dataclass(slots=True)
class RunOutputs:
    """The paths written for a run, with the key rasters named.

    Attributes:
        baseline_nbr: The baseline NBR GeoTIFF.
        postfire_nbr: The post-fire NBR GeoTIFF.
        dnbr: The dNBR (delta NBR) GeoTIFF.
        severity: The seven-class severity GeoTIFF.
        burnt_mask: The burnt-mask GeoTIFF.
        summary_csv: The one-row ``summary.csv``.
        vectors: Any GeoJSON polygon files written (empty when disabled).
        manifest: The ``run_manifest.json``.
        all_paths: Every file written, in write order.
    """

    baseline_nbr: Path
    postfire_nbr: Path
    dnbr: Path
    severity: Path
    burnt_mask: Path
    summary_csv: Path
    vectors: list[Path]
    manifest: Path
    all_paths: list[Path]


def write_run_outputs(
    config: BurntAreaConfig, result: AnalysisResult, outdir: str | Path
) -> RunOutputs:
    """Write all outputs for a run and return their paths.

    Args:
        config: The run configuration.
        result: The analysis result to serialise.
        outdir: The output directory (created if needed).

    Returns:
        A :class:`RunOutputs` with the individual raster paths resolved.
    """
    out = Path(outdir)
    # write_result_rasters returns, in this fixed order:
    # [baseline_nbr, postfire_nbr, delta_nbr, dnbr_severity, burnt_mask].
    baseline_nbr, postfire_nbr, dnbr, severity, burnt_mask = export_mod.write_result_rasters(
        config, result, out
    )
    summary_csv = export_mod.write_summary_csv(config, result, out)
    vectors = export_mod.write_vectors(config, result, out)
    manifest = export_mod.write_manifest(config, result, out)
    all_paths = [
        baseline_nbr,
        postfire_nbr,
        dnbr,
        severity,
        burnt_mask,
        summary_csv,
        *vectors,
        manifest,
    ]
    return RunOutputs(
        baseline_nbr=baseline_nbr,
        postfire_nbr=postfire_nbr,
        dnbr=dnbr,
        severity=severity,
        burnt_mask=burnt_mask,
        summary_csv=summary_csv,
        vectors=vectors,
        manifest=manifest,
        all_paths=all_paths,
    )
