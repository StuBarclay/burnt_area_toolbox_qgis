"""Sandbox tests for the pure-numpy severity science.

These reproduce the exact expected values from the upstream
``burnt_area_mapping`` project's ``tests/test_analysis.py`` so the port is
provably faithful. No GDAL or ``qgis`` is needed here.
"""

from __future__ import annotations

import numpy as np
from burnt_area_toolbox.bacore import severity


def test_compute_nbr_returns_expected_values() -> None:
    nir = np.array([[0.8, 0.5]])
    swir22 = np.array([[0.2, 0.5]])

    result = severity.compute_nbr(nir, swir22)

    np.testing.assert_allclose(result, np.array([[0.6, 0.0]]))


def test_compute_nbr_zero_denominator_is_nan() -> None:
    nir = np.array([[0.0, 0.3]])
    swir22 = np.array([[0.0, 0.1]])

    result = severity.compute_nbr(nir, swir22)

    assert np.isnan(result[0, 0])
    np.testing.assert_allclose(result[0, 1], 0.5)


def test_compute_nbr_propagates_nan() -> None:
    nir = np.array([[np.nan, 0.8]])
    swir22 = np.array([[0.2, 0.2]])

    result = severity.compute_nbr(nir, swir22)

    assert np.isnan(result[0, 0])
    np.testing.assert_allclose(result[0, 1], 0.6)


def test_classify_dnbr_assigns_expected_classes() -> None:
    delta_nbr = np.array([[-0.40, -0.20, 0.00, 0.20, 0.35, 0.50, 0.80, np.nan]])

    result = severity.classify_dnbr(delta_nbr)

    np.testing.assert_array_equal(result, np.array([[1, 2, 3, 4, 5, 6, 7, 0]], dtype=np.uint8))
    assert result.dtype == np.uint8


def test_classify_dnbr_break_boundaries_are_inclusive_lower() -> None:
    # Exactly-on-break values fall into the higher class (breaks are the
    # lower bound of each class): -0.250 -> 2, 0.660 -> 7.
    delta_nbr = np.array([[-0.250, -0.100, 0.100, 0.270, 0.440, 0.660]])

    result = severity.classify_dnbr(delta_nbr)

    np.testing.assert_array_equal(result, np.array([[2, 3, 4, 5, 6, 7]], dtype=np.uint8))


def test_cleanup_burnt_mask_removes_speckles_and_fills_holes() -> None:
    mask = np.array(
        [
            [1, 1, 1, 0, 0],
            [1, 0, 1, 0, 0],
            [1, 1, 1, 0, 0],
            [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 1],
        ],
        dtype=float,
    )

    cleaned = severity.cleanup_burnt_mask(mask, min_patch_pixels=2, fill_holes_pixels=2)

    np.testing.assert_array_equal(
        cleaned,
        np.array(
            [
                [1, 1, 1, 0, 0],
                [1, 1, 1, 0, 0],
                [1, 1, 1, 0, 0],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0],
            ],
            dtype=float,
        ),
    )


def test_cleanup_burnt_mask_preserves_nodata() -> None:
    mask = np.array([[1.0, np.nan], [1.0, 1.0]])

    cleaned = severity.cleanup_burnt_mask(mask)

    assert np.isnan(cleaned[0, 1])
    assert cleaned[0, 0] == 1.0


def test_sieve_no_op_for_size_one() -> None:
    data = np.array([[1, 0], [0, 1]], dtype=np.uint8)
    np.testing.assert_array_equal(severity.sieve(data, size=1), data)


def test_pixel_area_km2() -> None:
    assert severity.pixel_area_km2(20) == 0.0004
    assert severity.pixel_area_km2(10) == 0.0001


def test_summarize_severity_areas() -> None:
    severity_class = np.array([[7, 4], [0, 4]], dtype=np.uint8)

    areas = severity.summarize_severity_areas(severity_class, resolution_m=20)

    assert areas["High Severity"] == 0.0004
    assert areas["Low Severity"] == 0.0008
    assert areas["Unburned"] == 0.0


def test_area_summary_matches_upstream_run() -> None:
    # Mirrors test_run_analysis_computes_expected_area_summary: dNBR of
    # [[1.1], [0.2]] at 20 m with threshold 0.5.
    delta_nbr = np.array([[1.1], [0.2]])
    burnt_mask = np.array([[1.0], [0.0]])

    summary = severity.area_summary(delta_nbr, burnt_mask, threshold=0.5, resolution_m=20)

    assert summary["burnt_area_km2"] == 0.0004
    assert summary["unburnt_area_km2"] == 0.0004
    assert summary["valid_area_km2"] == 0.0008
    assert summary["nodata_area_km2"] == 0.0
