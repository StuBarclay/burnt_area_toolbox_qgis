"""Tests for the glue-level feedback adapters.

These exercise :mod:`burnt_area_toolbox.algorithms._progress`, which is
deliberately ``qgis``-free (it needs only the duck-typed feedback protocol:
``pushInfo`` / ``setProgress`` / ``setProgressText`` / ``isCanceled``), so the
whole module runs in the plain sandbox and in CI. A minimal fake feedback
records the calls each adapter makes.
"""

from __future__ import annotations

import pytest
from burnt_area_toolbox.algorithms._progress import (
    make_cancel,
    make_message,
    make_scene_progress,
)


class FakeFeedback:
    """A minimal ``QgsProcessingFeedback`` stand-in that records calls."""

    def __init__(self, *, cancelled: bool = False) -> None:
        self.info: list[str] = []
        self.progress: list[float] = []
        self.texts: list[str] = []
        self._cancelled = cancelled

    def pushInfo(self, text: str) -> None:  # noqa: N802 - QGIS feedback name
        self.info.append(text)

    def setProgress(self, value: float) -> None:  # noqa: N802 - QGIS name
        self.progress.append(value)

    def setProgressText(self, text: str) -> None:  # noqa: N802 - QGIS name
        self.texts.append(text)

    def isCanceled(self) -> bool:  # noqa: N802 - QGIS name
        return self._cancelled


def test_make_message_pushes_info_lines() -> None:
    """The message callable forwards each string to ``pushInfo``."""
    feedback = FakeFeedback()
    message = make_message(feedback)
    message("hello")
    message("world")
    assert feedback.info == ["hello", "world"]


def test_make_message_tolerates_none_feedback() -> None:
    """A ``None`` feedback yields a no-op message callable."""
    message = make_message(None)
    message("ignored")  # must not raise


def test_scene_progress_interpolates_between_start_and_end() -> None:
    """Progress advances linearly from ``start`` to ``end`` across the scenes."""
    feedback = FakeFeedback()
    scene_progress = make_scene_progress(feedback, start=10.0, end=90.0)

    scene_progress(0, 4)
    scene_progress(2, 4)
    scene_progress(4, 4)

    assert feedback.progress[0] == pytest.approx(10.0)
    assert feedback.progress[1] == pytest.approx(50.0)
    assert feedback.progress[-1] == pytest.approx(90.0)
    assert feedback.texts[-1] == "Loaded 4 of 4 scenes"


def test_scene_progress_ignores_zero_total() -> None:
    """A zero total does not divide-by-zero or emit a progress value."""
    feedback = FakeFeedback()
    scene_progress = make_scene_progress(feedback)
    scene_progress(0, 0)
    assert feedback.progress == []
    # The progress-text hook is still allowed to fire.
    assert feedback.texts == ["Loaded 0 of 0 scenes"]


def test_scene_progress_tolerates_partial_feedback() -> None:
    """A feedback object missing the optional hooks does not break."""

    class Bare:
        """Exposes neither ``setProgress`` nor ``setProgressText``."""

    make_scene_progress(Bare())(1, 2)  # must not raise


def test_make_cancel_reflects_feedback_state() -> None:
    """The predicate mirrors the feedback's ``isCanceled`` result."""
    assert make_cancel(FakeFeedback(cancelled=False))() is False
    assert make_cancel(FakeFeedback(cancelled=True))() is True


def test_make_cancel_defaults_false_without_feedback() -> None:
    """A ``None`` feedback yields a predicate that is always false."""
    assert make_cancel(None)() is False
