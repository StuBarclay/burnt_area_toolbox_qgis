"""Graphical dialog wrapper for the burnt-area toolbox.

The dialog is a thin front-end: it gathers a handful of friendly inputs and
delegates to the registered Processing algorithms
(``burntarea:dnbrfromrasters`` and ``burntarea:dnbrfromstac``), so there is
exactly one implementation of the calculation. Its import lives behind
:mod:`burnt_area_toolbox.plugin` and is only pulled in when the user opens the
dialog, keeping the Processing-only load path free of Qt-widget imports.
"""
