"""Processing algorithm: compute dNBR burnt area from two user-supplied rasters."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from burnt_area_toolbox.algorithms._ba_style import (
    apply_burnt_mask_style,
    apply_dnbr_style,
    apply_severity_style,
)
from burnt_area_toolbox.algorithms._config_build import config_for_rasters
from burnt_area_toolbox.algorithms._help import HELP_URL
from burnt_area_toolbox.algorithms._outputs import write_run_outputs
from burnt_area_toolbox.algorithms._qgis_io import (
    crs_to_epsg_string,
    export_raster,
    raster_source_path,
)
from burnt_area_toolbox.bacore import pipeline, raster

_RESAMPLING = ["Bilinear", "Nearest neighbour"]


class DnbrFromRastersAlgorithm(QgsProcessingAlgorithm):
    """Compute dNBR, burn severity and burnt area from pre/post-fire rasters."""

    PRE = "PRE"
    POST = "POST"
    ALREADY_NBR = "ALREADY_NBR"
    NIR_BAND = "NIR_BAND"
    SWIR_BAND = "SWIR_BAND"
    RESAMPLING = "RESAMPLING"
    THRESHOLD = "THRESHOLD"
    MIN_PATCH_PIXELS = "MIN_PATCH_PIXELS"
    FILL_HOLES_PIXELS = "FILL_HOLES_PIXELS"
    AREA_NAME = "AREA_NAME"
    FIRE_DATE = "FIRE_DATE"
    EXPORT_POLYGONS = "EXPORT_POLYGONS"
    VECTOR_MIN_AREA_KM2 = "VECTOR_MIN_AREA_KM2"
    OUTPUT_SEVERITY = "OUTPUT_SEVERITY"
    OUTPUT_DNBR = "OUTPUT_DNBR"
    OUTPUT_BURNT_MASK = "OUTPUT_BURNT_MASK"
    OUTPUT_DIR = "OUTPUT_DIR"

    # -- identity ---------------------------------------------------------
    def name(self) -> str:
        return "dnbrfromrasters"

    def displayName(self) -> str:
        return "Burnt area from rasters (dNBR)"

    def group(self) -> str:
        return "Burnt area mapping"

    def groupId(self) -> str:
        return "burntarea"

    def createInstance(self) -> DnbrFromRastersAlgorithm:
        return DnbrFromRastersAlgorithm()

    def flags(self) -> Any:
        # Safe to run on a Processing background thread: it only reads/writes
        # rasters through GDAL and runs pure-NumPy maths, never touching the map
        # canvas or ``iface`` from the worker thread. Threading stays ENABLED so
        # the progress bar is live and the run can be cancelled.
        return super().flags()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Computes the differenced Normalised Burn Ratio (dNBR), a seven-class "
            "burn-severity raster and a cleaned burnt-area mask from a pre-fire "
            "and a post-fire raster you already have.\n\n"
            "By default each raster's Near-Infrared and shortwave-infrared "
            "(SWIR-2, ~2.2 µm) bands are read to compute NBR = (NIR - SWIR) / "
            "(NIR + SWIR); dNBR = pre-fire NBR - post-fire NBR. Tick 'Rasters "
            "already hold NBR' when your inputs are single-band NBR images, and "
            "the NIR band index is then read straight through.\n\n"
            "Both rasters are resampled onto the post-fire raster's grid, so they "
            "may differ in extent, resolution or CRS. A pixel counts as burnt "
            "where dNBR exceeds the threshold (0.10 detects low-severity burns "
            "and above; raise it toward 0.27 to restrict to moderate/high "
            "severity). Optional speckle removal and hole filling clean the mask.\n\n"
            "The maximum-detail outputs (all five GeoTIFFs, a summary CSV, "
            "optional polygons and a run manifest) are written when you set an "
            "output folder; otherwise only the rasters you name are produced."
        )

    # -- parameters -------------------------------------------------------
    def initAlgorithm(self, config=None) -> None:
        self.addParameter(QgsProcessingParameterRasterLayer(self.PRE, "Pre-fire raster"))
        self.addParameter(QgsProcessingParameterRasterLayer(self.POST, "Post-fire raster"))
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ALREADY_NBR,
                "Rasters already hold NBR (single band)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.NIR_BAND,
                "NIR band index (or the NBR band when 'already NBR' is ticked)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SWIR_BAND,
                "SWIR-2 band index (ignored when 'already NBR' is ticked)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=2,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.RESAMPLING, "Resampling", options=_RESAMPLING, defaultValue=0
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
            QgsProcessingParameterString(
                self.FIRE_DATE,
                "Fire date (YYYY-MM-DD, optional; recorded in the manifest)",
                optional=True,
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
        pre_layer = self.parameterAsRasterLayer(parameters, self.PRE, context)
        post_layer = self.parameterAsRasterLayer(parameters, self.POST, context)
        if pre_layer is None or post_layer is None:
            raise QgsProcessingException("Both a pre-fire and a post-fire raster are required.")

        already_nbr = self.parameterAsBool(parameters, self.ALREADY_NBR, context)
        nir_band = self.parameterAsInt(parameters, self.NIR_BAND, context)
        swir_band = self.parameterAsInt(parameters, self.SWIR_BAND, context)
        resampling = (
            "nearest"
            if self.parameterAsEnum(parameters, self.RESAMPLING, context) == 1
            else "bilinear"
        )
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        min_patch = self.parameterAsInt(parameters, self.MIN_PATCH_PIXELS, context)
        fill_holes = self.parameterAsInt(parameters, self.FILL_HOLES_PIXELS, context)
        area_name = self.parameterAsString(parameters, self.AREA_NAME, context).strip()
        fire_date = self.parameterAsString(parameters, self.FIRE_DATE, context).strip()
        export_polygons = self.parameterAsBool(parameters, self.EXPORT_POLYGONS, context)
        vector_min_area = self.parameterAsDouble(parameters, self.VECTOR_MIN_AREA_KM2, context)

        pre_source = raster_source_path(pre_layer, feedback)
        post_source = raster_source_path(post_layer, feedback)

        feedback.pushInfo("Reading the post-fire raster grid…")
        grid = raster.open_grid(post_source)
        output_crs = crs_to_epsg_string(post_layer.crs()) or "EPSG:4326"

        folder = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        temp_output_dir: Path | None = None
        if folder:
            output_dir = Path(folder)
        elif export_polygons:
            raise QgsProcessingException(
                "Set an output folder to keep the exported polygons, or untick that option."
            )
        else:
            # No folder to keep: write into a scratch directory we delete once
            # the requested rasters have been copied to their destinations.
            output_dir = Path(tempfile.mkdtemp(prefix="burntarea_"))
            temp_output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            config = config_for_rasters(
                grid,
                output_crs=output_crs,
                area_name=area_name,
                fire_date=fire_date or None,
                threshold=threshold,
                min_burnt_patch_pixels=min_patch,
                fill_holes_pixels=fill_holes,
                export_polygons=export_polygons,
                vector_min_area_km2=vector_min_area,
            )

            feedback.setProgress(10)
            if feedback.isCanceled():
                return {}
            feedback.pushInfo("Computing NBR, dNBR and burn severity…")
            try:
                result = pipeline.run_from_rasters(
                    config,
                    grid,
                    pre_source=pre_source,
                    post_source=post_source,
                    nir_band=nir_band,
                    swir_band=None if already_nbr else swir_band,
                    resampling=resampling,
                )
            except (ValueError, FileNotFoundError, KeyError, TypeError) as error:
                raise QgsProcessingException(str(error)) from error
            feedback.setProgress(70)
            if feedback.isCanceled():
                return {}

            feedback.pushInfo(
                f"Burnt area: {result.burnt_area_km2:.3f} km² of "
                f"{result.valid_area_km2:.3f} km² valid."
            )
            # Only write the full result set when the user set a folder to keep;
            # otherwise write just the three exportable rasters.
            outputs = write_run_outputs(config, result, output_dir, full_set=bool(folder))
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

            # Remember the destinations so postProcessAlgorithm can style each
            # layer once QGIS has loaded it into the project.
            self._style_targets = [
                (severity_dest, "severity"),
                (dnbr_dest, "dnbr"),
                (burnt_dest, "burnt_mask"),
            ]
            self._results = {
                self.OUTPUT_SEVERITY: severity_dest,
                self.OUTPUT_DNBR: dnbr_dest,
                self.OUTPUT_BURNT_MASK: burnt_dest,
                self.OUTPUT_DIR: str(output_dir) if folder else "",
            }
            return self._results
        finally:
            if temp_output_dir is not None:
                shutil.rmtree(temp_output_dir, ignore_errors=True)

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
