


from .core._exceptions import qcv_suppress_exception as _qcv_suppress
from .core._i18n import tr, install_qcalview_translator, remove_qcalview_translator
from .core._compat import QC, dialog_exec, QAction
import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
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
        
        
        
        
        self._canvas_preview_jobs_previous = None
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_path = os.path.join(
            self.plugin_dir, "resources", "icons", "qcalview_icon.png"
        )

    def initGui(self):
        qcv_log(f"Plugin {RELEASE_LABEL} initialisé", "PLUGIN", "SUCCESS")
        plugin_icon = QIcon(self.icon_path)
        self.action = QAction(plugin_icon, tr("QCALVIEW"), self.iface.mainWindow())
        self.action.triggered.connect(self.toggle_dock)
        self.iface.addPluginToMenu("&QCALVIEW", self.action)
        self.iface.addToolBarIcon(self.action)

        
        
        
        try:
            plugins_menu = self.iface.pluginMenu()
            for menu_action in plugins_menu.actions():
                submenu = menu_action.menu()
                if submenu is None:
                    continue
                title = str(submenu.title() or "").replace("&", "").strip()
                if title == "QCALVIEW":
                    submenu.setIcon(plugin_icon)
                    menu_action.setIcon(plugin_icon)
                    break
        except Exception as exc:
            qcv_log(f"Icône du menu QCALVIEW non appliquée : {exc}", "PLUGIN", "WARNING")

        self.settings_action = QAction(plugin_icon, tr("Paramètres QCALVIEW"), self.iface.mainWindow())
        self.settings_action.triggered.connect(self.open_settings)
        self.iface.addPluginToMenu("&QCALVIEW", self.settings_action)

        
        
        self.layer_context_action = QAction(plugin_icon, tr("Ajouter à QCALVIEW"), self.iface.mainWindow())
        self.layer_context_action.triggered.connect(self.add_active_layer_to_qcalview)
        try:
            self.iface.addCustomActionForLayerType(
                self.layer_context_action, "", Qgis.LayerType.Vector, True
            )
        except Exception as exc:
            qcv_log(f"Action contextuelle non installée : {exc}", "PLUGIN", "WARNING")

    def unload(self):
        qcv_log("Déchargement du plugin", "PLUGIN", "INFO")
        self._restore_canvas_preview_jobs()
        if self.action:
            self.iface.removePluginMenu("&QCALVIEW", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.settings_action:
            self.iface.removePluginMenu("&QCALVIEW", self.settings_action)
        if self.layer_context_action:
            try:
                self.iface.removeCustomActionForLayerType(self.layer_context_action)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "qcalview_photo_plugin.py:84")
            self.layer_context_action = None
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock = None

        remove_qcalview_translator()
        self._qcv_translator = None

    def _disable_canvas_preview_jobs(self):
        
        if self._canvas_preview_jobs_previous is not None:
            return
        try:
            canvas = self.iface.mapCanvas()
            previous = bool(canvas.previewJobsEnabled())
            self._canvas_preview_jobs_previous = previous
            if previous:
                canvas.setPreviewJobsEnabled(False)
            qcv_log(
                f"Preview jobs canevas suspendus pendant QCALVIEW (état initial={previous})",
                "PLUGIN",
                "INFO",
            )
        except Exception as exc:
            self._canvas_preview_jobs_previous = None
            qcv_log(f"Impossible de suspendre les preview jobs : {exc}", "PLUGIN", "WARNING")

    def _restore_canvas_preview_jobs(self):
        
        previous = self._canvas_preview_jobs_previous
        if previous is None:
            return
        self._canvas_preview_jobs_previous = None
        try:
            self.iface.mapCanvas().setPreviewJobsEnabled(bool(previous))
            qcv_log(
                f"Preview jobs canevas restaurés (état={bool(previous)})",
                "PLUGIN",
                "INFO",
            )
        except Exception as exc:
            qcv_log(f"Impossible de restaurer les preview jobs : {exc}", "PLUGIN", "WARNING")

    def _on_dock_visibility_changed(self, visible):
        if bool(visible):
            self._disable_canvas_preview_jobs()
        else:
            self._restore_canvas_preview_jobs()

    def _ensure_dock(self, show=True):
        
        
        
        self._disable_canvas_preview_jobs()
        if self.dock is None:
            self.dock = QCalViewDock(self.iface)
            self.iface.addDockWidget(QC.Qt_DockWidgetArea_RightDockWidgetArea, self.dock)
            self.dock.setFloating(True)
            try:
                self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "qcalview_photo_plugin.py:146")
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
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "qcalview_photo_plugin.py:184")

    def toggle_dock(self):
        if self.dock is None:
            self._ensure_dock(show=True)
            return
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self._disable_canvas_preview_jobs()
            self.dock.setFloating(True)
            self.dock.show()
            self.dock.raise_()
            self.dock.activateWindow()
