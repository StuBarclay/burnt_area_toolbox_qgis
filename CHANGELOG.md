# Changelog

All notable changes to the Burnt Area Toolbox (dNBR) QGIS plugin are recorded
here. The format follows [Keep a Changelog](https://keepachangelog.com/), and
the project uses [Semantic Versioning](https://semver.org/).

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
