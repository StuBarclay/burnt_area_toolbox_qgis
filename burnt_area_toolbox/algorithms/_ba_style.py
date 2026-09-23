"""Default styling for the burnt-area output rasters.

Three renderers are offered, matching the three rasters a run can load into a
project:

* :func:`apply_severity_style` -- a categorised palette for the seven-class
  dNBR severity raster, taken straight from
  :data:`burnt_area_toolbox.bacore.constants.SEVERITY_CLASSES` so the colours
  and labels are the single source of truth shared with the manifest.
* :func:`apply_dnbr_style` -- a continuous blue-to-red pseudocolour ramp for the
  raw dNBR raster, with its break values anchored on the same severity breaks.
* :func:`apply_burnt_mask_style` -- a two-class palette painting burnt pixels in
  the burnt-orange the upstream project uses and leaving unburnt/nodata clear.

The colour/label tables are plain data with no ``qgis`` import at module load,
so they can be unit-tested outside QGIS. Each renderer imports the ``qgis``
runtime lazily, only when actually called inside QGIS.
"""

from __future__ import annotations

from typing import Any

from burnt_area_toolbox.bacore.constants import SEVERITY_BREAKS, SEVERITY_CLASSES

#: Hex colour burnt pixels are painted in the burnt-mask style (matches the
#: upstream ``burnt_area_mapping`` burnt colour).
BURNT_COLOR = "#d95f0e"

#: The two burnt-mask classes as ``(value, label, hex)``. ``0`` (unburnt) is
#: left transparent by omitting it from the rendered classes.
BURNT_MASK_CLASSES: tuple[tuple[float, str, str], ...] = ((1.0, "Burnt", BURNT_COLOR),)


def apply_severity_style(layer: Any) -> bool:
    """Apply the seven-class dNBR severity palette to a loaded raster layer.

    Builds a :class:`QgsPalettedRasterRenderer` from
    :data:`~burnt_area_toolbox.bacore.constants.SEVERITY_CLASSES`. Pixels that
    match no class (code ``0`` = nodata / outside the valid area) are left
    unrendered (transparent).

    Args:
        layer: The raster layer to style. ``None`` or a non-raster/invalid
            layer is ignored so callers need not pre-check.

    Returns:
        ``True`` if the palette was applied, ``False`` otherwise.
    """
    from qgis.core import QgsPalettedRasterRenderer, QgsRasterLayer
    from qgis.PyQt.QtGui import QColor

    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    provider = layer.dataProvider()
    if provider is None:
        return False
    classes = [
        QgsPalettedRasterRenderer.Class(float(cls.code), QColor(cls.color), cls.label)
        for cls in SEVERITY_CLASSES
    ]
    renderer = QgsPalettedRasterRenderer(provider, 1, classes)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True


def apply_burnt_mask_style(layer: Any) -> bool:
    """Paint burnt pixels in burnt-orange and leave unburnt/nodata transparent.

    Args:
        layer: The burnt-mask raster layer to style.

    Returns:
        ``True`` if the palette was applied, ``False`` otherwise.
    """
    from qgis.core import QgsPalettedRasterRenderer, QgsRasterLayer
    from qgis.PyQt.QtGui import QColor

    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    provider = layer.dataProvider()
    if provider is None:
        return False
    classes = [
        QgsPalettedRasterRenderer.Class(value, QColor(hex_color), label)
        for value, label, hex_color in BURNT_MASK_CLASSES
    ]
    renderer = QgsPalettedRasterRenderer(provider, 1, classes)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True


#: Interpolated dNBR colour stops as ``(dnbr, hex)`` pairs, spanning the range
#: the severity classes cover. Blue/green for negative dNBR (regrowth /
#: unburned), through yellow at the low-severity onset, to dark red for the
#: high-severity tail -- anchored on
#: :data:`~burnt_area_toolbox.bacore.constants.SEVERITY_BREAKS`.
def _dnbr_ramp_stops() -> tuple[tuple[float, str], ...]:
    """Return the dNBR pseudocolour stops anchored on the severity breaks."""
    low, mod_low, unburned_hi, low_sev, mod_low_sev, high = SEVERITY_BREAKS
    return (
        (low, "#2c7bb6"),  # < -0.25 enhanced regrowth (high)
        (mod_low, "#abd9e9"),  # -0.25 enhanced regrowth (low)
        (unburned_hi, "#1a9850"),  # -0.10 unburned
        (low_sev, "#ffffbf"),  # 0.10 low severity onset
        (mod_low_sev, "#fdae61"),  # 0.27 moderate-low
        (high, "#d7191c"),  # 0.44 moderate-high
        (high + 0.34, "#7f0000"),  # ~0.78 high severity tail
    )


def apply_dnbr_style(layer: Any) -> bool:
    """Apply a continuous blue-to-red pseudocolour ramp to a dNBR raster.

    The ramp's break values are anchored on
    :data:`~burnt_area_toolbox.bacore.constants.SEVERITY_BREAKS`, so the visual
    transitions line up with the categorical severity classes. Nodata (``nan``)
    cells are left transparent.

    Args:
        layer: The dNBR raster layer to style.

    Returns:
        ``True`` if the ramp was applied, ``False`` otherwise.
    """
    from qgis.core import (
        QgsColorRampShader,
        QgsRasterLayer,
        QgsRasterShader,
        QgsSingleBandPseudoColorRenderer,
    )
    from qgis.PyQt.QtGui import QColor

    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    provider = layer.dataProvider()
    if provider is None:
        return False

    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Interpolated)
    items = [
        QgsColorRampShader.ColorRampItem(value, QColor(hex_color), f"{value:g}")
        for value, hex_color in _dnbr_ramp_stops()
    ]
    ramp.setColorRampItemList(items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True
