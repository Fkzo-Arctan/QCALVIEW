# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
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
        # Visibilité propre à QCALVIEW : permet de neutraliser temporairement une couche
        # sans modifier le thème ou la visibilité QGIS.
        self.visible = True
        # Opacité propre à la couche overlay (1.0 = opaque).
        # Utilisée pour respecter la transparence QGIS par couche, comme dans QCALVIEW Lite.
        self.opacity = 1.0
        # Étiquettes
        self.show_labels = True
        self.label_field = label_field
        self.label_size = label_size
        self.label_offset = label_offset  # décalage fin (px)
        self.label_pos = "N"             # N, NE, E, SE, S, SW, W, NW, C
        self.label_text_color = QColor(20,20,20,255)
        # Fond (“ombrelle”)
        self.label_bg = False
        self.label_bg_color = QColor(255,255,255,220)
        self.label_bg_padding = 4
        self.label_bg_radius = 4
        # Halo (contour du texte)
        self.label_halo = False
        self.label_halo_color = QColor(0,0,0,220)
        self.label_halo_width = 2
        # Callout (trait d’accroche)
        self.label_callout = False
        self.label_callout_color = QColor(0,0,0,180)
        self.label_callout_width = 1
        # --- Nouveaux réglages d'ancrage/texte ---
        self.label_anchor_mode = "AUTO"   # "AUTO" | "CENTROID" | "IMAGE_CENTER"
        self.label_text = ""              # si non vide, remplace le champ
        # --- 2,5D par couche ---
        # Conserver True par défaut pour rester compatible avec le comportement historique
        # dès lors que la case globale "Extruder les polygones" est cochée.
        self.enable_25d = True
        self.height_field_override = ""   # vide => utiliser le champ global
        self.default_height_override = None # None => utiliser la hauteur globale
        # --- Remplissages / occultation visuelle ---
        self.fill_polygons = True
        self.fill_color = QColor(color.red(), color.green(), color.blue(), 255)
        self.fill_walls = True
        # Reprendre automatiquement le style courant de la couche QGIS
        # (couleurs, épaisseurs, remplissage, style de trait) tant que
        # l'utilisateur ne désactive pas explicitement ce comportement.
        self.use_qgis_style = True
        # Reprise fidèle best-effort du remplissage polygonal QGIS
        # (SimpleFill / LinePatternFill / PointPatternFill / GradientFill).
        self.qgis_fill_style = None
        self.pen_style = QC.Qt_PenStyle_SolidLine
        # Contexte optionnel issu d'un thème QGIS. Sert surtout à documenter
        # l'origine de la couche dans la liste QCALVIEW et à permettre une
        # resynchronisation ultérieure.
        self.qgis_theme_name = ""
        self.qgis_theme_style_name = ""
        # --- Représentation schématique AVR 0/1 ---
        # Désactivée par défaut pour préserver strictement le rendu historique.
        self.schematic_enabled = False
        self.schematic_symbol_id = ""
        # Taxonomie de bibliothèque (type/famille) indépendante du générateur.
        # Permet de filtrer logiquement les modèles SVG/PNG sans changer le rendu.
        self.schematic_type = ""
        self.schematic_family = ""
        self.schematic_params = {}
        # Modèles graphiques optionnels (SVG/PNG), 3 maximum. Vide = modèle de la définition JSON.
        self.schematic_asset_paths = []
        # Par défaut, les étiquettes suivent celles de la couche QGIS si elles existent.
        self.use_qgis_labels = True
        self.qgis_label_is_expression = False
        self.qgis_label_expr = ""


def apply_pdv_qml_style(self, layer, qml_rel_path="core/style/STYLE-PDV.qml"):
    """
    Applique le style QML STYLE-PDV.qml à la couche PDV.
    Compatibilité PyQGIS : selon les versions, loadNamedStyle peut retourner
    (ok, msg) ou (msg, ok).
    """
    if not isinstance(layer, QgsVectorLayer):
        return False

    plugin_dir = os.path.dirname(os.path.dirname(__file__))
    qml_path = os.path.join(plugin_dir, qml_rel_path)

    # 40.19.4 : le QML reste volontairement la source de vérité éditable.
    # Journalisation détaillée pour diagnostiquer les différences QGIS 3 / QGIS 4.
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


def update_pdv_qml_vars(self, layer, yaw_deg, hfov_deg, range_m, pitch_deg, is360=False):
    """
    Met à jour les variables consommées par STYLE-PDV.qml :
      - @fov_yaw   (°)
      - @fov_hfov  (°) -> si 360° coché, on force 360
      - @fov_is360 (0/1)
      - @fov_range (m)
      - @fov_pitch (°)
    """
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

        layer.triggerRepaint()
        return True
    except Exception as e:
        QgsMessageLog.logMessage(tr(f"[QCALVIEW] update_pdv_qml_vars error: {e}"), "QCALVIEW", QC.Qgis_MessageLevel_Warning)
        return False

