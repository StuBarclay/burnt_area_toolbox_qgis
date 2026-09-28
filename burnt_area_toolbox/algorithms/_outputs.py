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

    The three key rasters (``dnbr``, ``severity``, ``burnt_mask``) are always
    written. The remaining files are only produced for the full result set (an
    output folder was set); in the quick "key rasters only" mode they are
    ``None``.

    Attributes:
        baseline_nbr: The baseline NBR GeoTIFF (``None`` in key-rasters mode).
        postfire_nbr: The post-fire NBR GeoTIFF (``None`` in key-rasters mode).
        dnbr: The dNBR (delta NBR) GeoTIFF.
        severity: The seven-class severity GeoTIFF.
        burnt_mask: The burnt-mask GeoTIFF.
        summary_csv: The one-row ``summary.csv`` (``None`` in key-rasters mode).
        vectors: Any GeoJSON polygon files written (empty when disabled).
        manifest: The ``run_manifest.json`` (``None`` in key-rasters mode).
        all_paths: Every file written, in write order.
    """

    baseline_nbr: Path | None
    postfire_nbr: Path | None
    dnbr: Path
    severity: Path
    burnt_mask: Path
    summary_csv: Path | None
    vectors: list[Path]
    manifest: Path | None
    all_paths: list[Path]


def write_run_outputs(
    config: BurntAreaConfig,
    result: AnalysisResult,
    outdir: str | Path,
    *,
    full_set: bool = True,
) -> RunOutputs:
    """Write a run's outputs and return their paths.

    Args:
        config: The run configuration.
        result: The analysis result to serialise.
        outdir: The output directory (created if needed).
        full_set: When ``True`` (an output folder the user wants to keep),
            write the full result set -- all five GeoTIFFs, the summary CSV,
            the optional polygons and the manifest. When ``False`` (a scratch
            directory that will be discarded), write only the three exportable
            rasters, so a quick run does not spend I/O on files that would be
            thrown away.

    Returns:
        A :class:`RunOutputs` with the individual raster paths resolved.
    """
    out = Path(outdir)

    if not full_set:
        dnbr, severity, burnt_mask = export_mod.write_key_rasters(config, result, out)
        return RunOutputs(
            baseline_nbr=None,
            postfire_nbr=None,
            dnbr=dnbr,
            severity=severity,
            burnt_mask=burnt_mask,
            summary_csv=None,
            vectors=[],
            manifest=None,
            all_paths=[dnbr, severity, burnt_mask],
        )

    # write_result_rasters returns, in this fixed order:
    # [baseline_nbr, postfire_nbr, delta_nbr, dnbr_severity, burnt_mask].
    baseline_nbr, postfire_nbr, dnbr, severity, burnt_mask = export_mod.write_result_rasters(
        config, result, out
    )
    summary_csv = export_mod.write_summary_csv(config, result, out)
    vectors = export_mod.write_vectors(config, result, out)
    # The manifest lists exactly the files this run wrote (not any pre-existing
    # contents of a user-chosen folder), so pass them explicitly.
    written = [baseline_nbr, postfire_nbr, dnbr, severity, burnt_mask, summary_csv, *vectors]
    manifest = export_mod.write_manifest(config, result, out, written=written)
    all_paths = [*written, manifest]
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
