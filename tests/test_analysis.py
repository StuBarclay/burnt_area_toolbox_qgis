"""Sandbox tests for the numpy dNBR orchestration (no GDAL required)."""

from __future__ import annotations

from datetime import date

import numpy as np
from burnt_area_toolbox.bacore import analysis
from burnt_area_toolbox.bacore._rio.affine import Affine
from burnt_area_toolbox.bacore.config import BBox, BurntAreaConfig
from burnt_area_toolbox.bacore.raster import RasterGrid
from burnt_area_toolbox.bacore.scenes import Scene


def _grid(height: int, width: int, resolution_m: float = 100.0) -> RasterGrid:
    transform = Affine(resolution_m, 0.0, 0.0, 0.0, -resolution_m, 0.0)
    return RasterGrid(transform=transform, crs=None, width=width, height=height)


def _config(**overrides: object) -> BurntAreaConfig:
    params: dict[str, object] = {
        "project_name": "demo",
        "catalog_url": "https://example.test",
        "collection": "sentinel-2-l2a",
        "area_name": "Test Area",
        "fire_date": date(2020, 1, 5),
        "baseline_length_months": 6,
        "post_window_days": 30,
        "threshold": 0.2,
        "max_cloud_cover": 30,
        "min_valid_fraction": 0.4,
        "output_crs": "EPSG:3577",
        "resolution_m": 100,
        "bbox": BBox(150.0, -33.0, 151.0, -32.0),
    }
    params.update(overrides)
    return BurntAreaConfig(**params)  # type: ignore[arg-type]


def test_compute_burnt_area_tallies_and_classes() -> None:
    baseline = np.array([[0.5, 0.5], [0.5, np.nan]])
    postfire = np.array([[0.0, 0.35], [0.5, 0.5]])
    # delta = [[0.5, 0.15], [0.0, nan]] (0.15 avoids the class-3/4 boundary at 0.1)
    result = analysis.compute_burnt_area(baseline, postfire, _grid(2, 2), _config())

    # area_per_pixel = 100^2 / 1e6 = 0.01 km^2
    assert result.burnt_area_km2 == 0.01  # only the 0.5 delta pixel exceeds 0.2
    assert result.unburnt_area_km2 == 0.02  # the 0.15 and 0.0 pixels
    assert result.valid_area_km2 == 0.03  # three finite delta cells
    assert result.nodata_area_km2 == 0.01  # the nan cell

    np.testing.assert_array_equal(result.severity_class, np.array([[6, 4], [3, 0]], dtype=np.uint8))
    assert result.severity_area_km2["Moderate-high Severity"] == 0.01
    assert result.severity_area_km2["Low Severity"] == 0.01
    assert result.severity_area_km2["Unburned"] == 0.01


def test_compute_burnt_area_respects_aoi_mask() -> None:
    baseline = np.array([[0.9, 0.9]])
    postfire = np.array([[0.0, 0.0]])  # delta 0.9 everywhere -> burnt
    aoi = np.array([[True, False]])  # only the left cell is inside the AOI
    result = analysis.compute_burnt_area(baseline, postfire, _grid(1, 2), _config(), aoi_mask=aoi)

    assert result.burnt_area_km2 == 0.01  # one in-AOI pixel
    assert result.nodata_area_km2 == 0.01  # the masked-out pixel is nodata
    assert np.isnan(result.delta_nbr[0, 1])


def _scene(
    nir: float, swir: float, scl: int, scene_id: str, obs: str, shape: tuple[int, int]
) -> Scene:
    return Scene(
        bands={
            "nir": np.full(shape, nir, dtype=float),
            "swir22": np.full(shape, swir, dtype=float),
        },
        scl=np.full(shape, scl),
        scene_id=scene_id,
        observation_date=obs,
    )


def test_run_from_scenes_best_single_picks_and_computes() -> None:
    shape = (1, 1)
    baseline = [_scene(0.6, 0.2, 4, "base-1", "2019-12-01", shape)]
    postfire = [
        _scene(0.2, 0.6, 4, "post-far", "2020-01-30", shape),
        _scene(0.2, 0.6, 4, "post-near", "2020-01-07", shape),
    ]
    result = analysis.run_from_scenes(baseline, postfire, _grid(*shape), _config())

    # baseline NBR = (0.6-0.2)/(0.8) = 0.5 ; postfire NBR = (0.2-0.6)/0.8 = -0.5
    # delta = 1.0 -> burnt (threshold 0.2)
    assert result.selected_postfire_scene_id == "post-near"
    assert result.burnt_area_km2 == 0.01
    assert result.baseline_scene_ids_used == ["base-1"]
