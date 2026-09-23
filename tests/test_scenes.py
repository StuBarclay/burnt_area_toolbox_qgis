"""Sandbox tests for the pure-numpy scene selection and compositing."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from burnt_area_toolbox.bacore import scenes
from burnt_area_toolbox.bacore.scenes import BurntAreaError, Scene


def _scene(nir: list[list[float]], scl: list[list[int]] | None, **kwargs: object) -> Scene:
    bands = {"nir": np.array(nir, dtype=float)}
    scl_arr = None if scl is None else np.array(scl)
    return Scene(bands=bands, scl=scl_arr, **kwargs)  # type: ignore[arg-type]


def test_scene_valid_fraction_counts_clear_classes() -> None:
    # SCL 4 (veg) and 5 (bare) are clear; 1 (defective) and 9 (high cloud) are not.
    scl = np.array([[4, 5], [1, 9]])
    assert scenes.scene_valid_fraction(scl) == 0.5


def test_scene_valid_fraction_none_is_fully_clear() -> None:
    assert scenes.scene_valid_fraction(None) == 1.0


def test_build_scene_audit_and_retained_ids() -> None:
    audit = scenes.build_scene_audit(
        [
            _scene([[0.2]], [[4], [4]], scene_id="clear", observation_date="2020-01-07"),
            _scene([[0.2]], [[4], [1]], scene_id="cloudy", observation_date="2020-01-08"),
        ],
        min_valid_fraction=0.8,
    )

    assert audit[0]["kept"] is True
    assert audit[1]["kept"] is False
    assert scenes.retained_scene_ids(audit) == ["clear"]


def test_apply_quality_mask_drops_and_masks() -> None:
    kept = scenes.apply_quality_mask(
        [
            _scene([[0.2, 0.3]], [[4, 1]], scene_id="a"),  # 0.5 clear -> dropped
            _scene([[0.5, 0.6]], [[4, 1]], scene_id="b"),  # dropped too
            _scene([[0.7, 0.8]], [[4, 4]], scene_id="c"),  # 1.0 clear -> kept
        ],
        min_valid_fraction=0.6,
    )

    assert [scene.scene_id for scene in kept] == ["c"]


def test_apply_quality_mask_masks_cloudy_pixels_to_nan() -> None:
    kept = scenes.apply_quality_mask(
        [_scene([[0.7, 0.8]], [[4, 1]], scene_id="c")],
        min_valid_fraction=0.4,
    )

    band = kept[0].bands["nir"]
    assert band[0, 0] == 0.7
    assert np.isnan(band[0, 1])


def test_apply_quality_mask_raises_when_all_dropped() -> None:
    with pytest.raises(BurntAreaError):
        scenes.apply_quality_mask(
            [_scene([[0.2]], [[1], [1]], scene_id="a")],
            min_valid_fraction=0.9,
        )


def test_composite_median_skips_nan() -> None:
    result = scenes.composite_median(
        [
            Scene(bands={"nir": np.array([[0.2, np.nan]])}),
            Scene(bands={"nir": np.array([[0.4, 0.6]])}),
            Scene(bands={"nir": np.array([[0.6, 0.8]])}),
        ]
    )

    np.testing.assert_allclose(result["nir"], np.array([[0.4, 0.7]]))


def test_composite_median_all_nan_pixel_stays_nan() -> None:
    result = scenes.composite_median(
        [
            Scene(bands={"nir": np.array([[np.nan]])}),
            Scene(bands={"nir": np.array([[np.nan]])}),
        ]
    )

    assert np.isnan(result["nir"][0, 0])


def test_select_best_postfire_prefers_clearer_then_closer() -> None:
    # Mirrors test_select_best_postfire_image_prefers_clearer_then_closer_scene.
    scene_a = _scene([[0.2], [0.2]], [[4], [1]], scene_id="scene-a", observation_date="2020-01-20")
    scene_b = _scene([[0.7], [0.7]], [[4], [4]], scene_id="scene-b", observation_date="2020-01-07")
    scene_c = _scene([[0.9], [0.9]], [[4], [4]], scene_id="scene-c", observation_date="2020-01-09")

    bands, scene_id, valid_fraction, observation_date = scenes.select_best_postfire(
        [scene_a, scene_b, scene_c], fire_date=date(2020, 1, 5)
    )

    assert scene_id == "scene-b"
    assert observation_date == "2020-01-07"
    assert valid_fraction == 1.0
    np.testing.assert_allclose(bands["nir"], np.array([[0.7], [0.7]]))


def test_select_best_postfire_single_scene() -> None:
    only = _scene([[0.5]], [[4]], scene_id="only", observation_date="2020-02-01")
    _bands, scene_id, valid_fraction, _observation_date = scenes.select_best_postfire(
        [only], fire_date=date(2020, 1, 5)
    )
    assert scene_id == "only"
    assert valid_fraction == 1.0


def test_select_best_postfire_empty_raises() -> None:
    with pytest.raises(BurntAreaError):
        scenes.select_best_postfire([], fire_date=date(2020, 1, 5))
