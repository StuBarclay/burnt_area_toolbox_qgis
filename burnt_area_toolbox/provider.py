"""The QGIS Processing provider that registers the burnt-area algorithms."""

from __future__ import annotations

from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from burnt_area_toolbox.algorithms import _algorithms


class BurntAreaProvider(QgsProcessingProvider):
    """Groups the burnt-area toolbox algorithms under one Processing provider."""

    def id(self) -> str:
        return "burntarea"

    def name(self) -> str:
        return "Burnt Area Toolbox (dNBR)"

    def longName(self) -> str:
        return "Burnt Area Toolbox — Sentinel-2 dNBR burn severity"

    def icon(self) -> QIcon:
        icon_path = Path(__file__).with_name("icon.svg")
        if icon_path.exists():
            return QIcon(str(icon_path))
        return super().icon()

    def loadAlgorithms(self) -> None:
        for algorithm in _algorithms():
            self.addAlgorithm(algorithm)
