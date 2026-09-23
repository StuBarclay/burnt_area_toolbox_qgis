"""Sandbox tests for the urllib STAC client (network stubbed, no GDAL)."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest
from burnt_area_toolbox.bacore import stac
from burnt_area_toolbox.bacore.scenes import BurntAreaError
from burnt_area_toolbox.bacore.stac import StacItem


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _feature(item_id: str, dt: str, cloud: float, assets: dict[str, str]) -> dict[str, Any]:
    return {
        "id": item_id,
        "properties": {"datetime": dt, "eo:cloud_cover": cloud},
        "assets": {name: {"href": href} for name, href in assets.items()},
    }


def test_search_items_parses_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "features": [
            _feature("b", "2020-01-08T00:00:00Z", 5.0, {"nir": "b_nir.tif"}),
            _feature("a", "2020-01-07T00:00:00Z", 1.0, {"nir": "a_nir.tif"}),
        ],
        "links": [],
    }

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    items = stac.search_items(
        "https://api.test/v1",
        "sentinel-2-l2a",
        "2020-01-06",
        "2020-02-04",
        bbox=(150.0, -33.0, 151.0, -32.0),
        max_cloud_cover=30.0,
    )

    assert [item.item_id for item in items] == ["a", "b"]  # sorted by datetime
    assert items[0].observation_date == "2020-01-07"
    assert items[0].cloud_cover == 1.0


def test_search_items_sends_rfc3339_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the ``datetime`` sent to the API is a full RFC 3339 interval.

    Earth Search v1 rejects bare ``YYYY-MM-DD`` bounds with HTTP 400
    ("does not match RFC3339 format"), so ``search_items`` must widen the
    inclusive date window to whole-day UTC timestamps joined with ``/``.
    """
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        feature = _feature("a", "2019-01-07T00:00:00Z", 1.0, {"nir": "a.tif"})
        return _FakeResponse({"features": [feature], "links": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    stac.search_items(
        "https://api.test/v1",
        "sentinel-2-l2a",
        "2018-11-30",
        "2019-11-29",
        bbox=(150.0, -33.0, 151.0, -32.0),
    )

    assert captured["body"]["datetime"] == "2018-11-30T00:00:00Z/2019-11-29T23:59:59Z"


def test_rfc3339_datetime_range_expands_bare_dates() -> None:
    """A ``YYYY-MM-DD`` window becomes a whole-day UTC interval joined with ``/``."""
    assert (
        stac._rfc3339_datetime_range("2018-11-30", "2019-11-29")
        == "2018-11-30T00:00:00Z/2019-11-29T23:59:59Z"
    )


def test_as_rfc3339_passes_through_existing_timestamps() -> None:
    """Values that already carry a time are preserved; a missing zone gains ``Z``."""
    # Already zoned -> unchanged.
    assert stac._as_rfc3339("2020-01-06T12:30:00Z", end_of_day=False) == "2020-01-06T12:30:00Z"
    offset = "2020-01-06T12:30:00+10:00"
    assert stac._as_rfc3339(offset, end_of_day=True) == offset
    # Naive datetime (no zone) -> gains a trailing Z.
    assert stac._as_rfc3339("2020-01-06T12:30:00", end_of_day=False) == "2020-01-06T12:30:00Z"
    # Bare date -> widened to the correct edge of the day.
    assert stac._as_rfc3339("2020-01-06", end_of_day=False) == "2020-01-06T00:00:00Z"
    assert stac._as_rfc3339("2020-01-06", end_of_day=True) == "2020-01-06T23:59:59Z"


def test_search_items_follows_get_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = {
        "features": [_feature("a", "2020-01-07T00:00:00Z", 1.0, {"nir": "a.tif"})],
        "links": [{"rel": "next", "href": "https://api.test/v1/search?page=2", "method": "GET"}],
    }
    page2 = {
        "features": [_feature("b", "2020-01-08T00:00:00Z", 2.0, {"nir": "b.tif"})],
        "links": [],
    }

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        url = request.full_url if hasattr(request, "full_url") else str(request)
        return _FakeResponse(page2 if url.endswith("page=2") else page1)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    items = stac.search_items(
        "https://api.test/v1",
        "sentinel-2-l2a",
        "2020-01-06",
        "2020-02-04",
        bbox=(150.0, -33.0, 151.0, -32.0),
    )
    assert [item.item_id for item in items] == ["a", "b"]


def test_search_items_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        return _FakeResponse({"features": [], "links": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(BurntAreaError):
        stac.search_items(
            "https://api.test/v1",
            "sentinel-2-l2a",
            "2020-01-06",
            "2020-02-04",
            bbox=(150.0, -33.0, 151.0, -32.0),
        )


def test_select_scenes_keeps_least_cloudy_in_date_order() -> None:
    items = [
        StacItem("a", "2020-01-01T00:00:00Z", 40.0, {}),
        StacItem("b", "2020-01-02T00:00:00Z", 5.0, {}),
        StacItem("c", "2020-01-03T00:00:00Z", 20.0, {}),
    ]
    # Keeps the two clearest (b=5, c=20), drops a=40, returned in date order.
    assert [item.item_id for item in stac.select_scenes(items, 2)] == ["b", "c"]


def test_select_scenes_ranks_missing_cloud_cover_last() -> None:
    items = [
        StacItem("a", "2020-01-01T00:00:00Z", None, {}),
        StacItem("b", "2020-01-02T00:00:00Z", 50.0, {}),
    ]
    assert [item.item_id for item in stac.select_scenes(items, 1)] == ["b"]


def test_select_scenes_no_cap_returns_all() -> None:
    items = [
        StacItem("a", "2020-01-01T00:00:00Z", 1.0, {}),
        StacItem("b", "2020-01-02T00:00:00Z", 2.0, {}),
    ]
    for limit in (None, 0, -1, 2, 99):
        result = stac.select_scenes(items, limit)
        assert [item.item_id for item in result] == ["a", "b"]
    # A no-cap result is always a fresh list, never the caller's own.
    assert stac.select_scenes(items, None) is not items


def test_resolve_band_assets_prefers_candidate_order() -> None:
    item = StacItem(
        item_id="x",
        datetime="2020-01-07T00:00:00Z",
        cloud_cover=1.0,
        assets={"nir08": "nir.tif", "swir22": "swir.tif", "scl": "scl.tif"},
    )
    resolved = stac.resolve_band_assets(item, ["nir", "swir22", "scl"])
    assert resolved == {"nir": "nir.tif", "swir22": "swir.tif", "scl": "scl.tif"}


def test_resolve_band_assets_raises_on_missing() -> None:
    item = StacItem(item_id="x", datetime=None, cloud_cover=None, assets={"nir": "nir.tif"})
    with pytest.raises(BurntAreaError):
        stac.resolve_band_assets(item, ["nir", "swir22"])


def test_vsicurl_wraps_remote_only() -> None:
    assert stac._vsicurl("https://host/a.tif") == "/vsicurl/https://host/a.tif"
    assert stac._vsicurl("/local/a.tif") == "/local/a.tif"
