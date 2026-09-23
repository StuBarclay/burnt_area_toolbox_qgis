"""QGIS plugin wiring: register the Processing provider and the GUI dialog.

Kept deliberately thin. All dNBR computation lives in
:mod:`burnt_area_toolbox.bacore`; the Processing algorithms live in
:mod:`burnt_area_toolbox.algorithms`; this module only connects them to the
QGIS application (a Processing provider plus a Plugins-menu action that opens
the friendly dialog).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from burnt_area_toolbox.provider import BurntAreaProvider

if TYPE_CHECKING:
    from burnt_area_toolbox.gui.dialog import BurntAreaDialog


class BurntAreaToolboxPlugin:
    """Top-level plugin object instantiated by ``classFactory``."""

    def __init__(self, iface) -> None:
        """Store the QGIS interface and prepare empty handles.

        Args:
            iface: The ``QgisInterface`` handed to the plugin by QGIS.
        """
        self.iface = iface
        self.provider: BurntAreaProvider | None = None
        self._action: QAction | None = None
        self._dialog: BurntAreaDialog | None = None

    # -- lifecycle --------------------------------------------------------
    def initProcessing(self) -> None:
        """Create and register the Processing provider."""
        self.provider = BurntAreaProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self) -> None:
        """Register the provider and add the toolbar/menu action."""
        self.initProcessing()

        icon_path = Path(__file__).with_name("icon.svg")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._action = QAction(icon, "Burnt Area Toolbox…", self.iface.mainWindow())
        self._action.setObjectName("burntAreaToolboxOpenDialog")
        self._action.triggered.connect(self._open_dialog)
        self.iface.addPluginToMenu("&Burnt Area Toolbox", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self) -> None:
        """Remove the action and deregister the Processing provider."""
        if self._action is not None:
            self.iface.removePluginMenu("&Burnt Area Toolbox", self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # -- actions ----------------------------------------------------------
    def _open_dialog(self) -> None:
        """Open (creating on first use) the friendly burnt-area dialog."""
        # Imported lazily so a headless/Processing-only load never needs Qt
        # widgets, and so an import error in the dialog cannot break the
        # Processing provider registration above.
        from burnt_area_toolbox.gui.dialog import BurntAreaDialog

        if self._dialog is None:
            self._dialog = BurntAreaDialog(self.iface, self.iface.mainWindow())
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
