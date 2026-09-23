"""Compute core for the Burnt Area Toolbox QGIS plugin.

This package holds all of the burnt-area science and file I/O, kept free of
any ``qgis`` or PyQt import so it stays importable (and unit-testable) in
plain Python. Two layers live here:

* A pure-:mod:`numpy` layer -- :mod:`~burnt_area_toolbox.bacore.severity`,
  :mod:`~burnt_area_toolbox.bacore.scenes` and
  :mod:`~burnt_area_toolbox.bacore.config` -- that implements the dNBR
  severity classification, burnt-mask cleanup, per-scene quality/valid-fraction
  logic and the run configuration with no GDAL dependency at all.
* A GDAL-backed layer -- :mod:`~burnt_area_toolbox.bacore.raster`,
  :mod:`~burnt_area_toolbox.bacore.stac`, :mod:`~burnt_area_toolbox.bacore.vector`
  and :mod:`~burnt_area_toolbox.bacore.analysis` -- that reads and writes
  rasters, searches a STAC catalogue and downloads Sentinel-2 imagery, and
  orchestrates a full run. It reaches GDAL only through the vendored
  :mod:`~burnt_area_toolbox.bacore._rio` shim, so the plugin needs no
  third-party Python packages beyond what QGIS ships.

Nodata is represented as ``numpy.nan`` throughout the float bands, so the
NBR/dNBR arithmetic propagates missing data naturally.
"""

from __future__ import annotations
