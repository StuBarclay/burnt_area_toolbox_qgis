"""dNBR severity science, implemented in pure :mod:`numpy`.

This module carries the burnt-area numerics with no GDAL or ``qgis``
dependency, so it is fully exercised by the sandbox test-suite. It mirrors
the upstream ``burnt_area_mapping`` project exactly:

* :func:`compute_nbr` -- the Normalised Burn Ratio from NIR and SWIR22;
* :func:`classify_dnbr` -- the seven-class dNBR severity classification;
* :func:`sieve` and :func:`cleanup_burnt_mask` -- morphological cleanup of a
  binary burnt mask (speckle removal and hole filling), reproducing
  :func:`rasterio.features.sieve` for the binary case;
* :func:`summarize_severity_areas` and :func:`area_summary` -- per-class and
  burnt/unburnt/valid/nodata area tallies.

Nodata is carried as ``numpy.nan`` in the float bands, so NBR/dNBR
arithmetic propagates missing observations without a magic sentinel.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from numpy.typing import NDArray

from .constants import SEVERITY_BREAKS, SEVERITY_CODE_TO_LABEL


def compute_nbr(
    nir: NDArray[np.floating],
    swir22: NDArray[np.floating],
) -> NDArray[np.float64]:
    """Return the Normalised Burn Ratio ``(nir - swir22) / (nir + swir22)``.

    Args:
        nir: Near-infrared reflectance band.
        swir22: Short-wave infrared (2.2 micron) reflectance band.

    Returns:
        The NBR as a float64 array. Cells where ``nir + swir22`` is zero (or
        where either input is ``nan``) become ``nan``, matching the upstream
        floating-point behaviour.
    """
    nir_arr = np.asarray(nir, dtype=np.float64)
    swir_arr = np.asarray(swir22, dtype=np.float64)
    denominator = nir_arr + swir_arr
    with np.errstate(divide="ignore", invalid="ignore"):
        nbr = (nir_arr - swir_arr) / denominator
    # 0/0 already yields nan; guard an exact non-nan zero denominator too.
    nbr = np.where(denominator == 0, np.nan, nbr)
    return np.asarray(nbr, dtype=np.float64)


def classify_dnbr(delta_nbr: NDArray[np.floating]) -> NDArray[np.uint8]:
    """Classify a dNBR array into the seven severity classes.

    The class breaks are (upper-exclusive): ``< -0.250 -> 1``,
    ``[-0.250, -0.100) -> 2``, ``[-0.100, 0.100) -> 3``,
    ``[0.100, 0.270) -> 4``, ``[0.270, 0.440) -> 5``,
    ``[0.440, 0.660) -> 6``, ``>= 0.660 -> 7``. Cells that are ``nan``
    (nodata) map to ``0``.

    Args:
        delta_nbr: The dNBR array (baseline NBR minus post-fire NBR).

    Returns:
        A ``uint8`` array of class codes in ``0..7``.
    """
    delta = np.asarray(delta_nbr, dtype=np.float64)
    valid = ~np.isnan(delta)
    # Every valid cell starts in class 1, then climbs one class for each
    # ascending break value it meets or exceeds; nodata (nan) maps to 0.
    codes = np.ones(delta.shape, dtype=np.uint8)
    for threshold in SEVERITY_BREAKS:
        codes = np.where(valid & (delta >= threshold), codes + 1, codes)
    return np.where(valid, codes, 0).astype(np.uint8)


def _neighbour_offsets(connectivity: int) -> tuple[tuple[int, int], ...]:
    """Return pixel-neighbour offsets for 4- or 8-connectivity."""
    if connectivity == 8:
        return (
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        )
    return ((-1, 0), (1, 0), (0, -1), (0, 1))


def sieve(
    data: NDArray[np.integer],
    size: int,
    connectivity: int = 4,
) -> NDArray[np.integer]:
    """Remove connected regions smaller than ``size`` pixels.

    Faithfully reproduces :func:`rasterio.features.sieve` for the integer
    (in practice binary) arrays this project sieves: every maximal region of
    equal-valued, ``connectivity``-connected cells whose area is *smaller
    than* ``size`` is replaced with the value of its largest neighbouring
    region. Regions with no neighbour (a lone region filling the array) are
    left untouched.

    Args:
        data: A 2-D integer array (e.g. a 0/1 mask).
        size: Minimum region size (pixel count) to retain; regions with
            fewer than ``size`` pixels are merged into their largest
            neighbour.
        connectivity: ``4`` (edge) or ``8`` (edge + corner) connectivity.

    Returns:
        A new array with small regions merged away.
    """
    arr = np.asarray(data)
    out = arr.copy()
    if size <= 1 or arr.size == 0:
        return out

    rows, cols = arr.shape
    offsets = _neighbour_offsets(connectivity)
    labels = np.full((rows, cols), -1, dtype=np.int64)
    region_value: list[int] = []
    region_cells: list[list[tuple[int, int]]] = []

    # Label maximal equal-valued connected components.
    for r in range(rows):
        for c in range(cols):
            if labels[r, c] != -1:
                continue
            value = int(arr[r, c])
            label = len(region_value)
            labels[r, c] = label
            cells: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(r, c)])
            while queue:
                cr, cc = queue.popleft()
                cells.append((cr, cc))
                for dr, dc in offsets:
                    nr, nc = cr + dr, cc + dc
                    if (
                        0 <= nr < rows
                        and 0 <= nc < cols
                        and labels[nr, nc] == -1
                        and int(arr[nr, nc]) == value
                    ):
                        labels[nr, nc] = label
                        queue.append((nr, nc))
            region_value.append(value)
            region_cells.append(cells)

    region_size = [len(cells) for cells in region_cells]

    # Merge each too-small region into its largest neighbour. Process
    # smallest-first so a speckle is resolved before larger regions.
    for label in sorted(range(len(region_size)), key=lambda i: region_size[i]):
        if region_size[label] >= size:
            continue
        neighbour_sizes: dict[int, int] = {}
        for cr, cc in region_cells[label]:
            for dr, dc in offsets:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    other = int(labels[nr, nc])
                    if other != label:
                        neighbour_sizes[other] = region_size[other]
        if not neighbour_sizes:
            continue
        # Largest neighbour wins; ties broken deterministically by label.
        best = max(neighbour_sizes, key=lambda lbl: (neighbour_sizes[lbl], -lbl))
        new_value = region_value[best]
        for cr, cc in region_cells[label]:
            out[cr, cc] = new_value
        # Fold this region into its neighbour so later merges see it merged.
        region_value[label] = new_value
        region_size[best] += region_size[label]

    return out


def cleanup_burnt_mask(
    burnt_mask: NDArray[np.floating],
    *,
    min_patch_pixels: int = 0,
    fill_holes_pixels: int = 0,
) -> NDArray[np.float64]:
    """Remove speckles from and fill holes in a burnt mask.

    Reproduces the upstream cleanup: nodata (``nan``) cells are treated as
    ``0`` for the morphology, small burnt patches (fewer than
    ``min_patch_pixels`` pixels) are removed with :func:`sieve`, then small
    unburnt holes (fewer than ``fill_holes_pixels`` pixels) inside burnt
    areas are filled by sieving the inverted mask. Cells that were nodata in
    the input are restored to ``nan`` in the output.

    Args:
        burnt_mask: A float array where ``nan`` is nodata and finite values
            are ``0`` (unburnt) or ``1``/truthy (burnt).
        min_patch_pixels: Minimum burnt-patch size to keep (``0`` disables
            speckle removal).
        fill_holes_pixels: Minimum unburnt-hole size to keep (``0`` disables
            hole filling).

    Returns:
        A float64 array with ``1.0``/``0.0`` for burnt/unburnt and ``nan``
        preserved where the input was nodata.
    """
    mask = np.asarray(burnt_mask, dtype=np.float64)
    nodata = np.isnan(mask)
    cleaned = np.where(nodata, 0.0, mask)
    cleaned = (cleaned != 0).astype(np.uint8)

    if min_patch_pixels > 0:
        cleaned = sieve(cleaned, size=min_patch_pixels, connectivity=8).astype(np.uint8)
    if fill_holes_pixels > 0:
        inverted = sieve(
            (1 - cleaned).astype(np.uint8),
            size=fill_holes_pixels,
            connectivity=8,
        ).astype(np.uint8)
        cleaned = (1 - inverted).astype(np.uint8)

    result = cleaned.astype(np.float64)
    result[nodata] = np.nan
    return result


def pixel_area_km2(resolution_m: float) -> float:
    """Return the ground area of one square pixel in km^2.

    Args:
        resolution_m: The pixel size in metres.

    Returns:
        ``resolution_m ** 2 / 1_000_000``.
    """
    return (float(resolution_m) ** 2) / 1_000_000.0


def summarize_severity_areas(
    severity_class: NDArray[np.integer],
    resolution_m: float,
) -> dict[str, float]:
    """Return the area (km^2) of each named severity class.

    Args:
        severity_class: A class-code raster (``0..7``).
        resolution_m: The pixel size in metres.

    Returns:
        A mapping of class label -> area in km^2, in ascending code order.
    """
    codes = np.asarray(severity_class)
    area_per_pixel = pixel_area_km2(resolution_m)
    return {
        SEVERITY_CODE_TO_LABEL[code]: float(int((codes == code).sum()) * area_per_pixel)
        for code in SEVERITY_CODE_TO_LABEL
    }


def area_summary(
    delta_nbr: NDArray[np.floating],
    burnt_mask: NDArray[np.floating],
    threshold: float,
    resolution_m: float,
) -> dict[str, float]:
    """Return burnt / unburnt / valid / nodata areas (km^2).

    Mirrors the upstream tallies: ``burnt`` counts truthy cells of the
    cleaned mask; ``valid`` counts finite dNBR cells; ``unburnt`` counts
    finite dNBR cells at or below ``threshold``; ``nodata`` counts ``nan``
    dNBR cells.

    Args:
        delta_nbr: The dNBR array (``nan`` = nodata).
        burnt_mask: The cleaned burnt mask (``nan`` = nodata, else 0/1).
        threshold: The dNBR burnt threshold.
        resolution_m: The pixel size in metres.

    Returns:
        A mapping with ``burnt_area_km2``, ``unburnt_area_km2``,
        ``valid_area_km2`` and ``nodata_area_km2``.
    """
    delta = np.asarray(delta_nbr, dtype=np.float64)
    mask = np.asarray(burnt_mask, dtype=np.float64)
    area_per_pixel = pixel_area_km2(resolution_m)

    finite = ~np.isnan(delta)
    burnt = float(np.nansum(np.where(np.isnan(mask), 0.0, mask)) * area_per_pixel)
    valid = float(int(finite.sum()) * area_per_pixel)
    unburnt = float(int((finite & (delta <= threshold)).sum()) * area_per_pixel)
    nodata = float(int((~finite).sum()) * area_per_pixel)
    return {
        "burnt_area_km2": burnt,
        "unburnt_area_km2": unburnt,
        "valid_area_km2": valid,
        "nodata_area_km2": nodata,
    }
