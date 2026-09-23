"""Run configuration for the burnt-area pipeline, free of GDAL and ``qgis``.

:class:`BurntAreaConfig` bundles everything a STAC-driven run needs -- the
catalogue and collection, the area of interest, the fire date and the
baseline/post-fire windows derived from it, plus the quality and cleanup
thresholds. It mirrors the upstream ``burnt_area_mapping`` project so runs
are reproducible, but drops the fields that only made sense for the original
CLI (matplotlib PNGs, dask, folium QA maps and boundary clipping, which are
deferred in the plugin). The dialog and the STAC algorithm build one of these
either directly from user parameters or from a YAML file via
:func:`load_config`.

Date handling is deliberately identical to upstream: the baseline window is
``baseline_length_months * 30.4375`` days ending the day before the fire, and
the post-fire window runs from the day after the fire for ``post_window_days``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

_VALID_STRATEGIES = frozenset({"best_single", "median"})


@dataclass(slots=True)
class BBox:
    """A geographic bounding box in longitude/latitude degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return ``(min_lon, min_lat, max_lon, max_lat)``."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


@dataclass(slots=True)
class BurntAreaConfig:
    """Parameters for one burnt-area run.

    Attributes:
        project_name: A human-readable label for the run.
        catalog_url: The STAC API root (e.g. Earth Search v1).
        collection: The imagery collection id (e.g. ``sentinel-2-l2a``).
        area_name: A short slug-able name for the study area.
        fire_date: The fire date as ``"YYYY-MM-DD"``, ``date`` or ``datetime``.
        baseline_length_months: Months of pre-fire imagery for the baseline.
        post_window_days: Days after the fire to search for post-fire imagery.
        threshold: The dNBR value above which a pixel counts as burnt.
        max_cloud_cover: The maximum scene cloud cover to search for (percent).
        min_valid_fraction: The minimum clear-pixel fraction to keep a scene.
        output_crs: The output CRS as an authority string (e.g. ``EPSG:3577``).
        resolution_m: The output pixel size in metres.
        bbox: The area of interest as a lon/lat bounding box.
        aoi_path: An optional vector AOI file (an alternative to ``bbox``).
        aoi_dissolve: Whether to dissolve a multi-feature AOI before use.
        postfire_strategy: ``"best_single"`` or ``"median"``.
        min_burnt_patch_pixels: Speckle-removal threshold for the burnt mask.
        fill_holes_pixels: Hole-fill threshold for the burnt mask.
        export_polygons: Whether to export cleaned burnt-area polygons.
        export_raw_polygons: Whether to export un-cleaned burnt-area polygons.
        vector_min_area_km2: Drop exported polygons smaller than this.
        vector_simplify_tolerance_m: Simplify tolerance for exported polygons.
        max_scenes_per_window: Optional cap on the number of scenes downloaded
            per search window -- applied to the baseline and post-fire searches
            independently. When set, the clearest (least-cloudy) scenes are
            kept. ``None`` (or ``0`` in a YAML config) means no limit.
        config_path: The YAML file the config was loaded from, if any (used to
            resolve relative ``aoi_path`` values).
    """

    project_name: str
    catalog_url: str
    collection: str
    area_name: str
    fire_date: str | date | datetime
    baseline_length_months: int
    post_window_days: int
    threshold: float
    max_cloud_cover: float
    min_valid_fraction: float
    output_crs: str
    resolution_m: int
    bbox: BBox | None = None
    aoi_path: str | Path | None = None
    aoi_dissolve: bool = True
    postfire_strategy: str = "best_single"
    min_burnt_patch_pixels: int = 0
    fill_holes_pixels: int = 0
    export_polygons: bool = False
    export_raw_polygons: bool = True
    vector_min_area_km2: float = 0.0
    vector_simplify_tolerance_m: float = 0.0
    max_scenes_per_window: int | None = None
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        for field_name in ("project_name", "catalog_url", "collection", "area_name", "output_crs"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"'{field_name}' must be a non-empty string.")

        if self.bbox is None and not self.aoi_path:
            raise ValueError("Either 'bbox' or 'aoi_path' must be provided.")
        if self.postfire_strategy not in _VALID_STRATEGIES:
            raise ValueError("'postfire_strategy' must be 'best_single' or 'median'.")
        if self.baseline_length_months <= 0:
            raise ValueError("'baseline_length_months' must be greater than 0.")
        if self.post_window_days <= 0:
            raise ValueError("'post_window_days' must be greater than 0.")
        if not -1.0 <= self.threshold <= 2.0:
            raise ValueError("'threshold' must be between -1.0 and 2.0.")
        if not 0.0 <= self.max_cloud_cover <= 100.0:
            raise ValueError("'max_cloud_cover' must be between 0 and 100.")
        if not 0.0 <= self.min_valid_fraction <= 1.0:
            raise ValueError("'min_valid_fraction' must be between 0 and 1.")
        if self.resolution_m <= 0:
            raise ValueError("'resolution_m' must be greater than 0.")
        if self.min_burnt_patch_pixels < 0:
            raise ValueError("'min_burnt_patch_pixels' must be 0 or greater.")
        if self.fill_holes_pixels < 0:
            raise ValueError("'fill_holes_pixels' must be 0 or greater.")
        if self.vector_min_area_km2 < 0:
            raise ValueError("'vector_min_area_km2' must be 0 or greater.")
        if self.vector_simplify_tolerance_m < 0:
            raise ValueError("'vector_simplify_tolerance_m' must be 0 or greater.")
        if self.max_scenes_per_window is not None and self.max_scenes_per_window < 1:
            raise ValueError("'max_scenes_per_window' must be 1 or greater (or None for no limit).")
        if self.aoi_path and not self.resolved_aoi_path.exists():
            raise ValueError(f"AOI file '{self.resolved_aoi_path}' does not exist.")

    @property
    def fire_datetime(self) -> datetime:
        """Return the fire date as a :class:`datetime` at midnight."""
        if isinstance(self.fire_date, datetime):
            return self.fire_date
        if isinstance(self.fire_date, date):
            return datetime.combine(self.fire_date, datetime.min.time())
        return datetime.strptime(self.fire_date, "%Y-%m-%d")

    @property
    def fire_date_obj(self) -> date:
        """Return the fire date as a plain :class:`date`."""
        return self.fire_datetime.date()

    @property
    def fire_date_str(self) -> str:
        """Return the fire date as ``"YYYY-MM-DD"``."""
        return self.fire_datetime.strftime("%Y-%m-%d")

    @property
    def baseline_delta(self) -> timedelta:
        """Return the baseline window length as a :class:`timedelta`."""
        return timedelta(days=round(self.baseline_length_months * 30.4375))

    @property
    def baseline_range(self) -> tuple[str, str]:
        """Return the ``(start, end)`` baseline date range (end = fire - 1 day)."""
        start = self.fire_datetime - self.baseline_delta
        end = self.fire_datetime - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @property
    def postfire_range(self) -> tuple[str, str]:
        """Return the ``(start, end)`` post-fire date range (start = fire + 1 day)."""
        start = self.fire_datetime + timedelta(days=1)
        end = self.fire_datetime + timedelta(days=self.post_window_days)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @property
    def resolved_aoi_path(self) -> Path:
        """Return the AOI path, resolved relative to the config file if needed."""
        if not self.aoi_path:
            raise ValueError("No AOI path is configured.")
        return self._resolve_path(self.aoi_path)

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        if self.config_path is not None:
            return (self.config_path.parent / path).resolve()
        return path.resolve()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping for the run manifest."""
        return {
            "project_name": self.project_name,
            "catalog_url": self.catalog_url,
            "collection": self.collection,
            "area_name": self.area_name,
            "fire_date": self.fire_date_str,
            "baseline_length_months": self.baseline_length_months,
            "post_window_days": self.post_window_days,
            "threshold": self.threshold,
            "max_cloud_cover": self.max_cloud_cover,
            "min_valid_fraction": self.min_valid_fraction,
            "output_crs": self.output_crs,
            "resolution_m": self.resolution_m,
            "bbox": (
                None
                if self.bbox is None
                else {
                    "min_lon": self.bbox.min_lon,
                    "min_lat": self.bbox.min_lat,
                    "max_lon": self.bbox.max_lon,
                    "max_lat": self.bbox.max_lat,
                }
            ),
            "aoi_path": None if not self.aoi_path else str(self.resolved_aoi_path),
            "aoi_dissolve": self.aoi_dissolve,
            "postfire_strategy": self.postfire_strategy,
            "min_burnt_patch_pixels": self.min_burnt_patch_pixels,
            "fill_holes_pixels": self.fill_holes_pixels,
            "export_polygons": self.export_polygons,
            "export_raw_polygons": self.export_raw_polygons,
            "vector_min_area_km2": self.vector_min_area_km2,
            "vector_simplify_tolerance_m": self.vector_simplify_tolerance_m,
            "max_scenes_per_window": self.max_scenes_per_window,
        }


def load_config(path: str | Path) -> BurntAreaConfig:
    """Load a :class:`BurntAreaConfig` from a YAML mapping file.

    Args:
        path: Path to a YAML config file.

    Returns:
        The parsed and validated configuration.

    Raises:
        ValueError: If the file does not contain a YAML mapping.
    """
    config_path = Path(path).resolve()
    with open(config_path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file '{config_path}' did not contain a YAML mapping.")

    bbox_raw = raw.get("bbox")
    bbox = BBox(**bbox_raw) if bbox_raw else None
    return BurntAreaConfig(
        project_name=raw["project_name"],
        catalog_url=raw["catalog_url"],
        collection=raw["collection"],
        area_name=raw["area_name"],
        fire_date=raw["fire_date"],
        baseline_length_months=int(raw["baseline_length_months"]),
        post_window_days=int(raw["post_window_days"]),
        threshold=float(raw["threshold"]),
        max_cloud_cover=float(raw["max_cloud_cover"]),
        min_valid_fraction=float(raw["min_valid_fraction"]),
        output_crs=raw["output_crs"],
        resolution_m=int(raw["resolution_m"]),
        bbox=bbox,
        aoi_path=raw.get("aoi_path"),
        aoi_dissolve=bool(raw.get("aoi_dissolve", True)),
        postfire_strategy=raw.get("postfire_strategy", "best_single"),
        min_burnt_patch_pixels=int(raw.get("min_burnt_patch_pixels", 0)),
        fill_holes_pixels=int(raw.get("fill_holes_pixels", 0)),
        export_polygons=bool(raw.get("export_polygons", False)),
        export_raw_polygons=bool(raw.get("export_raw_polygons", True)),
        vector_min_area_km2=float(raw.get("vector_min_area_km2", 0.0)),
        vector_simplify_tolerance_m=float(raw.get("vector_simplify_tolerance_m", 0.0)),
        max_scenes_per_window=(
            None
            if raw.get("max_scenes_per_window") in (None, 0)
            else int(raw["max_scenes_per_window"])
        ),
        config_path=config_path,
    )
