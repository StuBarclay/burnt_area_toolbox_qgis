"""burnt_area_toolbox -- QGIS plugin: Sentinel-2 dNBR burnt-area / burn-severity mapping.

Top-level plugin package. It is deliberately kept import-light: importing
``burnt_area_toolbox`` must NOT pull in PyQt or the ``qgis`` runtime, so the
pure-Python compute core (:mod:`burnt_area_toolbox.bacore`) stays importable
and testable outside QGIS. QGIS constructs the plugin by calling
:func:`classFactory`, which imports the GUI/wiring layer lazily.
"""

from __future__ import annotations

import sys

#: Plugin version (kept in sync with ``metadata.txt``).
__version__ = "0.1.1"

#: The compute core uses ``@dataclass(slots=True)`` and ``zip(..., strict=True)``,
#: both introduced in Python 3.10. QGIS bundles this interpreter, so this maps
#: to a minimum QGIS of ~3.34 (which ships Python 3.12); older builds such as
#: 3.22/3.28 on Windows still ship Python 3.9.
_MIN_PYTHON = (3, 10)


def classFactory(iface):  # noqa: N802 - name mandated by the QGIS plugin API
    """Construct and return the plugin instance.

    This is the entry point QGIS calls when the plugin is loaded.

    Args:
        iface: The :class:`qgis.gui.QgisInterface` instance QGIS passes to
            every plugin.

    Returns:
        A :class:`burnt_area_toolbox.plugin.BurntAreaToolboxPlugin`.

    Raises:
        RuntimeError: If QGIS is running on Python older than 3.10, with a
            clear message instead of a cryptic error from the core.
    """
    if sys.version_info < _MIN_PYTHON:
        have = ".".join(str(v) for v in sys.version_info[:3])
        need = ".".join(str(v) for v in _MIN_PYTHON)
        raise RuntimeError(
            f"Burnt Area Toolbox requires Python {need}+ but this QGIS is "
            f"running Python {have}. Please use QGIS 3.34 LTR or newer (which "
            "bundles a compatible Python)."
        )

    from burnt_area_toolbox.plugin import BurntAreaToolboxPlugin

    return BurntAreaToolboxPlugin(iface)
