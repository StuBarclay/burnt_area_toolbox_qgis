"""QGIS-side tests for the Processing glue the plain sandbox cannot import.

Everything here needs a real ``qgis`` (and ``osgeo``) runtime -- the parameter
definitions, the provider wiring, the algorithm ``flags()`` and the raster
styling all touch the QGIS API. The module is therefore skipped where QGIS is
absent (the plain sandbox and the hosted CI runners) and runs in full inside a
QGIS Python environment, e.g. via ``qgis_testrunner`` or ``pytest`` launched
from a shell where ``python`` is the QGIS-bundled interpreter.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("qgis", reason="QGIS Python runtime required")
pytest.importorskip("osgeo", reason="GDAL Python bindings (osgeo) required")

import numpy as np
from burnt_area_toolbox.algorithms import _algorithms
from burnt_area_toolbox.algorithms._ba_style import (
    apply_burnt_mask_style,
    apply_dnbr_style,
    apply_severity_style,
)
from burnt_area_toolbox.algorithms._help import HELP_URL
from burnt_area_toolbox.algorithms._qgis_io import _gdal_openable, raster_source_path
from burnt_area_toolbox.algorithms.dnbr_from_rasters import DnbrFromRastersAlgorithm
from burnt_area_toolbox.algorithms.dnbr_from_stac import DnbrFromStacAlgorithm
from burnt_area_toolbox.provider import BurntAreaProvider
from osgeo import gdal, osr
from qgis.core import (
    QgsApplication,
    QgsPalettedRasterRenderer,
    QgsProcessingAlgorithm,
    QgsRasterLayer,
    QgsSingleBandPseudoColorRenderer,
)

#: The algorithm ids that must always be present in the provider.
_EXPECTED_IDS = {"dnbrfromrasters", "dnbrfromstac"}


@pytest.fixture(scope="module", autouse=True)
def qgis_app() -> Iterator[QgsApplication]:
    """Ensure a QgsApplication exists for the module (reusing any running one)."""
    app = QgsApplication.instance()
    created = False
    if app is None:
        app = QgsApplication([], False)
        app.initQgis()
        created = True
    yield app
    if created:
        app.exitQgis()


def _no_threading_flag() -> Any:
    """Return the ``FlagNoThreading`` enum member across PyQt5/PyQt6."""
    flag = getattr(QgsProcessingAlgorithm, "FlagNoThreading", None)
    if flag is None:  # PyQt6 / QGIS 4 scopes the enum
        flag = QgsProcessingAlgorithm.Flag.FlagNoThreading
    return flag


def _write_geotiff(
    path: Path, array: np.ndarray[Any, Any], gdal_type: int, nodata: float | None = None
) -> None:
    """Create a tiny single-band GeoTIFF at ``path`` from ``array``."""
    height, width = array.shape
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), width, height, 1, gdal_type)
    dataset.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.WriteArray(array)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.FlushCache()
    dataset = None


def test_provider_registers_two_algorithms() -> None:
    """The factory yields the two algorithms with unique, non-empty ids."""
    algorithms = _algorithms()
    names = [alg.name() for alg in algorithms]
    assert len(algorithms) == 2
    assert all(names)  # no blank ids
    assert len(set(names)) == len(names)  # all unique
    assert set(names) == _EXPECTED_IDS


def test_provider_id_is_stable() -> None:
    """The provider id used in ``processing.run`` strings stays ``burntarea``."""
    assert BurntAreaProvider().id() == "burntarea"


def test_create_instance_and_init_for_all() -> None:
    """Each algorithm round-trips through createInstance/initAlgorithm."""
    for alg in _algorithms():
        clone = alg.createInstance()
        assert isinstance(clone, type(alg))
        clone.initAlgorithm()
        assert clone.parameterDefinitions()  # at least one parameter defined


def test_all_algorithms_expose_help_url() -> None:
    """Every algorithm points its Processing "Help" button at the README."""
    for alg in _algorithms():
        assert alg.helpUrl() == HELP_URL


def test_both_algorithms_keep_threading_enabled() -> None:
    """``flags()`` must NOT set FlagNoThreading (keeps progress/cancel live)."""
    for alg in (DnbrFromRastersAlgorithm(), DnbrFromStacAlgorithm()):
        assert not bool(alg.flags() & _no_threading_flag())


def test_from_rasters_exposes_expected_parameters() -> None:
    """The from-rasters tool carries its pre/post and severity-output params."""
    alg = DnbrFromRastersAlgorithm()
    alg.initAlgorithm()
    names = {param.name() for param in alg.parameterDefinitions()}
    assert {"PRE", "POST", "THRESHOLD", "OUTPUT_SEVERITY"} <= names


def test_from_stac_exposes_expected_parameters() -> None:
    """The STAC tool carries the extent, fire-date and catalogue params."""
    alg = DnbrFromStacAlgorithm()
    alg.initAlgorithm()
    names = {param.name() for param in alg.parameterDefinitions()}
    assert {"EXTENT", "FIRE_DATE", "CATALOG_URL", "MAX_SCENES", "OUTPUT_SEVERITY"} <= names


def test_styles_ignore_bad_layers() -> None:
    """Each styler tolerates ``None`` and invalid layers without raising."""
    for styler in (apply_severity_style, apply_dnbr_style, apply_burnt_mask_style):
        assert styler(None) is False
        assert styler(QgsRasterLayer("/does/not/exist.tif", "x")) is False


def test_severity_style_sets_paletted_renderer(tmp_path: Path) -> None:
    """A seven-class severity raster is given a paletted renderer."""
    path = tmp_path / "severity.tif"
    _write_geotiff(
        path,
        np.array([[0, 1, 2, 3], [4, 5, 6, 7], [0, 3, 3, 4], [1, 2, 3, 0]], dtype=np.uint8),
        gdal.GDT_Byte,
    )
    layer = QgsRasterLayer(str(path), "severity")
    assert layer.isValid()
    assert apply_severity_style(layer) is True
    assert isinstance(layer.renderer(), QgsPalettedRasterRenderer)


def test_burnt_mask_style_sets_paletted_renderer(tmp_path: Path) -> None:
    """A 0/1 burnt-mask raster is given a paletted renderer."""
    path = tmp_path / "burnt.tif"
    _write_geotiff(
        path,
        np.array([[0, 1, 1, 0], [1, 1, 0, 0], [0, 0, 1, 1], [1, 0, 1, 0]], dtype=np.uint8),
        gdal.GDT_Byte,
    )
    layer = QgsRasterLayer(str(path), "burnt")
    assert layer.isValid()
    assert apply_burnt_mask_style(layer) is True
    assert isinstance(layer.renderer(), QgsPalettedRasterRenderer)


def test_dnbr_style_sets_pseudocolour_renderer(tmp_path: Path) -> None:
    """A continuous dNBR raster is given a single-band pseudocolour renderer."""
    path = tmp_path / "dnbr.tif"
    _write_geotiff(
        path,
        np.array(
            [
                [-0.3, -0.1, 0.1, 0.3],
                [0.5, 0.7, 0.0, -0.2],
                [0.2, 0.4, 0.6, 0.8],
                [0.1, 0.0, -0.1, 0.2],
            ],
            dtype=np.float32,
        ),
        gdal.GDT_Float32,
    )
    layer = QgsRasterLayer(str(path), "dnbr")
    assert layer.isValid()
    assert apply_dnbr_style(layer) is True
    assert isinstance(layer.renderer(), QgsSingleBandPseudoColorRenderer)


def test_raster_source_path_returns_plain_file(tmp_path: Path) -> None:
    """A plain file-backed raster is returned as an on-disk, GDAL-openable path."""
    path = tmp_path / "post.tif"
    _write_geotiff(path, np.ones((4, 4), dtype=np.float32), gdal.GDT_Float32)
    layer = QgsRasterLayer(str(path), "post")
    assert layer.isValid()
    returned = raster_source_path(layer)
    assert Path(returned).exists()
    assert _gdal_openable(str(Path(returned)))


def test_dialog_imports_inside_qgis() -> None:
    """The GUI dialog imports and constructs its class object under QGIS."""
    from burnt_area_toolbox.gui.dialog import BurntAreaDialog

    assert BurntAreaDialog is not None
