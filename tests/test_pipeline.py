"""Sandbox tests for the GDAL-free pipeline helpers (scene cap + progress).

The heavy :func:`run_stac` / :func:`run_from_rasters` entry points reach GDAL
(and, in QGIS, the network), so they are exercised elsewhere. These tests cover
the two small pure-Python helpers that shape a run: the per-window scene cap and
the progress re-basing that keeps a single bar monotonic across both loading
phases. ``pipeline`` imports without a GDAL install, so both run in the sandbox.
"""

from __future__ import annotations

from burnt_area_toolbox.bacore import pipeline
from burnt_area_toolbox.bacore.stac import StacItem


def _items(count: int) -> list[StacItem]:
    """Return ``count`` scenes with ascending dates and cloud cover 0..count-1."""
    return [
        StacItem(chr(ord("a") + i), f"2020-01-0{i + 1}T00:00:00Z", float(i), {})
        for i in range(count)
    ]


def test_offset_progress_rebases_done_and_total() -> None:
    calls: list[tuple[int, int]] = []
    callback = pipeline._offset_progress(
        lambda done, total: calls.append((done, total)), offset=3, total=5
    )
    assert callback is not None
    callback(1, 2)
    callback(2, 2)
    # done is offset past the first phase; total is the combined total.
    assert calls == [(4, 5), (5, 5)]


def test_offset_progress_none_passes_through() -> None:
    assert pipeline._offset_progress(None, 0, 4) is None


def test_cap_scenes_no_cap_returns_same_list_without_a_message() -> None:
    messages: list[str] = []
    items = _items(3)
    assert pipeline._cap_scenes(items, None, "baseline", messages.append) is items
    assert pipeline._cap_scenes(items, 5, "baseline", messages.append) is items
    assert messages == []


def test_cap_scenes_keeps_clearest_and_notes() -> None:
    messages: list[str] = []
    items = _items(4)  # cloud cover 0, 1, 2, 3
    result = pipeline._cap_scenes(items, 2, "post-fire", messages.append)
    # The two least-cloudy scenes, returned in date order.
    assert [item.item_id for item in result] == ["a", "b"]
    assert len(messages) == 1
    assert "least-cloudy post-fire" in messages[0]
