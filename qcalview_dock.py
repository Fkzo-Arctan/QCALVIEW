# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from .core._i18n import tr
from .core._compat import QC, dialog_exec
import os, json, math, html
from qgis.PyQt.QtCore import Qt, QSize, QPoint, QTimer, QElapsedTimer, QRect, pyqtSignal, QSettings
from qgis.PyQt import uic
from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QPixmap, QFont, QIcon
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QColorDialog, QGroupBox, QFormLayout, QLineEdit, QScrollArea,
    QMessageBox, QTabWidget, QGridLayout, QToolButton, QFrame, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QDialog,
    QDialogButtonBox
)
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsWkbTypes, QgsPointXY, QgsFeature, QgsGeometry, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsVectorLayer
)
from qgis.core import QgsExpressionContextUtils as _ECU
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand, QgsVertexMarker, QgsMapTool
from .projector import (project_point, hfov_from_focal_sensor, vfov_from_hfov_ratio)
from .ui.widgets import CollapsibleBox
from .core._cache_manager import SmartCacheManager
from .core._adaptive_render import AdaptiveRenderScheduler
from .core._profiler import get_profiler
from .core._release import PUBLIC_EXPERIMENTAL_LIMITED


class ClickableLabel(QLabel):
    clicked = pyqtSignal(int, int)
    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)
        self.clicked.emit(ev.pos().x(), ev.pos().y())


class AzimuthSpinBox(QDoubleSpinBox):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(-1.0e9, 1.0e9)
        # Une saisie clavier ne pousse pas les états intermédiaires (5, 52, 520)
        # dans le moteur ; seule la valeur validée est publiée.
        self.setKeyboardTracking(False)

    @staticmethod
    def _mod360(value):
        try:
            out = float(value) % 360.0
            if out < 0.0:
                out += 360.0
            # Évite d'afficher 360,00 à cause d'un résidu flottant.
            if abs(out - 360.0) < 1.0e-10 or abs(out) < 1.0e-12:
                out = 0.0
            return out
        except Exception:
            return 0.0

    def normalizeModulo(self):
        value = self._mod360(self.value())
        if abs(float(self.value()) - value) > 1.0e-12:
            self.setValue(value)
        return value

    def stepBy(self, steps):
        try:
            step = float(self.singleStep())
            value = self._mod360(float(self.value()) + float(steps) * step)
            self.setValue(value)
        except Exception:
            super().stepBy(steps)


class QCalViewDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("QCALVIEW", iface.mainWindow())
        self.iface = iface
        self.setObjectName("QCALVIEWDock")
        self.setAllowedAreas(QC.Qt_DockWidgetArea_AllDockWidgetAreas)
        self.setFeatures(QC.QDockWidget_DockWidgetFeature_DockWidgetClosable | QC.QDockWidget_DockWidgetFeature_DockWidgetMovable | QC.QDockWidget_DockWidgetFeature_DockWidgetFloatable)
        self.setFloating(True)
        self.resize(780, 980)
        self.setMinimumSize(280, 240)

        # État
        self.photo_path = None
        self.image = None
        self.last_preview = None
        self.viewer = None
        self._viewer_click_connected = False
        self._image_pick_mode = None
        self._last_nav_target = None

        # Caches

        self._base_cache = {}
        self._z_cache = {}
        self._overlay_cache = {}
        try:
            self.cache_mgr = SmartCacheManager()
        except Exception as e:
            print(f"Cache manager non disponible: {e}")
            self.cache_mgr = None
        
        self._cam_layer = None
        self._cam_connections = []
        self._camera_bound_layer = None
        self._camera_bound_connections = []
        self._camera_sync_guard = False
        self._camera_last_loaded_fid = None
        self._camera_current_fid = None
        self._camera_current_photo_path = None
        self._camera_current_view_mode = 'AUTO'
        self._camera_drafts = {}
        self._camera_loading_feature = False
        self._camera_autosave_dirty = False
        self._camera_autosave_timer = QTimer(self)
        self._camera_autosave_timer.setSingleShot(True)
        self._camera_autosave_timer.setInterval(1200)
        self._camera_internal_write = False
        self._camera_feature_refresh_timer = QTimer(self)
        self._camera_feature_refresh_timer.setSingleShot(True)
        self._camera_feature_refresh_timer.setInterval(120)
        self._camera_live_enabled = False
        self._camera_live_pending_fid = None
        self._camera_live_refresh_timer = QTimer(self)
        self._camera_live_refresh_timer.setSingleShot(True)
        self._camera_live_refresh_timer.setInterval(250)
        self._camera_live_refresh_timer.timeout.connect(self._camera_live_refresh_current)
        self._horizon = None
        self._horizon_params = None

        self._render_edit_widgets = set()
        self._render_edit_pending = False
        self._render_request_generation = 0
        self._render_scheduled_generation = 0
        self._render_delay_override_ms = None
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(120)
        self.debounce.timeout.connect(self._render_debounce_timeout)

        self.render_scheduler = None
        self._current_render_quality = 'high'
        self._camera_autosave_timer.timeout.connect(self._camera_autosave_timeout)
        self._camera_feature_refresh_timer.timeout.connect(self._camera_refresh_feature_list)

        # --- UI ---
        self._settings = QSettings("ArcTan", "QCALVIEW")

        self._ui_initializing = True
        self._export_tab_loaded = False
        self._theme_combo_loaded = False
        self._suspend_theme_auto_apply = False
        root = self._load_designer_shell()


        # --- Canvas overlays (heading + HFOV wedge) ---
        self._rb_dir = None
        self._rb_fov = None
        self._rb_pick = None
        self._vm_pick = None
        try:
            canvas = self.iface.mapCanvas()
            from qgis.PyQt.QtGui import QColor
            self._rb_dir = QgsRubberBand(canvas, False)  # line
            self._rb_dir.setWidth(2)
            self._rb_dir.setColor(QColor(0, 180, 255, 200))
            self._rb_fov = QgsRubberBand(canvas, True)   # polygon
            self._rb_fov.setWidth(1)
            self._rb_fov.setColor(QColor(0, 180, 255, 60))
            self._rb_fov.setFillColor(QColor(0, 180, 255, 40))
        except Exception:
            pass



        # --- PARAMETRES CAMERA ET PROJECTION DE LA PHOTO ---

        g_cam = CollapsibleBox("Paramètres caméra", checked=True)
        content_cam = QWidget()
        f = QGridLayout(content_cam)
        self._calage_grid_layout = f
        self._calage_cards = []
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(10)
        f.setVerticalSpacing(8)

        # Cases UI pour les guides
        self.cb_show_center_axis = QCheckBox(tr("Afficher barre centrale"))
        self.cb_show_center_axis.setChecked(True)
        self.cb_show_center_axis.toggled.connect(lambda checked: self._on_toggle_center_axis(checked))
        self.cb_show_pdv_axis = QCheckBox(tr("Afficher barre azimut (PDV)"))
        self.cb_show_pdv_axis.setChecked(False)
        self.cb_show_pdv_axis.toggled.connect(lambda checked: self._on_toggle_pdv_axis(checked))

        self.cmb_proj = QComboBox(); self.cmb_proj.addItems(tr(["PINHOLE", "EQUIRECT", "CYLINDRICAL"]))
        self.spin_w = QSpinBox(); self.spin_w.setRange(256, 60000); self.spin_w.setValue(8000)
        self.spin_h = QSpinBox(); self.spin_h.setRange(256, 60000); self.spin_h.setValue(4000)
        self.d_yaw   = AzimuthSpinBox(); self._deg(self.d_yaw)

        self.d_yaw.setRange(-1.0e9, 1.0e9)
        self.d_yaw.editingFinished.connect(self.d_yaw.normalizeModulo)
        self.d_pitch = QDoubleSpinBox(); self._deg(self.d_pitch)
        self.d_roll  = QDoubleSpinBox(); self._deg(self.d_roll)

        self.cmb_orientation_step = QComboBox()
        self.cmb_orientation_step.addItem(tr("1°"), 1.0)
        self.cmb_orientation_step.addItem(tr("0,1°"), 0.1)
        self.cmb_orientation_step.addItem(tr("0,01°"), 0.01)
        self.cmb_orientation_step.setCurrentIndex(0)
        self.cmb_orientation_step.setToolTip(tr("Pas des flèches/molette pour l’azimut et le tangage : grossier, fin ou précision."))
        self.d_yaw.setSingleStep(1.0)
        self.d_pitch.setSingleStep(1.0)
        self.d_yaw_offset = QDoubleSpinBox(); self._deg(self.d_yaw_offset); self.d_yaw_offset.setValue(0.0)
        self.d_hfov  = QDoubleSpinBox(); self._deg(self.d_hfov); self.d_hfov.setRange(0.01, 360.0); self.d_hfov.setValue(60.0)
        self.d_vfov  = QDoubleSpinBox(); self._deg(self.d_vfov); self.d_vfov.setValue(40.0)
        self.cb_360  = QCheckBox(tr("Equirect 360x180")); self.cb_360.setChecked(False)
        self.d_maxdist = QDoubleSpinBox(); self.d_maxdist.setRange(0, 1000000); self.d_maxdist.setValue(5000.0); self.d_maxdist.setSuffix(tr(" m (0=∞)"))
        self.d_symdist = QDoubleSpinBox(); self.d_symdist.setRange(5.0, 1000000.0); self.d_symdist.setDecimals(1); self.d_symdist.setValue(200.0); self.d_symdist.setSuffix(tr(" u.carte"))
        self.cb_auto_depth = QCheckBox(tr("Profondeur auto (aperçu)")); self.cb_auto_depth.setChecked(True)
        self.cmb_perf_budget = QComboBox(); self.cmb_perf_budget.addItems(tr(["Sûr", "Équilibré", "Max détail"])); self.cmb_perf_budget.setCurrentIndex(1)
        self.cb_block_heavy_layers = QCheckBox(tr("Bloquer les couches trop lourdes")); self.cb_block_heavy_layers.setChecked(True)
        self.lbl_render_budget = QLabel(tr("Budget : -")); self.lbl_render_budget.setStyleSheet("color:#666;")
        self.d_focal = QDoubleSpinBox(); self.d_focal.setRange(0.1, 1000); self.d_focal.setDecimals(3); self.d_focal.setValue(35.0); self.d_focal.setSuffix(tr(" mm"))

        self.d_sensorw = QDoubleSpinBox(); self.d_sensorw.setRange(0.1, 100.0); self.d_sensorw.setDecimals(3); self.d_sensorw.setValue(36.0); self.d_sensorw.setSuffix(tr(" mm"))
        self.d_sensorw.setVisible(False)
        self.cb_auto_hfov = QCheckBox(tr("HFOV auto (métadonnées optiques)")); self.cb_auto_hfov.setChecked(True)
        self.d_camheight = QDoubleSpinBox(); self.d_camheight.setRange(0.0, 2000.0); self.d_camheight.setDecimals(2); self.d_camheight.setValue(1.7); self.d_camheight.setSuffix(tr(" m"))


        self.btn_schematic_bg_color = QPushButton(tr("#F2F2F2"))
        self.cb_schematic_bg_transparent = QCheckBox(tr("Fond transparent pour PNG"))
        try:
            self._schematic_update_background_controls()
        except Exception:
            pass
        self.btn_schematic_bg_color.clicked.connect(self._schematic_choose_background_color)
        self.cb_schematic_bg_transparent.toggled.connect(self._schematic_set_background_transparent)

        def _tip(widget, text):
            try:
                widget.setToolTip(tr(text))
            except Exception:
                pass

        _tip(self.d_yaw, "Azimut central de la vue, en degrés. Il oriente l'axe principal de la caméra et décale directement les overlays projetés dans l'image.")
        _tip(self.d_pitch, "Tangage de la caméra, en degrés. Une valeur positive/négative relève ou abaisse la visée et modifie la position verticale des overlays.")
        _tip(self.d_roll, "Roulis de la caméra, en degrés. Il corrige l'inclinaison latérale de la photo et fait pivoter les overlays autour de l'axe optique.")
        _tip(self.d_camheight, "Hauteur de la caméra au-dessus du terrain ou altitude Z utilisée selon le mode courant. Elle fixe l'origine 3D des rayons de projection.")
        _tip(self.cmb_proj, "Type de projection de l'image. PINHOLE pour photo rectilinéaire, CYLINDRICAL pour panorama cylindrique, EQUIRECT pour panorama 360×180.")
        _tip(self.cb_360, "Force le mode equirectangulaire complet 360° × 180°. À utiliser uniquement pour les panoramas sphériques complets.")
        _tip(self.d_maxdist, "Distance maximale de projection des overlays depuis le point de vue. Réduit le territoire traité et améliore les performances.")
        _tip(self.d_hfov, "Champ horizontal de l'image en degrés. C'est l'angle principal utilisé pour convertir les directions 3D des couches QGIS en positions X dans la photo.")
        _tip(self.d_vfov, "Champ vertical de l'image en degrés. Il convertit les élévations 3D en positions Y dans la photo et doit rester cohérent avec le ratio de l'image.")
        _tip(self.cb_auto_hfov, "Calcule automatiquement le HFOV à partir de la focale physique et de la largeur réelle du capteur lorsqu'elles sont disponibles ; l'équivalent 24×36 n'est utilisé qu'en repli.")
        _tip(self.spin_w, "Largeur de l'image en pixels. Elle sert au ratio image et au placement précis des overlays sur l'axe horizontal.")
        _tip(self.spin_h, "Hauteur de l'image en pixels. Elle sert au ratio image et au placement précis des overlays sur l'axe vertical.")
        _tip(self.d_focal, "Focale utilisée par le calcul automatique. QCALVIEW privilégie la focale physique EXIF avec la largeur réelle du capteur ; une focale équivalente 24×36 n'est utilisée qu'en repli.")
        _tip(self.d_sensorw, "Largeur physique du capteur utilisée en interne pour le HFOV. Elle est lue dans les métadonnées ; 36 mm sert uniquement au repli par focale équivalente 24×36.")
        _tip(self.btn_schematic_bg_color, "Couleur globale utilisée pour toutes les vues sans photographie. Elle n'est pas enregistrée par point de vue.")
        _tip(self.cb_schematic_bg_transparent, "Si activé, les vues schématiques exportées en PNG ont un véritable canal alpha. Dans l'aperçu, la transparence est matérialisée par un damier.")

        self.cmb_off_mode = QComboBox()
        self.cmb_off_mode.addItems(tr(["Pixels", "Pourcentage"]))
        self.spin_off_h = QDoubleSpinBox(); self.spin_off_h.setDecimals(3); self.spin_off_h.setRange(-5000.0, 5000.0); self.spin_off_h.setSingleStep(1.0); self.spin_off_h.setValue(0.0)
        self.spin_off_v = QDoubleSpinBox(); self.spin_off_v.setDecimals(3); self.spin_off_v.setRange(-5000.0, 5000.0); self.spin_off_v.setSingleStep(1.0); self.spin_off_v.setValue(0.0)
        self.btn_reset_offsets = QPushButton(tr("Réinitialiser offsets"))

        _tip(self.cmb_off_mode, "Unité des corrections de décalage image : pixels ou pourcentage de la largeur/hauteur.")
        _tip(self.spin_off_h, "Décalage horizontal appliqué à l'image/aux overlays pour corriger un centrage résiduel après calage.")
        _tip(self.spin_off_v, "Décalage vertical appliqué à l'image/aux overlays pour corriger une assiette résiduelle après calage.")
        _tip(self.btn_reset_offsets, "Remet les décalages horizontal et vertical à zéro.")
        _tip(self.cb_show_center_axis, "Affiche ou masque la barre centrale de l'image, utile pour contrôler le centre optique pendant le calage.")
        _tip(self.cb_show_pdv_axis, "Affiche ou masque la barre d'azimut du point de vue, utile pour visualiser l'axe de visée calibré.")
        _tip(self.d_symdist, "Longueur du symbole d'orientation sur la carte, exprimée en unités de la couche/projet.")
        _tip(self.cb_auto_depth, "Ajuste automatiquement la profondeur de l'aperçu pour accélérer le rendu pendant le travail.")
        _tip(self.cmb_perf_budget, "Profil de rendu utilisé pour équilibrer sécurité, vitesse et niveau de détail des overlays.")
        _tip(self.cb_block_heavy_layers, "Empêche les couches trop lourdes de bloquer l'interface lors du rendu des overlays.")
        _tip(self.lbl_render_budget, "Résumé du budget de rendu courant et de l'état des optimisations appliquées.")

        if not hasattr(self, "_compact_rows"):
            self._compact_rows = []

        def _compact_row(*widgets):

            w = QWidget()
            g = QGridLayout(w)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(4)
            items = []
            for item in widgets:
                if isinstance(item, tuple) and len(item) == 2:
                    lab = QLabel(tr(str(item[0])))
                    lab.setMinimumWidth(0)
                    lab.setSizePolicy(QC.QSizePolicy_Policy_Minimum, QC.QSizePolicy_Policy_Fixed)
                    wid = item[1]
                    try:
                        tip = wid.toolTip()
                        if tip:
                            lab.setToolTip(tr(tip))
                    except Exception:
                        pass
                    try:
                        wid.setMinimumWidth(0)
                        wid.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Fixed)
                    except Exception:
                        pass
                    items.append((lab, wid, "field"))
                elif isinstance(item, str):
                    lab = QLabel(tr(item))
                    lab.setMinimumWidth(0)
                    items.append((None, lab, "single"))
                elif item is not None:
                    try:
                        item.setMinimumWidth(0)
                    except Exception:
                        pass
                    items.append((None, item, "single"))
            w._qcv_grid = g
            w._qcv_items = items
            self._compact_rows.append(w)
            self._relayout_compact_row(w, 2)
            return w

        guides_row = QWidget()
        hg = QHBoxLayout(guides_row)
        hg.setContentsMargins(0, 0, 0, 0)
        hg.setSpacing(12)
        hg.addWidget(self.cb_show_center_axis)
        hg.addWidget(self.cb_show_pdv_axis)
        hg.addStretch(1)

        def _grid_card(row, col, title, widget, colspan=1):
            card = QFrame()
            card.setObjectName("qcvGridCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(8, 6, 8, 6)
            card_lay.setSpacing(4)
            if title:
                lab = QLabel(tr(title))
                lab.setObjectName("qcvFieldTitle")
                card_lay.addWidget(lab)
            card_lay.addWidget(widget)
            f.addWidget(card, row, col, 1, colspan)
            try:
                self._calage_cards.append((card, int(row), int(col), int(colspan)))
            except Exception:
                pass

        _grid_card(0, 0, "Orientation", _compact_row(("Azim.", self.d_yaw), ("Tang.", self.d_pitch), ("Pas", self.cmb_orientation_step), ("Roul.", self.d_roll), ("Alt./Z", self.d_camheight)))
        _grid_card(0, 1, "Projection", _compact_row(self.cmb_proj, self.cb_360, ("Distance", self.d_maxdist)))
        _grid_card(0, 2, "Paramètres caméra", _compact_row(("HFOV", self.d_hfov), ("VFOV", self.d_vfov), self.cb_auto_hfov, ("L", self.spin_w), ("H", self.spin_h), ("Focale", self.d_focal)))
        _grid_card(1, 0, "Guides image", guides_row)
        _grid_card(1, 1, "Offsets", _compact_row(("Unité", self.cmb_off_mode), ("H", self.spin_off_h), ("V", self.spin_off_v), self.btn_reset_offsets))
        _grid_card(1, 2, "Symbole carte", self.d_symdist)
        _grid_card(2, 0, "Performance", _compact_row(self.cb_auto_depth, ("Profil", self.cmb_perf_budget), self.cb_block_heavy_layers), 2)
        _grid_card(2, 2, "État rendu", self.lbl_render_budget)
        _grid_card(3, 0, "Vue schématique (global)", _compact_row(("Fond", self.btn_schematic_bg_color), self.cb_schematic_bg_transparent), 3)
        try:
            for _c in range(3):
                f.setColumnStretch(_c, 1)
        except Exception:
            pass
        self._relayout_calage_cards(force=True)

        g_cam.setContentLayout(f)
        self.tab_calage_layout.addWidget(g_cam)


        #--- POINT POSITION CAMERA ---
        self.grp_camera_layer = CollapsibleBox("Couche caméra", checked=True)
        content_campos = QWidget()
        f2 = QFormLayout(content_campos)
        self.cmb_camera = QgsMapLayerComboBox(); self.cmb_camera.setFilters(QC.QgsMapLayerProxyModel_Filter_PointLayer)
        self.cmb_cam_id_field = QComboBox()
        self.cmb_cam_label_field = QComboBox()
        self.cmb_cam_order_field = QComboBox()
        self.cmb_cam_image_field = QComboBox()
        self.cmb_cam_feature = QComboBox()
        self.btn_cam_prev = QPushButton(tr("◀"))
        self.btn_cam_next = QPushButton(tr("▶"))
        self.btn_cam_load = QPushButton(tr("Charger le point"))
        self.btn_cam_save = QPushButton(tr("Enregistrer paramètres + état"))
        self.btn_cam_fields = QPushButton(tr("Créer champs QCALVIEW"))
        self.btn_cam_source_auto = QPushButton(tr("Photo du champ"))
        self.btn_cam_source_photo = QPushButton(tr("Associer photo…"))
        self.btn_cam_source_schema = QPushButton(tr("Vue schématique"))
        self.btn_cam_batch_img = QPushButton(tr("Batch image+overlays…"))
        self.btn_cam_batch_png = QPushButton(tr("Batch overlays PNG…"))
        self.btn_cam_csv = QPushButton(tr("Exporter CSV variables…"))
        self.cb_cam_show_all = QCheckBox(tr("Afficher tous les cônes enregistrés"))
        self.cb_cam_show_all.setChecked(False)
        self.cb_cam_apply_style = QCheckBox(tr("Appliquer le style QCALVIEW à la couche PDV"))
        self.cb_cam_apply_style.setChecked(False)
        self.cb_cam_apply_style.setToolTip(tr("Désactivé par défaut. Si activé, le style QCALVIEW est appliqué uniquement à la couche explicitement choisie ci-dessus."))
        self.lbl_cam_status = QLabel(tr("Aucun point de vue chargé."))
        self.lbl_cam_status.setStyleSheet("color:#666;")
        f2.addRow(tr("Couche caméra"), self.cmb_camera)
        f2.addRow(tr(self.cb_cam_apply_style))
        f2.addRow(tr("Champ identifiant"), self.cmb_cam_id_field)
        f2.addRow(tr("Champ libellé"), self.cmb_cam_label_field)
        f2.addRow(tr("Champ d’ordre"), self.cmb_cam_order_field)
        f2.addRow(tr("Champ image"), self.cmb_cam_image_field)
        f2.addRow(tr("Point de vue"), _compact_row(self.btn_cam_prev, self.cmb_cam_feature, self.btn_cam_next))
        f2.addRow(tr("Source du PDV"), _compact_row(self.btn_cam_source_auto, self.btn_cam_source_photo, self.btn_cam_source_schema))
        self.cmb_cam_id_field.setToolTip(tr("Identifiant court du point de vue."))
        self.cmb_cam_label_field.setToolTip(tr("Libellé descriptif affiché dans la visionneuse. Peut être laissé vide."))
        self.cmb_cam_order_field.setToolTip(tr("Champ utilisé pour l’ordre des flèches précédent/suivant. Si vide, QCALVIEW utilise l’identifiant puis le FID."))
        self.cmb_cam_image_field.setToolTip(tr("Champ image utilisé en mode automatique. Un PDV réglé sur « Vue schématique » ignore volontairement tous les chemins d'image historiques."))
        self.btn_cam_source_auto.setToolTip(tr("Réutiliser la photographie indiquée par le Champ image / les champs historiques de la couche."))
        self.btn_cam_source_photo.setToolTip(tr("Choisir ou remplacer explicitement la photographie de ce point de vue."))
        self.btn_cam_source_schema.setToolTip(tr("Dissocier la photographie pour ce point de vue et enregistrer une vue schématique."))
        f2.addRow(tr(self.cb_cam_show_all))
        self.btn_cam_save.setToolTip(tr("Enregistre les paramètres caméra et l’état visuel du PDV : couches, styles, opacités, règles/catégories, thème et réglages QCALVIEW."))
        f2.addRow(tr("Synchronisation"), _compact_row(self.btn_cam_load, self.btn_cam_save, self.btn_cam_fields))
        f2.addRow(tr(self.lbl_cam_status))
        self.grp_camera_layer.setContentLayout(f2)
        self.tab_camera_layout.addWidget(self.grp_camera_layer)

        g_nav = QGroupBox(tr("Navigation image ↔ canevas"))
        fn = QFormLayout(g_nav)
        self.btn_pick_view_from_map = QPushButton(tr("Viser depuis le canevas"))
        self.btn_pick_ray_from_image = QPushButton(tr("Cliquer dans l’image → carte"))
        self.btn_stop_nav = QPushButton(tr("Stop"))
        self.cb_pick_sets_pitch = QCheckBox(tr("Tangage auto (si altitude dispo)"))
        self.cb_pick_sets_pitch.setChecked(True)
        self.cb_center_canvas_on_pick = QCheckBox(tr("Centrer la carte sur la cible"))
        self.cb_center_canvas_on_pick.setChecked(False)
        self.lbl_nav_state = QLabel(tr("Mode navigation : inactif"))
        self.lbl_nav_state.setStyleSheet("color:#666;")
        row_nav_btns = _compact_row(self.btn_pick_view_from_map, self.btn_pick_ray_from_image, self.btn_stop_nav)
        row_nav_opts = _compact_row(self.cb_pick_sets_pitch, self.cb_center_canvas_on_pick)
        fn.addRow(tr(row_nav_btns))
        fn.addRow(tr(row_nav_opts))
        fn.addRow(tr(self.lbl_nav_state))
        self.tab_camera_layout.addWidget(g_nav)

        self.grp_exp = CollapsibleBox(tr("Outils expérimentaux"), checked=False)
        content_exp = QWidget()
        fe = QFormLayout(content_exp)
        self.cb_show_experimental = QCheckBox(tr("Afficher modules expérimentaux"))
        self.cb_show_experimental.setChecked(self._read_bool_setting("QCALVIEW/ui/show_experimental_tools", False))
        self.lbl_exp_status = QLabel(tr("Les modules expérimentaux restent masqués par défaut."))
        self.lbl_exp_status.setStyleSheet("color:#666;")
        fe.addRow(tr(self.cb_show_experimental))
        fe.addRow(tr(self.lbl_exp_status))
        self.grp_exp.setContentLayout(fe)
        self.tab_camera_layout.addWidget(self.grp_exp)
        self.grp_exp.setVisible(False)  # piloté depuis la fenêtre Paramètres
 
        # --- GCP & CALCULS HFOV ET AZIMUTH/TANGAGE/ROULIS : OBJECTIF AUTOMATISER LE POSITIONNEMENT ---

        self.lbl_experimental_notice = QLabel(
            tr("ALPHA-40.20 — Ces outils sont encore en cours de développement. Leur comportement et leur interface peuvent évoluer dans les prochaines versions Alpha.")
        )
        self.lbl_experimental_notice.setWordWrap(True)
        self.lbl_experimental_notice.setStyleSheet("color:#666; padding:4px 2px 8px 2px;")
        self.tab_gcp_layout.addWidget(self.lbl_experimental_notice)
        
        self.gcps = []  # liste de dicts {u,v,x,y,z} avec u,v en px image full
        self._adding_gcp_uv = None
        self._gcp_markers = []
        self._maptool_backup = None
        self._monoplot_records = []
        self._monoplot_visible = []
        self._monoplot_counter = 0
        self._monoplot_active_tool = None

        self.grp_gcp = CollapsibleBox(tr("Recalage assisté"), checked=False)
        fg = QFormLayout(self.grp_gcp)

        self.list_gcp = QListWidget()
        btn_pick_center = QPushButton(tr("Pointer centre (PDV)"))
        btn_pick_center.clicked.connect(self.start_pick_pdv_center)
        hb = QHBoxLayout()
        self.btn_add_gcp = QPushButton(tr("Ajouter GCP (photo → carte)"))
        self.btn_del_gcp = QPushButton(tr("Supprimer"))
        self.btn_clear_gcp = QPushButton(tr("Tout effacer"))
        hb.addWidget(self.btn_add_gcp); hb.addWidget(self.btn_del_gcp); hb.addWidget(self.btn_clear_gcp)
        self.install_global_shortcuts()

        # Paramètres à estimer
        self.cb_sol_yaw = QCheckBox(tr("Yaw"));   self.cb_sol_yaw.setChecked(True)
        self.cb_sol_pitch = QCheckBox(tr("Pitch")); self.cb_sol_pitch.setChecked(True)
        self.cb_sol_roll = QCheckBox(tr("Roll"));  self.cb_sol_roll.setChecked(True)
        self.cb_sol_hfov = QCheckBox(tr("HFOV"));  self.cb_sol_hfov.setChecked(True)
        row_params = QHBoxLayout()
        row_params.addWidget(QLabel(tr("Paramètres à estimer :")))
        for w in (self.cb_sol_yaw, self.cb_sol_pitch, self.cb_sol_roll, self.cb_sol_hfov):
            row_params.addWidget(w)

        self.btn_solve = QPushButton(tr("Résoudre caméra"))

        fg.addRow(tr(self.list_gcp))
        fg.addRow(tr(hb))
        fg.addRow(tr(row_params))
        fg.addRow(tr(self.btn_solve))

        self.lbl_gcp_dev = QLabel(tr("Module expérimental dédié au recalage assisté et à la résolution des paramètres caméra."))
        self.lbl_gcp_dev.setStyleSheet("color:#666;")
        fg.addRow(tr(self.lbl_gcp_dev))

        self.grp_gcp.setContentLayout(fg)

        self.grp_monoplot = CollapsibleBox(tr("Monoplotting interactif (expérimental)"), checked=False)
        fm = QFormLayout(self.grp_monoplot)
        self.btn_monoplot_map = QPushButton(tr("Repère depuis la carte"))
        self.btn_monoplot_image = QPushButton(tr("Interroger le terrain depuis l'image"))
        self.btn_monoplot_stop = QPushButton(tr("Stop"))
        self.btn_monoplot_clear = QPushButton(tr("Effacer les repères"))
        self.list_monoplot = QListWidget()
        self.lbl_monoplot_status = QLabel(tr("Aucun repère monoplotting."))
        self.lbl_monoplot_status.setStyleSheet("color:#666;")
        fm.addRow(tr(_compact_row(self.btn_monoplot_map, self.btn_monoplot_image, self.btn_monoplot_stop)))
        fm.addRow(tr(self.btn_monoplot_clear))
        fm.addRow(tr(self.list_monoplot))
        fm.addRow(tr(self.lbl_monoplot_status))
        self.grp_monoplot.setContentLayout(fm)
        self.tab_gcp_layout.addWidget(self.grp_monoplot)
        self.tab_gcp_layout.addWidget(self.grp_gcp)

        self.btn_add_gcp.clicked.connect(self._start_add_gcp)
        self.btn_del_gcp.clicked.connect(self._delete_gcp)
        self.btn_clear_gcp.clicked.connect(self._clear_gcps)
        self.btn_solve.clicked.connect(self._solve_camera)
        self.btn_pick_view_from_map.clicked.connect(self.start_pick_view_from_canvas)
        self.btn_pick_ray_from_image.clicked.connect(self.start_image_to_canvas_pick)
        self.btn_stop_nav.clicked.connect(self.stop_interaction_tools)
        self.btn_monoplot_map.clicked.connect(self.start_monoplot_map_to_image)
        self.btn_monoplot_image.clicked.connect(self.start_monoplot_image_to_ground)
        self.btn_monoplot_stop.clicked.connect(self.stop_monoplot_tools)
        self.btn_monoplot_clear.clicked.connect(self.clear_monoplot_reperes)


        # --- GESTION DU RELIEF / DE L'AFFICHAGE DE LA TOPOGRAPHIE
        self._relief_grid_container = QWidget()
        self._relief_grid_layout = QGridLayout(self._relief_grid_container)
        self._relief_grid_layout.setContentsMargins(0, 0, 0, 0)
        self._relief_grid_layout.setHorizontalSpacing(10)
        self._relief_grid_layout.setVerticalSpacing(8)
        self.tab_relief_layout.addWidget(self._relief_grid_container)

        self.grp_dem = CollapsibleBox("Relief (MNT/MNS)", checked=True)
        content_dem = QWidget()
        form_dem = QFormLayout(content_dem)
        self.cmb_dem = QgsMapLayerComboBox(); self.cmb_dem.setFilters(QC.QgsMapLayerProxyModel_Filter_RasterLayer)
        self.cb_use_dem_z = QCheckBox(tr("Utiliser le MNT/MNS pour les altitudes (caméra + objets)"))
        self.cb_use_dem_z.setChecked(False)
        self.combo_relief_mode = QComboBox()
        self.combo_relief_mode.addItems(tr(["Aucun", "Transparent (masquant)", "Opaque", "Wireframe", "Ridgelines", "Skyline"]))
        self.combo_relief_mode.setCurrentIndex(0)
        self.spin_dem_step = QDoubleSpinBox(); self.spin_dem_step.setRange(5.0, 2000.0); self.spin_dem_step.setValue(150.0); self.spin_dem_step.setSuffix(tr(" m"))
        self.btn_dem_color = QPushButton(tr("Couleur relief…")); self._dem_color = QColor(255, 255, 0, 220)
        self.spin_dem_width = QSpinBox(); self.spin_dem_width.setRange(1, 6); self.spin_dem_width.setValue(1)
        self.spin_dem_alpha = QSpinBox(); self.spin_dem_alpha.setRange(0, 255); self.spin_dem_alpha.setValue(220); self.spin_dem_alpha.setVisible(False)
        self.cb_curvature = QCheckBox(tr("Prendre en compte la courbure terrestre"))
        self.cb_curvature.setChecked(True)
        self.d_earth_radius_km = QDoubleSpinBox(); self.d_earth_radius_km.setRange(6000.0, 7000.0); self.d_earth_radius_km.setDecimals(0)
        try:
            self.d_earth_radius_km.setValue(float(self._settings.value("QCALVIEW/physics/earth_radius_km", 6370.0)))
        except Exception:
            self.d_earth_radius_km.setValue(6370.0)
        self.d_earth_radius_km.setSuffix(tr(" km"))
        self.d_earth_radius_km.setToolTip(tr("Rayon terrestre utilisé pour la correction de courbure. Réglage avancé : Paramètres > Variables."))
        self.d_earth_radius_km.setVisible(False)
        form_dem.addRow(tr("Raster MNT/MNS"), self.cmb_dem)
        form_dem.addRow(tr(self.cb_use_dem_z))
        form_dem.addRow(tr("Mode relief"), self.combo_relief_mode)
        form_dem.addRow(tr("Échantillonnage"), self.spin_dem_step)
        form_dem.addRow(tr(self.cb_curvature))
        self._form_dem = form_dem
        hsty = QHBoxLayout(); hsty.addWidget(self.btn_dem_color); hsty.addWidget(QLabel(tr("Épaisseur"))); hsty.addWidget(self.spin_dem_width)
        form_dem.addRow(tr("Style relief"), hsty)

        self.cb_wire_dashed = QCheckBox(tr("Wireframe en pointillés"))
        form_dem.addRow(tr(self.cb_wire_dashed))

        self.combo_wire_mode = QComboBox()
        self.combo_wire_mode.addItem(tr("Filaire complet"), 0)
        self.combo_wire_mode.addItem(tr("Arêtes supérieures"), 2)
        self.combo_wire_mode.setCurrentIndex(1)
        self.lbl_wire_mode = QLabel(tr("Mode wireframe"))
        form_dem.addRow(tr(self.lbl_wire_mode), self.combo_wire_mode)

        # Contrôles legacy masqués, conservés pour compatibilité interne.
        self.cb_show_dem = QCheckBox(tr("_legacy_show_dem")); self.cb_show_dem.setChecked(False); self.cb_show_dem.setVisible(False)
        self.cb_transparent_topo = QCheckBox(tr("_legacy_transparent_topo")); self.cb_transparent_topo.setChecked(False); self.cb_transparent_topo.setVisible(False)
        self.cb_draw_skyline = QCheckBox(tr("_legacy_draw_skyline")); self.cb_draw_skyline.setChecked(False); self.cb_draw_skyline.setVisible(False)
        self.cb_draw_ridgelines = QCheckBox(tr("_legacy_draw_ridgelines")); self.cb_draw_ridgelines.setChecked(False); self.cb_draw_ridgelines.setVisible(False)
        self.cb_occ_terrain = QCheckBox(tr("_legacy_occ_terrain")); self.cb_occ_terrain.setChecked(False); self.cb_occ_terrain.setVisible(False)
        self.cb_skyline_fill = QCheckBox(tr("_legacy_sky_fill")); self.cb_skyline_fill.setChecked(False); self.cb_skyline_fill.setVisible(False)

        self.grp_dem.setContentLayout(form_dem)
        self._relief_grid_layout.addWidget(self.grp_dem, 0, 0)

        self.grp_skyline = CollapsibleBox("Skyline / ridgelines", checked=True)
        content_sky = QWidget()
        fs = QFormLayout(content_sky)
        self.spin_ridge_count = QSpinBox(); self.spin_ridge_count.setRange(1, 5); self.spin_ridge_count.setValue(1); self.spin_ridge_count.setVisible(False)
        self.d_ridge_gap = QDoubleSpinBox(); self.d_ridge_gap.setRange(10.0, 2000.0); self.d_ridge_gap.setDecimals(1); self.d_ridge_gap.setValue(250.0); self.d_ridge_gap.setSuffix(tr(" m"))
        self.d_ridge_prom = QDoubleSpinBox(); self.d_ridge_prom.setRange(0.1, 10.0); self.d_ridge_prom.setDecimals(2); self.d_ridge_prom.setValue(2.0); self.d_ridge_prom.setSuffix(tr(" °"))
        self.btn_sky_color = self.btn_dem_color
        self._sky_color = QColor(self._dem_color)
        self.spin_sky_width = self.spin_dem_width
        self.cb_sky_dashed = QCheckBox(tr("Skyline en pointillés"))
        self.spin_sky_fill_alpha = QSpinBox(); self.spin_sky_fill_alpha.setRange(0, 255); self.spin_sky_fill_alpha.setValue(255); self.spin_sky_fill_alpha.setVisible(False)
        fs.addRow(tr("Écart radial min ridgelines"), self.d_ridge_gap)
        fs.addRow(tr("Seuil angulaire ridgelines"), self.d_ridge_prom)
        fs.addRow(tr(self.cb_sky_dashed))
        self.grp_skyline.setContentLayout(fs)
        form_dem.addRow(tr(self.grp_skyline))


        # Occlusions & Débogage
        self.grp_occ = CollapsibleBox(tr("Occlusions / débogage"), checked=False)
        content_occ = QWidget()
        fo = QFormLayout(content_occ)
        self.cb_occ_layers = QCheckBox(tr("_legacy_occ_layers")); self.cb_occ_layers.setChecked(False); self.cb_occ_layers.setVisible(False)
        self.d_az_step = QDoubleSpinBox(); self.d_az_step.setRange(0.1, 5.0); self.d_az_step.setDecimals(2); self.d_az_step.setValue(0.5); self.d_az_step.setSuffix(tr(" °"))
        self.d_rad_step = QDoubleSpinBox(); self.d_rad_step.setRange(1.0, 500.0); self.d_rad_step.setDecimals(1); self.d_rad_step.setValue(50.0); self.d_rad_step.setSuffix(tr(" m"))
        self.d_eps = QDoubleSpinBox(); self.d_eps.setRange(0.0, 1.0); self.d_eps.setDecimals(2); self.d_eps.setValue(0.20); self.d_eps.setSuffix(tr(" °"))
        self.cb_occ_objects = QCheckBox(tr("Masquage inter-objets (legacy panoramique)")); self.cb_occ_objects.setChecked(True); self.cb_occ_objects.setVisible(False)
        self.cb_transparent_objects = QCheckBox(tr("Forcer toutes les couches en transparence (ancien mode global)")); self.cb_transparent_objects.setChecked(False)
        self.cb_debug_no_occ = QCheckBox(tr("Mode debug : ignorer toute occlusion")); self.cb_debug_no_occ.setChecked(False)
        self.cb_show_guides = QCheckBox(tr("Afficher repères FOV & axes")); self.cb_show_guides.setChecked(False)
        fo.addRow(tr(self.cb_occ_objects))
        fo.addRow(tr(self.cb_transparent_objects))
        fo.addRow(tr(self.cb_debug_no_occ))
        fo.addRow(tr(self.cb_show_guides))
        self.grp_occ.setContentLayout(fo)
        
        # --- Profiling (debug) ---
        self.profiler = get_profiler()
        self.cb_enable_profiler = QCheckBox(tr("Activer profiling (debug)"))
        self.cb_enable_profiler.toggled.connect(lambda c: setattr(self.profiler, 'enabled', c))
        self.btn_profiler_report = QPushButton(tr("📊 Rapport perf"))
        self.btn_profiler_report.clicked.connect(self._show_profiler_report)
        fo.addRow(tr(self.cb_enable_profiler))
        fo.addRow(tr(self.btn_profiler_report))
        self.tab_gcp_layout.addWidget(self.grp_occ)
        
        # --- PERSPECTIVE 3D ---
        self.grp_calib = CollapsibleBox(tr("Grille de projection / règle azimutale (expérimental)") if PUBLIC_EXPERIMENTAL_LIMITED else tr("Aides 3D (expérimental)"), checked=False)
        content_calib = QWidget()
        calib_v = QVBoxLayout(content_calib)

        self.cb_calib_enable = QCheckBox(tr("Activer la grille/cube de calibration"))
        self.cb_calib_enable.setChecked(False)
        self.cb_proj_grid_enable = QCheckBox(tr("Afficher la grille de projection"))
        self.cb_proj_grid_enable.setChecked(False)
        self.d_proj_grid_step = QDoubleSpinBox(); self.d_proj_grid_step.setRange(5.0, 45.0); self.d_proj_grid_step.setSingleStep(5.0); self.d_proj_grid_step.setDecimals(0); self.d_proj_grid_step.setValue(5.0); self.d_proj_grid_step.setSuffix(tr(" °"))
        self.cmb_calib_type = QComboBox(); self.cmb_calib_type.addItems(tr(["Plan au sol", "Plan vertical", "Cube étalon"]))

        self.d_calib_spacing = QDoubleSpinBox(); self.d_calib_spacing.setRange(0.1, 1000.0); self.d_calib_spacing.setValue(5.0); self.d_calib_spacing.setSuffix(tr(" m"))
        self.d_calib_width   = QDoubleSpinBox(); self.d_calib_width.setRange(1.0, 5000.0); self.d_calib_width.setValue(50.0); self.d_calib_width.setSuffix(tr(" m"))
        self.d_calib_depth   = QDoubleSpinBox(); self.d_calib_depth.setRange(0.0, 5000.0); self.d_calib_depth.setValue(50.0); self.d_calib_depth.setSuffix(tr(" m"))
        self.d_calib_height  = QDoubleSpinBox(); self.d_calib_height.setRange(0.0, 2000.0); self.d_calib_height.setValue(10.0); self.d_calib_height.setSuffix(tr(" m"))
        self.d_calib_dist    = QDoubleSpinBox(); self.d_calib_dist.setRange(0.0, 5000.0); self.d_calib_dist.setValue(30.0); self.d_calib_dist.setSuffix(tr(" m"))
        self.d_calib_elev    = QDoubleSpinBox(); self.d_calib_elev.setRange(-2000.0, 2000.0); self.d_calib_elev.setValue(0.0); self.d_calib_elev.setSuffix(tr(" m (offset)"))
        self.cb_calib_snap_dem = QCheckBox(tr("Caler la base sur le MNT au centre"))
        self.cb_calib_snap_dem.setChecked(True)

        self.btn_calib_color = QPushButton(tr("Couleur…")); self._calib_color = QColor(0, 255, 255, 200)
        self.spin_calib_width = QSpinBox(); self.spin_calib_width.setRange(1, 6); self.spin_calib_width.setValue(1)
        self.cb_calib_labels = QCheckBox(tr("Graduations (m)")); self.cb_calib_labels.setChecked(True)
        self.cb_calib_axes   = QCheckBox(tr("Axes XYZ au centre")); self.cb_calib_axes.setChecked(True)

        self.cb_az_rule_enable = QCheckBox(tr("Afficher la règle azimutale"))
        self.cb_az_rule_enable.setChecked(False)
        self.d_az_rule_band_pct = QDoubleSpinBox(); self.d_az_rule_band_pct.setRange(1.0, 10.0); self.d_az_rule_band_pct.setDecimals(1); self.d_az_rule_band_pct.setSingleStep(0.5); self.d_az_rule_band_pct.setValue(4.0); self.d_az_rule_band_pct.setSuffix(tr(" %"))
        self.d_az_rule_text_pct = QDoubleSpinBox(); self.d_az_rule_text_pct.setRange(20.0, 80.0); self.d_az_rule_text_pct.setDecimals(0); self.d_az_rule_text_pct.setSingleStep(5.0); self.d_az_rule_text_pct.setValue(38.0); self.d_az_rule_text_pct.setSuffix(tr(" % bandeau"))

        self.grp_proj_grid = QGroupBox(tr("Grille de projection"))
        fproj = QFormLayout(self.grp_proj_grid)
        fproj.addRow(tr(self.cb_proj_grid_enable))
        fproj.addRow(tr("Pas angulaire"), self.d_proj_grid_step)

        self.grp_az_rule = QGroupBox(tr("Règle azimutale"))
        faz = QFormLayout(self.grp_az_rule)
        faz.addRow(tr(self.cb_az_rule_enable))
        faz.addRow(tr("Hauteur du bandeau"), self.d_az_rule_band_pct)
        faz.addRow(tr("Taille du texte"), self.d_az_rule_text_pct)

        self.grp_calib_world = QGroupBox(tr("Grille / cube de calibration"))
        fcal = QFormLayout(self.grp_calib_world)
        fcal.addRow(tr(self.cb_calib_enable))
        fcal.addRow(tr("Type"), self.cmb_calib_type)
        fcal.addRow(tr("Pas (m)"), self.d_calib_spacing)
        fcal.addRow(tr("Largeur (m)"), self.d_calib_width)
        fcal.addRow(tr("Profondeur (m) / Cube"), self.d_calib_depth)
        fcal.addRow(tr("Hauteur (m) (vertical/cube)"), self.d_calib_height)
        fcal.addRow(tr("Distance devant caméra (m)"), self.d_calib_dist)
        fcal.addRow(tr("Décalage d’altitude (m)"), self.d_calib_elev)
        fcal.addRow(tr(self.cb_calib_snap_dem))
        hcal = QHBoxLayout(); hcal.addWidget(self.btn_calib_color); hcal.addWidget(QLabel(tr("Épaisseur"))); hcal.addWidget(self.spin_calib_width)
        fcal.addRow(tr("Style"), hcal)
        fcal.addRow(tr(self.cb_calib_labels))
        fcal.addRow(tr(self.cb_calib_axes))

        calib_v.addWidget(self.grp_proj_grid)
        calib_v.addWidget(self.grp_az_rule)
        calib_v.addWidget(self.grp_calib_world)
        self.grp_calib_world.setVisible(not PUBLIC_EXPERIMENTAL_LIMITED)
        self.grp_calib.setContentLayout(calib_v)
        self.tab_gcp_layout.addWidget(self.grp_calib)
        try:
            for _w in (self.grp_calib, self.grp_gcp, self.grp_monoplot, self.grp_occ):
                self.tab_gcp_layout.removeWidget(_w)
            for _w in (self.grp_calib, self.grp_gcp, self.grp_monoplot, self.grp_occ):
                _w.setChecked(False)
                self.tab_gcp_layout.addWidget(_w)
        except Exception:
            pass

        # --- 2,5D ---
        g_h = QGroupBox(tr("2,5D Hauteurs (végétation/bâti)"))
        fh = QFormLayout(g_h)
        self.cb_draw_2p5d = QCheckBox(tr("Extruder les polygones"))
        self.cb_draw_2p5d.setChecked(True)
        self.cb_force_horizontal_25d = QCheckBox(tr("Forcer l’horizontalité des volumes 2,5D"))
        self.cb_force_horizontal_25d.setChecked(True)
        self.txt_hfield = QLineEdit("hauteur")
        self.d_hdefault = QDoubleSpinBox(); self.d_hdefault.setRange(0.0, 500.0); self.d_hdefault.setValue(3.0); self.d_hdefault.setSuffix(tr(" m"))
        fh.addRow(tr(self.cb_draw_2p5d))
        fh.addRow(tr(self.cb_force_horizontal_25d))
        fh.addRow(tr("Champ hauteur (si existant) "), self.txt_hfield)
        fh.addRow(tr("Hauteur par défaut"), self.d_hdefault)
        self.tab_layers_layout.addWidget(g_h)
        try:
            self._relief_grid_layout.setColumnStretch(0, 1)
            self._relief_grid_layout.setColumnStretch(1, 1)
        except Exception:
            pass

        # --- COUCHES VECTEUR A REPRESENTER ---
        g_layers = CollapsibleBox("Couches vectorielles à projeter", checked=True)
        content_layers = QWidget()
        ly = QVBoxLayout(content_layers)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(8)

        hb_theme = QHBoxLayout()
        self.btn_refresh_themes = self._tool_button("Rafraîchir", "refresh.svg", "Actualiser la liste des thèmes QGIS")
        self.btn_apply_theme = QPushButton(tr("Appliquer le thème"))
        self.cb_theme_auto_sync = QCheckBox(tr("Synchro auto avec le thème"))
        self.cb_theme_auto_sync.setToolTip(tr("Réappliquer automatiquement le thème choisi quand sa définition change"))
        hb_theme.addWidget(QLabel(tr("Thème QGIS")))
        self.lbl_theme_in_layers = QLabel(tr("Sélection dans le cockpit supérieur"))
        self.lbl_theme_in_layers.setMinimumWidth(0)
        hb_theme.addWidget(self.lbl_theme_in_layers, 1)
        hb_theme.addWidget(self.btn_apply_theme)
        hb_theme.addWidget(self.btn_refresh_themes)
        hb_theme.addWidget(self.cb_theme_auto_sync)
        ly.addLayout(hb_theme)

        hb2 = QHBoxLayout()
        self.cmb_src = QgsMapLayerComboBox(); self.cmb_src.setFilters(QC.QgsMapLayerProxyModel_Filter_VectorLayer)
        self.cmb_src.setToolTip(tr("Seules les couches vectorielles sont proposées ; les rasters sont ignorés."))
        self.btn_add_layer = self._tool_button("Ajouter", "add.svg", "Ajouter la couche vectorielle sélectionnée")
        self.btn_remove_layer = self._tool_button("Retirer", "remove.svg", "Retirer la couche projetée")
        self.btn_up = self._tool_button("Monter", "move_up.svg", "Monter dans l'ordre de dessin")
        self.btn_down = self._tool_button("Descendre", "move_down.svg", "Descendre dans l'ordre de dessin")
        self.btn_style = self._tool_button("Style…", "style.svg", "Régler le style de la couche projetée")
        hb2.addWidget(self.cmb_src, 1)
        hb2.addWidget(self.btn_add_layer)
        hb2.addWidget(self.btn_remove_layer)
        hb2.addWidget(self.btn_style)
        hb2.addWidget(self.btn_up)
        hb2.addWidget(self.btn_down)
        ly.addLayout(hb2)

        self.list_layers = QTableWidget(0, 6)
        self.list_layers.setHorizontalHeaderLabels(tr(["Visible", "Couche", "Hauteur", "Mode", "Opacité", "Couleurs / style"]))
        self.list_layers.setSelectionBehavior(QC.QAbstractItemView_SelectionBehavior_SelectRows)
        self.list_layers.setSelectionMode(QC.QAbstractItemView_SelectionMode_SingleSelection)
        self.list_layers.setEditTriggers(QC.QAbstractItemView_EditTrigger_NoEditTriggers)
        self.list_layers.verticalHeader().setVisible(False)
        self.list_layers.setAlternatingRowColors(True)
        self.list_layers.setMinimumHeight(180)
        try:
            header = self.list_layers.horizontalHeader()
            header.setSectionResizeMode(0, QC.QHeaderView_ResizeMode_ResizeToContents)
            header.setSectionResizeMode(1, QC.QHeaderView_ResizeMode_Stretch)
            for _c in (2, 3, 4, 5):
                header.setSectionResizeMode(_c, QC.QHeaderView_ResizeMode_ResizeToContents)
        except Exception:
            pass
        ly.addWidget(self.list_layers, 1)

        g_layers.setContentLayout(ly)
        self.tab_layers_layout.addWidget(g_layers)
        # --- EXPORT ---
        g_export = CollapsibleBox("Export", checked=True)
        export_box = QWidget()
        export_layout = QGridLayout(export_box)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setHorizontalSpacing(8)
        export_layout.setVerticalSpacing(8)
        self.btn_refresh = self._tool_button("Actualiser", "render.svg", "Forcer un rendu haute qualité")
        self.btn_export_current = self._tool_button("Vue composite…", "export.svg", "Exporter la photo + overlays ou la vue schématique complète")
        self.btn_export_overlay = self._tool_button("Overlay seul PNG…", "export.svg", "Exporter les overlays seuls en PNG transparent")
        self.btn_export_legend  = self._tool_button("Légende PNG…", "legend.svg", "Exporter une légende PNG")
        self.cb_export_metadata = QCheckBox(tr("Écrire les métadonnées EXIF lorsque c’est possible"))
        self.cb_export_metadata.setVisible(False)
        export_layout.addWidget(self.btn_refresh, 0, 0)
        export_layout.addWidget(self.btn_export_current, 0, 1)
        export_layout.addWidget(self.btn_export_overlay, 0, 2)
        export_layout.addWidget(self.btn_export_legend, 0, 3)

        self.cb_batch_composite = QCheckBox(tr("Vue composite (photo JPG / schéma PNG)"))
        self.cb_batch_composite.setChecked(True)
        self.cb_batch_overlay = QCheckBox(tr("Overlay seul PNG transparent"))
        self.cb_batch_overlay.setChecked(False)
        self.cb_batch_csv = QCheckBox(tr("CSV variables"))
        self.cb_batch_csv.setChecked(False)
        self.btn_export_batch_selected = self._tool_button("Exporter la sélection…", "export.svg", "Exporter les points de vue cochés")
        self.btn_export_refresh_pdv = self._tool_button("Rafraîchir PDV", "refresh.svg", "Actualiser la liste des points de vue")
        self.btn_export_select_all = QPushButton(tr("Tout cocher"))
        self.btn_export_select_none = QPushButton(tr("Tout décocher"))
        self.btn_export_select_all.setToolTip(tr("Cocher tous les points de vue du tableau pour l'export"))
        self.btn_export_select_none.setToolTip(tr("Décocher tous les points de vue du tableau"))
        export_layout.addWidget(QLabel(tr("Batch")), 1, 0)
        export_layout.addWidget(_compact_row(self.cb_batch_composite, self.cb_batch_overlay, self.cb_batch_csv), 1, 1, 1, 2)
        export_layout.addWidget(_compact_row(self.btn_export_refresh_pdv, self.btn_export_select_all, self.btn_export_select_none, self.btn_export_batch_selected), 1, 3)

        self.tbl_export_pdv = QTableWidget(0, 13)
        self.tbl_export_pdv.setHorizontalHeaderLabels(tr([
            "✓", "PDV", "Image", "Projection", "Azimut", "Tangage", "Roulis",
            "HFOV", "VFOV", "Offset H", "Offset V", "Enregistré", "État"
        ]))
        self.tbl_export_pdv.setSelectionBehavior(QC.QAbstractItemView_SelectionBehavior_SelectRows)
        self.tbl_export_pdv.setSelectionMode(QC.QAbstractItemView_SelectionMode_ExtendedSelection)
        self.tbl_export_pdv.setAlternatingRowColors(True)
        self.tbl_export_pdv.verticalHeader().setVisible(False)
        self.tbl_export_pdv.setMinimumHeight(120)
        self.tbl_export_pdv.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Expanding)
        try:
            hdr = self.tbl_export_pdv.horizontalHeader()
            hdr.setSectionResizeMode(0, QC.QHeaderView_ResizeMode_ResizeToContents)
            hdr.setSectionResizeMode(1, QC.QHeaderView_ResizeMode_Stretch)
            hdr.setSectionResizeMode(2, QC.QHeaderView_ResizeMode_Stretch)
            for _c in range(3, 13):
                hdr.setSectionResizeMode(_c, QC.QHeaderView_ResizeMode_ResizeToContents)
        except Exception:
            pass
        export_layout.addWidget(self.tbl_export_pdv, 2, 0, 1, 4)
        try:
            export_layout.setRowStretch(2, 1)
            export_box.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Expanding)
            g_export.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Expanding)
        except Exception:
            pass
        self.lbl_batch_status = QLabel(tr("Le batch utilise les réglages effectifs affichés ci-dessus. « Brouillon » signale un réglage non encore enregistré dans la couche PDV."))
        self.lbl_batch_status.setWordWrap(True)
        self.lbl_batch_status.setStyleSheet("color:#666;")
        export_layout.addWidget(self.lbl_batch_status, 3, 0, 1, 4)
        g_export.setContentLayout(export_layout)
        self.tab_export_layout.addWidget(g_export)

        self.tab_camera_layout.addStretch(1)
        self.tab_calage_layout.addStretch(1)
        self.tab_relief_layout.addStretch(1)
        self.tab_layers_layout.addStretch(1)
        self.tab_export_layout.addStretch(1)
        self.tab_gcp_layout.addStretch(1)
        self.tab_calib3d_layout.addStretch(1)

        self.setWidget(root)

        # état
        self.layer_styles = []
        try:
            self.cb_export_metadata.setChecked(str(self._settings.value('QCALVIEW/export_write_metadata', 'true')).lower() in ('1','true','yes','on'))
            self.cb_lowlat.setChecked(self._read_bool_setting("QCALVIEW/ui/low_latency_preview", True))
            self.cb_show_labels.setChecked(self._read_bool_setting("QCALVIEW/ui/show_preview_labels", True))
        except Exception:
            self.cb_export_metadata.setChecked(True)

        # Synchronisation projet -> overlays QCALVIEW
        try:
            QgsProject.instance().layersWillBeRemoved.connect(self._on_project_layers_removed)
            try:
                QgsProject.instance().crsChanged.connect(self._update_ui_summary)
                QgsProject.instance().crsChanged.connect(self._update_canvas_fov)
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._prepare_qgis_theme_combo_lazy()
            self.cb_theme_auto_sync.setChecked(self._read_bool_setting('QCALVIEW/theme_auto_sync', False))
        except Exception:
            pass


        self.btn_load.clicked.connect(self.load_photo)
        self.btn_view.clicked.connect(self.open_viewer)
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        self.btn_fit_window.clicked.connect(self._fit_floating_window)
        self.cmb_quality.currentIndexChanged.connect(self._clear_base_cache_and_render)
        self.cb_lowlat.toggled.connect(self.render_preview)
        self.cb_show_labels.toggled.connect(self.render_preview)
        self.cb_auto_depth.toggled.connect(self.render_preview)
        self.cmb_perf_budget.currentIndexChanged.connect(self.render_preview)
        self.cb_block_heavy_layers.toggled.connect(self.render_preview)
        self.btn_add_layer.clicked.connect(self.add_layer)
        self.btn_remove_layer.clicked.connect(self.remove_layer)
        self.btn_up.clicked.connect(self.move_up)
        self.btn_down.clicked.connect(self.move_down)
        self.btn_style.clicked.connect(self.edit_style)
        self.btn_refresh_themes.clicked.connect(lambda: (setattr(self, '_theme_combo_loaded', True), self.refresh_qgis_themes()))
        self.cmb_qgis_theme.currentIndexChanged.connect(self._on_qgis_theme_changed)
        self.btn_apply_theme.clicked.connect(self.apply_qgis_theme_to_overlays)
        self.cb_theme_auto_sync.toggled.connect(self._on_qgis_theme_auto_sync_toggled)
        self.btn_refresh.clicked.connect(self.refresh_now_full)
        self.btn_refresh_top.clicked.connect(self._refresh_preview_and_viewer)
        self.btn_export_current.clicked.connect(self.export_current_composite)
        self.btn_export_overlay.clicked.connect(self.export_overlay)
        self.btn_export_legend.clicked.connect(self.export_legend)
        self.btn_export_refresh_pdv.clicked.connect(lambda: (setattr(self, '_export_tab_loaded', True), self._refresh_batch_pdv_table()))
        self.btn_export_select_all.clicked.connect(lambda: self._set_all_batch_rows_checked(True))
        self.btn_export_select_none.clicked.connect(lambda: self._set_all_batch_rows_checked(False))
        self.btn_export_batch_selected.clicked.connect(self.export_batch_selected)
        self.tbl_export_pdv.itemChanged.connect(self._on_batch_table_item_changed)
        self.tabs.currentChanged.connect(self._on_main_tab_changed)
        self.cb_export_metadata.toggled.connect(lambda v: self._settings.setValue('QCALVIEW/export_write_metadata', bool(v)))
        try:
            self.list_layers.itemChanged.connect(self._on_layer_table_item_changed)
        except Exception:
            pass
        try:
            self.list_layers.cellDoubleClicked.connect(self._on_layer_table_double_clicked)
        except Exception:
            pass

        self.cb_auto_hfov.toggled.connect(self._toggle_hfov_enable)
        self.btn_dem_color.clicked.connect(self._pick_dem_color)
        self.cmb_camera.layerChanged.connect(self._on_camera_layer_changed)
        self.cmb_camera.layerChanged.connect(self._after_camera_layer_changed)
        self.cmb_camera.layerChanged.connect(self._camera_on_layer_changed)
        self.grp_calib.toggled.connect(self.render_preview)
        self.cb_show_experimental.toggled.connect(self._toggle_experimental_ui)
        self.cmb_cam_id_field.currentIndexChanged.connect(lambda *_: self._camera_refresh_feature_list(autoload=False))
        self.cmb_cam_label_field.currentIndexChanged.connect(lambda *_: self._camera_refresh_feature_list(autoload=False))
        self.cmb_cam_order_field.currentIndexChanged.connect(lambda *_: self._camera_refresh_feature_list(autoload=False))
        self.cmb_cam_image_field.currentIndexChanged.connect(self._camera_on_feature_changed)
        self.cb_cam_apply_style.toggled.connect(self._on_camera_style_toggle)
        self.cmb_orientation_step.currentIndexChanged.connect(self._on_orientation_step_changed)
        self.cmb_cam_feature.currentIndexChanged.connect(self._monoplot_on_pdv_changed)
        self.cmb_cam_feature.currentIndexChanged.connect(self._camera_on_feature_changed)
        self.btn_cam_prev.clicked.connect(self._camera_prev_feature)
        self.btn_cam_next.clicked.connect(self._camera_next_feature)
        self.btn_cam_load.clicked.connect(self._camera_load_current_feature)
        self.btn_cam_save.clicked.connect(self._camera_save_current_feature)
        self.btn_cam_fields.clicked.connect(self._camera_ensure_fields)
        self.btn_cam_source_auto.clicked.connect(self._camera_use_auto_image_source)
        self.btn_cam_source_photo.clicked.connect(self._camera_associate_photo)
        self.btn_cam_source_schema.clicked.connect(self._camera_set_current_schematic)
        self.btn_cam_batch_img.clicked.connect(self.export_batch_composite)
        self.btn_cam_batch_png.clicked.connect(self.export_batch_overlay_only)
        self.btn_cam_csv.clicked.connect(self.export_camera_variables_csv)
        self.cb_cam_show_all.toggled.connect(self._sync_pdv_qml)
        for w in [self.cmb_proj, self.cb_360, self.d_yaw, self.d_pitch, self.d_roll,
                  self.d_hfov, self.d_vfov, self.d_camheight, self.spin_off_h, self.spin_off_v,
                  self.cmb_off_mode, self.d_maxdist, self.spin_w, self.spin_h, self.d_focal, self.d_sensorw]:
            for sig in ('valueChanged', 'currentIndexChanged', 'toggled', 'editingFinished'):
                if hasattr(w, sig):
                    try:
                        getattr(w, sig).connect(self._camera_schedule_autosave)
                    except Exception:
                        pass
        for w in [self.cb_calib_enable, self.cb_proj_grid_enable, self.d_proj_grid_step, self.cmb_calib_type, self.d_calib_spacing, self.d_calib_width,
                  self.d_calib_depth, self.d_calib_height, self.d_calib_dist, self.d_calib_elev,
                  self.cb_calib_snap_dem, self.cb_calib_labels, self.cb_calib_axes]:
            self._connect_change_to_schedule(w)
        self.btn_calib_color.clicked.connect(lambda: ( (lambda c=QColorDialog.getColor(self._calib_color, self, "Couleur calibration"):
                                                       setattr(self, "_calib_color", c) if c.isValid() else None)(), self.render_preview() ))
        self.cb_proj_grid_enable.toggled.connect(self.render_preview)
        self.d_proj_grid_step.valueChanged.connect(self.render_preview)
        self.cb_az_rule_enable.toggled.connect(self.render_preview)
        self.d_az_rule_band_pct.valueChanged.connect(self.render_preview)
        self.d_az_rule_text_pct.valueChanged.connect(self.render_preview)

        # DEM toggles
        #self.grp_dem.toggled.connect(self.render_preview)
        self.cb_use_dem_z.toggled.connect(self.render_preview)
        self.combo_relief_mode.currentIndexChanged.connect(self._on_relief_mode_changed)
        self.cb_curvature.toggled.connect(lambda *_: (setattr(self, '_horizon', None), setattr(self, '_horizon_params', None), getattr(self, '_overlay_cache', {}).clear(), self.render_preview()))
        self.d_earth_radius_km.valueChanged.connect(lambda *_: (setattr(self, '_horizon', None), setattr(self, '_horizon_params', None), getattr(self, '_overlay_cache', {}).clear(), self.render_preview()))
        self.spin_ridge_count.valueChanged.connect(self.render_preview)
        self.d_ridge_gap.valueChanged.connect(self.render_preview)
        self.d_ridge_prom.valueChanged.connect(self.render_preview)
        self.spin_dem_width.valueChanged.connect(self.render_preview)
        self.spin_dem_alpha.valueChanged.connect(self.render_preview)
        self.cb_wire_dashed.toggled.connect(self.render_preview)
        self.combo_wire_mode.currentIndexChanged.connect(self.render_preview)
        self.cb_sky_dashed.toggled.connect(self.render_preview)
        self.cb_skyline_fill.toggled.connect(self.render_preview)
        self.spin_sky_fill_alpha.valueChanged.connect(self.render_preview)
        
        # Occlusions / debug
        #self.grp_occ.toggled.connect(self.render_preview)
        self.cb_occ_objects.toggled.connect(self.render_preview)
        self.cb_transparent_objects.toggled.connect(self.render_preview)
        self.cb_debug_no_occ.toggled.connect(self.render_preview)
        self.cb_show_guides.toggled.connect(self.render_preview)
        self.d_az_step.valueChanged.connect(self.render_preview)
        self.d_rad_step.valueChanged.connect(self.render_preview)
        self.d_eps.valueChanged.connect(self.render_preview)

        # Contrôles influençant le rendu
        for w in [self.cmb_proj, self.spin_w, self.spin_h, self.d_yaw, self.d_yaw_offset, self.d_pitch, self.d_roll,
                  self.d_hfov, self.d_vfov, self.cb_360, self.d_focal, self.d_sensorw,
                  self.d_maxdist, self.cb_auto_depth, self.cmb_perf_budget, self.cb_block_heavy_layers, self.cmb_camera, self.cmb_dem, self.combo_relief_mode, self.spin_dem_step, self.spin_dem_alpha,
                  self.cb_curvature, self.d_earth_radius_km,
                  self.cb_proj_grid_enable, self.d_proj_grid_step,
                  self.cb_draw_2p5d, self.cb_force_horizontal_25d, self.txt_hfield, self.d_hdefault, self.d_camheight]:
            self._connect_change_to_schedule(w)

        self._toggle_hfov_enable(self.cb_auto_hfov.isChecked())
        self._sync_relief_mode_controls()
        
        self.cmb_off_mode.currentIndexChanged.connect(self._refresh_preview_and_viewer)
        self.cmb_off_mode.currentIndexChanged.connect(lambda *_: getattr(self, "_overlay_cache", {}).clear())

        self.spin_off_h.valueChanged.connect(self._refresh_preview_and_viewer)
        self.spin_off_v.valueChanged.connect(self._refresh_preview_and_viewer)

        # Rafraîchir la direction + emprise HFOV sur le canevas à chaque changement pertinent
        for w in [self.d_yaw, self.d_hfov, self.cb_360, self.d_maxdist, self.d_symdist, self.cmb_camera]:
            for sig in ('valueChanged','currentIndexChanged','toggled','editingFinished'):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._update_canvas_fov)
        self.debounce.timeout.connect(self._update_canvas_fov)
        
        # Sync QML à la volée (yaw/hfov/360/portée/pitch/projection)
        for w, sig in [
            (self.d_yaw,  'valueChanged'),
            (self.d_hfov, 'valueChanged'),
            (self.cb_360, 'toggled'),
            (self.d_maxdist, 'valueChanged'),
            (self.d_symdist, 'valueChanged'),
            (self.d_pitch, 'valueChanged'),
            (self.cmb_proj, 'currentIndexChanged'),
            
        ]:
            if hasattr(w, sig):
                getattr(w, sig).connect(self._sync_pdv_qml)
        self.cmb_off_mode.currentIndexChanged.connect(self._sync_pdv_qml)
        self.spin_off_h.valueChanged.connect(self._sync_pdv_qml)
        self.spin_off_v.valueChanged.connect(self._sync_pdv_qml)

        
        
        

        try:
            self.iface.mapCanvas().destinationCrsChanged.connect(self._update_canvas_fov)
        except Exception:
            pass

        
        def _on_projection_changed():
            txt = str(self.cmb_proj.currentText()).strip().upper()
            prev_txt = str(getattr(self, '_last_projection_ui', '') or '').strip().upper()
            # Le 360° n'est forcé automatiquement que pour l'équirectangulaire.
            auto_is360 = txt in ("EQUIRECT", "EQUIRECTANGULAR")
            try:
                self.cb_360.blockSignals(True)
                if auto_is360:
                    self.cb_360.setChecked(True)
                elif txt == 'CYLINDRICAL' and prev_txt in ('EQUIRECT', 'EQUIRECTANGULAR'):
                    self.cb_360.setChecked(False)
            finally:
                self.cb_360.blockSignals(False)

            if txt == 'CYLINDRICAL':
                try:
                    self.d_vfov.blockSignals(True)
                    self.d_vfov.setValue(40.0)
                finally:
                    self.d_vfov.blockSignals(False)

            self._last_projection_ui = txt
            # retracer immédiatement sur le canevas, mais pas pendant l'initialisation du dock.
            if not getattr(self, '_ui_initializing', False):
                self._update_canvas_fov()
        
        def _update_offset_ranges():
            # 0 = Pixels, 1 = Pourcentage (-0.5..+0.5 = -50%..+50%)
            is_pct = (self.cmb_off_mode.currentIndex() == 1)
            if is_pct:
                self.spin_off_h.setRange(-0.5, 0.5); self.spin_off_h.setSingleStep(0.005)
                self.spin_off_v.setRange(-0.5, 0.5); self.spin_off_v.setSingleStep(0.005)
            else:
                self.spin_off_h.setRange(-5000.0, 5000.0); self.spin_off_h.setSingleStep(1.0)
                self.spin_off_v.setRange(-5000.0, 5000.0); self.spin_off_v.setSingleStep(1.0)

        _update_offset_ranges()
        self.cmb_off_mode.currentIndexChanged.connect(_update_offset_ranges)

        self.btn_reset_offsets.clicked.connect(lambda: (
            self.spin_off_h.setValue(0.0),
            self.spin_off_v.setValue(0.0)
        ))
  
        
        
        def _sync_camera_style():
            try:
                layer = self.cmb_camera.currentLayer()
                if not layer:
                    return
                # Proj 360 automatique (abrégé EQUIRECT accepté)
                proj_txt = ""
                try:
                    proj_txt = str(self.cmb_proj.currentText()).strip().upper()
                except Exception:
                    pass
                is360 = bool(self._is360_mode())
        
                # portée graphique : clamp(md/5, 50, 500) sinon 200
                md = float(self.d_maxdist.value())
                rng = 200.0 if md <= 0 else max(50.0, min(500.0, md/5.0))
        
                # pousse les variables de style (méthode bindée depuis _layerstyle.py)
                self.update_pdv_qml_vars(
                    layer,
                    yaw_deg=(float(self.d_yaw.value()) % 360.0),
                    hfov_deg=(360.0 if is360 else float(self.d_hfov.value())),
                    is360=is360,
                    range_m=rng,
                    pitch_deg=float(self.d_pitch.value())
                )
            except Exception:
                pass

        self.cmb_proj.currentIndexChanged.connect(_on_projection_changed)
        
        # Miroir du câblage _update_canvas_fov → on met aussi à jour le style
        for w, sig in [
            (self.d_yaw, 'valueChanged'),
            (self.d_hfov, 'valueChanged'),
            (self.cb_360, 'toggled'),
            (self.d_maxdist, 'valueChanged'),
            (self.d_symdist, 'valueChanged'),
            (self.d_pitch, 'valueChanged'),
            (self.cmb_proj, 'currentIndexChanged'),
            (self.cmb_camera, 'layerChanged'),
        ]:
            if hasattr(w, sig):
                getattr(w, sig).connect(_sync_camera_style)


        _on_projection_changed()
        self._after_camera_layer_changed(self.cmb_camera.currentLayer())
        self._camera_on_layer_changed(self.cmb_camera.currentLayer())

        self._camera_point_defaults = {
            'qcv_proj': str(self.cmb_proj.currentText()).strip(),
            'qcv_360': 1 if self._is360_mode() else 0,
            'qcv_yaw': float(self.d_yaw.value()) % 360.0,
            'qcv_pitch': float(self.d_pitch.value()),
            'qcv_roll': float(self.d_roll.value()),
            'qcv_hfov': float(self.d_hfov.value()),
            'qcv_vfov': float(self.d_vfov.value()),
            'qcv_alt': float(self.d_camheight.value()),
            'qcv_ofh': float(self.spin_off_h.value()),
            'qcv_ofv': float(self.spin_off_v.value()),
            'qcv_ofmd': int(self.cmb_off_mode.currentIndex()),
            'qcv_mdst': float(self.d_maxdist.value()),
            'qcv_iw': int(self.spin_w.value()),
            'qcv_ih': int(self.spin_h.value()),
            'qcv_foc': float(self.d_focal.value()),
            'qcv_sens': float(self.d_sensorw.value()),
        }
        self._connect_summary_updates()
        self._restore_main_geometry()
        self._toggle_experimental_ui(self.cb_show_experimental.isChecked())

        self._ui_initializing = False
        try:
            self._update_canvas_fov()
        except Exception:
            pass
        self._update_ui_summary()
    

    def _load_designer_shell(self):

        ui_path = os.path.join(os.path.dirname(__file__), "ui", "qcalview_dock.ui")
        root = QWidget()
        if not os.path.exists(ui_path):
            raise RuntimeError(f"Fichier UI introuvable : {ui_path}")
        uic.loadUi(ui_path, root)
        if not root.styleSheet().strip():
            # Fallback uniquement : le style principal est porté par qcalview_dock.ui
            # afin de rester éditable dans Qt Designer.
            root.setStyleSheet("""
                QFrame#qcvCockpit, QFrame#qcvPreviewPanel { border: 1px solid rgba(120,140,150,90); border-radius: 8px; }
                QFrame#cardPhotoPdv, QFrame#cardProjection, QFrame#cardCamera, QFrame#cardTheme, QFrame#qcvGridCard { border: 1px solid rgba(120,140,150,60); border-radius: 7px; }
                QLabel#titlePhotoPdv, QLabel#titleProjection, QLabel#titleCamera, QLabel#titleTheme, QLabel#qcvFieldTitle, QLabel#previewPanelTitle { font-weight: 700; color: #7fb4ff; }
                QLabel#lbl_preview_info { color: #9aa5ad; }
                QToolButton { padding: 5px 8px; }
            """)
        self._ui_root = root
        def need(cls, name):
            w = root.findChild(cls, name)
            if w is None:
                raise RuntimeError(f"Widget Qt Designer manquant : {name}")
            return w
        def set_icon(label_name, icon_name, size=26):
            try:
                lab = need(QLabel, label_name)
                lab.setPixmap(self._icon(icon_name).pixmap(size, size))
            except Exception:
                pass
        def setup_btn(name, icon_name, tooltip, text=None, style=QC.Qt_ToolButtonStyle_ToolButtonTextBesideIcon):
            btn = need(QToolButton, name)
            if text is not None:
                btn.setText(tr(text))
            if icon_name:
                btn.setIcon(self._icon(icon_name))
            btn.setToolButtonStyle(style)
            btn.setAutoRaise(False)
            btn.setMinimumHeight(28)
            btn.setToolTip(tr(tooltip))
            btn.setSizePolicy(QC.QSizePolicy_Policy_Maximum, QC.QSizePolicy_Policy_Fixed)
            return btn

        self.lbl_info = need(QLabel, "lbl_info")
        self.lbl_info.setWordWrap(True)
        self.lbl_current_pdv = need(QLabel, "lbl_current_pdv")
        self.lbl_current_pdv.setStyleSheet("font-weight:700;")
        self.lbl_current_pdv.setTextFormat(QC.Qt_TextFormat_RichText)
        self.lbl_cockpit_projection = root.findChild(QLabel, "lbl_cockpit_projection")
        self.lbl_cockpit_camera = need(QLabel, "lbl_cockpit_camera")
        self.lbl_cockpit_camera.setTextFormat(QC.Qt_TextFormat_RichText)
        for _lab in tuple(x for x in (self.lbl_info, self.lbl_current_pdv, self.lbl_cockpit_projection, self.lbl_cockpit_camera) if x is not None):
            try:
                _lab.setTextInteractionFlags(QC.Qt_TextInteractionFlag_TextSelectableByMouse)
                _lab.setMinimumWidth(0)
            except Exception:
                pass
        self.cmb_qgis_theme = need(QComboBox, "cmb_qgis_theme")
        self.cmb_qgis_theme.setMinimumWidth(90)
        self.cmb_qgis_theme.setSizePolicy(QC.QSizePolicy_Policy_Ignored, QC.QSizePolicy_Policy_Fixed)
        self.cmb_qgis_theme.setToolTip(tr("Thème QGIS utilisé pour alimenter les couches projetées"))

        self.qcv_cockpit = need(QFrame, "qcvCockpit")
        self.qcv_cockpit.setSizePolicy(QC.QSizePolicy_Policy_Preferred, QC.QSizePolicy_Policy_Maximum)
        try:
            _proj_card = root.findChild(QFrame, "cardProjection")
            if _proj_card is not None:
                _proj_card.setVisible(False)
        except Exception:
            pass
        try:
            if root.layout() is not None:
                root.layout().setStretch(0, 0)
                root.layout().setStretch(1, 1)
            body = root.findChild(QWidget, "body")
            if body is not None:
                body.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Expanding)
        except Exception:
            pass

        set_icon("icoPhoto", "photo.svg")
        set_icon("icoProjection", "projection.svg")
        set_icon("icoCamera", "camera.svg")
        set_icon("icoTheme", "theme.svg")
        self.btn_load = setup_btn("btn_load", "photo.svg", "Charger une photo", "Photo")
        self.btn_view = setup_btn("btn_view", "viewer.svg", "Ouvrir la visionneuse pleine taille", "Visionneuse")
        self.btn_refresh_top = setup_btn("btn_refresh_top", "render.svg", "Rafraîchir uniquement l’aperçu et la visionneuse, sans recalcul complet des caches", "Aperçu")
        self.btn_settings = setup_btn("btn_settings", "settings.svg", "Paramètres et version de QCALVIEW", "Param.")
        self.btn_fit_window = setup_btn("btn_fit_window", "resize.svg", "Adapter la fenêtre flottante au contenu", "Adapter")

        self.tabs = need(QTabWidget, "tabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tab_camera = need(QScrollArea, "tab_camera")
        self.tab_calage = need(QScrollArea, "tab_calage")
        self.tab_relief = need(QScrollArea, "tab_relief")
        self.tab_layers = need(QScrollArea, "tab_layers")
        self.tab_export = need(QScrollArea, "tab_export")
        self.tab_gcp = need(QScrollArea, "tab_gcp")
        for _sa in (self.tab_camera, self.tab_calage, self.tab_relief, self.tab_layers, self.tab_export, self.tab_gcp):
            _sa.setWidgetResizable(True)
            _sa.setFrameShape(QC.QFrame_Shape_NoFrame)
        def _placeholder_layout(name, fallback_scrollarea=None):

            holder = root.findChild(QWidget, name)
            if holder is None:
                if fallback_scrollarea is not None and fallback_scrollarea.widget() is not None:
                    return fallback_scrollarea.widget().layout()
                raise RuntimeError(f"Placeholder Qt Designer manquant : {name}")
            lay = holder.layout()
            if lay is None:
                lay = QVBoxLayout(holder)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(8)
            # Supprime les libellés de repérage présents uniquement pour Qt Designer.
            try:
                while lay.count():
                    item = lay.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()
            except Exception:
                pass
            try:
                holder.setMinimumHeight(0)
            except Exception:
                pass
            setattr(self, name, holder)
            return lay

        self.tab_camera_layout = _placeholder_layout("placeholder_image_pdv", self.tab_camera)
        self.tab_calage_layout = _placeholder_layout("placeholder_calage", self.tab_calage)
        self.tab_relief_layout = _placeholder_layout("placeholder_relief", self.tab_relief)
        self.tab_layers_layout = _placeholder_layout("placeholder_projected_layers", self.tab_layers)
        self.tab_export_layout = _placeholder_layout("placeholder_export", self.tab_export)
        self.tab_gcp_layout = _placeholder_layout("placeholder_experimental", self.tab_gcp)
        self._exp_tab_insert_pos = self.tabs.indexOf(self.tab_gcp)
        self._calib_tab_index = -1
        self.tab_calib3d = QWidget()
        self.tab_calib3d_layout = QVBoxLayout(self.tab_calib3d)
        self.tab_calib3d_layout.setContentsMargins(8, 8, 8, 8)
        self.tab_calib3d_layout.setSpacing(8)
        try:
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_camera), self._icon("photo.svg"))
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_calage), self._icon("camera.svg"))
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_relief), self._icon("relief.svg"))
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_layers), self._icon("layers.svg"))
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_export), self._icon("export.svg"))
            self.tabs.setTabIcon(self.tabs.indexOf(self.tab_gcp), self._icon("tools.svg"))
        except Exception:
            pass

        self._preview_panel = need(QFrame, "qcvPreviewPanel")
        self.preview = ClickableLabel("Aperçu rapide")
        self.preview.setAlignment(QC.Qt_AlignmentFlag_AlignCenter)
        self.preview.setMinimumSize(QSize(180, 120))
        self.preview.setSizePolicy(QC.QSizePolicy_Policy_Ignored, QC.QSizePolicy_Policy_Ignored)
        self.preview.setStyleSheet("background:#111; color:#888; border-radius:6px;")
        self.preview.clicked.connect(self._on_preview_click)
        preview_slot = root.findChild(QWidget, "placeholder_preview") or root.findChild(QWidget, "preview_image_slot")
        if preview_slot is None:
            raise RuntimeError("Placeholder Qt Designer manquant : placeholder_preview")
        preview_layout = preview_slot.layout()
        if preview_layout is None:
            preview_layout = QVBoxLayout(preview_slot)
            preview_layout.setContentsMargins(0, 0, 0, 0)
        try:
            while preview_layout.count():
                item = preview_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        except Exception:
            pass
        self.placeholder_preview = preview_slot
        preview_layout.addWidget(self.preview, 1)
        self.cmb_quality = need(QComboBox, "cmb_quality")
        try:
            self.cmb_quality.setCurrentIndex(0)
        except Exception:
            pass
        self.cb_lowlat = need(QCheckBox, "cb_lowlat")
        self.cb_lowlat.setChecked(True)
        self.cb_show_labels = need(QCheckBox, "cb_show_labels")
        self.cb_show_labels.setChecked(True)
        self.lbl_preview_info = need(QLabel, "lbl_preview_info")
        self.lbl_preview_info.setWordWrap(True)
        self.lbl_preview_info.setObjectName("qcvPreviewInfo")
        self.btn_preview_open = root.findChild(QToolButton, "btn_preview_open")
        if self.btn_preview_open is not None:
            try:
                self.btn_preview_open.setVisible(False)
                self.btn_preview_open.setEnabled(False)
            except Exception:
                pass
        return root

    def _icon(self, name):
        try:
            path = os.path.join(os.path.dirname(__file__), "resources", "icons", str(name))
            return QIcon(path) if os.path.exists(path) else QIcon()
        except Exception:
            return QIcon()

    def _tool_button(self, text, icon_name=None, tooltip=""):
        btn = QToolButton()
        btn.setText(tr(text))
        if icon_name:
            btn.setIcon(self._icon(icon_name))
        btn.setToolButtonStyle(QC.Qt_ToolButtonStyle_ToolButtonTextBesideIcon)
        btn.setAutoRaise(False)
        btn.setMinimumHeight(28)
        btn.setToolTip(tr(tooltip or text))
        btn.setSizePolicy(QC.QSizePolicy_Policy_Maximum, QC.QSizePolicy_Policy_Fixed)
        return btn

    def _cockpit_card(self, title, icon_name, widgets):
        card = QFrame()
        card.setObjectName("qcvCockpitCard")
        card.setFrameShape(QC.QFrame_Shape_StyledPanel)
        card.setMinimumWidth(0)
        card.setSizePolicy(QC.QSizePolicy_Policy_Ignored, QC.QSizePolicy_Policy_Fixed)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        ico = QLabel()
        try:
            pm = self._icon(icon_name).pixmap(26, 26)
            ico.setPixmap(pm)
        except Exception:
            ico.setText(tr("•"))
        lay.addWidget(ico, 0, QC.Qt_AlignmentFlag_AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        ttl = QLabel(tr(title))
        ttl.setObjectName("qcvCardTitle")
        ttl.setWordWrap(True)
        ttl.setMinimumWidth(0)
        col.addWidget(ttl)
        for w in widgets:
            if isinstance(w, QLabel):
                w.setTextInteractionFlags(QC.Qt_TextInteractionFlag_TextSelectableByMouse)
            try:
                w.setMinimumWidth(0)
            except Exception:
                pass
            col.addWidget(w)
        lay.addLayout(col, 1)
        return card

    def _build_preview_panel(self):
        panel = QFrame()
        panel.setObjectName("qcvPreviewPanel")
        panel.setFrameShape(QC.QFrame_Shape_StyledPanel)
        panel.setMinimumWidth(180)
        panel.setMaximumWidth(360)
        panel.setMaximumWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel(tr("Aperçu rapide permanent"))
        title.setObjectName("qcvPanelTitle")
        head.addWidget(title)
        head.addStretch(1)
        btn_open = self._tool_button("", "viewer.svg", "Ouvrir la visionneuse pleine taille")
        btn_open.setToolButtonStyle(QC.Qt_ToolButtonStyle_ToolButtonIconOnly)
        btn_open.clicked.connect(self.open_viewer)
        head.addWidget(btn_open)
        layout.addLayout(head)

        layout.addWidget(self.preview, 1)

        quick = QGroupBox(tr("Options rapides"))
        q = QGridLayout(quick)
        q.setContentsMargins(8, 8, 8, 8)
        q.addWidget(QLabel(tr("Qualité")), 0, 0)
        q.addWidget(self.cmb_quality, 0, 1)
        q.addWidget(self.cb_lowlat, 1, 0, 1, 2)
        q.addWidget(self.cb_show_labels, 2, 0, 1, 2)
        layout.addWidget(quick, 0)

        self.lbl_preview_info = QLabel(tr("Aucune image chargée."))
        self.lbl_preview_info.setWordWrap(True)
        self.lbl_preview_info.setObjectName("qcvPreviewInfo")
        layout.addWidget(self.lbl_preview_info, 0)
        return panel

    def _read_bool_setting(self, key, default=False):
        try:
            val = self._settings.value(key, default)
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            return bool(default)

    def _prepare_qgis_theme_combo_lazy(self):
        """Initialise le combo thème sans scanner le projet au démarrage."""
        try:
            combo = getattr(self, 'cmb_qgis_theme', None)
            if combo is None:
                return
            saved = str(self._settings.value('QCALVIEW/theme_name', '') or '').strip()
            combo.blockSignals(True)
            try:
                combo.clear()
                if saved:
                    combo.addItem(tr(saved), saved)
                else:
                    combo.addItem(tr('— charger thèmes —'), '')
            finally:
                combo.blockSignals(False)
            self._theme_combo_loaded = False
        except Exception:
            pass

    def _lazy_load_qgis_themes(self):
        """Charge les thèmes seulement quand ils deviennent utiles."""
        if getattr(self, '_theme_combo_loaded', False):
            return
        try:
            self._theme_combo_loaded = True
            self.refresh_qgis_themes()
            # Restaurer le thème sauvegardé sans appliquer automatiquement les overlays.
            self._suspend_theme_auto_apply = True
            try:
                self._restore_qgis_theme_settings()
                if bool(getattr(self, 'cb_theme_auto_sync', None) and self.cb_theme_auto_sync.isChecked()):
                    self._on_qgis_theme_auto_sync_toggled(True)
            finally:
                self._suspend_theme_auto_apply = False
        except Exception:
            pass

    def _on_main_tab_changed(self, index):
        """Lazy loading des onglets coûteux."""
        try:
            widget = self.tabs.widget(int(index))
        except Exception:
            widget = None
        try:
            if widget is getattr(self, 'tab_layers', None):
                self._lazy_load_qgis_themes()
        except Exception:
            pass
        try:
            if widget is getattr(self, 'tab_export', None) and not getattr(self, '_export_tab_loaded', False):
                self._export_tab_loaded = True
                QTimer.singleShot(0, self._refresh_batch_pdv_table)
        except Exception:
            pass

    def _connect_summary_updates(self):
        targets = [
            getattr(self, "cmb_proj", None), getattr(self, "cmb_cam_feature", None),
            getattr(self, "cmb_camera", None), getattr(self, "cmb_qgis_theme", None),
            getattr(self, "d_yaw", None), getattr(self, "d_pitch", None),
            getattr(self, "d_roll", None), getattr(self, "d_hfov", None),
            getattr(self, "d_vfov", None), getattr(self, "d_camheight", None),
            getattr(self, "d_maxdist", None), getattr(self, "spin_w", None),
            getattr(self, "spin_h", None)
        ]
        for w in targets:
            if w is None:
                continue
            for sig in ("valueChanged", "currentIndexChanged", "layerChanged", "toggled", "editingFinished"):
                if hasattr(w, sig):
                    try:
                        getattr(w, sig).connect(self._update_ui_summary)
                    except Exception:
                        pass

    def _update_ui_summary(self, *_args):
        try:
            photo = os.path.basename(self.photo_path) if getattr(self, "photo_path", None) else "Aucune photo"
            pdv = ""
            try:
                pdv = self.cmb_cam_feature.currentText().strip()
            except Exception:
                pdv = ""
            self.lbl_info.setText(tr(photo))
            self.lbl_current_pdv.setText(tr(pdv if pdv else "Aucun PDV actif"))

            proj = str(self.cmb_proj.currentText()) if hasattr(self, "cmb_proj") else "—"
            hfov = float(self.d_hfov.value()) if hasattr(self, "d_hfov") else 0.0
            vfov = float(self.d_vfov.value()) if hasattr(self, "d_vfov") else 0.0
            if getattr(self, "lbl_cockpit_projection", None) is not None:
                self.lbl_cockpit_projection.setText(tr(f"{proj}\nHFOV : {hfov:.1f}° · VFOV : {vfov:.1f}°"))

            x = y = None
            crs_authid = ""
            try:
                x, y, crs_authid = self._current_camera_xy_project_crs()
            except Exception:
                pass
            cam_h = float(self.d_camheight.value()) if hasattr(self, "d_camheight") else 0.0
            crs_html = f" · <b>{html.escape(str(crs_authid))}</b>" if crs_authid else ""
            if x is None or y is None:
                self.lbl_cockpit_camera.setText(tr(f"<b>X : —</b>    <b>Y : —</b>{crs_html}<br>Z sol : —    Hauteur : {cam_h:.2f} m"))
            else:
                x_txt = f"{x:,.2f}".replace(",", " ")
                y_txt = f"{y:,.2f}".replace(",", " ")
                self.lbl_cockpit_camera.setText(tr(f"<b>X : {x_txt}</b>    <b>Y : {y_txt}</b>{crs_html}<br>Z sol : —    Hauteur : {cam_h:.2f} m"))

            yaw = float(self.d_yaw.value()) if hasattr(self, "d_yaw") else 0.0
            pitch = float(self.d_pitch.value()) if hasattr(self, "d_pitch") else 0.0
            roll = float(self.d_roll.value()) if hasattr(self, "d_roll") else 0.0
            md = float(self.d_maxdist.value()) if hasattr(self, "d_maxdist") else 0.0
            info = (
                f"PDV : {pdv or '—'}<br>"
                f"Projection : {proj}<br>"
                f"Azimut : {yaw:.1f}° · Tangage : {pitch:.1f}° · Roulis : {roll:.1f}°<br>"
                f"HFOV : {hfov:.1f}° · VFOV : {vfov:.1f}° · Distance max : {md:.0f} m"
            )
            if hasattr(self, "lbl_theme_in_layers") and hasattr(self, "cmb_qgis_theme"):
                try:
                    th = str(self.cmb_qgis_theme.currentText() or "—")
                    self.lbl_theme_in_layers.setText(tr(f"Thème actif : {th}"))
                except Exception:
                    pass
            if hasattr(self, "lbl_preview_info"):
                self.lbl_preview_info.setText(tr(info))
            if getattr(self, "viewer", None) is not None and hasattr(self.viewer, "update_info"):
                self.viewer.update_info()
        except Exception:
            pass

    def _relayout_compact_row(self, row_widget, groups_per_line=2):
        """Redistribue une ligne de réglages en petites colonnes lisibles."""
        try:
            grid = getattr(row_widget, "_qcv_grid", None)
            items = list(getattr(row_widget, "_qcv_items", []) or [])
            if grid is None:
                return
            while grid.count():
                item = grid.takeAt(0)
                if item.widget() is not None:
                    item.widget().setParent(row_widget)
            groups_per_line = max(1, int(groups_per_line or 1))
            r = 0
            c = 0
            for lab, wid, kind in items:
                if kind == "field" and lab is not None:
                    grid.addWidget(lab, r, c * 2)
                    grid.addWidget(wid, r, c * 2 + 1)
                    grid.setColumnStretch(c * 2, 0)
                    grid.setColumnStretch(c * 2 + 1, 1)
                    c += 1
                else:
                    grid.addWidget(wid, r, c * 2, 1, 2)
                    grid.setColumnStretch(c * 2, 1)
                    c += 1
                if c >= groups_per_line:
                    r += 1
                    c = 0
        except Exception:
            pass

    def _relayout_compact_rows(self, width=None):

        try:
            if width is None:
                width = int(self.tab_calage.viewport().width())
        except Exception:
            try:
                width = int(self.width())
            except Exception:
                width = 900
        groups = 1 if width < 620 else (2 if width < 980 else 3)
        for row in list(getattr(self, "_compact_rows", []) or []):
            self._relayout_compact_row(row, groups)

    def _relayout_calage_cards(self, force=False):

        try:
            layout = getattr(self, '_calage_grid_layout', None)
            cards = list(getattr(self, '_calage_cards', []) or [])
            if layout is None or not cards:
                return
            width = 0
            try:
                width = int(self.tab_calage.viewport().width())
            except Exception:
                try:
                    width = int(self.width())
                except Exception:
                    width = 0
            cols = 1 if width and width < 760 else (2 if width and width < 1120 else 3)
            self._relayout_compact_rows(width)
            if not force and getattr(self, '_calage_layout_cols', None) == cols:
                return
            self._calage_layout_cols = cols
            for card, _r, _c, _sp in cards:
                try:
                    layout.removeWidget(card)
                except Exception:
                    pass
            if cols >= 3:
                for card, r, c, sp in cards:
                    layout.addWidget(card, r, c, 1, min(int(sp), 3))
                for c in range(3):
                    layout.setColumnStretch(c, 1)
                return
            r = 0
            c = 0
            for card, _r, _c, sp in cards:
                span = cols if int(sp) > 1 else 1
                if span >= cols:
                    if c != 0:
                        r += 1
                        c = 0
                    layout.addWidget(card, r, 0, 1, cols)
                    r += 1
                    c = 0
                else:
                    layout.addWidget(card, r, c, 1, 1)
                    c += 1
                    if c >= cols:
                        r += 1
                        c = 0
            for col in range(3):
                layout.setColumnStretch(col, 1 if col < cols else 0)
        except Exception:
            pass

    def _fit_floating_window(self):

        try:
            if not self.isFloating():
                self.setFloating(True)
            screen = None
            try:
                screen = self.window().screen()
            except Exception:
                screen = None
            avail = screen.availableGeometry() if screen is not None else None
            hint = self.sizeHint()
            max_w = int(avail.width() * 0.96) if avail else 1280
            max_h = int(avail.height() * 0.94) if avail else 920
            target_w = min(1180, max_w)
            target_h = min(820, max_h)
            w = max(target_w, min(int(hint.width() or target_w), max_w))
            h = max(target_h, min(int(hint.height() or target_h), max_h))
            self.setMinimumSize(min(760, max_w), min(520, max_h))
            self.resize(w, h)
            try:
                self.window().resize(w, h)
            except Exception:
                pass
            self._relayout_calage_cards(force=True)
            QTimer.singleShot(0, lambda: (self.resize(w, h), self._relayout_calage_cards(force=True)))
        except Exception:
            try:
                self.adjustSize()
            except Exception:
                pass

    def _on_qgis_theme_changed(self, *_args):

        try:
            self._update_ui_summary()
        except Exception:
            pass
        try:
            theme_name = str(self.cmb_qgis_theme.currentData() or self.cmb_qgis_theme.currentText() or '').strip()
            if theme_name and not theme_name.startswith('—'):
                self._settings.setValue('QCALVIEW/theme_name', theme_name)
        except Exception:
            pass
        try:
            if (not getattr(self, '_ui_initializing', False)
                    and not getattr(self, '_suspend_theme_auto_apply', False)
                    and bool(getattr(self, 'cb_theme_auto_sync', None) and self.cb_theme_auto_sync.isChecked())):
                QTimer.singleShot(0, self.apply_qgis_theme_to_overlays)
        except Exception:
            pass

    def _on_orientation_step_changed(self, *_):
        try:
            step = float(self.cmb_orientation_step.currentData())
        except Exception:
            step = 1.0
        step = step if step in (1.0, 0.1, 0.01) else 1.0
        try: self.d_yaw.setSingleStep(step)
        except Exception: pass
        try: self.d_pitch.setSingleStep(step)
        except Exception: pass

    def _on_camera_style_toggle(self, checked):

        if not checked:
            return
        try:
            layer = self.cmb_camera.currentLayer()
            if layer is not None:
                self._after_camera_layer_changed(layer)
        except Exception as exc:
            try: self.iface.messageBar().pushWarning(tr("QCALVIEW"), tr(f"Style PDV non appliqué : {exc}"))
            except Exception: pass

    def _current_camera_xy_project_crs(self):

        try:
            lyr = self.cmb_camera.currentLayer()
            fid = getattr(self, '_camera_current_fid', None)
            if lyr is None or fid is None:
                return None, None, ''
            feat = lyr.getFeature(int(fid))
            if not feat or not feat.isValid() or not feat.geometry() or feat.geometry().isEmpty():
                return None, None, ''
            pt = feat.geometry().asPoint()
            src = lyr.crs() if hasattr(lyr, 'crs') else QgsCoordinateReferenceSystem()
            dst = QgsProject.instance().crs()
            try:
                if src.isValid() and dst.isValid() and src != dst:
                    tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
                    pt = tr.transform(QgsPointXY(pt))
            except Exception:
                pass
            authid = ''
            try:
                authid = dst.authid()
            except Exception:
                authid = ''
            return float(pt.x()), float(pt.y()), authid
        except Exception:
            return None, None, ''

    def resizeEvent(self, ev):
        try:
            self._relayout_calage_cards()
        except Exception:
            pass
        try:
            super().resizeEvent(ev)
        except Exception:
            pass
        try:
            if getattr(self, '_export_tab_loaded', False):
                QTimer.singleShot(0, self._fit_export_table_height)
        except Exception:
            pass

    def _restore_main_geometry(self):
        try:
            geom = self._settings.value("QCALVIEW/ui/main_geometry", None)
            if geom:
                self.restoreGeometry(geom)
        except Exception:
            pass

    def _save_main_geometry(self):
        try:
            self._settings.setValue("QCALVIEW/ui/main_geometry", self.saveGeometry())
        except Exception:
            pass

    def open_settings_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("QCALVIEW — Paramètres et version"))
        lay = QVBoxLayout(dlg)

        grp = QGroupBox(tr("Interface"))
        grid = QGridLayout(grp)
        chk_exp = QCheckBox(tr("Afficher l’onglet Outils expérimentaux"))
        chk_exp.setChecked(bool(self.cb_show_experimental.isChecked()))
        chk_lowlat = QCheckBox(tr("Activer l’aperçu faible latence"))
        chk_lowlat.setChecked(bool(self.cb_lowlat.isChecked()))
        chk_labels = QCheckBox(tr("Afficher les étiquettes dans l’aperçu"))
        chk_labels.setChecked(bool(self.cb_show_labels.isChecked()))
        chk_meta = QCheckBox(tr("Écrire les métadonnées EXIF lors des exports"))
        chk_meta.setChecked(bool(self.cb_export_metadata.isChecked()))
        grid.addWidget(chk_exp, 0, 0, 1, 2)
        grid.addWidget(chk_lowlat, 1, 0, 1, 2)
        grid.addWidget(chk_labels, 2, 0, 1, 2)
        grid.addWidget(chk_meta, 3, 0, 1, 2)
        lay.addWidget(grp)

        grp_vars = QGroupBox(tr("Variables"))
        form_vars = QFormLayout(grp_vars)
        spin_earth_radius = QDoubleSpinBox(); spin_earth_radius.setRange(6000.0, 7000.0); spin_earth_radius.setDecimals(0); spin_earth_radius.setSuffix(tr(" km"))
        try:
            spin_earth_radius.setValue(float(self.d_earth_radius_km.value()))
        except Exception:
            spin_earth_radius.setValue(6370.0)
        spin_max_layers = QSpinBox(); spin_max_layers.setRange(1, 100); spin_max_layers.setSuffix(tr(" couches"))
        spin_max_features = QSpinBox(); spin_max_features.setRange(1, 1000000); spin_max_features.setSuffix(tr(" objets"))
        try:
            spin_max_layers.setValue(int(self._settings.value("QCALVIEW/limits/max_overlay_layers", 20)))
        except Exception:
            spin_max_layers.setValue(20)
        try:
            spin_max_features.setValue(int(self._settings.value("QCALVIEW/limits/max_features_per_layer", 5000)))
        except Exception:
            spin_max_features.setValue(5000)
        txt_default_out = QLineEdit(str(self._settings.value("QCALVIEW/export/default_output_dir", "") or ""))
        btn_default_out = QPushButton(tr("Parcourir…"))
        row_out = QWidget(); row_lay = QHBoxLayout(row_out); row_lay.setContentsMargins(0,0,0,0); row_lay.setSpacing(6)
        row_lay.addWidget(txt_default_out, 1); row_lay.addWidget(btn_default_out)
        btn_default_out.clicked.connect(lambda: (lambda d=QFileDialog.getExistingDirectory(dlg, tr("Dossier de sortie par défaut"), txt_default_out.text()): txt_default_out.setText(tr(d)) if d else None)())
        form_vars.addRow(tr("Rayon terrestre"), spin_earth_radius)
        form_vars.addRow(tr("Couches max à projeter"), spin_max_layers)
        form_vars.addRow(tr("Éléments max par couche"), spin_max_features)
        form_vars.addRow(tr("Dossier de sortie par défaut"), row_out)
        lay.addWidget(grp_vars)

        version = QLabel(
            tr("<b>QCALVIEW</b><br>"
            "Version ALPHA-40.20 — expérimental<br>"
            "Simulation visuelle &amp; géomatique pour QGIS<br>"
            "Développé par Fabrice Kerzerho — ArcTan°<br>"
            "© 2026 Fabrice Kerzerho — ArcTan°<br>"
            "GNU GPL v3 ou ultérieure<br><br>"
            "<a href='https://www.qcalview.com'>qcalview.com</a> · "
            "<a href='https://www.arctan.fr'>arctan.fr</a> · "
            "contact@arctan.fr")
        )
        version.setOpenExternalLinks(True)
        version.setWordWrap(True)
        lay.addWidget(version)

        buttons = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel)
        lay.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dialog_exec(dlg) == QC.QDialog_DialogCode_Accepted:
            self.cb_show_experimental.setChecked(chk_exp.isChecked())
            self.cb_lowlat.setChecked(chk_lowlat.isChecked())
            self.cb_show_labels.setChecked(chk_labels.isChecked())
            self.cb_export_metadata.setChecked(chk_meta.isChecked())
            try:
                self._settings.setValue("QCALVIEW/ui/show_experimental_tools", bool(chk_exp.isChecked()))
                self._settings.setValue("QCALVIEW/ui/low_latency_preview", bool(chk_lowlat.isChecked()))
                self._settings.setValue("QCALVIEW/ui/show_preview_labels", bool(chk_labels.isChecked()))
                self._settings.setValue("QCALVIEW/export_write_metadata", bool(chk_meta.isChecked()))
                self._settings.setValue("QCALVIEW/physics/earth_radius_km", float(spin_earth_radius.value()))
                self._settings.setValue("QCALVIEW/limits/max_overlay_layers", int(spin_max_layers.value()))
                self._settings.setValue("QCALVIEW/limits/max_features_per_layer", int(spin_max_features.value()))
                self._settings.setValue("QCALVIEW/export/default_output_dir", str(txt_default_out.text()).strip())
                try:
                    self.d_earth_radius_km.setValue(float(spin_earth_radius.value()))
                except Exception:
                    pass
                try:
                    getattr(self, "_overlay_cache", {}).clear()
                    getattr(self, "_geom_cache", {}).clear()
                except Exception:
                    pass
            except Exception:
                pass
            self._update_ui_summary()

    def _toggle_experimental_ui(self, checked):
        try:
            want = bool(checked)
            try:
                self._settings.setValue("QCALVIEW/ui/show_experimental_tools", want)
            except Exception:
                pass
            idx = self.tabs.indexOf(self.tab_gcp)
            if want:
                if idx < 0:
                    insert_at = min(getattr(self, '_exp_tab_insert_pos', 4), self.tabs.count())
                    self.tabs.insertTab(insert_at, self.tab_gcp, self._icon("tools.svg"), tr("Outils expérimentaux"))
                if hasattr(self, 'grp_calib'):
                    self.grp_calib.setVisible(True)
                if hasattr(self, 'grp_calib_world'):
                    self.grp_calib_world.setVisible(not PUBLIC_EXPERIMENTAL_LIMITED)
                if hasattr(self, 'grp_gcp'):
                    self.grp_gcp.setVisible(not PUBLIC_EXPERIMENTAL_LIMITED)
                    self.grp_gcp.setChecked(False)
                if hasattr(self, 'grp_monoplot'):
                    self.grp_monoplot.setVisible(True)
                if hasattr(self, 'grp_occ'):
                    self.grp_occ.setVisible(not PUBLIC_EXPERIMENTAL_LIMITED)
            else:
                if idx >= 0:
                    current_is_exp = (self.tabs.currentWidget() is self.tab_gcp)
                    self.tabs.removeTab(idx)
                    if current_is_exp:
                        self.tabs.setCurrentWidget(self.tab_camera)
                if hasattr(self, 'grp_calib'):
                    self.grp_calib.setVisible(False)
                if hasattr(self, 'grp_gcp'):
                    self.grp_gcp.setVisible(False)
                if hasattr(self, 'grp_monoplot'):
                    self.grp_monoplot.setVisible(False)
                if hasattr(self, 'grp_occ'):
                    self.grp_occ.setVisible(False)
            self._calib_tab_index = -1
        except Exception:
            pass

    def refresh_now_full(self):
        try:
            if not getattr(self, '_camera_loading_feature', False):
                self._camera_save_current_feature()
        except Exception:
            pass
        try:
            self._sync_pdv_qml()
        except Exception:
            pass
        return self.force_refresh_now()

    def _is360_mode(self) -> bool:

        try:
            proj_txt = str(self.cmb_proj.currentText()).strip().upper()
            if proj_txt not in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL"):
                return False
            checked = bool(self.cb_360.isChecked())
            hfov_full = float(self.d_hfov.value()) >= 359.999
            return bool(checked or hfov_full)
        except Exception:
            return False

    def _hook_camera_layer_signals(self, layer):

        if not isinstance(layer, QgsVectorLayer):
            return
        try: layer.geometryChanged.connect(self._update_canvas_fov)
        except Exception: pass
        try: layer.committedGeometriesChanges.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass
        try: layer.featureAdded.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass
        try: layer.featuresDeleted.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass

    def _after_camera_layer_changed(self, layer):

        from .core._log import qcv_log
        import traceback

        try:
            if not layer:
                qcv_log("_after_camera_layer_changed: aucune couche PDV reçue", "PDV-QML", "WARNING")
                return

            try:
                layer_name = layer.name() if hasattr(layer, "name") else "<sans nom>"
                layer_id = layer.id() if hasattr(layer, "id") else "<sans id>"
                provider = layer.providerType() if hasattr(layer, "providerType") else "?"
                crs = layer.crs().authid() if hasattr(layer, "crs") and layer.crs().isValid() else "?"
                geom = layer.geometryType() if hasattr(layer, "geometryType") else "?"
                qcv_log(
                    f"_after_camera_layer_changed START — couche='{layer_name}', id={layer_id}, "
                    f"provider={provider}, crs={crs}, geometryType={geom}",
                    "PDV-QML", "INFO"
                )
            except Exception as meta_exc:
                qcv_log(f"Impossible de journaliser les métadonnées de couche : {meta_exc}", "PDV-QML", "WARNING")

            # 40.10 : ne jamais modifier la symbologie d'une couche à l'ouverture.
            # Le QML n'est appliqué que si l'utilisateur l'a explicitement demandé.
            apply_style = bool(getattr(self, 'cb_cam_apply_style', None) and self.cb_cam_apply_style.isChecked())
            qcv_log(f"Application STYLE-PDV.qml demandée={apply_style}", "PDV-QML", "INFO")
            if apply_style:
                try:
                    style_ok = bool(self.apply_pdv_qml_style(layer, "core/style/STYLE-PDV.qml"))
                    if style_ok:
                        qcv_log("STYLE-PDV.qml appliqué avec succès", "PDV-QML", "INFO")
                    else:
                        qcv_log(
                            "STYLE-PDV.qml NON appliqué — consulter les messages loadNamedStyle précédents",
                            "PDV-QML", "WARNING"
                        )
                except Exception as style_exc:
                    qcv_log(
                        f"Exception pendant apply_pdv_qml_style: {style_exc}\n{traceback.format_exc()}",
                        "PDV-QML", "CRITICAL"
                    )

            is360 = self._is360_mode()

            try:
                hfov_ui = float(self.d_hfov.value())
            except Exception:
                hfov_ui = 60.0
            hfov_val = 360.0 if is360 else max(1.0, min(359.9, hfov_ui))

            try:
                yaw = float(self.d_yaw.value())
            except Exception:
                yaw = 0.0
            try:
                pitch = float(self.d_pitch.value())
            except Exception:
                pitch = 0.0

            try:
                md = float(self.d_maxdist.value())
            except Exception:
                md = 0.0
            rng = 200.0 if md <= 0 else max(50.0, min(500.0, md / 5.0))
            rng_full = 200.0 if md <= 0 else float(md)
            try:
                symrng = float(self.d_symdist.value())
            except Exception:
                symrng = 200.0
            symrng = 200.0 if symrng <= 0 else float(symrng)
    
            from qgis.core import QgsExpressionContextUtils
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_is360", 1 if is360 else 0)
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_hfov",  float(hfov_val))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_yaw",   float(yaw))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_range", float(rng))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_range_full", float(rng_full))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_symrange", float(symrng))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_pitch", float(pitch))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_altagl", float(self.d_camheight.value()))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_proj", str(self.cmb_proj.currentText()).strip().upper())
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_show_all", 1 if getattr(self, 'cb_cam_show_all', None) and self.cb_cam_show_all.isChecked() else 0)
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_current_fid", int(getattr(self, '_camera_current_fid', -1) or -1))
            
            # --- Offsets (mode + valeurs) ---
            
            try:
                mode = 0 if str(self.cmb_off_mode.currentText()).lower().startswith("pixel") else 1
            except Exception:
                mode = 0
            _ECU.setLayerVariable(layer, "fov_off_mode", int(mode))

            if mode == 0:
                # Pixels
                _ECU.setLayerVariable(layer, "fov_dx_px", float(self.spin_off_h.value()))
                _ECU.setLayerVariable(layer, "fov_dy_px", float(self.spin_off_v.value()))
                _ECU.setLayerVariable(layer, "fov_dx_pct", 0.0)
                _ECU.setLayerVariable(layer, "fov_dy_pct", 0.0)
            else:
                # Pourcentage (-0.5..+0.5)
                _ECU.setLayerVariable(layer, "fov_dx_px", 0.0)
                _ECU.setLayerVariable(layer, "fov_dy_px", 0.0)
                _ECU.setLayerVariable(layer, "fov_dx_pct", float(self.spin_off_h.value()))
                _ECU.setLayerVariable(layer, "fov_dy_pct", float(self.spin_off_v.value()))

            
            

            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            self._sync_pdv_qml()

            qcv_log(
                f"_after_camera_layer_changed OK — is360={int(bool(is360))}, "
                f"yaw={float(yaw):.3f}, pitch={float(pitch):.3f}, hfov={float(hfov_val):.3f}, "
                f"range={float(rng):.1f}, range_full={float(rng_full):.1f}, symrange={float(symrng):.1f}",
                "PDV-QML", "INFO"
            )

        except Exception as exc:
            qcv_log(
                f"_after_camera_layer_changed ECHEC: {exc}\n{traceback.format_exc()}",
                "PDV-QML", "CRITICAL"
            )
            try:
                self.iface.messageBar().pushWarning(
                    tr("QCALVIEW"),
                    tr("Erreur lors de la mise à jour du style/variables PDV — voir Messages > QCALVIEW")
                )
            except Exception:
                pass



    def _sync_pdv_qml(self):

        try:
            layer = self.cmb_camera.currentLayer() if hasattr(self.cmb_camera, "currentLayer") else None
            if not layer:
                return

            # 360° uniquement si case cochée
            is360 = self._is360_mode()

            # HFOV : 360 si is360, sinon valeur UI bornée
            try:
                hfov_ui = float(self.d_hfov.value())
            except Exception:
                hfov_ui = 60.0
            hfov_val = 360.0 if is360 else max(1.0, min(359.9, hfov_ui))

            # Yaw / Pitch
            try:
                yaw = float(self.d_yaw.value())
            except Exception:
                yaw = 0.0
            try:
                pitch = float(self.d_pitch.value())
            except Exception:
                pitch = 0.0

            # Portée graphique (m)
            try:
                md = float(self.d_maxdist.value())
            except Exception:
                md = 0.0
            rng = 200.0 if md <= 0 else max(50.0, min(500.0, md / 5.0))
            rng_full = 200.0 if md <= 0 else float(md)
            try:
                symrng = float(self.d_symdist.value())
            except Exception:
                symrng = 200.0
            symrng = 200.0 if symrng <= 0 else float(symrng)

            # Pousser les variables à CHAQUE synchro (écrasement explicite)
            from qgis.core import QgsExpressionContextUtils
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_is360", 1 if is360 else 0)
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_hfov",  float(hfov_val))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_yaw",   float(yaw))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_range", float(rng))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_range_full", float(rng_full))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_symrange", float(symrng))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_pitch", float(pitch))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_altagl", float(self.d_camheight.value()))
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_proj", str(self.cmb_proj.currentText()).strip().upper())
            QgsExpressionContextUtils.setLayerVariable(layer, "fov_show_all", 1 if getattr(self, 'cb_cam_show_all', None) and self.cb_cam_show_all.isChecked() else 0)
            QgsExpressionContextUtils.setLayerVariable(layer, "qcv_current_fid", int(getattr(self, '_camera_current_fid', -1) or -1))
            
            # --- Offsets (mode + valeurs) ---

            try:
                mode = 0 if str(self.cmb_off_mode.currentText()).lower().startswith("pixel") else 1
            except Exception:
                mode = 0
            _ECU.setLayerVariable(layer, "fov_off_mode", int(mode))

            if mode == 0:
                # Pixels
                _ECU.setLayerVariable(layer, "fov_dx_px", float(self.spin_off_h.value()))
                _ECU.setLayerVariable(layer, "fov_dy_px", float(self.spin_off_v.value()))
                _ECU.setLayerVariable(layer, "fov_dx_pct", 0.0)
                _ECU.setLayerVariable(layer, "fov_dy_pct", 0.0)
            else:
                # Pourcentage (-0.5..+0.5)
                _ECU.setLayerVariable(layer, "fov_dx_px", 0.0)
                _ECU.setLayerVariable(layer, "fov_dy_px", 0.0)
                _ECU.setLayerVariable(layer, "fov_dx_pct", float(self.spin_off_h.value()))
                _ECU.setLayerVariable(layer, "fov_dy_pct", float(self.spin_off_v.value()))

            
            
            
            # Refresh
            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()

            # (Debug rapide) : décommente pour tracer les valeurs poussées
            # from qgis.core import QgsMessageLog, Qgis
            # QgsMessageLog.logMessage(
            #     f"[CALIB] is360={1 if is360 else 0}, hfov={hfov_val}, yaw={yaw}, range={rng}, pitch={pitch}",
            #     "QCALVIEW", QC.Qgis_MessageLevel_Info
            # )
    
        except Exception:
            pass


    def _teardown_overlays(self):
        """Nettoie les RubberBands FOV/azimut pour éviter les fuites visuelles."""
        for rb in (getattr(self, "_rb_dir", None), getattr(self, "_rb_fov", None), getattr(self, "_rb_pick", None)):
            try:
                if rb:
                    rb.reset(True)
                    rb.hide()
            except Exception:
                pass
        try:
            vm = getattr(self, "_vm_pick", None)
            if vm is not None:
                vm.hide()
        except Exception:
            pass

    def closeEvent(self, ev):

        try:
            self._save_main_geometry()
            viewer = getattr(self, "viewer", None)
            if viewer is not None:
                try:
                    self._settings.setValue("QCALVIEW/ui/viewer_geometry", viewer.saveGeometry())
                except Exception:
                    pass
                try:
                    viewer.close()
                except Exception:
                    try:
                        viewer.hide()
                    except Exception:
                        pass
            self._teardown_overlays()
        finally:
            super().closeEvent(ev)


def _do_render_with_quality(self, quality: str):

    if int(getattr(self, '_render_suspend_count', 0) or 0) > 0:
        self._render_resume_requested = True
        return
    prev_quality = getattr(self, '_current_render_quality', 'high')
    self._current_render_quality = str(quality or 'normal')
    try:
        self._render_preview_now()
    finally:
        self._current_render_quality = prev_quality
        try:
            if getattr(self, 'render_scheduler', None) is not None:
                self.render_scheduler.mark_render_complete()
        except Exception:
            pass

def force_refresh_now(self):

    try:
        self._render_request_generation = int(getattr(self, '_render_request_generation', 0)) + 1
    except Exception:
        self._render_request_generation = 1
    try:
        if hasattr(self, '_hq_render_timer'):
            self._hq_render_timer.stop()
    except Exception:
        pass
    try:
        _sched = getattr(self, 'render_scheduler', None)
        if _sched is not None:
            if getattr(_sched, 'debounce_timer', None) is not None:
                _sched.debounce_timer.stop()
            if getattr(_sched, 'quality_restore_timer', None) is not None:
                _sched.quality_restore_timer.stop()
    except Exception:
        pass
    try:
        if hasattr(self, "_overlay_cache") and isinstance(self._overlay_cache, dict):
            self._overlay_cache.clear()
    except Exception:
        pass
    try:
        self._base_cache.clear()
    except Exception:
        pass
    try:
        self._horizon = None
        self._horizon_params = None
    except Exception:
        pass
    prev_quality = getattr(self, "_current_render_quality", "high")
    prev_full = bool(getattr(self, '_viewer_full_res', False))
    try:
        self._current_render_quality = "high"
        self._viewer_full_res = True
        self._render_preview_now()
    finally:
        self._viewer_full_res = prev_full
        self._current_render_quality = prev_quality


def _show_profiler_report(self):
    """Affiche un rapport simple sans casser le chargement du plugin."""
    try:
        text = self.profiler.report_text() if hasattr(self, 'profiler') else 'Profiler non initialisé.'
    except Exception as e:
        text = f'Profiler indisponible: {e}'
    QMessageBox.information(self, tr('Rapport de performance'), tr(text))


# --- SPLIT-ONLY BINDINGS (auto-generated) ---




def _relief_mode_id(self):
    try:
        idx = int(self.combo_relief_mode.currentIndex())
    except Exception:
        idx = 0
    mapping = {
        0: 'none',
        1: 'transparent',
        2: 'opaque',
        3: 'wireframe',
        4: 'ridgelines',
        5: 'skyline',
    }
    return mapping.get(idx, 'none')


def _sync_relief_mode_controls(self):
    mode = _relief_mode_id(self)
    mapping = {
        'cb_show_dem': (mode == 'wireframe'),
        'cb_draw_skyline': (mode == 'skyline'),
        'cb_draw_ridgelines': (mode == 'ridgelines'),
        'cb_occ_terrain': (mode != 'none'),
        'cb_occ_layers': (mode != 'none'),
        'cb_transparent_topo': (mode == 'transparent'),
        'cb_skyline_fill': (mode == 'opaque'),
    }
    for attr, checked in mapping.items():
        try:
            w = getattr(self, attr, None)
            if w is None:
                continue
            old = w.blockSignals(True)
            w.setChecked(bool(checked))
            w.blockSignals(old)
        except Exception:
            pass

    wire_visible = (mode == 'wireframe')
    try:
        if getattr(self, 'cb_wire_dashed', None) is not None:
            self.cb_wire_dashed.setVisible(wire_visible)
    except Exception:
        pass
    try:
        if getattr(self, 'combo_wire_mode', None) is not None:
            self.combo_wire_mode.setVisible(wire_visible)
    except Exception:
        pass
    try:
        if getattr(self, 'lbl_wire_mode', None) is not None:
            self.lbl_wire_mode.setVisible(wire_visible)
    except Exception:
        pass

    skyline_visible = (mode == 'skyline')
    try:
        if getattr(self, 'grp_skyline', None) is not None:
            self.grp_skyline.setVisible(skyline_visible)
    except Exception:
        pass


def _on_relief_mode_changed(self, *_args):
    try:
        self._sync_relief_mode_controls()
    finally:
        try:
            self.render_preview()
        except Exception:
            pass

from .core._schematic_view_ops import (
    _schematic_background_color, _schematic_background_transparent,
    _schematic_update_background_controls, _schematic_choose_background_color,
    _schematic_set_background_transparent, _make_schematic_base,
    _activate_schematic_view, _is_schematic_view
)
setattr(QCalViewDock, '_schematic_background_color', _schematic_background_color)
setattr(QCalViewDock, '_schematic_background_transparent', _schematic_background_transparent)
setattr(QCalViewDock, '_schematic_update_background_controls', _schematic_update_background_controls)
setattr(QCalViewDock, '_schematic_choose_background_color', _schematic_choose_background_color)
setattr(QCalViewDock, '_schematic_set_background_transparent', _schematic_set_background_transparent)
setattr(QCalViewDock, '_make_schematic_base', _make_schematic_base)
setattr(QCalViewDock, '_activate_schematic_view', _activate_schematic_view)
setattr(QCalViewDock, '_is_schematic_view', _is_schematic_view)

from .core._utils_ops import _deg, _connect_change_to_schedule, _begin_render_field_edit, _end_render_field_edit, _on_camera_layer_changed, load_photo, open_viewer, add_layer, add_layer_object, _on_layer_table_double_clicked, remove_layer, current_style, move_up, move_down, _finite_uv, _safe_line, _azimuth_deg, _elev_deg, _make_z_sampler, edit_style, _get_base_scaled, _refresh_preview_and_viewer, _on_toggle_center_axis, _on_toggle_pdv_axis, install_global_shortcuts, _mb, _cancel_maptool, _refresh_layer_list_labels, _on_layer_table_item_changed, sync_all_layer_styles_from_qgis, _prune_missing_overlay_layers, _on_project_layers_removed, refresh_qgis_themes, apply_qgis_theme_to_overlays, _on_qgis_theme_auto_sync_toggled, _restore_qgis_theme_settings
setattr(QCalViewDock, '_deg', _deg)
setattr(QCalViewDock, '_connect_change_to_schedule', _connect_change_to_schedule)
setattr(QCalViewDock, '_begin_render_field_edit', _begin_render_field_edit)
setattr(QCalViewDock, '_end_render_field_edit', _end_render_field_edit)
setattr(QCalViewDock, '_on_camera_layer_changed', _on_camera_layer_changed)
setattr(QCalViewDock, 'load_photo', load_photo)
setattr(QCalViewDock, 'open_viewer', open_viewer)
setattr(QCalViewDock, 'add_layer', add_layer)
setattr(QCalViewDock, 'add_layer_object', add_layer_object)
setattr(QCalViewDock, '_on_layer_table_double_clicked', _on_layer_table_double_clicked)
setattr(QCalViewDock, 'remove_layer', remove_layer)
setattr(QCalViewDock, 'current_style', current_style)
setattr(QCalViewDock, 'move_up', move_up)
setattr(QCalViewDock, 'move_down', move_down)
setattr(QCalViewDock, '_finite_uv', _finite_uv)
setattr(QCalViewDock, '_safe_line', _safe_line)
setattr(QCalViewDock, '_azimuth_deg', _azimuth_deg)
setattr(QCalViewDock, '_elev_deg', _elev_deg)
setattr(QCalViewDock, '_make_z_sampler', _make_z_sampler)
setattr(QCalViewDock, 'edit_style', edit_style)
setattr(QCalViewDock, '_refresh_layer_list_labels', _refresh_layer_list_labels)
setattr(QCalViewDock, '_on_layer_table_item_changed', _on_layer_table_item_changed)
setattr(QCalViewDock, 'sync_all_layer_styles_from_qgis', sync_all_layer_styles_from_qgis)
setattr(QCalViewDock, '_prune_missing_overlay_layers', _prune_missing_overlay_layers)
setattr(QCalViewDock, '_on_project_layers_removed', _on_project_layers_removed)
setattr(QCalViewDock, 'refresh_qgis_themes', refresh_qgis_themes)
setattr(QCalViewDock, 'apply_qgis_theme_to_overlays', apply_qgis_theme_to_overlays)
setattr(QCalViewDock, '_on_qgis_theme_auto_sync_toggled', _on_qgis_theme_auto_sync_toggled)
setattr(QCalViewDock, '_restore_qgis_theme_settings', _restore_qgis_theme_settings)
setattr(QCalViewDock, '_get_base_scaled', _get_base_scaled)
setattr(QCalViewDock, '_refresh_preview_and_viewer', _refresh_preview_and_viewer)
setattr(QCalViewDock, '_on_toggle_center_axis', _on_toggle_center_axis)
setattr(QCalViewDock, '_on_toggle_pdv_axis', _on_toggle_pdv_axis)
setattr(QCalViewDock, 'install_global_shortcuts', install_global_shortcuts)
setattr(QCalViewDock, '_cancel_maptool', _cancel_maptool)
setattr(QCalViewDock, '_mb', _mb)

from .core._gcp_ops import _draw_calib_grid, _on_map_pick_for_gcp, _start_add_gcp, _delete_gcp, _clear_gcps, _clear_gcp_markers, _refresh_gcp_markers, _draw_gcps_overlay, _solve_camera, _update_canvas_fov, _on_preview_click, _on_map_pick_pdv_center, start_pick_pdv_center, _ensure_fov_rubberbands, start_pick_view_from_canvas, _on_map_pick_set_view, start_image_to_canvas_pick, stop_interaction_tools, _ensure_nav_overlays, _image_click_to_full_uv, _dispatch_image_uv_click, _handle_image_navigation_click_uv, _draw_canvas_pick_ray, _on_viewer_image_clicked
setattr(QCalViewDock, '_draw_calib_grid', _draw_calib_grid)
setattr(QCalViewDock, '_on_map_pick_for_gcp', _on_map_pick_for_gcp)
setattr(QCalViewDock, '_start_add_gcp', _start_add_gcp)
setattr(QCalViewDock, '_delete_gcp', _delete_gcp)
setattr(QCalViewDock, '_clear_gcps', _clear_gcps)
setattr(QCalViewDock, '_clear_gcp_markers', _clear_gcp_markers)
setattr(QCalViewDock, '_refresh_gcp_markers', _refresh_gcp_markers)
setattr(QCalViewDock, '_draw_gcps_overlay', _draw_gcps_overlay)
setattr(QCalViewDock, '_solve_camera', _solve_camera)
setattr(QCalViewDock, '_update_canvas_fov', _update_canvas_fov)
setattr(QCalViewDock, '_on_preview_click', _on_preview_click)
setattr(QCalViewDock, '_on_map_pick_pdv_center', _on_map_pick_pdv_center)
setattr(QCalViewDock, 'start_pick_pdv_center', start_pick_pdv_center)
setattr(QCalViewDock, '_ensure_fov_rubberbands', _ensure_fov_rubberbands)
setattr(QCalViewDock, 'start_pick_view_from_canvas', start_pick_view_from_canvas)
setattr(QCalViewDock, '_on_map_pick_set_view', _on_map_pick_set_view)
setattr(QCalViewDock, 'start_image_to_canvas_pick', start_image_to_canvas_pick)
setattr(QCalViewDock, 'stop_interaction_tools', stop_interaction_tools)
setattr(QCalViewDock, '_ensure_nav_overlays', _ensure_nav_overlays)
setattr(QCalViewDock, '_image_click_to_full_uv', _image_click_to_full_uv)
setattr(QCalViewDock, '_dispatch_image_uv_click', _dispatch_image_uv_click)
setattr(QCalViewDock, '_handle_image_navigation_click_uv', _handle_image_navigation_click_uv)
setattr(QCalViewDock, '_draw_canvas_pick_ray', _draw_canvas_pick_ray)
setattr(QCalViewDock, '_on_viewer_image_clicked', _on_viewer_image_clicked)
from .core._monoplot_ops import start_monoplot_map_to_image, start_monoplot_image_to_ground, clear_monoplot_reperes, stop_monoplot_tools, _monoplot_handle_image_click_uv, _draw_monoplot_overlay, _monoplot_on_pdv_changed, _monoplot_refresh_list, _monoplot_current_pdv_info, _monoplot_current_camera_context
setattr(QCalViewDock, 'start_monoplot_map_to_image', start_monoplot_map_to_image)
setattr(QCalViewDock, 'start_monoplot_image_to_ground', start_monoplot_image_to_ground)
setattr(QCalViewDock, 'clear_monoplot_reperes', clear_monoplot_reperes)
setattr(QCalViewDock, 'stop_monoplot_tools', stop_monoplot_tools)
setattr(QCalViewDock, '_monoplot_handle_image_click_uv', _monoplot_handle_image_click_uv)
setattr(QCalViewDock, '_draw_monoplot_overlay', _draw_monoplot_overlay)
setattr(QCalViewDock, '_monoplot_on_pdv_changed', _monoplot_on_pdv_changed)
setattr(QCalViewDock, '_monoplot_refresh_list', _monoplot_refresh_list)
setattr(QCalViewDock, '_monoplot_current_pdv_info', _monoplot_current_pdv_info)
setattr(QCalViewDock, '_monoplot_current_camera_context', _monoplot_current_camera_context)
from .core._render_ops import _pick_dem_color, _build_horizon_cache, _is_visible_by_horizon, _update_horizon_by_segment, _draw_dem_wireframe, _draw_skyline, _draw_dem_opaque, _draw_azimuth_rule, _make_pov_curved_sampler, _apply_pov_curvature_to_z, _curvature_drop_from_cam_xy, _effective_curvature_radius
from .core._render_ops import _draw_dem_ridgelines   # (si tu veux activer les crêtes)
setattr(QCalViewDock, '_draw_dem_ridgelines', _draw_dem_ridgelines)
setattr(QCalViewDock, '_pick_dem_color', _pick_dem_color)
setattr(QCalViewDock, '_build_horizon_cache', _build_horizon_cache)
setattr(QCalViewDock, '_is_visible_by_horizon', _is_visible_by_horizon)
setattr(QCalViewDock, '_update_horizon_by_segment', _update_horizon_by_segment)
setattr(QCalViewDock, '_draw_dem_wireframe', _draw_dem_wireframe)
setattr(QCalViewDock, '_draw_skyline', _draw_skyline)
setattr(QCalViewDock, '_draw_dem_opaque', _draw_dem_opaque)
setattr(QCalViewDock, '_draw_azimuth_rule', _draw_azimuth_rule)
from .core._layerstyle import apply_pdv_qml_style, update_pdv_qml_vars
setattr(QCalViewDock, 'apply_pdv_qml_style', apply_pdv_qml_style)
setattr(QCalViewDock, 'update_pdv_qml_vars', update_pdv_qml_vars)
from .core._labels_ops import _label_offset_from_pos, _label_anchor_uv
setattr(QCalViewDock, '_label_offset_from_pos', _label_offset_from_pos)
setattr(QCalViewDock, '_label_anchor_uv', _label_anchor_uv)
from .core._render_ops import _draw_label, render_preview, _render_debounce_timeout, _render_debounce_delay_ms, _clear_base_cache_and_render, _overlay_params_key, _draw_fov_frame, _draw_axes_debug, _render_preview_now, _render_overlay, export_overlay, _draw_center_and_pdv_guides, set_pdv_azimuth, _render_vector_layers_fast
setattr(QCalViewDock, '_draw_label', _draw_label)
setattr(QCalViewDock, 'render_preview', render_preview)
setattr(QCalViewDock, '_render_debounce_timeout', _render_debounce_timeout)
setattr(QCalViewDock, '_render_debounce_delay_ms', _render_debounce_delay_ms)
setattr(QCalViewDock, 'update_preview', render_preview)  # alias compat ancienne UI

setattr(QCalViewDock, '_clear_base_cache_and_render', _clear_base_cache_and_render)
setattr(QCalViewDock, '_draw_fov_frame', _draw_fov_frame)
setattr(QCalViewDock, '_draw_axes_debug', _draw_axes_debug)
setattr(QCalViewDock, '_render_preview_now', _render_preview_now)
setattr(QCalViewDock, '_render_overlay', _render_overlay)
setattr(QCalViewDock, 'export_overlay', export_overlay)
setattr(QCalViewDock, '_draw_center_and_pdv_guides', _draw_center_and_pdv_guides)
setattr(QCalViewDock, 'set_pdv_azimuth', set_pdv_azimuth)
setattr(QCalViewDock, '_render_vector_layers_fast', _render_vector_layers_fast)
setattr(QCalViewDock, '_overlay_params_key', _overlay_params_key)
setattr(QCalViewDock, '_do_render_with_quality', _do_render_with_quality)
setattr(QCalViewDock, 'force_refresh_now', force_refresh_now)
setattr(QCalViewDock, '_show_profiler_report', _show_profiler_report)
from .core._camera_layer_ops import (
    _camera_on_layer_changed, _camera_refresh_field_combos, _camera_refresh_feature_list,
    _camera_current_feature, _camera_on_feature_changed, _camera_prev_feature, _camera_next_feature,
    _camera_load_current_feature, _camera_save_current_feature, _camera_ensure_fields,
    _camera_connect_layer_runtime_signals, _camera_disconnect_layer_runtime_signals,
    _camera_on_layer_selection_changed, _camera_on_layer_subset_changed, _camera_on_layer_data_changed,
    _camera_assign_current_photo_path, _camera_select_combo_feature_by_fid, _camera_select_layer_feature,
    _camera_schedule_feature_refresh, _camera_schedule_autosave, _camera_autosave_timeout,
    _camera_set_current_schematic, _camera_use_auto_image_source, _camera_associate_photo,
    _camera_on_geometry_changed, _camera_live_refresh_current, _camera_refresh_current_view,
    _camera_set_live_enabled, _camera_metric_project_crs, _camera_point_in_work_crs,
    _camera_warn_if_non_metric_project
)
setattr(QCalViewDock, '_camera_on_layer_changed', _camera_on_layer_changed)
setattr(QCalViewDock, '_camera_refresh_field_combos', _camera_refresh_field_combos)
setattr(QCalViewDock, '_camera_refresh_feature_list', _camera_refresh_feature_list)
setattr(QCalViewDock, '_camera_current_feature', _camera_current_feature)
setattr(QCalViewDock, '_camera_on_feature_changed', _camera_on_feature_changed)
setattr(QCalViewDock, '_camera_prev_feature', _camera_prev_feature)
setattr(QCalViewDock, '_camera_next_feature', _camera_next_feature)
setattr(QCalViewDock, '_camera_load_current_feature', _camera_load_current_feature)
setattr(QCalViewDock, '_camera_save_current_feature', _camera_save_current_feature)
setattr(QCalViewDock, '_camera_schedule_autosave', _camera_schedule_autosave)
setattr(QCalViewDock, '_camera_autosave_timeout', _camera_autosave_timeout)
setattr(QCalViewDock, '_camera_ensure_fields', _camera_ensure_fields)
setattr(QCalViewDock, '_camera_connect_layer_runtime_signals', _camera_connect_layer_runtime_signals)
setattr(QCalViewDock, '_camera_disconnect_layer_runtime_signals', _camera_disconnect_layer_runtime_signals)
setattr(QCalViewDock, '_camera_on_layer_selection_changed', _camera_on_layer_selection_changed)
setattr(QCalViewDock, '_camera_on_layer_subset_changed', _camera_on_layer_subset_changed)
setattr(QCalViewDock, '_camera_on_layer_data_changed', _camera_on_layer_data_changed)
setattr(QCalViewDock, '_camera_assign_current_photo_path', _camera_assign_current_photo_path)
setattr(QCalViewDock, '_camera_select_combo_feature_by_fid', _camera_select_combo_feature_by_fid)
setattr(QCalViewDock, '_camera_select_layer_feature', _camera_select_layer_feature)
setattr(QCalViewDock, '_camera_schedule_feature_refresh', _camera_schedule_feature_refresh)
setattr(QCalViewDock, '_camera_set_current_schematic', _camera_set_current_schematic)
setattr(QCalViewDock, '_camera_use_auto_image_source', _camera_use_auto_image_source)
setattr(QCalViewDock, '_camera_associate_photo', _camera_associate_photo)
setattr(QCalViewDock, '_camera_on_geometry_changed', _camera_on_geometry_changed)
setattr(QCalViewDock, '_camera_live_refresh_current', _camera_live_refresh_current)
setattr(QCalViewDock, '_camera_refresh_current_view', _camera_refresh_current_view)
setattr(QCalViewDock, '_camera_set_live_enabled', _camera_set_live_enabled)
setattr(QCalViewDock, '_camera_metric_project_crs', _camera_metric_project_crs)
setattr(QCalViewDock, '_camera_point_in_work_crs', _camera_point_in_work_crs)
setattr(QCalViewDock, '_camera_warn_if_non_metric_project', _camera_warn_if_non_metric_project)
from .core._projection_ops import _toggle_hfov_enable
setattr(QCalViewDock, '_toggle_hfov_enable', _toggle_hfov_enable)
from .core._export_ops import export_legend, export_batch_composite, export_batch_overlay_only, export_camera_variables_csv, export_current_composite, export_batch_selected, _refresh_batch_pdv_table, _set_all_batch_rows_checked, _on_batch_table_item_changed, _fit_export_table_height
setattr(QCalViewDock, 'export_legend', export_legend)
setattr(QCalViewDock, 'export_batch_composite', export_batch_composite)
setattr(QCalViewDock, 'export_batch_overlay_only', export_batch_overlay_only)
setattr(QCalViewDock, 'export_camera_variables_csv', export_camera_variables_csv)
setattr(QCalViewDock, 'export_current_composite', export_current_composite)
setattr(QCalViewDock, 'export_batch_selected', export_batch_selected)
setattr(QCalViewDock, '_refresh_batch_pdv_table', _refresh_batch_pdv_table)
setattr(QCalViewDock, '_set_all_batch_rows_checked', _set_all_batch_rows_checked)
setattr(QCalViewDock, '_on_batch_table_item_changed', _on_batch_table_item_changed)
setattr(QCalViewDock, '_fit_export_table_height', _fit_export_table_height)
setattr(QCalViewDock, '_relief_mode_id', _relief_mode_id)
setattr(QCalViewDock, '_sync_relief_mode_controls', _sync_relief_mode_controls)
setattr(QCalViewDock, '_on_relief_mode_changed', _on_relief_mode_changed)
