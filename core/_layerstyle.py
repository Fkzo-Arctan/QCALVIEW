


from ._i18n import tr
from ._compat import QC, dialog_exec

import os, json, math, re
from qgis.PyQt.QtCore import Qt, QSize, QPoint, QTimer, QElapsedTimer, QRect, pyqtSignal
from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QPixmap, QFont
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QColorDialog, QGroupBox, QFormLayout, QLineEdit, QScrollArea
    )
from qgis.core import (
    QgsExpressionContextUtils, QgsSymbol, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsGeometryGeneratorSymbolLayer, QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol, QgsRuleBasedRenderer,
    QgsMessageLog, QgsVectorLayer, QgsField, QgsProject, Qgis
)
    

    
class LayerStyle:
    def __init__(self, layer, color=QColor(0,255,0,255), width=2, label_field=None, label_size=12, label_offset=QPoint(8,-8)):
        self.layer = layer
        self.color = color
        self.width = width
        
        
        self.visible = True
        
        
        self.opacity = 1.0
        
        self.show_labels = True
        self.label_field = label_field
        self.label_size = label_size
        self.label_offset = label_offset  
        self.label_pos = "N"             
        self.label_text_color = QColor(20,20,20,255)
        
        self.label_bg = False
        self.label_bg_color = QColor(255,255,255,220)
        self.label_bg_padding = 4
        self.label_bg_radius = 4
        
        self.label_halo = False
        self.label_halo_color = QColor(0,0,0,220)
        self.label_halo_width = 2
        
        self.label_callout = False
        self.label_callout_color = QColor(0,0,0,180)
        self.label_callout_width = 1
        
        self.label_anchor_mode = "AUTO"   
        self.label_text = ""              
        
        
        
        self.enable_25d = True
        self.height_field_override = ""   
        self.default_height_override = None 
        
        self.fill_polygons = True
        self.fill_color = QColor(color.red(), color.green(), color.blue(), 255)
        self.fill_walls = True
        
        
        
        self.use_qgis_style = True
        
        
        self.qgis_fill_style = None
        self.pen_style = QC.Qt_PenStyle_SolidLine
        
        
        
        self.qgis_theme_name = ""
        self.qgis_theme_style_name = ""
        
        
        self.schematic_enabled = False
        self.schematic_symbol_id = ""
        
        
        self.schematic_type = ""
        self.schematic_family = ""
        self.schematic_params = {}
        
        self.schematic_asset_paths = []
        
        self.use_qgis_labels = True
        self.qgis_label_is_expression = False
        self.qgis_label_expr = ""


def apply_pdv_qml_style(self, layer, qml_rel_path="core/style/STYLE-PDV.qml"):
    
    if not isinstance(layer, QgsVectorLayer):
        return False

    plugin_dir = os.path.dirname(os.path.dirname(__file__))
    qml_path = os.path.join(plugin_dir, qml_rel_path)

    
    
    try:
        layer_name = layer.name() if hasattr(layer, "name") else "<sans nom>"
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] loadNamedStyle START — couche='{layer_name}', "
            f"qml='{qml_path}', exists={os.path.isfile(qml_path)}, readable={os.access(qml_path, os.R_OK)}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Info
        )
    except Exception:
        pass

    if not os.path.isfile(qml_path):
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] QML introuvable : {qml_path}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Warning
        )
        return False

    ok = False
    msg = ""
    raw_ret = None
    try:
        ret = layer.loadNamedStyle(qml_path)
        raw_ret = ret
        if isinstance(ret, tuple) and len(ret) >= 2:
            a, b = ret[0], ret[1]
            if isinstance(a, bool):
                ok, msg = bool(a), str(b)
            elif isinstance(b, bool):
                ok, msg = bool(b), str(a)
            else:
                ok, msg = False, str(ret)
        else:
            ok = bool(ret)
            msg = ""
    except Exception as e:
        ok = False
        msg = str(e)

    if not ok:
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] loadNamedStyle KO: msg={msg!r}, raw_return={raw_ret!r}, qml={qml_path}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Warning
        )
        return False

    try:
        renderer = layer.renderer()
        renderer_class = renderer.__class__.__name__ if renderer is not None else "None"
        renderer_type = renderer.type() if renderer is not None and hasattr(renderer, "type") else "?"
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] loadNamedStyle OK: raw_return={raw_ret!r}, "
            f"renderer={renderer_class}, renderer.type={renderer_type}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Info
        )
    except Exception as renderer_exc:
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] Style chargé mais inspection renderer impossible: {renderer_exc}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Warning
        )

    layer.triggerRepaint()
    return True



PDV_AUTO_STYLE_NAME = "QCALVIEW — Couleurs automatiques"
PDV_MANUAL_STYLE_NAME = "QCALVIEW — Style modifiable"
PDV_AUTO_COLOR_PROPERTY = "QCALVIEW/pdv_auto_colors"


def _style_manager_names(manager):
    try:
        return [str(n) for n in manager.styles()]
    except Exception:
        return []


def _activate_qml_as_named_style(self, layer, style_name, qml_rel_path, refresh_existing=False):
    
    if not isinstance(layer, QgsVectorLayer):
        return False
    try:
        manager = layer.styleManager()
    except Exception:
        manager = None
    if manager is None:
        return apply_pdv_qml_style(self, layer, qml_rel_path)

    names = _style_manager_names(manager)
    if style_name in names:
        try:
            if not bool(manager.setCurrentStyle(style_name)):
                return False
        except Exception:
            return False
        if refresh_existing:
            return bool(apply_pdv_qml_style(self, layer, qml_rel_path))
        layer.triggerRepaint()
        return True

    
    
    
    previous = ''
    try:
        previous = str(manager.currentStyle())
    except Exception:
        previous = ''
    temp_base = "__QCALVIEW_STYLE_WORK__"
    temp_name = temp_base
    idx = 1
    while temp_name in names:
        temp_name = f"{temp_base}_{idx}"
        idx += 1
    try:
        if not bool(manager.addStyleFromLayer(temp_name)):
            return False
        if not bool(manager.setCurrentStyle(temp_name)):
            return False
        if not bool(apply_pdv_qml_style(self, layer, qml_rel_path)):
            try:
                if previous in _style_manager_names(manager):
                    manager.setCurrentStyle(previous)
            except Exception:
                pass
            try:
                manager.removeStyle(temp_name)
            except Exception:
                pass
            return False
        if not bool(manager.addStyleFromLayer(style_name)):
            return False
        if not bool(manager.setCurrentStyle(style_name)):
            return False
        try:
            manager.removeStyle(temp_name)
        except Exception:
            pass
        layer.triggerRepaint()
        return True
    except Exception as exc:
        QgsMessageLog.logMessage(
            tr(f"[QCALVIEW][PDV-QML] Création style nommé impossible: {exc}"),
            "QCALVIEW", QC.Qgis_MessageLevel_Warning
        )
        try:
            if previous in _style_manager_names(manager):
                manager.setCurrentStyle(previous)
        except Exception:
            pass
        try:
            if temp_name in _style_manager_names(manager):
                manager.removeStyle(temp_name)
        except Exception:
            pass
        return False


def apply_pdv_style_mode(self, layer, automatic=True):
    
    if not isinstance(layer, QgsVectorLayer):
        return False
    automatic = bool(automatic)
    try:
        layer.setCustomProperty(PDV_AUTO_COLOR_PROPERTY, 1 if automatic else 0)
    except Exception:
        pass
    if automatic:
        
        
        return _activate_qml_as_named_style(
            self, layer, PDV_AUTO_STYLE_NAME, "core/style/STYLE-PDV.qml", refresh_existing=True
        )
    
    
    return _activate_qml_as_named_style(
        self, layer, PDV_MANUAL_STYLE_NAME, "core/style/STYLE-MOD.qml", refresh_existing=False
    )


def pdv_layer_auto_colors(layer, default=True):
    if not isinstance(layer, QgsVectorLayer):
        return bool(default)
    try:
        value = layer.customProperty(PDV_AUTO_COLOR_PROPERTY, 1 if default else 0)
        if isinstance(value, str):
            return value.strip().lower() not in ('0', 'false', 'no', 'off', '')
        return bool(int(value)) if isinstance(value, (int, float)) else bool(value)
    except Exception:
        return bool(default)

def update_pdv_qml_vars(self, layer, yaw_deg, hfov_deg, range_m, pitch_deg, is360=False):
    
    if not isinstance(layer, QgsVectorLayer):
        return False

    try:
        yaw   = float(yaw_deg)
        pitch = float(pitch_deg)
        rng   = max(1.0, float(range_m))
        if bool(is360):
            hfov = 360.0
        else:
            hfov = max(1.0, min(360.0, float(hfov_deg)))

        QgsExpressionContextUtils.setLayerVariable(layer, "fov_yaw",   yaw)
        QgsExpressionContextUtils.setLayerVariable(layer, "fov_hfov",  hfov)
        QgsExpressionContextUtils.setLayerVariable(layer, "fov_is360", 1 if bool(is360) else 0)
        QgsExpressionContextUtils.setLayerVariable(layer, "fov_range", rng)
        QgsExpressionContextUtils.setLayerVariable(layer, "fov_pitch", pitch)
        try:
            layer_crs = layer.crs()
            work_crs = self._camera_metric_project_crs()
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_layer_crs", str(layer_crs.authid() or layer_crs.toWkt()))
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_work_crs", str(work_crs.authid() or work_crs.toWkt()) if work_crs is not None else "")
        except Exception:
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_layer_crs", "")
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_work_crs", "")

        layer.triggerRepaint()
        return True
    except Exception as e:
        QgsMessageLog.logMessage(tr(f"[QCALVIEW] update_pdv_qml_vars error: {e}"), "QCALVIEW", QC.Qgis_MessageLevel_Warning)
        return False

