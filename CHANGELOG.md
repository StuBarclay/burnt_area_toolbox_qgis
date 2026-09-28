# Changelog

All notable changes to the Burnt Area Toolbox (dNBR) QGIS plugin are recorded
here. The format follows [Keep a Changelog](https://keepachangelog.com/), and
the project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-24

This release hardens the plugin for real-world networks and large jobs, tidies
what it writes to disk, and prepares it for publication on the QGIS plugin
repository. It is marked *experimental* while broader coverage inside a running
QGIS lands.

### Added
- **Rasterio-parity tests** for the vendored `bacore/_rio` GDAL shim: when
  `rasterio` and `affine` are installed (dev/CI only), the test suite checks the
  shim's raster/vector I/O and `transform_geom` against real rasterio. The tests
  skip cleanly when those optional packages are absent, so the plugin still ships
  with no runtime dependencies beyond NumPy and the GDAL/OGR that QGIS bundles.
- A **GDAL-enabled continuous-integration job** that runs the GDAL-dependent
  tests (shim parity, sieve) inside an OSGeo/GDAL container, alongside the
  existing pure-Python gates.

### Changed
- **Remote Sentinel-2 reads are resilient to flaky networks.** Windowed reads of
  cloud-hosted COGs over `/vsicurl` now apply connect/read timeouts and retry
  transient failures with exponential back-off, instead of aborting an entire run
  on a single dropped connection. GDAL HTTP settings are set and restored around
  each remote read so nothing leaks into unrelated operations.
- **Speckle removal and hole filling use GDAL's `SieveFilter`,** which is far
  faster and lower-memory than the previous pure-Python region-growing sieve on
  large masks. The pure-Python implementation is retained as an automatic
  fallback (and as the reference the tests pin against) when GDAL is unavailable.
- **Runs without an output folder write only the three rasters that are actually
  returned** (dNBR, severity, burnt mask) to a scratch directory that is removed
  once QGIS has copied the results out. Setting an output folder still writes the
  full result set.
- **The run manifest now lists only the files that run produced,** rather than
  everything found in the output directory, so re-using a folder no longer
  reports stale artifacts.
- **Large-polygon areas are measured more accurately.** Polygon edges are
  densified before reprojection to an equal-area CRS, so the metre-squared area
  of long geographic edges no longer under-counts.
- The GUI dialog now runs long STAC jobs off the main thread with a cancellable
  progress dialog, keeping the QGIS window responsive during downloads.

### Fixed
- **CRS equality/hashing contract.** `CRS.__eq__` compares coordinate systems
  semantically (via OSR `IsSame`), so two equal CRS objects now always hash
  equal — they can be used safely as dict keys or in sets without surprising
  lookups.
- **Reprojection no longer leaks GDAL datasets** and raises a clear error instead
  of returning silently wrong data if a warp yields no result.

### Packaging
- Declared `license=Apache-2.0` in the plugin metadata and marked the release
  `experimental=True` for its first publication.
- Version bumped to 0.2.0 across `metadata.txt`, `pyproject.toml` and
  `burnt_area_toolbox.__version__`, resolving the previous version drift.
- The packaged ZIP excludes `__pycache__`, test and development files.

## [0.1.1] - 2026-09-23

### Added
- **Burnt area from STAC imagery (dNBR):** a new optional *Max scenes per
  window* parameter caps how many scenes are downloaded for the baseline and
  post-fire searches (each capped independently). When set, the clearest
  (least-cloudy) scenes are kept; `0` means no limit. This bounds download time
  and bandwidth on large extents or long windows without changing the default
  behaviour.

### Changed
- The per-scene progress bar now advances monotonically across both the
  baseline and post-fire loading phases, instead of resetting to the start when
  the post-fire load begins. Progress text still reads "Loaded X of Y scenes".

## [0.1.0] - 2026-09-23

### Added
- Initial release.
- **Burnt area from rasters (dNBR)** processing algorithm — differences a
  pre-fire and a post-fire raster the user already has. Reads NIR and SWIR-2
  bands to compute NBR and dNBR, or takes single-band NBR inputs directly.
  Both rasters are resampled onto the post-fire raster's grid, so they may
  differ in extent, resolution or CRS.
- **Burnt area from STAC imagery (dNBR)** processing algorithm — searches a
  public STAC catalogue (Earth Search / Sentinel-2 L2A by default, no login or
  API key required) for cloud-free imagery before and after a fire,
  median-composites a baseline, selects the clearest post-fire scene or a
  post-fire composite, and downloads only the bands it needs. Reports per-scene
  progress and is cancellable.
- Common outputs for both algorithms: a continuous dNBR raster, a seven-class
  burn-severity raster (USGS/UN-SPIDER breaks and palette) and a cleaned
  burnt-area mask, with optional speckle removal and hole filling. Setting an
  output folder additionally writes the full result set — all five GeoTIFFs, a
  summary CSV, optional burnt/severity polygons and a run manifest.
- Loaded severity, dNBR and burnt-mask layers are styled automatically with
  their palettes / colour ramp.
- A **Processing provider** ("Burnt Area Toolbox (dNBR)") plus a
  **Plugins → Burnt Area Toolbox** menu item / toolbar button that opens a
  friendly two-mode dialog, which remembers the last-used choices between
  sessions.
- Vendored GDAL shim (`bacore/_rio`) reimplementing the needed rasterio/fiona
  I/O on top of `osgeo`, so no extra Python packages are ever installed — the
  plugin uses only NumPy and GDAL/OGR/OSR, both of which ship with QGIS.
