"""Processing algorithm: fetch Sentinel-2 imagery from a STAC catalogue and map burnt area."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
)

from burnt_area_toolbox.algorithms._ba_style import (
    apply_burnt_mask_style,
    apply_dnbr_style,
    apply_severity_style,
)
from burnt_area_toolbox.algorithms._help import HELP_URL
from burnt_area_toolbox.algorithms._outputs import write_run_outputs
from burnt_area_toolbox.algorithms._progress import (
    make_cancel,
    make_message,
    make_scene_progress,
)
from burnt_area_toolbox.algorithms._qgis_io import (
    crs_to_epsg_string,
    export_raster,
)
from burnt_area_toolbox.bacore import pipeline
from burnt_area_toolbox.bacore.config import BBox, BurntAreaConfig
from burnt_area_toolbox.bacore.scenes import BurntAreaError

#: Default STAC endpoint: Earth Search v1 (Element 84), no authentication.
_DEFAULT_CATALOG = "https://earth-search.aws.element84.com/v1"
_DEFAULT_COLLECTION = "sentinel-2-l2a"
_STRATEGIES = ["Best single scene", "Median composite"]


class DnbrFromStacAlgorithm(QgsProcessingAlgorithm):
    """Search a STAC catalogue for Sentinel-2 imagery and map dNBR burn severity."""

    EXTENT = "EXTENT"
    FIRE_DATE = "FIRE_DATE"
    BASELINE_MONTHS = "BASELINE_MONTHS"
    POST_WINDOW_DAYS = "POST_WINDOW_DAYS"
    MAX_CLOUD = "MAX_CLOUD"
    MAX_SCENES = "MAX_SCENES"
    MIN_VALID_FRACTION = "MIN_VALID_FRACTION"
    POSTFIRE_STRATEGY = "POSTFIRE_STRATEGY"
    CATALOG_URL = "CATALOG_URL"
    COLLECTION = "COLLECTION"
    OUTPUT_CRS = "OUTPUT_CRS"
    RESOLUTION_M = "RESOLUTION_M"
    THRESHOLD = "THRESHOLD"
    MIN_PATCH_PIXELS = "MIN_PATCH_PIXELS"
    FILL_HOLES_PIXELS = "FILL_HOLES_PIXELS"
    AREA_NAME = "AREA_NAME"
    EXPORT_POLYGONS = "EXPORT_POLYGONS"
    VECTOR_MIN_AREA_KM2 = "VECTOR_MIN_AREA_KM2"
    OUTPUT_SEVERITY = "OUTPUT_SEVERITY"
    OUTPUT_DNBR = "OUTPUT_DNBR"
    OUTPUT_BURNT_MASK = "OUTPUT_BURNT_MASK"
    OUTPUT_DIR = "OUTPUT_DIR"

    # -- identity ---------------------------------------------------------
    def name(self) -> str:
        return "dnbrfromstac"

    def displayName(self) -> str:
        return "Burnt area from STAC imagery (dNBR)"

    def group(self) -> str:
        return "Burnt area mapping"

    def groupId(self) -> str:
        return "burntarea"

    def createInstance(self) -> DnbrFromStacAlgorithm:
        return DnbrFromStacAlgorithm()

    def flags(self) -> Any:
        # Downloads Sentinel-2 COGs and runs pure-NumPy maths off the GUI
        # thread; touches neither the canvas nor ``iface`` from the worker.
        # Threading stays ENABLED so the download is cancellable and the
        # per-scene progress bar stays live.
        return super().flags()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Searches a public STAC catalogue (Earth Search / Sentinel-2 L2A by "
            "default) for cloud-free imagery before and after a fire, downloads "
            "the NIR and SWIR bands, and maps the differenced Normalised Burn "
            "Ratio (dNBR), a seven-class burn-severity raster and a cleaned "
            "burnt-area mask over the extent you choose.\n\n"
            "A baseline is median-composited from imagery in the months before "
            "the fire; the post-fire image is either the single clearest scene "
            "in the days after the fire or a median composite. No login or API "
            "key is required for the default catalogue, but an internet "
            "connection is — larger extents and longer windows download more. "
            "To bound the download, cap the scenes per window: the clearest "
            "(least-cloudy) scenes are kept and progress is reported as each "
            "scene loads.\n\n"
            "A pixel counts as burnt where dNBR exceeds the threshold (0.10 "
            "detects low-severity burns and above). Set an output folder to keep "
            "the full result set (all five GeoTIFFs, a summary CSV, optional "
            "polygons and a run manifest)."
        )

    # -- parameters -------------------------------------------------------
    def initAlgorithm(self, config=None) -> None:
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, "Area of interest (extent)"))
        self.addParameter(
            QgsProcessingParameterString(self.FIRE_DATE, "Fire date (YYYY-MM-DD)", defaultValue="")
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BASELINE_MONTHS,
                "Baseline length before the fire (months)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=12,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.POST_WINDOW_DAYS,
                "Post-fire search window (days after the fire)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_CLOUD,
                "Maximum scene cloud cover (%)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=40.0,
                minValue=0.0,
                maxValue=100.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_SCENES,
                "Max scenes to download per window (baseline / post-fire each; 0 = no limit)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_VALID_FRACTION,
                "Minimum clear-pixel fraction to keep a scene (0-1)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                minValue=0.0,
                maxValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.POSTFIRE_STRATEGY,
                "Post-fire image selection",
                options=_STRATEGIES,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(self.OUTPUT_CRS, "Output CRS", defaultValue="EPSG:3577")
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RESOLUTION_M,
                "Output pixel size (metres)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=20,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD,
                "Burnt dNBR threshold",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.10,
                minValue=-1.0,
                maxValue=2.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_PATCH_PIXELS,
                "Minimum burnt patch size (pixels; 0 = off)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FILL_HOLES_PIXELS,
                "Fill burnt-mask holes up to (pixels; 0 = off)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.AREA_NAME, "Area name (used in filenames)", defaultValue="burnt_area"
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.EXPORT_POLYGONS,
                "Also export burnt / severity polygons (needs an output folder)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.VECTOR_MIN_AREA_KM2,
                "Minimum exported polygon area (km²; 0 = keep all)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                minValue=0.0,
            )
        )
        # -- advanced: catalogue endpoint ---------------------------------
        catalog = QgsProcessingParameterString(
            self.CATALOG_URL, "STAC catalogue URL", defaultValue=_DEFAULT_CATALOG
        )
        catalog.setFlags(catalog.flags() | QgsProcessingParameterString.FlagAdvanced)
        self.addParameter(catalog)
        collection = QgsProcessingParameterString(
            self.COLLECTION, "STAC collection id", defaultValue=_DEFAULT_COLLECTION
        )
        collection.setFlags(collection.flags() | QgsProcessingParameterString.FlagAdvanced)
        self.addParameter(collection)

        # -- outputs ------------------------------------------------------
        self.addParameter(
            QgsProcessingParameterRasterDestination(self.OUTPUT_SEVERITY, "Burn severity raster")
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_DNBR, "dNBR raster", optional=True, createByDefault=False
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_BURNT_MASK,
                "Burnt-area mask raster",
                optional=True,
                createByDefault=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_DIR,
                "Output folder for the full result set (all rasters, summary, polygons)",
                optional=True,
                createByDefault=False,
            )
        )

    # -- execution --------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback) -> dict[str, Any]:
        fire_date = self.parameterAsString(parameters, self.FIRE_DATE, context).strip()
        if not fire_date:
            raise QgsProcessingException("A fire date (YYYY-MM-DD) is required.")

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, wgs84)
        if rect.isEmpty():
            raise QgsProcessingException("An area-of-interest extent is required.")
        bbox = BBox(rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())

        output_crs = (
            crs_to_epsg_string(self.parameterAsCrs(parameters, self.OUTPUT_CRS, context))
            or "EPSG:3577"
        )
        strategy = (
            "median"
            if self.parameterAsEnum(parameters, self.POSTFIRE_STRATEGY, context) == 1
            else "best_single"
        )
        area_name = self.parameterAsString(parameters, self.AREA_NAME, context).strip()
        max_scenes_param = self.parameterAsInt(parameters, self.MAX_SCENES, context)
        max_scenes = max_scenes_param if max_scenes_param > 0 else None
        catalog_url = self.parameterAsString(parameters, self.CATALOG_URL, context).strip()
        collection = self.parameterAsString(parameters, self.COLLECTION, context).strip()
        export_polygons = self.parameterAsBool(parameters, self.EXPORT_POLYGONS, context)

        try:
            config = BurntAreaConfig(
                project_name="Burnt area (from STAC imagery)",
                catalog_url=catalog_url or _DEFAULT_CATALOG,
                collection=collection or _DEFAULT_COLLECTION,
                area_name=area_name or "burnt_area",
                fire_date=fire_date,
                baseline_length_months=self.parameterAsInt(
                    parameters, self.BASELINE_MONTHS, context
                ),
                post_window_days=self.parameterAsInt(parameters, self.POST_WINDOW_DAYS, context),
                threshold=self.parameterAsDouble(parameters, self.THRESHOLD, context),
                max_cloud_cover=self.parameterAsDouble(parameters, self.MAX_CLOUD, context),
                min_valid_fraction=self.parameterAsDouble(
                    parameters, self.MIN_VALID_FRACTION, context
                ),
                output_crs=output_crs,
                resolution_m=self.parameterAsInt(parameters, self.RESOLUTION_M, context),
                bbox=bbox,
                postfire_strategy=strategy,
                min_burnt_patch_pixels=self.parameterAsInt(
                    parameters, self.MIN_PATCH_PIXELS, context
                ),
                fill_holes_pixels=self.parameterAsInt(parameters, self.FILL_HOLES_PIXELS, context),
                export_polygons=export_polygons,
                export_raw_polygons=False,
                vector_min_area_km2=self.parameterAsDouble(
                    parameters, self.VECTOR_MIN_AREA_KM2, context
                ),
                max_scenes_per_window=max_scenes,
            )
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

        folder = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        if folder:
            output_dir = Path(folder)
        elif export_polygons:
            raise QgsProcessingException(
                "Set an output folder to keep the exported polygons, or untick that option."
            )
        else:
            output_dir = Path(tempfile.mkdtemp(prefix="burntarea_"))
        output_dir.mkdir(parents=True, exist_ok=True)

        feedback.pushInfo(
            f"Baseline {config.baseline_range[0]}..{config.baseline_range[1]}; "
            f"post-fire {config.postfire_range[0]}..{config.postfire_range[1]}."
        )
        try:
            result = pipeline.run_stac(
                config,
                message=make_message(feedback),
                scene_progress=make_scene_progress(feedback),
                is_cancelled=make_cancel(feedback),
            )
        except BurntAreaError as error:
            raise QgsProcessingException(str(error)) from error
        if result is None or feedback.isCanceled():
            feedback.pushInfo("Run cancelled.")
            return {}

        feedback.pushInfo(
            f"Burnt area: {result.burnt_area_km2:.3f} km² of {result.valid_area_km2:.3f} km² valid."
        )
        outputs = write_run_outputs(config, result, output_dir)
        for path in outputs.all_paths:
            feedback.pushInfo(f"  wrote {path}")

        severity_dest = export_raster(
            str(outputs.severity),
            self.parameterAsOutputLayer(parameters, self.OUTPUT_SEVERITY, context),
            feedback,
        )
        dnbr_dest = export_raster(
            str(outputs.dnbr),
            self.parameterAsOutputLayer(parameters, self.OUTPUT_DNBR, context),
            feedback,
        )
        burnt_dest = export_raster(
            str(outputs.burnt_mask),
            self.parameterAsOutputLayer(parameters, self.OUTPUT_BURNT_MASK, context),
            feedback,
        )
        feedback.setProgress(100)

        self._style_targets = [
            (severity_dest, "severity"),
            (dnbr_dest, "dnbr"),
            (burnt_dest, "burnt_mask"),
        ]
        self._results = {
            self.OUTPUT_SEVERITY: severity_dest,
            self.OUTPUT_DNBR: dnbr_dest,
            self.OUTPUT_BURNT_MASK: burnt_dest,
            self.OUTPUT_DIR: str(output_dir),
        }
        return self._results

    def postProcessAlgorithm(self, context, feedback) -> dict[str, Any]:
        """Style each loaded output raster with its palette / colour ramp."""
        from qgis.core import QgsProcessingUtils

        stylers = {
            "severity": apply_severity_style,
            "dnbr": apply_dnbr_style,
            "burnt_mask": apply_burnt_mask_style,
        }
        for dest, kind in getattr(self, "_style_targets", []):
            if not dest:
                continue
            layer = QgsProcessingUtils.mapLayerFromString(dest, context)
            if layer is not None and stylers[kind](layer):
                feedback.pushInfo(f"Styled the {kind} layer.")
        results: dict[str, Any] = getattr(self, "_results", {})
        return results
