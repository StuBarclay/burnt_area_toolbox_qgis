"""Tests for the run-output assembler.

:mod:`burnt_area_toolbox.algorithms._outputs` is a thin wrapper: its only real
logic is the fixed *order* in which the five rasters unpack and the way the
per-file paths are gathered into ``all_paths``. The GDAL-backed writers it
delegates to (in ``bacore.export``) are covered by the core's own tests, so
here they are monkeypatched to plain path stubs. That keeps this a pure-sandbox
test of the wrapper's contract, with no GDAL required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from burnt_area_toolbox.algorithms import _outputs
from burnt_area_toolbox.bacore import export as export_mod


def test_write_run_outputs_names_rasters_and_orders_all_paths(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The five rasters map to the right fields and ``all_paths`` is in order."""
    rasters = [
        tmp_path / "baseline_nbr.tif",
        tmp_path / "postfire_nbr.tif",
        tmp_path / "dnbr.tif",
        tmp_path / "dnbr_severity.tif",
        tmp_path / "burnt_mask.tif",
    ]
    summary = tmp_path / "summary.csv"
    vectors = [tmp_path / "burnt.geojson", tmp_path / "severity.geojson"]
    manifest = tmp_path / "run_manifest.json"

    monkeypatch.setattr(
        export_mod, "write_result_rasters", lambda config, result, out: list(rasters)
    )
    monkeypatch.setattr(export_mod, "write_summary_csv", lambda config, result, out: summary)
    monkeypatch.setattr(export_mod, "write_vectors", lambda config, result, out: vectors)
    monkeypatch.setattr(export_mod, "write_manifest", lambda config, result, out: manifest)

    outputs = _outputs.write_run_outputs(cast(Any, None), cast(Any, None), tmp_path)

    assert outputs.baseline_nbr == rasters[0]
    assert outputs.postfire_nbr == rasters[1]
    assert outputs.dnbr == rasters[2]
    assert outputs.severity == rasters[3]
    assert outputs.burnt_mask == rasters[4]
    assert outputs.summary_csv == summary
    assert outputs.vectors == vectors
    assert outputs.manifest == manifest
    # all_paths is: five rasters, then summary, then any vectors, then manifest.
    assert outputs.all_paths == [*rasters, summary, *vectors, manifest]


def test_write_run_outputs_handles_no_vectors(monkeypatch: Any, tmp_path: Path) -> None:
    """With polygon export off, ``vectors`` is empty and drops out of all_paths."""
    rasters = [tmp_path / f"r{i}.tif" for i in range(5)]
    summary = tmp_path / "summary.csv"
    manifest = tmp_path / "run_manifest.json"
    monkeypatch.setattr(
        export_mod, "write_result_rasters", lambda config, result, out: list(rasters)
    )
    monkeypatch.setattr(export_mod, "write_summary_csv", lambda config, result, out: summary)
    monkeypatch.setattr(export_mod, "write_vectors", lambda config, result, out: [])
    monkeypatch.setattr(export_mod, "write_manifest", lambda config, result, out: manifest)

    outputs = _outputs.write_run_outputs(cast(Any, None), cast(Any, None), tmp_path)

    assert outputs.vectors == []
    assert outputs.all_paths == [*rasters, summary, manifest]
