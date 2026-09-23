"""Sandbox tests for the vector area maths (no GDAL required)."""

from __future__ import annotations

from burnt_area_toolbox.bacore import vector
from burnt_area_toolbox.bacore.vector import VectorFeature


def _square(size: float) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, size], [size, size], [size, 0.0], [0.0, 0.0]]],
    }


def test_ring_area_of_unit_square() -> None:
    ring = [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0], [0.0, 0.0]]
    assert vector._ring_area(ring) == 100.0


def test_polygon_area_subtracts_holes() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0], [0.0, 0.0]],
            [[2.0, 2.0], [2.0, 4.0], [4.0, 4.0], [4.0, 2.0], [2.0, 2.0]],
        ],
    }
    assert vector._polygon_area(polygon) == 96.0  # 100 exterior - 4 hole


def test_polygon_area_multipolygon() -> None:
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            _square(10.0)["coordinates"],
            _square(2.0)["coordinates"],
        ],
    }
    assert vector._polygon_area(multi) == 104.0  # 100 + 4


def test_polygon_area_ignores_non_polygon() -> None:
    assert vector._polygon_area({"type": "Point", "coordinates": [1.0, 2.0]}) == 0.0


def test_filter_min_area() -> None:
    features = [
        VectorFeature(_square(1.0), 1, "Burnt", "#d95f0e", area_km2=0.4),
        VectorFeature(_square(1.0), 1, "Burnt", "#d95f0e", area_km2=1.5),
    ]
    kept = vector.filter_min_area(features, 1.0)
    assert [feature.area_km2 for feature in kept] == [1.5]
    assert vector.filter_min_area(features, 0.0) == features
