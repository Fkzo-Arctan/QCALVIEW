# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ._i18n import tr
from ._compat import QC, dialog_exec
"""QCALVIEW — gestion des vues schématiques sans photographie.

La photographie n'est pas une condition du modèle caméra : une vue peut être
rendue à partir des paramètres du PDV sur un fond global opaque ou transparent.
Les préférences de fond sont globales (QSettings), jamais stockées par feature.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QBrush
from qgis.PyQt.QtWidgets import QColorDialog

_BG_KEY = "QCALVIEW/schematic/background_color"
_ALPHA_KEY = "QCALVIEW/schematic/background_transparent"
_DEFAULT_BG = "#f2f2f2"


def _as_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _schematic_background_color(self):
    settings = getattr(self, "_settings", None)
    raw = settings.value(_BG_KEY, _DEFAULT_BG) if settings is not None else _DEFAULT_BG
    c = QColor(str(raw or _DEFAULT_BG))
    if not c.isValid():
        c = QColor(_DEFAULT_BG)
    c.setAlpha(255)
    return c


def _schematic_background_transparent(self):
    settings = getattr(self, "_settings", None)
    raw = settings.value(_ALPHA_KEY, False) if settings is not None else False
    return _as_bool(raw, False)


def _schematic_update_background_controls(self):
    c = _schematic_background_color(self)
    btn = getattr(self, "btn_schematic_bg_color", None)
    if btn is not None:
        try:
            # Lisible quel que soit le thème QGIS.
            lum = (0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue())
            fg = "#111111" if lum > 150 else "#ffffff"
            btn.setText(tr(c.name(QC.QColor_NameFormat_HexRgb).upper()))
            btn.setStyleSheet(
                "QPushButton { background:%s; color:%s; border:1px solid #777; padding:3px 8px; }" %
                (c.name(QC.QColor_NameFormat_HexRgb), fg)
            )
        except Exception:
            pass
    cb = getattr(self, "cb_schematic_bg_transparent", None)
    if cb is not None:
        try:
            old = cb.blockSignals(True)
            cb.setChecked(_schematic_background_transparent(self))
            cb.blockSignals(old)
        except Exception:
            pass


def _schematic_invalidate_base(self):
    try:
        getattr(self, "_base_cache", {}).clear()
    except Exception:
        pass
    try:
        mgr = getattr(self, "cache_mgr", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate("photo")
    except Exception:
        pass


def _schematic_choose_background_color(self):
    current = _schematic_background_color(self)
    c = QColorDialog.getColor(current, self, "Couleur du fond des vues schématiques")
    if not c.isValid():
        return
    c.setAlpha(255)
    try:
        self._settings.setValue(_BG_KEY, c.name(QC.QColor_NameFormat_HexRgb))
    except Exception:
        pass
    _schematic_update_background_controls(self)
    _schematic_invalidate_base(self)
    try:
        self.render_preview()
    except Exception:
        pass


def _schematic_set_background_transparent(self, checked):
    try:
        self._settings.setValue(_ALPHA_KEY, bool(checked))
    except Exception:
        pass
    _schematic_invalidate_base(self)
    try:
        self.render_preview()
    except Exception:
        pass


def _make_checkerboard(width, height):
    width = max(1, int(width)); height = max(1, int(height))
    img = QImage(width, height, QC.QImage_Format_Format_ARGB32_Premultiplied)
    img.fill(QColor(238, 238, 238, 255))
    p = QPainter(img)
    try:
        # Carreaux adaptatifs : suffisamment fins dans le dock, pas de motif bruité.
        cell = max(8, min(28, int(round(min(width, height) / 28.0))))
        light = QColor(248, 248, 248, 255)
        dark = QColor(220, 220, 220, 255)
        for y in range(0, height, cell):
            row = (y // cell) & 1
            for x in range(0, width, cell):
                col = (x // cell) & 1
                p.fillRect(x, y, cell, cell, light if (row ^ col) == 0 else dark)
    finally:
        p.end()
    return img


def _make_schematic_base(self, width, height, for_export=False, force_transparent=None):
    """Crée le fond d'une vue sans photo.

    - aperçu : un fond transparent est matérialisé par un damier uniquement à l'écran ;
    - export PNG : alpha réel à 0 lorsque la transparence globale est active ;
    - export JPEG / force_transparent=False : couleur globale opaque.
    """
    width = max(1, int(width)); height = max(1, int(height))
    transparent = (_schematic_background_transparent(self)
                   if force_transparent is None else bool(force_transparent))
    if transparent and not for_export:
        return _make_checkerboard(width, height)
    img = QImage(width, height, QC.QImage_Format_Format_ARGB32_Premultiplied)
    if transparent and for_export:
        img.fill(QColor(0, 0, 0, 0))
    else:
        img.fill(_schematic_background_color(self))
    return img


def _activate_schematic_view(self):
    """Bascule le runtime sur une vue sans photo sans modifier les attributs du PDV."""
    self.image = None
    self.photo_path = None
    self._camera_current_photo_path = None
    try:
        self._camera_last_photo_meta = {}
    except Exception:
        pass
    _schematic_invalidate_base(self)
    try:
        getattr(self, "_overlay_cache", {}).clear()
    except Exception:
        pass
    try:
        getattr(self, "_geom_cache", {}).clear()
    except Exception:
        pass


def _is_schematic_view(self):
    try:
        return getattr(self, "image", None) is None or self.image.isNull()
    except Exception:
        return True
