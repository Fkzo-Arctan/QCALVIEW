"""Terrain/MNT preflight guard for QCALVIEW renders and visual exports."""

from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsProject, QgsRasterLayer

from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._log import qcv_log


def terrain_layer_status(self):
    """Return ``(is_valid, layer, reason)`` for the selected terrain raster."""
    combo = getattr(self, "cmb_dem", None)
    if combo is None:
        return False, None, "missing_selector"

    try:
        layer = combo.currentLayer()
    except Exception:
        layer = None

    if layer is None:
        return False, None, "not_selected"
    if not isinstance(layer, QgsRasterLayer):
        return False, layer, "not_raster"

    try:
        if not layer.isValid():
            return False, layer, "invalid_layer"
    except Exception:
        return False, layer, "invalid_layer"

    try:
        project_layer = QgsProject.instance().mapLayer(layer.id())
        if project_layer is None:
            return False, layer, "not_in_project"
    except Exception:
        return False, layer, "not_in_project"

    try:
        provider = layer.dataProvider()
        if provider is None:
            return False, layer, "invalid_provider"
        is_valid = getattr(provider, "isValid", None)
        if callable(is_valid) and not bool(is_valid()):
            return False, layer, "invalid_provider"
    except Exception:
        return False, layer, "invalid_provider"

    try:
        crs = layer.crs()
        if crs is None or not crs.isValid():
            return False, layer, "invalid_crs"
    except Exception:
        return False, layer, "invalid_crs"

    try:
        if int(layer.bandCount()) < 1 or int(layer.width()) < 1 or int(layer.height()) < 1:
            return False, layer, "empty_raster"
    except Exception:
        return False, layer, "empty_raster"

    return True, layer, ""


def refresh_terrain_requirement_ui(self):
    """Refresh the persistent MNT requirement hint in the Relief panel."""
    valid, layer, reason = terrain_layer_status(self)
    label = getattr(self, "lbl_dem_required", None)
    if label is not None:
        try:
            if valid:
                label.clear()
                label.setVisible(False)
            else:
                label.setText(tr(
                    "MNT/MNS requis pour le rendu : sélectionnez un raster de topographie."
                ))
                label.setVisible(True)
        except Exception as exc:
            _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    try:
        combo = getattr(self, "cmb_dem", None)
        if combo is not None:
            combo.setToolTip(tr(
                "QCALVIEW nécessite un modèle numérique de terrain ou de surface "
                "explicitement sélectionné avant tout rendu."
            ))
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    return valid, layer, reason


def _focus_terrain_selector(self):
    try:
        tabs = getattr(self, "tabs", None)
        relief_tab = getattr(self, "tab_relief", None)
        if tabs is not None and relief_tab is not None:
            tabs.setCurrentWidget(relief_tab)
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    try:
        group = getattr(self, "grp_dem", None)
        if group is not None and hasattr(group, "setChecked"):
            group.setChecked(True)
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    try:
        combo = getattr(self, "cmb_dem", None)
        if combo is not None:
            combo.setFocus()
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
def validate_terrain_layer(self, notify=True, purpose="render"):
    """Block visual rendering when no valid terrain raster is selected.

    For normal rendering and startup, the persistent warning inside QCALVIEW's
    Relief panel is the only user-facing notification. Exports keep a blocking
    QCALVIEW dialog because the requested operation cannot continue.
    """
    valid, _layer, _reason = refresh_terrain_requirement_ui(self)
    if valid:
        setattr(self, "_terrain_guard_warned", False)
        return True

    if not notify:
        return False

    _focus_terrain_selector(self)

    if str(purpose).lower().startswith("export"):
        QMessageBox.warning(
            self,
            tr("QCALVIEW — MNT/MNS requis"),
            tr(
                "Export impossible : aucun MNT/MNS valide n’est sélectionné.\n\n"
                "Sélectionnez un raster de topographie dans l’onglet Relief, "
                "puis relancez l’export."
            ),
        )
        qcv_log(
            "Export bloqué : aucun MNT/MNS valide sélectionné.",
            "TERRAIN/GUARD",
            "WARNING",
        )
        return False

    if not bool(getattr(self, "_terrain_guard_warned", False)):
        setattr(self, "_terrain_guard_warned", True)
        qcv_log(
            "Rendu suspendu : aucun MNT/MNS valide sélectionné.",
            "TERRAIN/GUARD",
            "WARNING",
        )
    return False


def on_terrain_layer_changed(self, _layer=None):
    """Reset warning throttling and render as soon as a valid MNT is selected."""
    setattr(self, "_terrain_guard_warned", False)
    valid, _layer, _reason = refresh_terrain_requirement_ui(self)
    if not valid:
        return

    try:
        getattr(self, "_overlay_cache", {}).clear()
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    try:
        getattr(self, "_z_cache", {}).clear()
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
    try:
        self._horizon = None
        self._horizon_params = None
    except Exception as exc:
        _qcv_suppress(exc, "core/_terrain_guard.py:suppressed")
