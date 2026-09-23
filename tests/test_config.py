"""Sandbox tests for the run configuration (dates, validation, YAML load)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from burnt_area_toolbox.bacore.config import BBox, BurntAreaConfig, load_config


def make_config(**overrides: object) -> BurntAreaConfig:
    params: dict[str, object] = {
        "project_name": "demo",
        "catalog_url": "https://example.test",
        "collection": "sentinel-2-l2a",
        "area_name": "Test Area",
        "fire_date": date(2020, 1, 5),
        "baseline_length_months": 6,
        "post_window_days": 30,
        "threshold": 0.5,
        "max_cloud_cover": 30,
        "min_valid_fraction": 0.8,
        "output_crs": "EPSG:3577",
        "resolution_m": 20,
        "bbox": BBox(150.0, -33.0, 151.0, -32.0),
    }
    params.update(overrides)
    return BurntAreaConfig(**params)  # type: ignore[arg-type]


def test_bbox_as_tuple() -> None:
    assert BBox(1.0, 2.0, 3.0, 4.0).as_tuple() == (1.0, 2.0, 3.0, 4.0)


def test_fire_date_parsing_from_string() -> None:
    config = make_config(fire_date="2020-01-05")
    assert config.fire_date_str == "2020-01-05"
    assert config.fire_date_obj == date(2020, 1, 5)


def test_baseline_and_postfire_ranges() -> None:
    config = make_config()
    # 6 months * 30.4375 = 182.625 -> round 183 days before the fire.
    assert config.baseline_range == ("2019-07-06", "2020-01-04")
    assert config.postfire_range == ("2020-01-06", "2020-02-04")


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="threshold"):
        make_config(threshold=5.0)


def test_invalid_strategy_rejected() -> None:
    with pytest.raises(ValueError, match="postfire_strategy"):
        make_config(postfire_strategy="mean")


def test_requires_bbox_or_aoi() -> None:
    with pytest.raises(ValueError, match="bbox"):
        make_config(bbox=None)


def test_to_dict_roundtrips_key_fields() -> None:
    data = make_config().to_dict()
    assert data["fire_date"] == "2020-01-05"
    assert data["bbox"] == {
        "min_lon": 150.0,
        "min_lat": -33.0,
        "max_lon": 151.0,
        "max_lat": -32.0,
    }
    assert data["postfire_strategy"] == "best_single"


def test_max_scenes_default_is_unlimited() -> None:
    config = make_config()
    assert config.max_scenes_per_window is None
    assert config.to_dict()["max_scenes_per_window"] is None


def test_max_scenes_positive_is_kept() -> None:
    config = make_config(max_scenes_per_window=3)
    assert config.max_scenes_per_window == 3
    assert config.to_dict()["max_scenes_per_window"] == 3


def test_invalid_max_scenes_rejected() -> None:
    with pytest.raises(ValueError, match="max_scenes_per_window"):
        make_config(max_scenes_per_window=0)


def test_load_config_maps_zero_max_scenes_to_none(tmp_path: Path) -> None:
    base = {
        "project_name": "demo",
        "catalog_url": "https://example.test",
        "collection": "sentinel-2-l2a",
        "area_name": "Test Area",
        "fire_date": "2020-01-05",
        "baseline_length_months": 6,
        "post_window_days": 30,
        "threshold": 0.5,
        "max_cloud_cover": 30,
        "min_valid_fraction": 0.8,
        "output_crs": "EPSG:3577",
        "resolution_m": 20,
        "bbox": {"min_lon": 150.0, "min_lat": -33.0, "max_lon": 151.0, "max_lat": -32.0},
    }
    zero_file = tmp_path / "zero.yml"
    zero_file.write_text(yaml.safe_dump({**base, "max_scenes_per_window": 0}), encoding="utf-8")
    assert load_config(zero_file).max_scenes_per_window is None

    capped_file = tmp_path / "capped.yml"
    capped_file.write_text(yaml.safe_dump({**base, "max_scenes_per_window": 4}), encoding="utf-8")
    assert load_config(capped_file).max_scenes_per_window == 4


def test_load_config_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "run.yml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "project_name": "demo",
                "catalog_url": "https://example.test",
                "collection": "sentinel-2-l2a",
                "area_name": "Test Area",
                "fire_date": "2020-01-05",
                "baseline_length_months": 6,
                "post_window_days": 30,
                "threshold": 0.5,
                "max_cloud_cover": 30,
                "min_valid_fraction": 0.8,
                "output_crs": "EPSG:3577",
                "resolution_m": 20,
                "bbox": {
                    "min_lon": 150.0,
                    "min_lat": -33.0,
                    "max_lon": 151.0,
                    "max_lat": -32.0,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.project_name == "demo"
    assert config.fire_date_str == "2020-01-05"
    assert config.bbox is not None
    assert config.bbox.max_lat == -32.0
