"""Shared constants for the burnt-area compute core.

Deliberately free of any ``qgis``/``osgeo`` import so the tables can be
reused for styling, exported to run manifests and unit-tested in plain
Python. Everything here mirrors the original ``burnt_area_mapping`` project
so results are identical.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class SeverityClass(NamedTuple):
    """One dNBR severity class: its raster code, label and display colour."""

    code: int
    label: str
    color: str


#: The seven dNBR severity classes in ascending order. ``code`` is the raster
#: pixel value the classifier writes; ``0`` is reserved for nodata (outside
#: the valid area) and is not listed here. Labels and colours follow the
#: USGS/UN-SPIDER dNBR convention used by the upstream project.
SEVERITY_CLASSES: Final[tuple[SeverityClass, ...]] = (
    SeverityClass(1, "Enhanced regrowth, high (post-fire)", "#7A8F2A"),
    SeverityClass(2, "Enhanced regrowth, low (post-fire)", "#C9DD4D"),
    SeverityClass(3, "Unburned", "#19D34A"),
    SeverityClass(4, "Low Severity", "#FFF200"),
    SeverityClass(5, "Moderate-low Severity", "#FFB347"),
    SeverityClass(6, "Moderate-high Severity", "#FF6A00"),
    SeverityClass(7, "High Severity", "#8A1BE2"),
)

#: Map of severity code -> human-readable label.
SEVERITY_CODE_TO_LABEL: Final[dict[int, str]] = {cls.code: cls.label for cls in SEVERITY_CLASSES}

#: Map of severity code -> hex colour.
SEVERITY_CODE_TO_COLOR: Final[dict[int, str]] = {cls.code: cls.color for cls in SEVERITY_CLASSES}

#: Upper-exclusive dNBR break values for the severity classes, in code order
#: ``1..7``. A pixel is class ``k`` when ``delta_nbr < SEVERITY_BREAKS[k-1]``
#: (checked from the lowest class up); class ``7`` is the open-ended tail.
#: These are the exact thresholds the classifier applies:
#: ``<-0.250 -> 1``, ``[-0.250,-0.100) -> 2``, ``[-0.100,0.100) -> 3``,
#: ``[0.100,0.270) -> 4``, ``[0.270,0.440) -> 5``, ``[0.440,0.660) -> 6``,
#: ``>=0.660 -> 7``.
SEVERITY_BREAKS: Final[tuple[float, ...]] = (
    -0.250,
    -0.100,
    0.100,
    0.270,
    0.440,
    0.660,
)

#: Sentinel-2 Scene Classification Layer (SCL) values treated as clear
#: (valid) observations: 2 dark-area pixels, 4 vegetation, 5 bare soil,
#: 6 water, 7 unclassified, 11 snow/ice. Cloud, cloud-shadow, cirrus and
#: saturated/defective pixels are therefore excluded.
CLEAR_SCL_CLASSES: Final[frozenset[int]] = frozenset({2, 4, 5, 6, 7, 11})

#: Common Sentinel-2 asset aliases across STAC catalogues, mapping each
#: logical band the core needs to the candidate asset keys to look for, most
#: preferred first. Mirrors the upstream project's discovery order.
BAND_CANDIDATES: Final[dict[str, tuple[str, ...]]] = {
    "blue": ("blue", "B02", "nbart_blue"),
    "green": ("green", "B03", "nbart_green"),
    "red": ("red", "B04", "nbart_red"),
    "nir": ("nir", "nir08", "B08", "nbart_nir_1"),
    "swir22": ("swir22", "B12", "nbart_swir_3"),
    "scl": ("scl", "SCL"),
}

#: The logical bands required to compute NBR (the two-band minimum) and the
#: full visual/quality set the STAC loader fetches.
NBR_BANDS: Final[tuple[str, ...]] = ("nir", "swir22")
VISUAL_BANDS: Final[tuple[str, ...]] = ("blue", "green", "red", "nir", "swir22")
