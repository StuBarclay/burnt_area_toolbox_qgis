"""Bridging helpers between QGIS layers and the compute core's file I/O.

The compute core reads and writes plain files (GeoTIFF rasters). These helpers
turn QGIS Processing raster inputs into paths the core can open, and mirror the
GeoTIFFs the core writes into the Processing raster destinations the user chose,
so the algorithm classes stay small and the conversions are tested in one place.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessingException,
    QgsRasterLayer,
)

from burnt_area_toolbox.algorithms._raster_source import (
    build_vrt_xml,
    resolve_raster_source,
    source_candidates,
)


def raster_source_path(layer: QgsRasterLayer, feedback: Any = None) -> str:
    """Return a path the compute core can both find on disk and open.

    Accepts a broad range of raster sources, not just plain files on disk:

    1. A plain local file (e.g. a GeoTIFF) is returned unchanged -- the fast,
       zero-copy path.
    2. Any GDAL *dataset descriptor* the layer exposes -- a raster held inside
       an ESRI File Geodatabase (``OpenFileGDB:"...gdb":layer``), a NetCDF/HDF
       subdataset, a ``/vsicurl/`` path or a WCS XML descriptor -- is wrapped
       in a tiny virtual raster (VRT). The compute core opens rasters by path
       and first checks the path *exists*, which a bare descriptor never does;
       the VRT is a real file that merely references the descriptor and reads
       it lazily, so a large scene is never copied in full (only the pixel
       window the core requests is read from the underlying source).
    3. Anything else QGIS can render but GDAL cannot open directly (an
       in-memory raster, a WMS/WCS layer without a file backing, an exotic
       provider) is materialised to a temporary GeoTIFF via QGIS's own raster
       writer, and that file path is returned.

    Args:
        layer: The raster layer chosen for the algorithm.
        feedback: Optional Processing feedback for a progress line when a
            layer has to be wrapped or materialised.

    Returns:
        A file path the compute core can open (a plain file, a VRT wrapping a
        GDAL descriptor, or a materialised GeoTIFF).

    Raises:
        QgsProcessingException: If no layer is supplied, or the layer cannot
            be read by GDAL or exported by QGIS.
    """
    if layer is None:
        raise QgsProcessingException("A raster layer is required.")

    raw = str(layer.source())
    try:
        uri = str(layer.dataProvider().dataSourceUri())
    except Exception:  # pragma: no cover - defensive, provider-specific
        uri = ""

    return resolve_raster_source(
        source_candidates(raw, uri),
        exists=lambda candidate: Path(candidate).exists(),
        wrap_descriptor=lambda candidate: _vrt_for_descriptor(candidate, feedback),
        materialise=lambda: _materialise_raster(layer, feedback),
    )


def _vrt_for_descriptor(source: str, feedback: Any = None) -> str | None:
    """Wrap a GDAL dataset descriptor in a small VRT file the core can open.

    ``source`` is something GDAL understands but that is not a plain file on
    disk -- a raster inside an ESRI File Geodatabase
    (``OpenFileGDB:"...gdb":layer``), a NetCDF/HDF subdataset, a ``/vsicurl/``
    path or a WCS XML descriptor. The compute core opens rasters by path and
    first checks the path *exists*, which a bare descriptor never does, so the
    descriptor is wrapped in a tiny virtual raster (VRT). The VRT is a real
    file that only references the source and reads it lazily: a large scene is
    never copied in full -- only the pixel window the core requests is read
    from the underlying source.

    The VRT XML is assembled by hand (:func:`build_vrt_xml`) from the source's
    size, projection, geotransform and per-band datatype/nodata, then written
    with a plain file write, then reopened with GDAL as a guard. If GDAL cannot
    open ``source`` or cannot reopen the VRT, ``None`` is returned so the caller
    falls back rather than handing the core a file it cannot read.

    Args:
        source: A GDAL dataset descriptor or connection string.
        feedback: Optional Processing feedback for a progress line.

    Returns:
        The path to the written ``.vrt`` file, or ``None``.
    """
    from osgeo import gdal

    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        try:
            dataset = gdal.Open(source, gdal.GA_ReadOnly)
        except RuntimeError:
            dataset = None
        if dataset is None:
            return None
        try:
            vrt_xml = _vrt_xml_for_open_dataset(dataset, source)
        finally:
            dataset = None  # close the probe handle
    finally:
        gdal.PopErrorHandler()

    if vrt_xml is None:  # pragma: no cover - defensive, empty/odd source
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="burntarea_vrt_"))
    vrt_path = tmp_dir / "source.vrt"
    vrt_path.write_text(vrt_xml, encoding="utf-8")

    # Never hand the core a VRT GDAL cannot actually reopen.
    if not _gdal_openable(str(vrt_path)):  # pragma: no cover - defensive
        return None
    if feedback is not None:
        feedback.pushInfo(f"  wrapped GDAL source in a VRT -> {vrt_path}")
    return str(vrt_path)


def _vrt_xml_for_open_dataset(dataset: Any, source: str) -> str | None:
    """Read an open GDAL dataset's metadata and build a VRT that references it.

    Args:
        dataset: An open ``gdal.Dataset`` for ``source``.
        source: The descriptor/path the VRT bands should read from.

    Returns:
        A VRT XML document, or ``None`` if the dataset exposes no bands.
    """
    from osgeo import gdal

    band_count = int(dataset.RasterCount)
    if band_count < 1:
        return None
    bands: list[tuple[str, float | None]] = []
    for band_index in range(1, band_count + 1):
        band = dataset.GetRasterBand(band_index)
        dtype_name = str(gdal.GetDataTypeName(band.DataType))
        nodata = band.GetNoDataValue()
        bands.append((dtype_name, None if nodata is None else float(nodata)))

    wkt = dataset.GetProjection() or ""
    try:
        geotransform = dataset.GetGeoTransform(can_return_null=True)
    except TypeError:  # pragma: no cover - very old GDAL binding
        geotransform = dataset.GetGeoTransform()

    return build_vrt_xml(
        width=int(dataset.RasterXSize),
        height=int(dataset.RasterYSize),
        source=source,
        bands=bands,
        srs_wkt=str(wkt),
        geotransform=None if geotransform is None else [float(v) for v in geotransform],
    )


def _gdal_openable(source: str) -> bool:
    """Return True if GDAL can open ``source`` (quietly, read-only)."""
    from osgeo import gdal

    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        try:
            dataset = gdal.Open(source, gdal.GA_ReadOnly)
        except RuntimeError:
            return False
    finally:
        gdal.PopErrorHandler()
    if dataset is None:
        return False
    dataset = None  # close
    return True


def _safe_stem(name: str) -> str:
    """Turn a layer name into a filesystem-safe file stem."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return cleaned.strip("_") or "raster"


def _materialise_raster(layer: QgsRasterLayer, feedback: Any = None) -> str:
    """Export any QGIS-readable raster layer to a temporary GeoTIFF.

    Used as a last resort for sources GDAL cannot open directly (in-memory
    rasters, some WMS/WCS layers, exotic providers). Uses QGIS's own raster
    pipeline, so it works for any provider QGIS can read.

    Args:
        layer: The raster layer to export.
        feedback: Optional Processing feedback for a progress line.

    Returns:
        The path to the written GeoTIFF.

    Raises:
        QgsProcessingException: If the layer is unreadable or the export fails.
    """
    from qgis.core import QgsRasterFileWriter, QgsRasterPipe

    provider = layer.dataProvider()
    if provider is None or not layer.isValid():
        raise QgsProcessingException(f"Raster layer '{layer.name()}' is not readable by QGIS.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="burntarea_src_"))
    out_path = tmp_dir / f"{_safe_stem(layer.name())}.tif"

    pipe = QgsRasterPipe()
    if not pipe.set(provider.clone()):
        raise QgsProcessingException(f"Could not build a raster pipeline for '{layer.name()}'.")
    writer = QgsRasterFileWriter(str(out_path))
    error = writer.writeRaster(
        pipe,
        provider.xSize(),
        provider.ySize(),
        provider.extent(),
        provider.crs(),
    )
    if int(error) != 0:  # QgsRasterFileWriter.NoError == 0
        raise QgsProcessingException(
            f"Raster layer '{layer.name()}' has no source GDAL can read and "
            f"could not be exported to a GeoTIFF (writer error {int(error)}); "
            "export it to a GeoTIFF manually first."
        )
    if feedback is not None:
        feedback.pushInfo(f"  materialised '{layer.name()}' -> {out_path}")
    return str(out_path)


def export_raster(source_tif: str, destination: str, feedback: Any = None) -> str:
    """Mirror a GeoTIFF the core wrote to a Processing raster destination.

    The compute core always writes GeoTIFFs into a working folder. Processing
    raster destinations, by contrast, may be a temporary file or a path the
    user named in another format. This copies the core's file to the
    destination when the extensions match, or translates it with GDAL when
    they differ (so a user who named e.g. ``result.img`` still gets that
    format), and returns the resulting path. When the destination is empty or
    already resolves to the same file, the source path is returned unchanged.

    Args:
        source_tif: Path to the GeoTIFF the core produced.
        destination: The resolved Processing raster-destination path (may be
            empty).
        feedback: Optional Processing feedback for a progress line.

    Returns:
        The path the raster now lives at (the destination, or the source when
        no export was needed).
    """
    if not destination:
        return source_tif
    src = Path(source_tif)
    dst = Path(destination)
    if src.resolve() == dst.resolve():
        return source_tif
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == dst.suffix.lower():
        shutil.copy2(src, dst)
    else:
        from osgeo import gdal

        gdal.UseExceptions()
        gdal.Translate(str(dst), str(src))
    if feedback is not None:
        feedback.pushInfo(f"  exported raster: {dst}")
    return str(dst)


def crs_to_epsg_string(crs: QgsCoordinateReferenceSystem) -> str | None:
    """Return a CRS as an ``"EPSG:NNNN"`` string, or ``None`` if unknown.

    Args:
        crs: A QGIS coordinate reference system.

    Returns:
        The authid when it is an EPSG code, else ``None``.
    """
    if crs is None or not crs.isValid():
        return None
    authid = crs.authid()  # e.g. "EPSG:3577"
    return authid if authid.upper().startswith("EPSG:") else None
