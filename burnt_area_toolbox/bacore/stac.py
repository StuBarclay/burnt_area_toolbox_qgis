"""A dependency-free STAC client and Cloud-Optimised GeoTIFF scene loader.

The STAC algorithm needs to (a) search a STAC API for Sentinel-2 items in a
date window and area, and (b) pull the required bands out of each item's
Cloud-Optimised GeoTIFF assets onto the common analysis grid. The upstream
project used ``pystac-client`` and ``odc-stac`` for this; to keep the plugin
self-contained on what QGIS already ships, the search here is a small
:mod:`urllib` client against the STAC API ``/search`` endpoint, and the band
reads go through :func:`~burnt_area_toolbox.bacore.raster.read_source_on_grid`
(GDAL ``/vsicurl`` windowed warps) so only the AOI window of each remote scene
is fetched.

Everything at module load is import-light (standard library plus the
GDAL-free :class:`~burnt_area_toolbox.bacore.scenes.Scene`), so the search
logic can be unit tested by stubbing :func:`urllib.request.urlopen`; GDAL only
enters when a scene's pixels are actually read.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .constants import BAND_CANDIDATES, NBR_BANDS
from .scenes import BurntAreaError, Scene

if TYPE_CHECKING:
    from .raster import RasterGrid

#: Default network timeout, in seconds, for STAC API calls.
_DEFAULT_TIMEOUT = 60.0

#: Hard cap on pages followed, so a misbehaving ``next`` link cannot loop.
_MAX_PAGES = 50


def _as_rfc3339(value: str, *, end_of_day: bool) -> str:
    """Return ``value`` as an RFC 3339 UTC timestamp.

    A bare ``YYYY-MM-DD`` date is widened to a full instant -- the first
    moment of the day (``T00:00:00Z``), or the last (``T23:59:59Z``) when
    ``end_of_day`` -- because STAC servers validate ``datetime`` against RFC
    3339 and reject date-only values. A value that already carries a time is
    returned as-is, gaining a trailing ``Z`` only if it has no explicit zone.
    """
    text = value.strip()
    if "T" not in text:
        return f"{text}T23:59:59Z" if end_of_day else f"{text}T00:00:00Z"
    time_part = text[text.index("T") + 1 :]
    if text.endswith("Z") or "+" in time_part or "-" in time_part:
        return text
    return f"{text}Z"


def _rfc3339_datetime_range(start_date: str, end_date: str) -> str:
    """Build a STAC ``datetime`` interval string, normalised to RFC 3339.

    Earth Search v1 (stac-server) validates the ``datetime`` query value and
    rejects bare ``YYYY-MM-DD`` bounds with ``HTTP 400 ... does not match
    RFC3339 format``. The inclusive date window is therefore expressed as full
    UTC timestamps covering the whole start and end days, joined with ``/`` per
    the STAC / OGC interval syntax.
    """
    start = _as_rfc3339(start_date, end_of_day=False)
    end = _as_rfc3339(end_date, end_of_day=True)
    return f"{start}/{end}"


@dataclass(slots=True)
class StacItem:
    """A minimal view of a STAC item: its id, date, cloud cover and assets.

    Attributes:
        item_id: The STAC item id.
        datetime: The acquisition timestamp string, or ``None``.
        cloud_cover: The item's ``eo:cloud_cover`` percentage, or ``None``.
        assets: A mapping of asset name to its href (download URL).
    """

    item_id: str
    datetime: str | None
    cloud_cover: float | None
    assets: dict[str, str] = field(default_factory=dict)

    @property
    def observation_date(self) -> str | None:
        """Return the acquisition date as ``"YYYY-MM-DD"`` (or ``None``)."""
        return None if not self.datetime else self.datetime[:10]


def _request_json(url: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    """POST (or GET when ``body`` is ``None``) a URL and parse the JSON reply.

    Args:
        url: The endpoint URL.
        body: A JSON body to POST, or ``None`` for a GET.
        timeout: The socket timeout, in seconds.

    Returns:
        The parsed JSON object.

    Raises:
        BurntAreaError: On any network/HTTP/JSON error.
    """
    headers = {"Accept": "application/json", "User-Agent": "burnt-area-toolbox-qgis"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = exc.read().decode("utf-8", "replace")[:500] if hasattr(exc, "read") else ""
        raise BurntAreaError(f"STAC request to {url} failed: HTTP {exc.code}. {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise BurntAreaError(f"STAC request to {url} failed: {exc.reason}.") from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BurntAreaError(f"STAC response from {url} was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise BurntAreaError(f"STAC response from {url} was not a JSON object.")
    return parsed


def _parse_feature(feature: dict[str, Any]) -> StacItem:
    """Turn one STAC ``Feature`` mapping into a :class:`StacItem`."""
    properties = feature.get("properties") or {}
    cloud_cover = properties.get("eo:cloud_cover")
    assets: dict[str, str] = {}
    for name, asset in (feature.get("assets") or {}).items():
        href = asset.get("href") if isinstance(asset, dict) else None
        if href:
            assets[str(name)] = str(href)
    return StacItem(
        item_id=str(feature.get("id", "")),
        datetime=properties.get("datetime"),
        cloud_cover=None if cloud_cover is None else float(cloud_cover),
        assets=assets,
    )


def _next_page(
    links: list[dict[str, Any]], search_url: str, body: dict[str, Any]
) -> tuple[str, dict[str, Any] | None] | None:
    """Return the ``(url, body)`` for the next page, or ``None`` if last.

    Handles both GET and POST paginated ``next`` links, including the STAC API
    ``merge`` convention where the link body is merged into the original
    search body.
    """
    for link in links:
        if str(link.get("rel")) != "next":
            continue
        href = str(link.get("href") or search_url)
        method = str(link.get("method", "GET")).upper()
        if method == "POST":
            merged = dict(body)
            if link.get("merge"):
                merged.update(link.get("body") or {})
            else:
                merged = dict(link.get("body") or body)
            return href, merged
        return href, None
    return None


def search_items(
    catalog_url: str,
    collection: str,
    start_date: str,
    end_date: str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    intersects: dict[str, Any] | None = None,
    max_cloud_cover: float = 100.0,
    limit: int = 100,
    max_items: int | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[StacItem]:
    """Search a STAC API for items in a date window and area.

    Mirrors the upstream query: the given ``collection`` and ``datetime``
    range, an ``eo:cloud_cover < max_cloud_cover`` filter, and either a
    ``bbox`` or an ``intersects`` geometry. Results are returned sorted by
    ``(datetime, id)``, matching the deterministic ordering the analysis
    relies on.

    Args:
        catalog_url: The STAC API root (e.g. Earth Search v1).
        collection: The collection id to search (e.g. ``sentinel-2-l2a``).
        start_date: The inclusive window start, ``"YYYY-MM-DD"``.
        end_date: The inclusive window end, ``"YYYY-MM-DD"``.
        bbox: A lon/lat ``(min_lon, min_lat, max_lon, max_lat)`` box.
        intersects: A GeoJSON geometry to intersect (used instead of ``bbox``).
        max_cloud_cover: The upper bound on scene cloud cover (percent).
        limit: The page size to request.
        max_items: An optional cap on the number of items returned.
        timeout: The per-request socket timeout, in seconds.

    Returns:
        The matching items, sorted by ``(datetime, id)``.

    Raises:
        BurntAreaError: On a network error or when no items match.
    """
    search_url = f"{catalog_url.rstrip('/')}/search"
    body: dict[str, Any] = {
        "collections": [collection],
        "datetime": _rfc3339_datetime_range(start_date, end_date),
        "query": {"eo:cloud_cover": {"lt": max_cloud_cover}},
        "limit": int(limit),
    }
    if intersects is not None:
        body["intersects"] = intersects
    elif bbox is not None:
        body["bbox"] = [float(v) for v in bbox]
    else:
        raise ValueError("search_items requires either a bbox or an intersects geometry.")

    items: list[StacItem] = []
    url: str = search_url
    request_body: dict[str, Any] | None = body
    for _page in range(_MAX_PAGES):
        payload = _request_json(url, request_body, timeout)
        for feature in payload.get("features") or []:
            items.append(_parse_feature(feature))
            if max_items is not None and len(items) >= max_items:
                break
        if max_items is not None and len(items) >= max_items:
            break
        nxt = _next_page(payload.get("links") or [], search_url, body)
        if nxt is None:
            break
        url, request_body = nxt

    if not items:
        raise BurntAreaError(
            f"No STAC items found for '{collection}' between {start_date} and {end_date}. "
            "Widen the date window, raise max_cloud_cover, or check the area of interest."
        )
    items.sort(key=lambda item: (item.datetime or "", item.item_id))
    return items


def select_scenes(items: list[StacItem], limit: int | None) -> list[StacItem]:
    """Return at most ``limit`` scenes, keeping the clearest ones.

    Downloading every matching scene can be slow and bandwidth-heavy, so a run
    may cap how many are fetched. When a cap applies, the scenes with the lowest
    ``eo:cloud_cover`` are kept (items with no reported cloud cover rank last),
    then the kept scenes are returned in chronological ``(datetime, id)`` order
    so the downstream compositing still sees them in time order.

    Args:
        items: The candidate scenes (typically already date-sorted).
        limit: The maximum number of scenes to keep. ``None`` or a value less
            than ``1`` means no cap; a value at or above ``len(items)`` returns
            the list unchanged.

    Returns:
        The retained scenes, in chronological order.
    """
    if limit is None or limit < 1 or limit >= len(items):
        return list(items)
    ranked = sorted(
        items,
        key=lambda item: (
            item.cloud_cover is None,
            item.cloud_cover if item.cloud_cover is not None else 0.0,
            item.datetime or "",
            item.item_id,
        ),
    )
    chosen = ranked[:limit]
    chosen.sort(key=lambda item: (item.datetime or "", item.item_id))
    return chosen


def resolve_band_assets(item: StacItem, required: list[str]) -> dict[str, str]:
    """Map each required logical band to the item's matching asset href.

    Uses :data:`~burnt_area_toolbox.bacore.constants.BAND_CANDIDATES` to try
    the common asset aliases in preference order, mirroring the upstream band
    discovery.

    Args:
        item: The STAC item to resolve assets on.
        required: The logical band names needed (e.g. ``["nir", "swir22"]``).

    Returns:
        A mapping of logical band name to asset href.

    Raises:
        BurntAreaError: If any required band has no matching asset.
    """
    mapping: dict[str, str] = {}
    for logical in required:
        for candidate in BAND_CANDIDATES[logical]:
            if candidate in item.assets:
                mapping[logical] = item.assets[candidate]
                break
        else:
            raise BurntAreaError(
                f"Item '{item.item_id}' has no '{logical}' asset. "
                f"Available assets: {sorted(item.assets)}"
            )
    return mapping


def _vsicurl(href: str) -> str:
    """Return a GDAL-openable descriptor for a (possibly remote) href."""
    if href.startswith(("http://", "https://")):
        return f"/vsicurl/{href}"
    return href


def load_scene(
    item: StacItem,
    grid: RasterGrid,
    *,
    bands: tuple[str, ...] = NBR_BANDS,
    include_scl: bool = True,
) -> Scene:
    """Load one STAC item's bands onto ``grid`` as a :class:`Scene`.

    Reflectance bands are read with bilinear resampling and the SCL with
    nearest-neighbour (preserving its class codes). Each read is a windowed
    GDAL ``/vsicurl`` warp, so only the grid's footprint of the remote COG is
    transferred.

    Args:
        item: The STAC item to load.
        grid: The destination analysis grid.
        bands: The logical reflectance bands to read (defaults to the NBR
            pair ``("nir", "swir22")``).
        include_scl: Whether to also read the Scene Classification Layer.

    Returns:
        The loaded :class:`Scene`.
    """
    from .raster import read_source_on_grid

    required = list(bands) + (["scl"] if include_scl else [])
    assets = resolve_band_assets(item, required)
    scene_bands = {
        name: read_source_on_grid(_vsicurl(assets[name]), grid, resampling="bilinear")
        for name in bands
    }
    scl = None
    if include_scl:
        scl = read_source_on_grid(_vsicurl(assets["scl"]), grid, resampling="nearest")
    return Scene(
        bands=scene_bands,
        scl=scl,
        scene_id=item.item_id,
        observation_date=item.observation_date,
        cloud_cover=item.cloud_cover,
    )


def load_scenes(
    items: list[StacItem],
    grid: RasterGrid,
    *,
    bands: tuple[str, ...] = NBR_BANDS,
    include_scl: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[Scene]:
    """Load every item in ``items`` onto ``grid`` (see :func:`load_scene`).

    Args:
        items: The STAC items to load.
        grid: The destination analysis grid.
        bands: The logical reflectance bands to read.
        include_scl: Whether to also read the SCL.
        progress: An optional ``(done, total)`` callback invoked after each
            scene, so the GUI can report progress and honour cancellation.

    Returns:
        The loaded scenes, in the order of ``items``.
    """
    total = len(items)
    loaded: list[Scene] = []
    for index, item in enumerate(items):
        loaded.append(load_scene(item, grid, bands=bands, include_scl=include_scl))
        if progress is not None:
            progress(index + 1, total)
    return loaded
