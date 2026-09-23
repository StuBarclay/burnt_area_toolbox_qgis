"""Tests for the GDAL-free raster-source resolution policy.

:mod:`burnt_area_toolbox.algorithms._raster_source` holds the decision behind
``_qgis_io.raster_source_path`` with no ``qgis`` / ``osgeo`` dependency -- the
QGIS probes are injected as callables -- so the three-tier branch logic, the
candidate list building and the hand-assembled VRT XML can all be unit-tested
in the plain sandbox. The GDAL-backed integration is exercised by
``test_qgis_glue``.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from burnt_area_toolbox.algorithms._raster_source import (
    build_vrt_xml,
    resolve_raster_source,
    source_candidates,
)


def test_plain_existing_file_is_returned_unchanged() -> None:
    """Tier 1: the first candidate that exists on disk is used as-is."""
    result = resolve_raster_source(
        ["/data/pre.tif", "/data/pre.tif|extra"],
        exists=lambda path: path == "/data/pre.tif",
        wrap_descriptor=lambda path: "SHOULD NOT BE CALLED",
        materialise=lambda: "SHOULD NOT BE CALLED",
    )
    assert result == "/data/pre.tif"


def test_descriptor_is_wrapped_when_no_plain_file() -> None:
    """Tier 2: the first wrappable descriptor becomes a VRT path."""
    result = resolve_raster_source(
        ['OpenFileGDB:"/data/x.gdb":veg', "/data/other"],
        exists=lambda path: False,
        wrap_descriptor=lambda path: "/tmp/wrap.vrt" if path.startswith("OpenFileGDB") else None,
        materialise=lambda: "SHOULD NOT BE CALLED",
    )
    assert result == "/tmp/wrap.vrt"


def test_materialise_is_the_last_resort() -> None:
    """Tier 3: with nothing on disk and nothing wrappable, the layer is materialised."""
    calls: list[str] = []

    def materialise() -> str:
        calls.append("materialised")
        return "/tmp/materialised.tif"

    result = resolve_raster_source(
        ["", "weird://source"],
        exists=lambda path: False,
        wrap_descriptor=lambda path: None,
        materialise=materialise,
    )
    assert result == "/tmp/materialised.tif"
    assert calls == ["materialised"]


def test_source_candidates_strips_pipe_and_dedupes() -> None:
    """The pre-pipe part comes first, then the raw source, then the URI; no blanks/dupes."""
    candidates = source_candidates("file.tif|layername=veg", "file.tif")
    assert candidates == ["file.tif", "file.tif|layername=veg"]


def test_source_candidates_keeps_distinct_uri() -> None:
    """A distinct data-source URI is retained after the raw source."""
    candidates = source_candidates("a.tif", "b.tif")
    assert candidates == ["a.tif", "b.tif"]


def test_build_vrt_xml_is_well_formed_and_references_source() -> None:
    """The VRT parses, starts with ``<VRTDataset`` and points bands at the source."""
    xml = build_vrt_xml(
        width=4,
        height=3,
        source='OpenFileGDB:"/data/x.gdb":veg',
        bands=[("Float32", -9999.0), ("Byte", None)],
        srs_wkt='GEOGCS["WGS 84"]',
        geotransform=[150.0, 0.01, 0.0, -33.0, 0.0, -0.01],
    )
    assert xml.startswith("<VRTDataset")
    root = ET.fromstring(xml)  # raises on malformed XML
    assert root.tag == "VRTDataset"
    assert root.attrib["rasterXSize"] == "4"
    assert root.attrib["rasterYSize"] == "3"
    bands = root.findall("VRTRasterBand")
    assert len(bands) == 2
    # The descriptor is referenced verbatim (relativeToVRT=0) in every band.
    for band in bands:
        source_filename = band.find(".//SourceFilename")
        assert source_filename is not None
        assert source_filename.attrib["relativeToVRT"] == "0"
        assert source_filename.text == 'OpenFileGDB:"/data/x.gdb":veg'
    # Only the first band declares a NoDataValue.
    assert bands[0].find("NoDataValue") is not None
    assert bands[1].find("NoDataValue") is None


def test_build_vrt_xml_escapes_special_characters() -> None:
    """A source containing XML metacharacters stays well-formed and round-trips."""
    xml = build_vrt_xml(
        width=1,
        height=1,
        source='NETCDF:"/a & b/<grid>.nc":Band1',
        bands=[("Float32", None)],
    )
    root = ET.fromstring(xml)
    source_filename = root.find(".//SourceFilename")
    assert source_filename is not None
    assert source_filename.text == 'NETCDF:"/a & b/<grid>.nc":Band1'
