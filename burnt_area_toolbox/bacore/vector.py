"""Polygonise burnt/severity rasters into vector features and GeoJSON.

The optional vector outputs turn the burnt mask and the dNBR severity raster
into polygons, each tagged with its class, label, colour and an equal-area
size in square kilometres. Polygonisation is delegated to the vendored
:func:`~burnt_area_toolbox.bacore._rio.features.shapes` (GDAL ``Polygonize``);
areas are measured in the grid's own CRS when it is projected, otherwise on
the equal-area EPSG:6933 projection (matching the upstream project); and the
GeoJSON is emitted in WGS84 so it is portable.

Only the pure-Python pieces are imported at module load, so the shoelace area
maths is unit testable without GDAL; the shim is imported lazily where a
polygonisation or reprojection is actually performed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from .constants import SEVERITY_CODE_TO_COLOR, SEVERITY_CODE_TO_LABEL
from .raster import float_to_coded

if TYPE_CHECKING:
    from .raster import RasterGrid

#: The label/colour used for the single-class burnt-area polygons.
_BURNT_LABEL = "Burnt"
_BURNT_COLOR = "#d95f0e"

#: Equal-area CRS used to measure polygon areas when the grid is geographic.
_EQUAL_AREA_CRS = "EPSG:6933"

#: The output GeoJSON CRS (the format's default and only portable choice).
_GEOJSON_CRS = "EPSG:4326"


@dataclass(slots=True)
class VectorFeature:
    """One polygonised region with its class metadata and area.

    Attributes:
        geometry: The GeoJSON geometry, in the analysis grid's CRS.
        class_code: The raster class code the region came from.
        label: The human-readable class label.
        color: The class display colour (hex).
        area_km2: The region's area in square kilometres (equal-area).
    """

    geometry: dict[str, Any]
    class_code: int
    label: str
    color: str
    area_km2: float


def _ring_area(ring: list[list[float]]) -> float:
    """Return the absolute shoelace area of a single coordinate ring."""
    total = 0.0
    count = len(ring)
    for index in range(count):
        x0, y0 = ring[index][0], ring[index][1]
        x1, y1 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        total += x0 * y1 - x1 * y0
    return abs(total) * 0.5


def _polygon_area(geometry: dict[str, Any]) -> float:
    """Return the planar area of a (Multi)Polygon: exterior minus holes."""
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    polygons: list[Any]
    if geom_type == "Polygon":
        polygons = [coordinates]
    elif geom_type == "MultiPolygon":
        polygons = list(coordinates)
    else:
        return 0.0

    area = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        area += _ring_area(polygon[0])
        for hole in polygon[1:]:
            area -= _ring_area(hole)
    return area


def _projected_area_km2(geometry: dict[str, Any], grid: RasterGrid) -> float:
    """Return a geometry's area in km^2, reprojecting to equal-area if needed."""
    crs = grid.crs
    if crs is not None and crs.is_projected:
        return _polygon_area(geometry) / 1_000_000.0

    from ._rio.warp import transform_geom

    reprojected = transform_geom(
        crs if crs is not None else _GEOJSON_CRS, _EQUAL_AREA_CRS, geometry
    )
    return _polygon_area(reprojected) / 1_000_000.0


def _polygonise(
    coded: NDArray[np.uint8],
    mask: NDArray[np.bool_],
    grid: RasterGrid,
) -> list[tuple[dict[str, Any], int]]:
    """Return ``(geometry, value)`` pairs for the masked cells of ``coded``."""
    from ._rio.features import shapes

    results: list[tuple[dict[str, Any], int]] = []
    for geometry, value in shapes(coded, mask=mask, transform=grid.transform):
        results.append((geometry, int(value)))
    return results


def burnt_polygons(burnt_mask: NDArray[np.floating], grid: RasterGrid) -> list[VectorFeature]:
    """Polygonise a burnt mask into ``Burnt`` features.

    Args:
        burnt_mask: The cleaned burnt mask (``1.0``/``0.0``, ``nan`` nodata).
        grid: The analysis grid the mask lives on.

    Returns:
        One :class:`VectorFeature` per burnt region.
    """
    coded = float_to_coded(burnt_mask, fill=0)
    mask = coded.astype(bool)
    features: list[VectorFeature] = []
    for geometry, value in _polygonise(coded, mask, grid):
        if value != 1:
            continue
        features.append(
            VectorFeature(
                geometry=geometry,
                class_code=1,
                label=_BURNT_LABEL,
                color=_BURNT_COLOR,
                area_km2=_projected_area_km2(geometry, grid),
            )
        )
    return features


def severity_polygons(severity_class: NDArray[np.integer], grid: RasterGrid) -> list[VectorFeature]:
    """Polygonise a dNBR severity raster into per-class features.

    Args:
        severity_class: The severity class raster (``0`` nodata, ``1..7``).
        grid: The analysis grid the raster lives on.

    Returns:
        One :class:`VectorFeature` per severity region (nodata excluded).
    """
    coded = np.asarray(severity_class, dtype=np.uint8)
    mask = coded > 0
    features: list[VectorFeature] = []
    for geometry, value in _polygonise(coded, mask, grid):
        if value not in SEVERITY_CODE_TO_LABEL:
            continue
        features.append(
            VectorFeature(
                geometry=geometry,
                class_code=value,
                label=SEVERITY_CODE_TO_LABEL[value],
                color=SEVERITY_CODE_TO_COLOR[value],
                area_km2=_projected_area_km2(geometry, grid),
            )
        )
    return features


def filter_min_area(features: list[VectorFeature], min_area_km2: float) -> list[VectorFeature]:
    """Return only the features at or above ``min_area_km2`` (``0`` keeps all)."""
    if min_area_km2 <= 0:
        return list(features)
    return [feature for feature in features if feature.area_km2 >= min_area_km2]


def write_geojson(features: list[VectorFeature], path: str | Path, grid: RasterGrid) -> Path:
    """Write ``features`` to a WGS84 GeoJSON ``FeatureCollection``.

    Geometries are reprojected from the grid CRS to EPSG:4326 so the output is
    valid, portable GeoJSON; the recorded ``area_km2`` is unchanged (it was
    measured on an equal-area projection).

    Args:
        features: The features to write.
        path: The output ``.geojson`` path.
        grid: The analysis grid (source CRS for the geometries).

    Returns:
        The output path.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    crs = grid.crs
    reproject = crs is not None and crs.to_string() != _GEOJSON_CRS
    if reproject:
        from ._rio.warp import transform_geom

    collection: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    for feature in features:
        geometry = feature.geometry
        if reproject:
            geometry = transform_geom(crs, _GEOJSON_CRS, geometry)
        collection["features"].append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "class_code": feature.class_code,
                    "label": feature.label,
                    "color": feature.color,
                    "area_km2": feature.area_km2,
                },
            }
        )
    out.write_text(json.dumps(collection), encoding="utf-8")
    return out
