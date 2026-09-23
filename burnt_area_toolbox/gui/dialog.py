"""A friendly dialog for the two common burnt-area cases.

Offers a mode switch between:

* **From my rasters** -- difference a pre-fire and a post-fire raster you
  already have, and
* **Fetch from STAC imagery** -- search a public Sentinel-2 catalogue over an
  extent and a fire date and download the imagery.

Either way it gathers a handful of friendly inputs, writes the outputs into a
folder you choose, and runs the matching Processing algorithm
(``burntarea:dnbrfromrasters`` / ``burntarea:dnbrfromstac``), then loads and
styles the severity, dNBR and burnt-mask rasters. Anything more involved
(polygon AOIs, alternate catalogues, non-default band indices) is available
through the Processing Toolbox.
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsMapLayerProxyModel,
    QgsProcessingFeedback,
    QgsReferencedRectangle,
    QgsSettings,
)
from qgis.gui import (
    QgsExtentWidget,
    QgsFileWidget,
    QgsMapLayerComboBox,
    QgsProjectionSelectionWidget,
)
from qgis.PyQt.QtCore import QDate
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

# Reuse the algorithms' own option lists so the dialog's combo ordering stays
# in lock-step with the enum indices the algorithms expect.
from burnt_area_toolbox.algorithms._ba_style import (
    apply_burnt_mask_style,
    apply_dnbr_style,
    apply_severity_style,
)
from burnt_area_toolbox.algorithms.dnbr_from_rasters import _RESAMPLING
from burnt_area_toolbox.algorithms.dnbr_from_stac import (
    _DEFAULT_CATALOG,
    _DEFAULT_COLLECTION,
    _STRATEGIES,
)

_MODES = ["From my rasters", "Fetch from STAC imagery"]

#: Prefix for this dialog's persisted choices in the QGIS user settings.
_SETTINGS_PREFIX = "BurntAreaToolbox/dialog/"


class _LogFeedback(QgsProcessingFeedback):
    """Routes Processing feedback into the dialog's log pane."""

    def __init__(self, sink) -> None:
        super().__init__()
        self._sink = sink

    def pushInfo(self, info: str) -> None:
        self._sink(info)

    def reportError(self, error: str, fatalError: bool = False) -> None:
        self._sink(f"ERROR: {error}")

    def pushCommandInfo(self, info: str) -> None:
        self._sink(info)


class BurntAreaDialog(QDialog):
    """Simple front-end that delegates to the burnt-area Processing algorithms."""

    def __init__(self, iface, parent=None) -> None:
        """Build the dialog widgets.

        Args:
            iface: The QGIS interface (used to add result layers).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Burnt Area Toolbox (dNBR)")
        self.setMinimumWidth(560)
        self._build_ui()

    # -- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Map burnt area and burn severity from the differenced Normalised "
            "Burn Ratio (dNBR). Either difference two rasters you already have, "
            "or fetch Sentinel-2 imagery from a public STAC catalogue over an "
            "extent and a fire date."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        mode_form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(_MODES)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_form.addRow("Mode:", self.mode_combo)
        layout.addLayout(mode_form)

        layout.addWidget(self._build_raster_group())
        layout.addWidget(self._build_stac_group())
        layout.addWidget(self._build_common_group())

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Run output appears here…")
        self.log.setMinimumHeight(130)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox()
        # Fully-scoped Qt enum names (``ButtonRole.AcceptRole``): PyQt6, which
        # QGIS 4 ships, dropped the unscoped aliases for Qt's own enums; the
        # scoped form also works on the PyQt5 that QGIS 3.x ships.
        self.run_button = self.buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.buttons.accepted.connect(self._on_run)
        self.buttons.rejected.connect(self.close)
        layout.addWidget(self.buttons)

        self._restore_settings()
        self._on_mode_changed(self.mode_combo.currentIndex())
        self._on_already_nbr_toggled(self.already_nbr_check.isChecked())

    def _build_raster_group(self) -> QGroupBox:
        group = QGroupBox("Pre / post-fire rasters")
        form = QFormLayout(group)

        self.pre_combo = QgsMapLayerComboBox()
        self.pre_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("Pre-fire raster:", self.pre_combo)

        self.post_combo = QgsMapLayerComboBox()
        self.post_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("Post-fire raster:", self.post_combo)

        self.already_nbr_check = QCheckBox("Rasters already hold NBR (single band)")
        self.already_nbr_check.toggled.connect(self._on_already_nbr_toggled)
        form.addRow("", self.already_nbr_check)

        self.nir_spin = QSpinBox()
        self.nir_spin.setRange(1, 999)
        self.nir_spin.setValue(1)
        form.addRow("NIR band index:", self.nir_spin)

        self.swir_spin = QSpinBox()
        self.swir_spin.setRange(1, 999)
        self.swir_spin.setValue(2)
        form.addRow("SWIR-2 band index:", self.swir_spin)

        self.resampling_combo = QComboBox()
        self.resampling_combo.addItems(_RESAMPLING)
        form.addRow("Resampling:", self.resampling_combo)

        self.raster_group = group
        return group

    def _build_stac_group(self) -> QGroupBox:
        group = QGroupBox("STAC imagery search")
        form = QFormLayout(group)

        self.extent_widget = QgsExtentWidget()
        if self.iface is not None:
            self.extent_widget.setMapCanvas(self.iface.mapCanvas())
        form.addRow("Area of interest (extent):", self.extent_widget)

        self.fire_date_edit = QDateEdit()
        self.fire_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.fire_date_edit.setCalendarPopup(True)
        self.fire_date_edit.setDate(QDate.currentDate())
        form.addRow("Fire date:", self.fire_date_edit)

        self.baseline_spin = QSpinBox()
        self.baseline_spin.setRange(1, 120)
        self.baseline_spin.setValue(12)
        form.addRow("Baseline length (months):", self.baseline_spin)

        self.post_window_spin = QSpinBox()
        self.post_window_spin.setRange(1, 3650)
        self.post_window_spin.setValue(60)
        form.addRow("Post-fire window (days):", self.post_window_spin)

        self.max_cloud_spin = QDoubleSpinBox()
        self.max_cloud_spin.setRange(0.0, 100.0)
        self.max_cloud_spin.setValue(40.0)
        form.addRow("Maximum cloud cover (%):", self.max_cloud_spin)

        self.max_scenes_spin = QSpinBox()
        self.max_scenes_spin.setRange(0, 1000)
        self.max_scenes_spin.setValue(0)
        self.max_scenes_spin.setToolTip(
            "Cap how many scenes are downloaded in each window (baseline and "
            "post-fire). The clearest scenes are kept. 0 = download every match."
        )
        form.addRow("Max scenes per window (0 = no limit):", self.max_scenes_spin)

        self.min_valid_spin = QDoubleSpinBox()
        self.min_valid_spin.setRange(0.0, 1.0)
        self.min_valid_spin.setSingleStep(0.05)
        self.min_valid_spin.setValue(0.0)
        form.addRow("Minimum clear-pixel fraction:", self.min_valid_spin)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(_STRATEGIES)
        form.addRow("Post-fire image:", self.strategy_combo)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:3577"))
        form.addRow("Output CRS:", self.crs_widget)

        self.resolution_spin = QSpinBox()
        self.resolution_spin.setRange(1, 1000)
        self.resolution_spin.setValue(20)
        form.addRow("Output pixel size (m):", self.resolution_spin)

        self.catalog_edit = QLineEdit(_DEFAULT_CATALOG)
        form.addRow("STAC catalogue URL:", self.catalog_edit)

        self.collection_edit = QLineEdit(_DEFAULT_COLLECTION)
        form.addRow("STAC collection id:", self.collection_edit)

        self.stac_group = group
        return group

    def _build_common_group(self) -> QGroupBox:
        group = QGroupBox("Analysis and output")
        form = QFormLayout(group)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(-1.0, 2.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(3)
        self.threshold_spin.setValue(0.10)
        form.addRow("Burnt dNBR threshold:", self.threshold_spin)

        self.min_patch_spin = QSpinBox()
        self.min_patch_spin.setRange(0, 1_000_000)
        self.min_patch_spin.setValue(0)
        form.addRow("Minimum burnt patch (pixels):", self.min_patch_spin)

        self.fill_holes_spin = QSpinBox()
        self.fill_holes_spin.setRange(0, 1_000_000)
        self.fill_holes_spin.setValue(0)
        form.addRow("Fill mask holes up to (pixels):", self.fill_holes_spin)

        self.area_name_edit = QLineEdit("burnt_area")
        form.addRow("Area name:", self.area_name_edit)

        self.export_polygons_check = QCheckBox("Also export burnt / severity polygons")
        form.addRow("", self.export_polygons_check)

        self.output_widget = QgsFileWidget()
        self.output_widget.setStorageMode(QgsFileWidget.GetDirectory)
        form.addRow("Output folder:", self.output_widget)

        self.common_group = group
        return group

    # -- widget state -----------------------------------------------------
    def _on_mode_changed(self, index: int) -> None:
        from_rasters = index == 0
        self.raster_group.setVisible(from_rasters)
        self.stac_group.setVisible(not from_rasters)

    def _on_already_nbr_toggled(self, checked: bool) -> None:
        # A single-band NBR raster is read straight through, so the SWIR band
        # index is irrelevant and the NIR field selects the NBR band itself.
        self.swir_spin.setEnabled(not checked)

    # -- settings persistence ---------------------------------------------
    def _restore_settings(self) -> None:
        """Restore the last-used scalar choices from the QGIS user settings.

        Only lightweight, always-valid choices are persisted; map-layer
        selections are left to QGIS since a stored layer may not exist in a
        later project. Combo indices are clamped to the current option lists.
        """
        settings = QgsSettings()

        def _f(key: str, default: float) -> float:
            return float(settings.value(_SETTINGS_PREFIX + key, default, type=float))

        def _i(key: str, default: int) -> int:
            return int(settings.value(_SETTINGS_PREFIX + key, default, type=int))

        def _index(key: str, combo: QComboBox) -> int:
            stored = _i(key, 0)
            return int(max(0, min(stored, combo.count() - 1)))

        self.mode_combo.setCurrentIndex(_index("mode", self.mode_combo))
        self.already_nbr_check.setChecked(
            settings.value(_SETTINGS_PREFIX + "already_nbr", False, type=bool)
        )
        self.nir_spin.setValue(_i("nir_band", 1))
        self.swir_spin.setValue(_i("swir_band", 2))
        self.resampling_combo.setCurrentIndex(_index("resampling", self.resampling_combo))

        self.baseline_spin.setValue(_i("baseline_months", 12))
        self.post_window_spin.setValue(_i("post_window_days", 60))
        self.max_cloud_spin.setValue(_f("max_cloud", 40.0))
        self.max_scenes_spin.setValue(_i("max_scenes", 0))
        self.min_valid_spin.setValue(_f("min_valid_fraction", 0.0))
        self.strategy_combo.setCurrentIndex(_index("strategy", self.strategy_combo))
        self.resolution_spin.setValue(_i("resolution_m", 20))
        catalog = settings.value(_SETTINGS_PREFIX + "catalog_url", "", type=str)
        if catalog:
            self.catalog_edit.setText(catalog)
        collection = settings.value(_SETTINGS_PREFIX + "collection", "", type=str)
        if collection:
            self.collection_edit.setText(collection)
        crs_authid = settings.value(_SETTINGS_PREFIX + "output_crs", "", type=str)
        if crs_authid:
            crs = QgsCoordinateReferenceSystem(crs_authid)
            if crs.isValid():
                self.crs_widget.setCrs(crs)

        self.threshold_spin.setValue(_f("threshold", 0.10))
        self.min_patch_spin.setValue(_i("min_patch", 0))
        self.fill_holes_spin.setValue(_i("fill_holes", 0))
        area_name = settings.value(_SETTINGS_PREFIX + "area_name", "", type=str)
        if area_name:
            self.area_name_edit.setText(area_name)
        self.export_polygons_check.setChecked(
            settings.value(_SETTINGS_PREFIX + "export_polygons", False, type=bool)
        )
        output_dir = settings.value(_SETTINGS_PREFIX + "output_dir", "", type=str)
        if output_dir:
            self.output_widget.setFilePath(output_dir)

    def _save_settings(self) -> None:
        """Persist the current scalar choices to the QGIS user settings."""
        settings = QgsSettings()
        settings.setValue(_SETTINGS_PREFIX + "mode", self.mode_combo.currentIndex())
        settings.setValue(_SETTINGS_PREFIX + "already_nbr", self.already_nbr_check.isChecked())
        settings.setValue(_SETTINGS_PREFIX + "nir_band", self.nir_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "swir_band", self.swir_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "resampling", self.resampling_combo.currentIndex())
        settings.setValue(_SETTINGS_PREFIX + "baseline_months", self.baseline_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "post_window_days", self.post_window_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "max_cloud", self.max_cloud_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "max_scenes", self.max_scenes_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "min_valid_fraction", self.min_valid_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "strategy", self.strategy_combo.currentIndex())
        settings.setValue(_SETTINGS_PREFIX + "resolution_m", self.resolution_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "catalog_url", self.catalog_edit.text().strip())
        settings.setValue(_SETTINGS_PREFIX + "collection", self.collection_edit.text().strip())
        crs = self.crs_widget.crs()
        if crs.isValid():
            settings.setValue(_SETTINGS_PREFIX + "output_crs", crs.authid())
        settings.setValue(_SETTINGS_PREFIX + "threshold", self.threshold_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "min_patch", self.min_patch_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "fill_holes", self.fill_holes_spin.value())
        settings.setValue(_SETTINGS_PREFIX + "area_name", self.area_name_edit.text().strip())
        settings.setValue(
            _SETTINGS_PREFIX + "export_polygons", self.export_polygons_check.isChecked()
        )
        settings.setValue(_SETTINGS_PREFIX + "output_dir", self.output_widget.filePath().strip())

    # -- helpers ----------------------------------------------------------
    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)
        self.log.repaint()

    def _output_paths(self, output_dir: str) -> dict[str, str]:
        """Return the three named raster destinations inside the output folder."""
        folder = Path(output_dir)
        return {
            "OUTPUT_SEVERITY": str(folder / "dnbr_severity.tif"),
            "OUTPUT_DNBR": str(folder / "dnbr.tif"),
            "OUTPUT_BURNT_MASK": str(folder / "burnt_mask.tif"),
        }

    # -- run --------------------------------------------------------------
    def _on_run(self) -> None:
        output_dir = self.output_widget.filePath().strip()
        if not output_dir:
            self._log("Please choose an output folder.")
            return

        from_rasters = self.mode_combo.currentIndex() == 0
        if from_rasters:
            algorithm_id, params = self._raster_params(output_dir)
        else:
            algorithm_id, params = self._stac_params(output_dir)
        if params is None:
            return

        self._save_settings()

        try:
            from qgis import processing
        except ImportError:  # pragma: no cover - environment dependent
            import processing

        self.run_button.setEnabled(False)
        self._log("Running…")
        # Catch broadly on purpose: any failure should be surfaced in the log
        # pane rather than raising into the QGIS UI.
        try:
            results = processing.run(algorithm_id, params, feedback=_LogFeedback(self._log))
        except Exception as error:
            self._log(f"Run failed: {error}")
            self.run_button.setEnabled(True)
            return

        self._load_and_style(results)
        self.run_button.setEnabled(True)

    def _raster_params(self, output_dir: str):
        pre_layer = self.pre_combo.currentLayer()
        post_layer = self.post_combo.currentLayer()
        if pre_layer is None or post_layer is None:
            self._log("Please choose both a pre-fire and a post-fire raster.")
            return "burntarea:dnbrfromrasters", None

        params = {
            "PRE": pre_layer,
            "POST": post_layer,
            "ALREADY_NBR": self.already_nbr_check.isChecked(),
            "NIR_BAND": self.nir_spin.value(),
            "SWIR_BAND": self.swir_spin.value(),
            "RESAMPLING": self.resampling_combo.currentIndex(),
            "THRESHOLD": self.threshold_spin.value(),
            "MIN_PATCH_PIXELS": self.min_patch_spin.value(),
            "FILL_HOLES_PIXELS": self.fill_holes_spin.value(),
            "AREA_NAME": self.area_name_edit.text().strip() or "burnt_area",
            "FIRE_DATE": "",
            "EXPORT_POLYGONS": self.export_polygons_check.isChecked(),
            "VECTOR_MIN_AREA_KM2": 0.0,
            "OUTPUT_DIR": output_dir,
            **self._output_paths(output_dir),
        }
        return "burntarea:dnbrfromrasters", params

    def _stac_params(self, output_dir: str):
        if not self.extent_widget.isValid():
            self._log("Please set an area-of-interest extent.")
            return "burntarea:dnbrfromstac", None

        extent = QgsReferencedRectangle(
            self.extent_widget.outputExtent(), self.extent_widget.outputCrs()
        )
        crs = self.crs_widget.crs()
        params = {
            "EXTENT": extent,
            "FIRE_DATE": self.fire_date_edit.date().toString("yyyy-MM-dd"),
            "BASELINE_MONTHS": self.baseline_spin.value(),
            "POST_WINDOW_DAYS": self.post_window_spin.value(),
            "MAX_CLOUD": self.max_cloud_spin.value(),
            "MAX_SCENES": self.max_scenes_spin.value(),
            "MIN_VALID_FRACTION": self.min_valid_spin.value(),
            "POSTFIRE_STRATEGY": self.strategy_combo.currentIndex(),
            "OUTPUT_CRS": crs if crs.isValid() else QgsCoordinateReferenceSystem("EPSG:3577"),
            "RESOLUTION_M": self.resolution_spin.value(),
            "THRESHOLD": self.threshold_spin.value(),
            "MIN_PATCH_PIXELS": self.min_patch_spin.value(),
            "FILL_HOLES_PIXELS": self.fill_holes_spin.value(),
            "AREA_NAME": self.area_name_edit.text().strip() or "burnt_area",
            "EXPORT_POLYGONS": self.export_polygons_check.isChecked(),
            "VECTOR_MIN_AREA_KM2": 0.0,
            "CATALOG_URL": self.catalog_edit.text().strip() or _DEFAULT_CATALOG,
            "COLLECTION": self.collection_edit.text().strip() or _DEFAULT_COLLECTION,
            "OUTPUT_DIR": output_dir,
            **self._output_paths(output_dir),
        }
        return "burntarea:dnbrfromstac", params

    def _load_and_style(self, results) -> None:
        """Load the three output rasters into the project and style each one."""
        loaded = 0
        for key, title, styler in (
            ("OUTPUT_SEVERITY", "Burn severity", apply_severity_style),
            ("OUTPUT_DNBR", "dNBR", apply_dnbr_style),
            ("OUTPUT_BURNT_MASK", "Burnt area", apply_burnt_mask_style),
        ):
            path = results.get(key)
            if not path:
                continue
            layer = self.iface.addRasterLayer(str(path), title)
            if layer is not None and layer.isValid():
                styler(layer)
                loaded += 1
                self._log(f"Loaded {path}")
        if loaded:
            self._log(f"Done. Loaded {loaded} layer(s).")
        else:
            self._log("Done, but no output rasters were returned.")
