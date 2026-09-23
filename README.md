# Burnt Area Toolbox (dNBR) — QGIS plugin

A QGIS plugin that maps **burnt area** and **burn severity** from the
**differenced Normalised Burn Ratio (dNBR)** of Sentinel-2 imagery. Give it a
pair of rasters you already have, or let it fetch cloud-free scenes from a
public STAC catalogue for you.

**No extra Python packages are installed** — the plugin has no `rasterio`,
`fiona` or `scipy` runtime dependency. It uses only NumPy and GDAL/OGR/OSR,
which ship inside QGIS.

> This tool is a decision aid. dNBR thresholds and severity breaks follow the
> widely-used USGS/UN-SPIDER convention, but the right threshold varies with
> vegetation, sensor and fire. Treat the output as a first pass and validate
> fire extent and severity against field or higher-resolution data before
> relying on it.

## What you get

The plugin adds a **Processing provider** ("Burnt Area Toolbox (dNBR)") with two
algorithms, and a **Plugins → Burnt Area Toolbox** menu item / toolbar button
that opens a dialog for the common case. The dialog remembers your last-used
choices between sessions.

Processing algorithms:

- **Burnt area from rasters (dNBR)** — differences a pre-fire and a post-fire
  raster you already have. By default it reads each raster's Near-Infrared and
  shortwave-infrared (SWIR-2, ~2.2 µm) bands and computes
  `NBR = (NIR − SWIR) / (NIR + SWIR)`, then `dNBR = pre-fire NBR − post-fire
  NBR`. Tick *Rasters already hold NBR* when your inputs are single-band NBR
  images. Both rasters are resampled onto the post-fire raster's grid, so they
  may differ in extent, resolution or CRS.
- **Burnt area from STAC imagery (dNBR)** — searches a public STAC catalogue
  (Earth Search / Sentinel-2 L2A by default, **no login or API key required**)
  for cloud-free imagery before and after a fire. It median-composites a
  baseline from the months before the fire, takes either the single clearest
  post-fire scene or a post-fire median composite, downloads only the NIR and
  SWIR bands it needs (plus the scene-classification mask for cloud screening),
  and maps dNBR over the extent you choose. An internet connection is required;
  larger extents and longer windows download more.

Both algorithms produce the same outputs:

- a **dNBR** raster (continuous, styled with a blue-to-red ramp),
- a **seven-class burn-severity** raster (styled with the USGS/UN-SPIDER
  palette: enhanced regrowth → unburned → low / moderate-low / moderate-high /
  high severity), and
- a cleaned **burnt-area mask** (burnt pixels where `dNBR > threshold`, with
  optional speckle removal and hole filling).

Set an **output folder** to also keep the full result set: all five GeoTIFFs
(baseline NBR, post-fire NBR, dNBR, severity, burnt mask), a one-row summary
CSV, optional burnt/severity polygons and a run manifest recording every
parameter used.

The loaded severity, dNBR and burnt-mask layers are **styled automatically**.

## The science, briefly

The Normalised Burn Ratio contrasts the near-infrared band (high over healthy
vegetation) with the shortwave-infrared band (high over bare, charred ground):

```
NBR  = (NIR − SWIR2) / (NIR + SWIR2)
dNBR = NBR_prefire − NBR_postfire
```

A fire drops NBR, so dNBR is positive over burnt ground and larger where the
burn is more severe. A pixel is classed **burnt** where `dNBR` exceeds the
threshold (default `0.10`, which catches low-severity burns and above; raise it
toward `0.27` to restrict to moderate/high severity). The seven severity classes
use the standard upper-exclusive dNBR breaks
`(−0.250, −0.100, 0.100, 0.270, 0.440, 0.660)`.

## Installation

**From a ZIP** (the usual way): in QGIS, *Plugins → Manage and Install
Plugins → Install from ZIP*, and choose `burnt_area_toolbox.zip`. Then enable
**Burnt Area Toolbox (dNBR)** in the *Installed* tab.

**From source** (for development): copy or symlink the `burnt_area_toolbox`
folder into your QGIS plugins directory
(`…/QGIS3/profiles/default/python/plugins/`) and enable it.

Requires **QGIS 3.34 LTR or newer** (which bundles Python 3.10+).

## No extra dependencies

The plugin ships a small vendored GDAL shim (`bacore/_rio`) that reimplements
the slice of the `rasterio` / `fiona` I/O API the compute core needs directly on
top of `osgeo` (GDAL/OGR/OSR). Because GDAL and NumPy already come with QGIS,
installing the plugin never triggers a `pip install`, which keeps it working on
locked-down and offline machines.

## Development

The project runs four gates, all locally and in CI:

```bash
pip install -e ".[dev]"
ruff check .          # lint
ruff format --check . # formatting
mypy                  # strict type checking
pytest -q             # tests
```

The pure compute core (`bacore`), the GDAL shims' numeric logic, the config
builder, the progress/feedback glue, the styling tables and the help metadata
are all exercised without a QGIS runtime. The QGIS-side tests
(`tests/test_qgis_glue.py`) and the GDAL-backed raster tests skip automatically
via `pytest.importorskip` where `qgis` / `osgeo` are unavailable (the plain
sandbox and the hosted CI runners), and run in full inside a QGIS Python
environment.

## Licence

Apache-2.0 — see [`burnt_area_toolbox/LICENSE`](burnt_area_toolbox/LICENSE).
