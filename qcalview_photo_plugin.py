# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from .core._i18n import tr, install_qcalview_translator, remove_qcalview_translator
from .core._compat import QC, dialog_exec, QAction
from qgis.PyQt.QtCore import Qt
from qgis.utils import iface
from qgis.core import Qgis, QgsVectorLayer
from .qcalview_dock import QCalViewDock
from .core._log import qcv_log
from .core._release import RELEASE_LABEL

class QCalViewPlugin:
    def __init__(self, iface_):
        self.iface = iface_
        self._qcv_translator = install_qcalview_translator(self.iface.mainWindow())
        self.action = None
        self.settings_action = None
        self.layer_context_action = None
        self.dock = None

    def initGui(self):
        qcv_log(f"Plugin {RELEASE_LABEL} initialisé", "PLUGIN", "SUCCESS")
        self.action = QAction(tr("QCALVIEW"), self.iface.mainWindow())
        self.action.triggered.connect(self.toggle_dock)
        self.iface.addPluginToMenu("&QCALVIEW", self.action)
        self.iface.addToolBarIcon(self.action)
        self.settings_action = QAction(tr("Paramètres QCALVIEW"), self.iface.mainWindow())
        self.settings_action.triggered.connect(self.open_settings)
        self.iface.addPluginToMenu("&QCALVIEW", self.settings_action)

        # Raccourci ergonomique dans l'arborescence QGIS : une seule action,
        # uniquement pour les couches vectorielles compatibles avec les overlays.
        self.layer_context_action = QAction(tr("Ajouter à QCALVIEW"), self.iface.mainWindow())
        self.layer_context_action.triggered.connect(self.add_active_layer_to_qcalview)
        try:
            self.iface.addCustomActionForLayerType(
                self.layer_context_action, "", Qgis.LayerType.Vector, True
            )
        except Exception as exc:
            qcv_log(f"Action contextuelle non installée : {exc}", "PLUGIN", "WARNING")

    def unload(self):
        qcv_log("Déchargement du plugin", "PLUGIN", "INFO")
        if self.action:
            self.iface.removePluginMenu("&QCALVIEW", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.settings_action:
            self.iface.removePluginMenu("&QCALVIEW", self.settings_action)
        if self.layer_context_action:
            try:
                self.iface.removeCustomActionForLayerType(self.layer_context_action)
            except Exception:
                pass
            self.layer_context_action = None
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock = None

        remove_qcalview_translator()
        self._qcv_translator = None

    def _ensure_dock(self, show=True):
        if self.dock is None:
            self.dock = QCalViewDock(self.iface)
            self.iface.addDockWidget(QC.Qt_DockWidgetArea_RightDockWidgetArea, self.dock)
            self.dock.setFloating(True)
        if show:
            self.dock.show()
            self.dock.raise_()
            self.dock.activateWindow()
        return self.dock

    def add_active_layer_to_qcalview(self):

        layer = None
        try:
            view = self.iface.layerTreeView()
            current = getattr(view, 'currentLayer', None)
            if callable(current):
                layer = current()
        except Exception:
            layer = None
        if layer is None:
            try:
                layer = self.iface.activeLayer()
            except Exception:
                layer = None
        if not isinstance(layer, QgsVectorLayer):
            return
        dock = self._ensure_dock(show=True)
        try:
            dock.add_layer_object(layer)
        except Exception as exc:
            qcv_log(f"Ajout contextuel impossible pour {layer.name()}: {exc}", "PLUGIN", "WARNING")

    def open_settings(self):
        self._ensure_dock(show=False)
        self.dock.show()
        self.dock.raise_()
        self.dock.activateWindow()
        try:
            self.dock.open_settings_dialog()
        except Exception:
            pass

    def toggle_dock(self):
        if self.dock is None:
            self._ensure_dock(show=True)
            return
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self.dock.setFloating(True)
            self.dock.show()
            self.dock.raise_()
            self.dock.activateWindow()
