"""Unit tests for the burnt-area output style data.

``burnt_area_toolbox.algorithms._ba_style`` keeps its value/label/colour
tables free of any ``qgis`` import, so they can be checked here against the
compute core's canonical severity classes without a QGIS runtime. The renderer
builders themselves (``apply_severity_style`` etc.) need QGIS and are exercised
by ``test_qgis_glue`` instead.
"""

from __future__ import annotations

import re

from burnt_area_toolbox.algorithms._ba_style import (
    BURNT_COLOR,
    BURNT_MASK_CLASSES,
    _dnbr_ramp_stops,
)
from burnt_area_toolbox.bacore.constants import SEVERITY_BREAKS, SEVERITY_CLASSES

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_burnt_color_is_valid_hex() -> None:
    """The burnt-mask paint colour is a well-formed hex triple."""
    assert _HEX.match(BURNT_COLOR)


def test_burnt_mask_classes_paint_only_the_burnt_value() -> None:
    """Only value ``1`` (burnt) is rendered; ``0`` is left transparent."""
    values = [value for value, _label, _hex in BURNT_MASK_CLASSES]
    assert values == [1.0]
    _value, label, hex_color = BURNT_MASK_CLASSES[0]
    assert label.strip()
    assert hex_color == BURNT_COLOR


def test_dnbr_ramp_is_anchored_on_the_severity_breaks() -> None:
    """The first six ramp stops sit exactly on the severity break values."""
    stops = _dnbr_ramp_stops()
    stop_values = [value for value, _hex in stops]
    # Six severity breaks + one open-ended high-severity tail stop.
    assert len(stops) == len(SEVERITY_BREAKS) + 1
    assert stop_values[: len(SEVERITY_BREAKS)] == list(SEVERITY_BREAKS)


def test_dnbr_ramp_stops_are_ascending_with_valid_colours() -> None:
    """Ramp stops climb monotonically and every colour is valid hex."""
    stops = _dnbr_ramp_stops()
    values = [value for value, _hex in stops]
    assert values == sorted(values)
    assert all(_HEX.match(hex_color) for _value, hex_color in stops)


def test_severity_classes_are_the_seven_expected_codes() -> None:
    """The severity table the palette is built from covers codes 1..7."""
    codes = [cls.code for cls in SEVERITY_CLASSES]
    assert codes == [1, 2, 3, 4, 5, 6, 7]
    assert all(cls.label.strip() for cls in SEVERITY_CLASSES)
    assert all(_HEX.match(cls.color) for cls in SEVERITY_CLASSES)
