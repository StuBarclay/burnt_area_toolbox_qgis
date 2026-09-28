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
    monkeypatch.setattr(
        export_mod, "write_manifest", lambda config, result, out, written=None: manifest
    )

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
    monkeypatch.setattr(
        export_mod, "write_manifest", lambda config, result, out, written=None: manifest
    )

    outputs = _outputs.write_run_outputs(cast(Any, None), cast(Any, None), tmp_path)

    assert outputs.vectors == []
    assert outputs.all_paths == [*rasters, summary, manifest]


def test_write_run_outputs_key_rasters_only(monkeypatch: Any, tmp_path: Path) -> None:
    """With ``full_set=False`` only the three key rasters are written."""
    keys = (tmp_path / "dnbr.tif", tmp_path / "severity.tif", tmp_path / "burnt.tif")

    def _boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("full-set writers must not be called in key-rasters mode")

    monkeypatch.setattr(export_mod, "write_key_rasters", lambda config, result, out: keys)
    monkeypatch.setattr(export_mod, "write_result_rasters", _boom)
    monkeypatch.setattr(export_mod, "write_summary_csv", _boom)
    monkeypatch.setattr(export_mod, "write_vectors", _boom)
    monkeypatch.setattr(export_mod, "write_manifest", _boom)

    outputs = _outputs.write_run_outputs(cast(Any, None), cast(Any, None), tmp_path, full_set=False)

    assert (outputs.dnbr, outputs.severity, outputs.burnt_mask) == keys
    assert outputs.baseline_nbr is None
    assert outputs.postfire_nbr is None
    assert outputs.summary_csv is None
    assert outputs.manifest is None
    assert outputs.vectors == []
    assert outputs.all_paths == list(keys)
