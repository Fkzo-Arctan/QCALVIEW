


from ._i18n import tr
from ._compat import QC, dialog_exec, QShortcut, enum_int
from qgis.PyQt import uic
from ._log import qcv_log
from ._image_io import load_working_image, read_scaled_for_owner, PhotoReadError
import os, json, math, re
import numpy as np
from qgis.PyQt.QtCore import Qt, QSize, QPoint, QTimer, QElapsedTimer, QRect, QRectF, pyqtSignal, QEvent, QSettings
from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QPixmap, QFont, QKeySequence, QTransform
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QColorDialog, QGroupBox, QFormLayout, QLineEdit, QScrollArea, 
    QDialog, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QSizePolicy,
    QSlider, QDialogButtonBox, QFrame, QMessageBox,
    QTableWidgetItem
)
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsWkbTypes, QgsPointXY, QgsFeature, QgsGeometry, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsVectorLayer, QgsRenderContext,
    QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils
)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand, QgsVertexMarker, QgsMapTool
from ._layerstyle import LayerStyle
from ._dialogs_ops import QInputDialogWithDefault
from ._schematic_symbols import get_symbol_library
from ._schematic_ui import (
    populate_symbol_combo, browse_symbol_library, edit_symbol_params, select_symbol_assets,
    populate_type_combo, populate_family_combo, symbol_taxonomy, filter_assets_for_context,
)
from ._schematic_tools import calculate_hedge_occlusion_dialog
from ..projector import hfov_from_focal_sensor, vfov_from_hfov_ratio


def _opacity_factor(value, default=1.0):
    
    try:
        v = float(value)
        if v > 1.0 and v <= 100.0:
            v /= 100.0
        if not math.isfinite(v):
            return float(default)
        return max(0.0, min(1.0, v))
    except Exception:
        return float(default)


def _qgis_style_opacity(layer, renderer=None, symbol=None, fallback=1.0):
    
    op = _opacity_factor(fallback, 1.0)
    for obj in (layer, renderer, symbol):
        if obj is None:
            continue
        try:
            attr = getattr(obj, 'opacity', None)
            if callable(attr):
                op *= _opacity_factor(attr(), 1.0)
            elif attr is not None:
                op *= _opacity_factor(attr, 1.0)
        except Exception:
            pass
    return max(0.0, min(1.0, op))

def _read_metadata_text_head(path: str, max_bytes: int = 4 * 1024 * 1024) -> str:
    
    try:
        with open(path, "rb") as f:
            return f.read(int(max_bytes)).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _parse_xmp_absolute_altitude(path) -> float | None:
    
    try:
        text = _read_metadata_text_head(path)
        m = re.search(r"(?:drone-dji:)?AbsoluteAltitude[^0-9\-\.]*(-?\d+(?:\.\d+)?)", text, re.I)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


def _parse_xmp_gpano_projection(jpeg_path: str) -> dict:
    
    out = {}
    try:
        text = _read_metadata_text_head(jpeg_path)
        
        keys = [
            "ProjectionType", "UsePanoramaViewer",
            "FullPanoWidthPixels", "FullPanoHeightPixels",
            "CroppedAreaImageWidthPixels", "CroppedAreaImageHeightPixels",
            "CroppedAreaLeftPixels", "CroppedAreaTopPixels",
        ]
        for k in keys:
            m = re.search(rf"GPano:{k}\s*>\s*([^<]+)\s*<", text, flags=re.I)
            if m:
                val = m.group(1).strip()
                
                if k.endswith("Pixels"):
                    try: out[k] = int(float(val))
                    except: out[k] = val
                elif k == "UsePanoramaViewer":
                    out[k] = val.lower() in ("true", "1", "yes")
                else:
                    out[k] = val
    except Exception:
        pass
    return out
    
def _rat_to_float(x, default=None):
    try:
        if hasattr(x, "numerator") and hasattr(x, "denominator") and x.denominator != 0:
            return float(x.numerator) / float(x.denominator)
        if isinstance(x, tuple) and len(x) == 2 and x[1]:
            return float(x[0]) / float(x[1])
        if isinstance(x, (int, float)):
            return float(x)
    except Exception:
        pass
    return default

def _mm_per_unit(res_unit: int) -> float:
    
    if res_unit == 2:
        return 25.4
    if res_unit == 3:
        return 10.0
    if res_unit == 4:
        return 1.0
    if res_unit == 5:
        return 0.001
    return 0.0

def _sensor_from_fpr(img_w_px, img_h_px, xres, yres, unit) -> tuple:
    
    mm_per = _mm_per_unit(int(unit or 0))
    if mm_per <= 0:
        return (None, None, None, None)
    xres = _rat_to_float(xres, 0.0) or 0.0
    yres = _rat_to_float(yres, 0.0) or 0.0
    if xres <= 0 or yres <= 0:
        return (None, None, None, None)
    px_pitch_x = mm_per / xres
    px_pitch_y = mm_per / yres
    sw = float(img_w_px) * px_pitch_x
    sh = float(img_h_px) * px_pitch_y
    return (sw, sh, px_pitch_x, px_pitch_y)

def _parse_xmp_relative_altitude(path) -> float | None:
    
    try:
        text = _read_metadata_text_head(path)
        m = re.search(r"(?:drone-dji:)?RelativeAltitude[^0-9\-\.]*(-?\d+(?:\.\d+)?)", text, re.I)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None

def read_exif(path):
    
    out = {
        'ImageWidth': None, 'ImageHeight': None,
        'FocalLength': None, 'FocalLength35mmEq': None,
        'SensorWidthMM': None, 'SensorHeightMM': None,
        'PixelPitchMM_X': None, 'PixelPitchMM_Y': None,
        'Azimuth': None, 'AltitudeMSL': None, 'RelativeAltitudeAGL': None,
        'CameraMake': None, 'CameraModel': None,
    }
    try:
        from PIL import Image, ExifTags
        
        
        
        _old_max_pixels = getattr(Image, 'MAX_IMAGE_PIXELS', None)
        try:
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as img:
                out['ImageWidth'], out['ImageHeight'] = img.size

                
                try:
                    exif = img._getexif() or {}
                except Exception:
                    try:
                        exif = dict(img.getexif().items()) or {}
                    except Exception:
                        exif = {}
        finally:
            try:
                Image.MAX_IMAGE_PIXELS = _old_max_pixels
            except Exception:
                pass

        TAGS = ExifTags.TAGS
        GPSTAGS = getattr(ExifTags, "GPSTAGS", {})
        name2id = {name: tid for tid, name in TAGS.items()}

        def get(tagname, default=None):
            tid = name2id.get(tagname)
            return exif.get(tid, default)

        
        out['CameraMake'] = get('Make', None)
        out['CameraModel'] = get('Model', None)

        
        out['FocalLength'] = _rat_to_float(get('FocalLength', None), None)

        
        w = get('ExifImageWidth', None) or get('ImageWidth', None)
        h = get('ExifImageHeight', None) or get('ImageLength', None)
        wv = _rat_to_float(w, None); hv = _rat_to_float(h, None)
        if isinstance(wv, (int, float)) and wv > 0: out['ImageWidth'] = int(wv)
        if isinstance(hv, (int, float)) and hv > 0: out['ImageHeight'] = int(hv)

        
        xres = get('FocalPlaneXResolution', None)
        yres = get('FocalPlaneYResolution', None)
        unit = get('FocalPlaneResolutionUnit', None)
        sw, sh, ppx, ppy = _sensor_from_fpr(out['ImageWidth'] or 0, out['ImageHeight'] or 0, xres, yres, unit)
        out['SensorWidthMM'] = sw; out['SensorHeightMM'] = sh
        out['PixelPitchMM_X'] = ppx; out['PixelPitchMM_Y'] = ppy

        
        f35 = _rat_to_float(get('FocalLengthIn35mmFilm', None), None)
        if isinstance(f35, (int, float)) and f35 > 0:
            out['FocalLength35mmEq'] = float(f35)
            if (not out['SensorWidthMM']) and out['FocalLength']:
                try:
                    out['SensorWidthMM'] = float(out['FocalLength']) * 36.0 / float(f35)
                except Exception:
                    pass

        
        if (out['FocalLength'] and out['SensorWidthMM']):
            try:
                out['FocalLength35mmEq'] = float(out['FocalLength']) * 36.0 / float(out['SensorWidthMM'])
            except Exception:
                pass

        
        gps = get('GPSInfo', None)
        if isinstance(gps, dict):
            gps_named = { (GPSTAGS.get(k, k)): v for k, v in gps.items() }

            alt = gps_named.get('GPSAltitude')
            alt_ref = gps_named.get('GPSAltitudeRef')  
            alt_m = _rat_to_float(alt, None)
            if isinstance(alt_m, (int, float)):
                if alt_ref in (1, b'\\x01'):
                    alt_m = -abs(float(alt_m))
                out['AltitudeMSL'] = float(alt_m)

            imgdir = gps_named.get('GPSImgDirection')
            az = _rat_to_float(imgdir, None)
            if isinstance(az, (int, float)):
                out['Azimuth'] = float(az) % 360.0

        
        agl = _parse_xmp_relative_altitude(path)
        if isinstance(agl, (int, float)):
            out['RelativeAltitudeAGL'] = float(agl)

        abs_alt = _parse_xmp_absolute_altitude(path)
        if isinstance(abs_alt, (int, float)) and out['AltitudeMSL'] is None:
            out['AltitudeMSL'] = float(abs_alt)
        
        
        gp = _parse_xmp_gpano_projection(path)
        if gp:
            
            proj = gp.get("ProjectionType")
            if proj:
                out["Projection"] = proj.lower()  
            
            for k in ("UsePanoramaViewer",
                      "FullPanoWidthPixels", "FullPanoHeightPixels",
                      "CroppedAreaImageWidthPixels", "CroppedAreaImageHeightPixels",
                      "CroppedAreaLeftPixels", "CroppedAreaTopPixels"):
                if k in gp:
                    out[k] = gp[k]

        
        
        
        
        
        if "Projection" not in out and out.get("ImageWidth") and out.get("ImageHeight"):
            w, h = int(out["ImageWidth"]), int(out["ImageHeight"])
            ratio = (w / float(h)) if h else 0.0
            if 1.98 <= ratio <= 2.02:
                out["Projection"] = "equirectangular"
                out["ProjectionSource"] = "ratio-2:1"
    except Exception:
        pass
    
    return out


class FmvViewerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("QCALVIEW — Visionneuse"))
        self.label = QLabel(tr("Aperçu"))
        self.label.setAlignment(QC.Qt_AlignmentFlag_AlignCenter)
        v = QVBoxLayout(self)
        v.addWidget(self.label, 1)
        self._last_img = None
        self.resize(1000, 600)

    def update_image(self, qimage):
        self._last_img = QImage(qimage)
        self._refresh()

    def sync_pdv_controls(self):
        owner = getattr(self, '_owner', None)
        combo = getattr(self, '_cmb_pdv', None)
        src = getattr(owner, 'cmb_cam_feature', None) if owner is not None else None
        if combo is None or src is None:
            return
        self._pdv_sync_guard = True
        try:
            combo.blockSignals(True)
            combo.clear()
            for i in range(src.count()):
                combo.addItem(tr(src.itemText(i)), src.itemData(i))
            fid = getattr(owner, '_camera_current_fid', None)
            idx = combo.findData(int(fid)) if fid is not None else src.currentIndex()
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        finally:
            try: combo.blockSignals(False)
            except Exception: pass
            self._pdv_sync_guard = False

    def _on_viewer_pdv_changed(self, index):
        if getattr(self, '_pdv_sync_guard', False):
            return
        owner = getattr(self, '_owner', None)
        combo = getattr(self, '_cmb_pdv', None)
        if owner is None or combo is None or index < 0:
            return
        try:
            fid = combo.itemData(index)
            owner._camera_select_combo_feature_by_fid(int(fid), autoload=True)
            self.sync_pdv_controls()
            self.update_info()
        except Exception:
            pass

    def _navigate_pdv(self, delta):
        owner = getattr(self, '_owner', None)
        if owner is None:
            return
        try:
            if int(delta) < 0: owner._camera_prev_feature()
            else: owner._camera_next_feature()
            self.sync_pdv_controls()
        except Exception:
            pass

    def _on_live_toggled(self, checked):
        owner = getattr(self, '_owner', None)
        if owner is not None:
            owner._camera_live_enabled = bool(checked)

    def _refresh_from_owner(self):
        owner = getattr(self, '_owner', None)
        if owner is None:
            return
        try:
            owner._camera_refresh_current_view(force=True)
        except Exception:
            try: owner.force_refresh_now()
            except Exception: pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev); self._refresh()

    def _refresh(self):
        if self._last_img is None: return
        pix = QPixmap.fromImage(self._last_img)
        self.label.setPixmap(pix.scaled(self.label.size(), QC.Qt_AspectRatioMode_KeepAspectRatio, QC.Qt_TransformationMode_SmoothTransformation))














def _deg(self, spin):
    spin.setRange(-359.0, 359.0); spin.setSingleStep(0.1); spin.setDecimals(2)

def _begin_render_field_edit(self, widget=None):
    
    try:
        key = id(widget) if widget is not None else -1
        active = getattr(self, '_render_edit_widgets', None)
        if not isinstance(active, set):
            active = set()
            self._render_edit_widgets = active
        active.add(key)
        self._render_edit_pending = True
        try:
            self.debounce.stop()
        except Exception:
            pass
        try:
            if hasattr(self, '_hq_render_timer'):
                self._hq_render_timer.stop()
        except Exception:
            pass
        try:
            sched = getattr(self, 'render_scheduler', None)
            if sched is not None:
                if getattr(sched, 'debounce_timer', None) is not None:
                    sched.debounce_timer.stop()
                if getattr(sched, 'quality_restore_timer', None) is not None:
                    sched.quality_restore_timer.stop()
        except Exception:
            pass
    except Exception:
        pass


def _end_render_field_edit(self, widget=None):
    
    try:
        active = getattr(self, '_render_edit_widgets', None)
        if not isinstance(active, set):
            active = set()
            self._render_edit_widgets = active
        key = id(widget) if widget is not None else -1
        active.discard(key)
        
        if active:
            return
        pending = bool(getattr(self, '_render_edit_pending', False))
        self._render_edit_pending = False
        if pending and not getattr(self, '_ui_initializing', False):
            
            
            
            try:
                if hasattr(self, '_camera_schedule_autosave'):
                    self._camera_schedule_autosave()
            except Exception:
                pass
            try:
                if hasattr(self, '_sync_pdv_qml'):
                    self._sync_pdv_qml()
            except Exception:
                pass
            
            
            
            
            expensive_names = ('d_yaw', 'd_yaw_offset', 'd_pitch', 'd_roll', 'd_camheight',
                               'd_hfov', 'd_vfov', 'd_maxdist', 'd_focal', 'd_sensorw')
            expensive = any(widget is getattr(self, n, None) for n in expensive_names)
            try:
                pano = str(self.cmb_proj.currentText()).strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
            except Exception:
                pano = False
            self._render_delay_override_ms = 380 if (expensive and pano) else 90
            try:
                self.render_preview()
            finally:
                self._render_delay_override_ms = None
    except Exception:
        try:
            self.render_preview()
        except Exception:
            pass


def _connect_change_to_schedule(self, widget):
    
    if widget is None:
        return

    
    
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        try:
            widget.valueChanged.connect(self.render_preview)
        except Exception:
            pass
        try:
            le = widget.lineEdit()
            if le is not None:
                le.textEdited.connect(lambda *_a, w=widget: self._begin_render_field_edit(w))
        except Exception:
            pass
        try:
            widget.editingFinished.connect(lambda w=widget: self._end_render_field_edit(w))
        except Exception:
            pass
        return

    if isinstance(widget, QLineEdit):
        try:
            widget.textEdited.connect(lambda *_a, w=widget: self._begin_render_field_edit(w))
        except Exception:
            pass
        try:
            widget.editingFinished.connect(lambda w=widget: self._end_render_field_edit(w))
        except Exception:
            pass
        return

    
    
    for sig in ('currentIndexChanged', 'toggled', 'valueChanged', 'textChanged'):
        if hasattr(widget, sig):
            try:
                getattr(widget, sig).connect(self.render_preview)
                return
            except Exception:
                continue

def _on_camera_layer_changed(self, layer):
    for sig, func in self._cam_connections:
        try: sig.disconnect(func)
        except: pass
    self._cam_connections.clear()
    self._cam_layer = layer
    if isinstance(layer, QgsVectorLayer):
        
        
        
        try:
            layer.featureAdded.connect(lambda *a, **k: self.render_preview()); self._cam_connections.append((layer.featureAdded, self.render_preview))
        except: pass
        try:
            layer.featureAdded.connect(lambda *a, **k: self.render_preview()); self._cam_connections.append((layer.featureAdded, self.render_preview))
        except: pass
        try:
            layer.featuresDeleted.connect(lambda *a, **k: self.render_preview()); self._cam_connections.append((layer.featuresDeleted, self.render_preview))
        except: pass

def load_photo(self):
    path, _ = QFileDialog.getOpenFileName(self, tr("Choisir une photo"), "", tr("Images (*.jpg *.jpeg *.png *.tif *.tiff)"))
    if not path:
        return False
    prev_loading = getattr(self, '_camera_loading_feature', False)
    self._camera_loading_feature = True
    try:
        working_image, source_info = load_working_image(path)
    except Exception as e:
        self._camera_loading_feature = prev_loading
        qcv_log(f"Échec chargement photo {path}: {e}", 'PHOTO/IO', 'CRITICAL')
        try:
            QMessageBox.warning(self, tr("QCALVIEW — chargement image"),
                                tr("Impossible de charger cette image.\n\n" + str(e) +
                                "\n\nConsultez le journal QCALVIEW pour le détail."))
        except Exception:
            pass
        return False
    self.photo_path = path
    self.image = working_image
    self._photo_source_info = dict(source_info or {})
    self._photo_is_proxy = bool(self._photo_source_info.get('proxy', False))
    self._base_cache.clear(); self._z_cache.clear(); self._horizon = None; self._horizon_params = None
    try:
        getattr(self, "_overlay_cache", {}).clear()
    except Exception:
        pass
    try:
        getattr(self, "_geom_cache", {}).clear()
    except Exception:
        pass
    try:
        self._layer_cache_versions = {}
    except Exception:
        pass

    ex = read_exif(path); info = []
    try:
        sw = int(self._photo_source_info.get('width', 0) or 0)
        sh = int(self._photo_source_info.get('height', 0) or 0)
        if not ex.get('ImageWidth') and sw > 0:
            ex['ImageWidth'] = sw
        if not ex.get('ImageHeight') and sh > 0:
            ex['ImageHeight'] = sh
        if bool(self._photo_is_proxy):
            pw = int(self._photo_source_info.get('proxy_width', self.image.width()))
            ph = int(self._photo_source_info.get('proxy_height', self.image.height()))
            info.append(f"Source {sw}×{sh} px · proxy {pw}×{ph}")
        if not ex.get('Projection') and sh > 0 and (float(sw) / float(sh)) >= 3.0:
            info.append("Panorama large : projection manuelle conservée")
    except Exception:
        pass
    
    
    
    
    proj = (ex.get("Projection") or "").lower()
    try:
        current_mode = str(self.cmb_proj.currentText()).strip().upper()
    except Exception:
        current_mode = str(getattr(self, '_projection_mode', 'PINHOLE')).strip().upper()
    mode = current_mode if current_mode in ("PINHOLE", "EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL") else "PINHOLE"
    if proj == "equirectangular":
        mode = "EQUIRECT"
    elif proj == "cylindrical":
        mode = "CYLINDRICAL"
    elif proj in ("rectilinear", "pinhole"):
        mode = "PINHOLE"

    
    updated = False
    for cmb_name in ("cmb_projection", "cmb_proj"):
        cmb = getattr(self, cmb_name, None)
        if cmb is not None:
            try:
                idx = cmb.findText(mode)
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
                else:
                    cmb.setCurrentText(mode)
                updated = True
            except Exception:
                pass
        
    if not updated:
        self._projection_mode = mode

    
    
    
    
    try:
        mode_upper = str(mode).strip().upper()
        is_equirect = mode_upper in ('EQUIRECT', 'EQUIRECTANGULAR')
        auto_full_equirect = False
        if is_equirect:
            iw = int(ex.get('ImageWidth') or 0)
            ih = int(ex.get('ImageHeight') or 0)
            ratio_2_1 = bool(ih > 0 and 1.98 <= (iw / float(ih)) <= 2.02)

            fw = int(ex.get('FullPanoWidthPixels') or 0)
            fh = int(ex.get('FullPanoHeightPixels') or 0)
            cw = int(ex.get('CroppedAreaImageWidthPixels') or iw or 0)
            ch = int(ex.get('CroppedAreaImageHeightPixels') or ih or 0)
            left = int(ex.get('CroppedAreaLeftPixels') or 0)
            top = int(ex.get('CroppedAreaTopPixels') or 0)
            gpano_full = bool(
                fw > 0 and fh > 0
                and cw == fw and ch == fh
                and left == 0 and top == 0
            )
            auto_full_equirect = bool(ratio_2_1 or gpano_full)

        self.cb_360.blockSignals(True)
        self.cb_360.setEnabled(is_equirect)
        if not is_equirect:
            self.cb_360.setChecked(False)
        elif auto_full_equirect:
            self.cb_360.setChecked(True)
        self.cb_360.blockSignals(False)

        if is_equirect and auto_full_equirect:
            self.d_hfov.blockSignals(True)
            self.d_vfov.blockSignals(True)
            self.d_hfov.setValue(360.0)
            self.d_vfov.setValue(180.0)
            self.d_hfov.setEnabled(False)
            self.d_vfov.setEnabled(False)
            self.d_hfov.blockSignals(False)
            self.d_vfov.blockSignals(False)
    except Exception:
        try:
            self.cb_360.blockSignals(False)
            self.d_hfov.blockSignals(False)
            self.d_vfov.blockSignals(False)
        except Exception:
            pass

    
    
    
    
    focal = ex.get('FocalLength')
    sensor_w = ex.get('SensorWidthMM')
    focal_35 = ex.get('FocalLength35mmEq')

    def _positive(v):
        try:
            v = float(v)
            return math.isfinite(v) and v > 0.0
        except Exception:
            return False

    optics_ok = False
    if _positive(focal) and _positive(sensor_w):
        self.d_focal.setValue(float(focal))
        self.d_sensorw.setValue(float(sensor_w))
        info.append(f"Focale {float(focal):.2f} mm")
        info.append(f"Capteur {float(sensor_w):.2f} mm")
        optics_ok = True
    elif _positive(focal_35):
        
        
        self.d_focal.setValue(float(focal_35))
        self.d_sensorw.setValue(36.0)
        info.append(f"Focale Eq. 24×36 {float(focal_35):.2f} mm")
        optics_ok = True
    else:
        
        
        optics_ok = False
    
    if ex.get('RelativeAltitudeAGL') is not None and hasattr(self, 'd_agl'):
        self.d_agl.setValue(ex['RelativeAltitudeAGL'])
        info.append(f"AGL {ex['RelativeAltitudeAGL']:.2f} m")
    
    if ex.get('AltitudeMSL') is not None and hasattr(self, 'd_alt'):
        self.d_alt.setValue(ex['AltitudeMSL'])
        info.append(f"Alt. MSL {ex['AltitudeMSL']:.2f} m")
    if ex.get('ImageWidth') and ex.get('ImageHeight'):
        self.spin_w.setValue(int(ex['ImageWidth'])); self.spin_h.setValue(int(ex['ImageHeight']))
        info.append(f"{ex['ImageWidth']}×{ex['ImageHeight']} px")
    if ex.get('RelativeAltitudeAGL') is None and ex.get('AltitudeMSL') is None:
        info.append("Altitude non trouvée dans les métadonnées")
    self.lbl_info.setText(tr(" | ".join(info) if info else os.path.basename(path)))

    if self.cb_auto_hfov.isChecked() and optics_ok:
        HFOV = hfov_from_focal_sensor(self.d_focal.value(), self.d_sensorw.value())
        self.d_hfov.blockSignals(True); self.d_hfov.setValue(HFOV); self.d_hfov.blockSignals(False)
        vfov = 40.0 if mode == 'CYLINDRICAL' else vfov_from_hfov_ratio(HFOV, self.spin_w.value(), self.spin_h.value())
        self.d_vfov.blockSignals(True); self.d_vfov.setValue(vfov); self.d_vfov.blockSignals(False)
    elif self.cb_auto_hfov.isChecked() and mode == 'PINHOLE':
        
        
        self.cb_auto_hfov.blockSignals(True)
        self.cb_auto_hfov.setChecked(False)
        self.cb_auto_hfov.blockSignals(False)
        self.d_hfov.setEnabled(True)
        info.append("HFOV auto indisponible")
        self.lbl_info.setText(tr(" | ".join(info) if info else os.path.basename(path)))

    try:
        self._camera_assign_current_photo_path(path)
    except Exception:
        pass
    finally:
        self._camera_loading_feature = prev_loading

    self.render_preview()
    try:
        self._camera_schedule_autosave()
    except Exception:
        pass
    
    try:
        layer = self.cmb_camera.currentLayer()
        if layer:
            proj_txt = ""
            try:
                proj_txt = str(self.cmb_proj.currentText()).strip().upper()
            except Exception:
                pass
            is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (bool(self.cb_360.isChecked()) and proj_txt in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL"))
            md = float(self.d_maxdist.value())
            rng = 200.0 if md <= 0 else max(50.0, min(500.0, md/5.0))
            self.update_pdv_qml_vars(
                layer,
                yaw_deg=float(self.d_yaw.value()),
                hfov_deg=(360.0 if is360 else float(self.d_hfov.value())),
                is360=is360,
                range_m=rng,
                pitch_deg=float(self.d_pitch.value())
            )
            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()    
            
    except Exception:
        pass
    try:
        if hasattr(self, "_update_ui_summary"):
            self._update_ui_summary()
    except Exception:
        pass
    return True


def _to_pixmap(src) -> QPixmap:
    if isinstance(src, QPixmap):
        return src
    if isinstance(src, QImage):
        return QPixmap.fromImage(src)
    if isinstance(src, (str, bytes)):
        pm = QPixmap()
        pm.load(src) if isinstance(src, str) else pm.loadFromData(src)
        return pm
    return QPixmap()

class _ImageViewer(QDialog):
    imageClicked = pyqtSignal(float, float)
    def __init__(self, parent=None, owner=None):
        super().__init__(parent)
        self._owner = owner
        self.setWindowTitle(tr("QCALVIEW — Visionneuse"))
        self.setModal(False)
        try:
            self.setWindowFlags(self.windowFlags() | QC.Qt_WindowType_Window | QC.Qt_WindowType_WindowMinMaxButtonsHint | QC.Qt_WindowType_WindowCloseButtonHint)
            self.setSizeGripEnabled(True)
            self.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Expanding)
            self.setMinimumSize(320, 220)
            self.setMaximumSize(16777215, 16777215)
        except Exception:
            pass
        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        try:
            self._view.setMinimumSize(160, 120)
            self._view.setMaximumSize(16777215, 16777215)
            self._view.setSizePolicy(QC.QSizePolicy_Policy_Ignored, QC.QSizePolicy_Policy_Ignored)
        except Exception:
            pass
        self._view.viewport().installEventFilter(self)
        self._view.setDragMode(QC.QGraphicsView_DragMode_ScrollHandDrag)
        self._view.setTransformationAnchor(QC.QGraphicsView_ViewportAnchor_AnchorUnderMouse)
        self._view.setResizeAnchor(QC.QGraphicsView_ViewportAnchor_AnchorUnderMouse)
        self._pix = QGraphicsPixmapItem()
        self._pix.setZValue(0)
        self._scene.addItem(tr(self._pix))
        
        
        self._overlay_pix = QGraphicsPixmapItem()
        self._overlay_pix.setZValue(10)
        self._overlay_pix.setVisible(False)
        self._scene.addItem(tr(self._overlay_pix))
        
        
        btn_plus = QPushButton(tr("Zoom +"))
        btn_moins = QPushButton(tr("Zoom −"))
        btn_fit = QPushButton(tr("Ajuster"))
        btn_100 = QPushButton(tr("100 %"))
        btn_capture = QPushButton(tr("Capture…"))
        btn_export_full = QPushButton(tr("Export…"))
        btn_refresh = QPushButton(tr("↻ Rafraîchir"))
        chk_live = QCheckBox(tr("Live"))
        chk_live.setChecked(bool(getattr(owner, '_camera_live_enabled', False)) if owner is not None else False)
        chk_live.setToolTip(tr("Actualise automatiquement la visionneuse après modification du PDV ou d’une couche projetée (après relâchement / stabilisation)."))
        btn_prev_pdv = QPushButton(tr("◀"))
        btn_next_pdv = QPushButton(tr("▶"))
        cmb_pdv = QComboBox()
        cmb_pdv.setMinimumWidth(150)
        cmb_pdv.setToolTip(tr("Accès direct à un point de vue ; l’ordre suit le champ d’ordre configuré dans QCALVIEW."))
        info = QLabel(tr("Astuce : molette pour zoomer, glisser pour déplacer."))

        
        lbl_op = QLabel(tr("Opacité"))
        sld_op = QSlider(QC.Qt_Orientation_Horizontal)
        sld_op.setRange(0, 100)
        sld_op.setValue(100)            
        sld_op.setMinimumWidth(60)
        sld_op.setMaximumWidth(140)
        
        
        chk_overlay = QCheckBox(tr("Overlay"))
        chk_overlay.setChecked(True)

        info = QLabel(tr("Molette: zoom · Glisser: déplacer · Raccourcis: +/-/1/F/O"))
        info.setStyleSheet("color: #666;")
        info.setMinimumWidth(0)
        info.setSizePolicy(QC.QSizePolicy_Policy_Ignored, QC.QSizePolicy_Policy_Fixed)
        
        top = QHBoxLayout()
        for b in (btn_plus, btn_moins, btn_100, btn_fit, btn_capture, btn_export_full, btn_refresh, btn_prev_pdv, btn_next_pdv):
            try:
                b.setMinimumWidth(0)
                b.setSizePolicy(QC.QSizePolicy_Policy_Maximum, QC.QSizePolicy_Policy_Fixed)
            except Exception:
                pass
            top.addWidget(b)
        top.addSpacing(8)
        top.addWidget(QLabel(tr("PDV")))
        top.addWidget(cmb_pdv)
        top.addWidget(chk_live)
        top.addSpacing(12)
        top.addWidget(chk_overlay)
        top.addSpacing(12)
        top.addWidget(lbl_op)
        top.addWidget(sld_op)
        top.addStretch(1)
        top.addWidget(info)

        self._info_panel = QLabel(tr("Informations\n—"))
        self._info_panel.setWordWrap(True)
        self._info_panel.setMinimumWidth(110)
        self._info_panel.setMaximumWidth(240)
        self._info_panel.setTextInteractionFlags(QC.Qt_TextInteractionFlag_TextSelectableByMouse)
        try:
            self._info_panel.setMinimumHeight(0)
            self._info_panel.setMaximumHeight(16777215)
            self._info_panel.setSizePolicy(QC.QSizePolicy_Policy_Fixed, QC.QSizePolicy_Policy_Ignored)
        except Exception:
            pass
        self._info_panel.setStyleSheet("background:#202629; color:#e9eef2; padding:10px; border-radius:6px;")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(self._view, 1)
        body.addWidget(self._info_panel, 0)
        lay.addLayout(body, 1)

        btn_plus.clicked.connect(lambda: self._zoom(1.25))
        btn_moins.clicked.connect(lambda: self._zoom(0.8))
        btn_fit.clicked.connect(self._fit)
        btn_100.clicked.connect(self._reset)
        btn_capture.clicked.connect(self.export_window_capture)
        btn_export_full.clicked.connect(self.export_full_resolution)
        btn_refresh.clicked.connect(self._refresh_from_owner)
        btn_prev_pdv.clicked.connect(lambda: self._navigate_pdv(-1))
        btn_next_pdv.clicked.connect(lambda: self._navigate_pdv(+1))
        cmb_pdv.currentIndexChanged.connect(self._on_viewer_pdv_changed)
        chk_live.toggled.connect(self._on_live_toggled)

        
        def _apply_opacity(val):
            self._overlay_pix.setOpacity(val / 100.0)
        sld_op.valueChanged.connect(_apply_opacity)
        _apply_opacity(100)

        
        def _toggle_overlay(checked):
            self._overlay_pix.setVisible(checked and not self._overlay_pix.pixmap().isNull())
        chk_overlay.toggled.connect(_toggle_overlay)

        
        QShortcut(QKeySequence("+"), self, activated=lambda: self._zoom(1.25))
        QShortcut(QKeySequence("-"), self, activated=lambda: self._zoom(0.8))
        QShortcut(QKeySequence("1"), self, activated=self._reset)
        QShortcut(QKeySequence("F"), self, activated=self._fit)
        QShortcut(QKeySequence("O"), self, activated=lambda: chk_overlay.toggle())
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self.export_full_resolution)
        QShortcut(QKeySequence("Ctrl+Alt+S"), self, activated=self.export_window_capture)
        
        
        self._sld_op = sld_op
        self._chk_overlay = chk_overlay
        self._cmb_pdv = cmb_pdv
        self._chk_live = chk_live
        self._pdv_sync_guard = False
        self.sync_pdv_controls()

        self._scale = 1.0
        self._has_pix = False
        self._settings = QSettings("ArcTan", "QCALVIEW")
        self._geometry_restored = False
        try:
            geom = self._settings.value("QCALVIEW/ui/viewer_geometry", None)
            if geom:
                self.restoreGeometry(geom)
                self._geometry_restored = True
            else:
                self.resize(1100, 700)
                self._geometry_restored = True
        except Exception:
            self.resize(1100, 700)
            self._geometry_restored = True
        self.update_info()

    
    
    
    def sync_pdv_controls(self):
        owner = getattr(self, '_owner', None)
        combo = getattr(self, '_cmb_pdv', None)
        src = getattr(owner, 'cmb_cam_feature', None) if owner is not None else None
        if combo is None or src is None:
            return
        self._pdv_sync_guard = True
        try:
            combo.blockSignals(True)
            combo.clear()
            for i in range(src.count()):
                combo.addItem(tr(src.itemText(i)), src.itemData(i))
            fid = getattr(owner, '_camera_current_fid', None)
            idx = combo.findData(int(fid)) if fid is not None else src.currentIndex()
            if idx >= 0:
                combo.setCurrentIndex(idx)
        finally:
            try:
                combo.blockSignals(False)
            except Exception:
                pass
            self._pdv_sync_guard = False

    def _on_viewer_pdv_changed(self, index):
        if getattr(self, '_pdv_sync_guard', False):
            return
        owner = getattr(self, '_owner', None)
        combo = getattr(self, '_cmb_pdv', None)
        if owner is None or combo is None or index < 0:
            return
        fid = combo.itemData(index)
        if fid is None:
            return
        try:
            owner._camera_select_combo_feature_by_fid(int(fid), autoload=True)
            self.sync_pdv_controls()
            self.update_info()
        except Exception as exc:
            try:
                owner.iface.messageBar().pushWarning(tr('QCALVIEW'), tr(f'Changement de PDV impossible : {exc}'))
            except Exception:
                pass

    def _navigate_pdv(self, delta):
        owner = getattr(self, '_owner', None)
        if owner is None:
            return
        try:
            if int(delta) < 0:
                owner._camera_prev_feature()
            else:
                owner._camera_next_feature()
            self.sync_pdv_controls()
            self.update_info()
        except Exception as exc:
            try:
                owner.iface.messageBar().pushWarning(tr('QCALVIEW'), tr(f'Navigation PDV impossible : {exc}'))
            except Exception:
                pass

    def _on_live_toggled(self, checked):
        owner = getattr(self, '_owner', None)
        if owner is None:
            return
        try:
            owner._camera_set_live_enabled(bool(checked))
        except Exception:
            owner._camera_live_enabled = bool(checked)

    def _refresh_from_owner(self):
        owner = getattr(self, '_owner', None)
        if owner is None:
            return
        try:
            owner._camera_refresh_current_view(force=True)
        except Exception as exc:
            try:
                owner.force_refresh_now()
            except Exception:
                try:
                    owner.iface.messageBar().pushWarning(tr('QCALVIEW'), tr(f'Rafraîchissement impossible : {exc}'))
                except Exception:
                    pass

    def resizeEvent(self, ev):
        try:
            super().resizeEvent(ev)
        except Exception:
            pass
        
        try:
            self._settings.setValue("QCALVIEW/ui/viewer_geometry", self.saveGeometry())
        except Exception:
            pass

    def closeEvent(self, ev):
        try:
            self._settings.setValue("QCALVIEW/ui/viewer_geometry", self.saveGeometry())
        except Exception:
            pass
        super().closeEvent(ev)

    def update_info(self):
        owner = getattr(self, "_owner", None)
        if owner is None:
            self._info_panel.setText(tr("Informations\n—"))
            return
        try:
            photo = os.path.basename(getattr(owner, "photo_path", "") or "") or "—"
            pdv = "—"
            try:
                pdv = owner.cmb_cam_feature.currentText().strip() or "—"
            except Exception:
                pass
            yaw = float(owner.d_yaw.value()) if hasattr(owner, "d_yaw") else 0.0
            pitch = float(owner.d_pitch.value()) if hasattr(owner, "d_pitch") else 0.0
            roll = float(owner.d_roll.value()) if hasattr(owner, "d_roll") else 0.0
            hfov = float(owner.d_hfov.value()) if hasattr(owner, "d_hfov") else 0.0
            vfov = float(owner.d_vfov.value()) if hasattr(owner, "d_vfov") else 0.0
            hcam = float(owner.d_camheight.value()) if hasattr(owner, "d_camheight") else 0.0
            proj = str(owner.cmb_proj.currentText()) if hasattr(owner, "cmb_proj") else "—"
            x = y = None
            crs_authid = ""
            try:
                if hasattr(owner, "_current_camera_xy_project_crs"):
                    x, y, crs_authid = owner._current_camera_xy_project_crs()
            except Exception:
                pass
            xy = "X : —\nY : —" if x is None or y is None else f"X : {x:,.2f}\nY : {y:,.2f}".replace(",", " ")
            if crs_authid:
                xy += f"\nCRS : {crs_authid}"
            try:
                self.sync_pdv_controls()
                self.setWindowTitle(tr(f"QCALVIEW — Visionneuse — {pdv}"))
            except Exception:
                pass
            self._info_panel.setText(
                tr(f"<b>Informations</b><br>"
                f"{xy.replace(chr(10), '<br>')}<br>"
                f"Hauteur caméra : {hcam:.2f} m<br>"
                f"Azimut : {yaw:.1f}°<br>"
                f"Tangage : {pitch:.1f}°<br>"
                f"Roulis : {roll:.1f}°<br>"
                f"HFOV : {hfov:.1f}°<br>"
                f"VFOV : {vfov:.1f}°<br>"
                f"Projection : {proj}<br>"
                f"Image : {photo}<br>"
                f"PDV : {pdv}")
            )
        except Exception:
            self._info_panel.setText(tr("Informations<br>—"))

    def _save_image_dialog(self, title, default_name):
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr(title),
            default_name,
            tr("PNG (*.png);;JPEG (*.jpg *.jpeg);;TIFF (*.tif *.tiff)")
        )
        return path

    def _save_qimage(self, img: QImage, path: str):
        if img is None or img.isNull() or not path:
            return False
        ext = os.path.splitext(path)[1].lower()
        fmt = {
            ".png": "PNG",
            ".jpg": "JPG",
            ".jpeg": "JPG",
            ".tif": "TIFF",
            ".tiff": "TIFF",
        }.get(ext, "PNG")
        if fmt == "PNG" and not ext:
            path += ".png"
        return img.save(path, fmt)

    def export_window_capture(self):
        path = self._save_image_dialog("Exporter capture de la fenêtre", "qcalview_capture.png")
        if not path:
            return
        shot = self.grab()
        if not shot.isNull():
            shot.save(path)

    def export_full_resolution(self):
        path = self._save_image_dialog("Exporter image pleine résolution avec overlays", "qcalview_export.png")
        if not path:
            return

        owner = getattr(self, "_owner", None)
        try:
            if owner is not None:
                W = int(owner.spin_w.value())
                H = int(owner.spin_h.value())
            else:
                pm = self._pix.pixmap()
                W = int(pm.width()) if not pm.isNull() else 0
                H = int(pm.height()) if not pm.isNull() else 0
            if W <= 0 or H <= 0:
                return

            if owner is not None:
                base = owner._get_base_scaled(W, H)
            else:
                base_pm = self._pix.pixmap()
                if base_pm.isNull():
                    return
                base = base_pm.toImage()

            composed = QImage(base)
            if bool(getattr(self, "_chk_overlay", None) and self._chk_overlay.isChecked()):
                if owner is not None:
                    key = owner._overlay_params_key(W, H)
                    overlay = getattr(owner, "_overlay_cache", {}).get(key)
                    if overlay is None or overlay.isNull():
                        overlay = owner._render_overlay(width=W, height=H)
                        if not hasattr(owner, "_overlay_cache"):
                            owner._overlay_cache = {}
                        owner._overlay_cache[key] = overlay
                else:
                    pm = self._overlay_pix.pixmap()
                    overlay = pm.toImage() if not pm.isNull() else None

                if overlay is not None and not overlay.isNull():
                    qp = QPainter(composed)
                    qp.drawImage(0, 0, overlay)
                    qp.end()

            ok_saved = self._save_qimage(composed, path)
            if ok_saved and owner is not None:
                try:
                    from ._export_ops import _write_metadata_with_exiftool
                    from ._camera_layer_ops import _camera_layer, _camera_current_feature, _camera_set_status
                    ok_meta, err_meta = _write_metadata_with_exiftool(owner, path, _camera_layer(owner), _camera_current_feature(owner))
                    if not ok_meta:
                        _camera_set_status(owner, err_meta or 'Échec écriture métadonnées.', '#c44')
                except Exception:
                    pass
        except Exception:
            pass

    def set_pixmap(self, pm: QPixmap):
        if pm.isNull():
            return
        try:
            pm.setDevicePixelRatio(1.0)
        except Exception:
            pass

        
        keep_transform = self._view.transform()
        keep_center = self._view.mapToScene(self._view.viewport().rect().center())
    
        self._pix.setPixmap(pm)
        self._pix.setOffset(0, 0)
        self._scene.setSceneRect(QRectF(pm.rect()))

        if not self._has_pix:
            
            self._reset()
            self._has_pix = True
        else:
            
            self._view.setTransform(keep_transform)
            self._view.centerOn(keep_center)

        
    def update_image(self, image_or_path):
        pm = _to_pixmap(image_or_path)
        if not pm.isNull():
            self.set_pixmap(pm)
            
    def update_overlay(self, overlay):
        pm = _to_pixmap(overlay)
        if pm.isNull():
            self.clear_overlay(); return
        try:
            pm.setDevicePixelRatio(1.0)
        except Exception:
            pass

        base_pm = self._pix.pixmap()
        self._overlay_pix.setPixmap(pm)
        self._overlay_pix.setOffset(0, 0)
        try:
            self._overlay_pix.setTransformationMode(QC.Qt_TransformationMode_SmoothTransformation)
        except Exception:
            pass
        try:
            if (not base_pm.isNull()) and pm.width() > 0 and pm.height() > 0:
                sx = float(base_pm.width()) / float(pm.width())
                sy = float(base_pm.height()) / float(pm.height())
                tr = QTransform()
                tr.scale(sx, sy)
                self._overlay_pix.setTransform(tr)
            else:
                self._overlay_pix.setTransform(QTransform())
        except Exception:
            pass
        self._overlay_pix.setVisible(self._chk_overlay.isChecked())
        self.update_info()

    def clear_overlay(self):
        self._overlay_pix.setPixmap(QPixmap())
        self._overlay_pix.setVisible(False)
            
    def wheelEvent(self, ev):
        
        factor = 1.25 if ev.angleDelta().y() > 0 else 0.8
        self._zoom(factor)
    
    def eventFilter(self, obj, ev):
        
        if obj is self._view.viewport() and ev.type() == QC.QEvent_Type_Wheel:
            self.wheelEvent(ev)
            return True  
        if obj is self._view.viewport() and ev.type() == QC.QEvent_Type_MouseButtonPress:
            try:
                if ev.button() == QC.Qt_MouseButton_LeftButton:
                    sp = self._view.mapToScene(ev.pos())
                    br = self._pix.boundingRect()
                    if br.contains(sp):
                        self.imageClicked.emit(float(sp.x()), float(sp.y()))
            except Exception:
                pass
        return super().eventFilter(obj, ev)
    

    def _zoom(self, factor):
        self._scale *= factor
        self._view.scale(factor, factor)

    def _reset(self):
        
        self._view.resetTransform()
        self._scale = 1.0

    def _fit(self):
        
        br = self._pix.boundingRect()
        if br.isNull():
            return
        self._view.fitInView(br, QC.Qt_AspectRatioMode_KeepAspectRatio)
        self._scale = 1.0  


def open_viewer(self):
    src = getattr(self, "image", None) or getattr(self, "photo_path", None)
    
    
    if src is None:
        try:
            src = self._make_schematic_base(int(self.spin_w.value()), int(self.spin_h.value()), for_export=False)
        except Exception:
            src = None
    if src is None:
        return
    if not getattr(self, 'viewer', None):
        self.viewer = _ImageViewer(self.iface.mainWindow(), owner=self)
    if getattr(self, 'viewer', None) is not None and not getattr(self, '_viewer_click_connected', False):
        try:
            self.viewer.imageClicked.connect(self._on_viewer_image_clicked)
            self._viewer_click_connected = True
        except Exception:
            pass
    
    self.viewer.update_image(src)
    
    
    ov = getattr(self, "overlay_image", None) or getattr(self, "overlay_path", None)
    if ov is not None:
        self.viewer.update_overlay(ov)
    else:
        self.viewer.clear_overlay()    
    
    
    try:
        self.viewer.update_info()
    except Exception:
        pass
    self.viewer.show()
    self.viewer.raise_()
    self.viewer.activateWindow()



def _normalize_qt_pen_style(val):
    try:
        if val in (QC.Qt_PenStyle_NoPen, QC.Qt_PenStyle_SolidLine, QC.Qt_PenStyle_DashLine, QC.Qt_PenStyle_DotLine, QC.Qt_PenStyle_DashDotLine, QC.Qt_PenStyle_DashDotDotLine):
            return val
    except Exception:
        pass
    try:
        ival = enum_int(val)
        mapping = {
            enum_int(QC.Qt_PenStyle_NoPen): QC.Qt_PenStyle_NoPen,
            enum_int(QC.Qt_PenStyle_SolidLine): QC.Qt_PenStyle_SolidLine,
            enum_int(QC.Qt_PenStyle_DashLine): QC.Qt_PenStyle_DashLine,
            enum_int(QC.Qt_PenStyle_DotLine): QC.Qt_PenStyle_DotLine,
            enum_int(QC.Qt_PenStyle_DashDotLine): QC.Qt_PenStyle_DashDotLine,
            enum_int(QC.Qt_PenStyle_DashDotDotLine): QC.Qt_PenStyle_DashDotDotLine,
        }
        return mapping.get(ival, QC.Qt_PenStyle_SolidLine)
    except Exception:
        return QC.Qt_PenStyle_SolidLine


def _parse_qcolor_value(val, default=None):
    if isinstance(val, QColor):
        return QColor(val)
    if val is None:
        return QColor(default) if default is not None else None
    try:
        txt = str(val).strip()
        if not txt:
            return QColor(default) if default is not None else None
        txt = txt.split(',rgb:')[0]
        parts = [p.strip() for p in txt.split(',') if p.strip() != '']
        if len(parts) >= 4:
            return QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])), int(float(parts[3])))
        if len(parts) >= 3:
            return QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])), 255)
        c = QColor(txt)
        if c.isValid():
            return c
    except Exception:
        pass
    return QColor(default) if default is not None else None


def _float_prop(props, keys, default=None):
    if isinstance(keys, str):
        keys = [keys]
    for k in keys:
        try:
            if k in props and props[k] not in (None, ''):
                return float(str(props[k]).replace(',', '.'))
        except Exception:
            pass
    return default


def _str_prop(props, keys, default=''):
    if isinstance(keys, str):
        keys = [keys]
    for k in keys:
        try:
            if k in props and props[k] not in (None, ''):
                return str(props[k])
        except Exception:
            pass
    return default


def _pen_style_from_text(txt, default=QC.Qt_PenStyle_SolidLine):
    t = (txt or '').strip().lower()
    mapping = {
        'no': QC.Qt_PenStyle_NoPen, 'none': QC.Qt_PenStyle_NoPen,
        'solid': QC.Qt_PenStyle_SolidLine,
        'dash': QC.Qt_PenStyle_DashLine, 'dashed': QC.Qt_PenStyle_DashLine,
        'dot': QC.Qt_PenStyle_DotLine, 'dotted': QC.Qt_PenStyle_DotLine,
        'dash dot': QC.Qt_PenStyle_DashDotLine, 'dashdot': QC.Qt_PenStyle_DashDotLine,
        'dash dot dot': QC.Qt_PenStyle_DashDotDotLine, 'dashdotdot': QC.Qt_PenStyle_DashDotDotLine,
    }
    return mapping.get(t, default)


def _mm_to_px_estimate(mm):
    try:
        return max(1.0, float(mm) * 3.78)
    except Exception:
        return 4.0



def _crop_nontransparent_qimage(img, alpha_threshold=6, pad=0):
    if img is None or img.isNull():
        return None
    try:
        src = img.convertToFormat(QC.QImage_Format_Format_RGBA8888)
        nbytes = int(src.width()) * int(src.height()) * 4
        ptr = src.constBits() if hasattr(src, 'constBits') else src.bits()
        if hasattr(ptr, 'asstring'):
            raw = ptr.asstring(nbytes)
        else:
            if hasattr(ptr, 'setsize'):
                ptr.setsize(nbytes)
            raw = bytes(ptr)
        if len(raw) < nbytes:
            return src
        arr = np.frombuffer(raw[:nbytes], np.uint8).copy().reshape((src.height(), src.width(), 4))
        ys, xs = np.where(arr[:, :, 3] > int(alpha_threshold))
        if xs.size == 0 or ys.size == 0:
            return src
        x0 = max(0, int(xs.min()) - int(pad))
        y0 = max(0, int(ys.min()) - int(pad))
        x1 = min(src.width() - 1, int(xs.max()) + int(pad))
        y1 = min(src.height() - 1, int(ys.max()) + int(pad))
        return src.copy(x0, y0, max(1, x1 - x0 + 1), max(1, y1 - y0 + 1))
    except Exception:
        return img


def _render_symbol_preview_tile(sym, size=96):
    if sym is None:
        return None
    side = max(48, int(size or 96))
    img = None
    try:
        if hasattr(sym, 'bigSymbolPreviewImage'):
            img = sym.bigSymbolPreviewImage()
    except Exception:
        img = None
    if img is None or img.isNull():
        try:
            img = QImage(side, side, QC.QImage_Format_Format_ARGB32_Premultiplied)
            img.fill(QColor(0, 0, 0, 0))
            p = QPainter(img)
            try:
                if hasattr(sym, 'drawPreviewIcon'):
                    sym.drawPreviewIcon(p, QSize(side, side))
            finally:
                p.end()
        except Exception:
            img = None
    if img is None or img.isNull():
        return None
    try:
        img = img.convertToFormat(QC.QImage_Format_Format_RGBA8888)
    except Exception:
        pass
    img = _crop_nontransparent_qimage(img, alpha_threshold=6, pad=1) or img
    try:
        if img.width() > 12 and img.height() > 12:
            img = img.copy(1, 1, max(1, img.width() - 2), max(1, img.height() - 2))
    except Exception:
        pass
    return img


def _texture_key_from_image(img):
    if img is None or img.isNull():
        return None
    try:
        ck = int(img.cacheKey())
    except Exception:
        ck = id(img)
    return (ck, int(img.width()), int(img.height()))



def _symbol_layer_props_safe(sl):
    try:
        return sl.properties() or {}
    except Exception:
        return {}


def _extract_first_line_symbol_props(sym):
    out = {}
    if sym is None:
        return out
    try:
        n = int(sym.symbolLayerCount())
    except Exception:
        n = 0
    for i in range(n):
        try:
            sl = sym.symbolLayer(i)
        except Exception:
            sl = None
        if sl is None:
            continue
        props = _symbol_layer_props_safe(sl)
        lname = ((getattr(sl, 'layerType', lambda: '')() or '') + ' ' + sl.__class__.__name__).lower()
        if 'simpleline' in lname or 'markerline' in lname:
            out.update(props)
            return out
    return out


def _extract_first_marker_symbol_props(sym):
    out = {}
    if sym is None:
        return out
    try:
        n = int(sym.symbolLayerCount())
    except Exception:
        n = 0
    for i in range(n):
        try:
            sl = sym.symbolLayer(i)
        except Exception:
            sl = None
        if sl is None:
            continue
        props = _symbol_layer_props_safe(sl)
        lname = ((getattr(sl, 'layerType', lambda: '')() or '') + ' ' + sl.__class__.__name__).lower()
        if 'simplemarker' in lname or 'svgmarker' in lname:
            out.update(props)
            return out
    return out

def _extract_qgis_fill_style_from_symbol(sym, default_fill, default_line, default_width):
    fill_style = {
        'kind': 'simple',
        'color': QColor(default_fill),
        'outline_color': QColor(default_line),
        'outline_width': float(default_width),
        'pen_style': QC.Qt_PenStyle_SolidLine,
    }
    if sym is None:
        return fill_style

    try:
        nsl = int(sym.symbolLayerCount())
    except Exception:
        nsl = 0

    for i in range(nsl):
        try:
            sl = sym.symbolLayer(i)
        except Exception:
            sl = None
        if sl is None:
            continue
        try:
            props = sl.properties() or {}
        except Exception:
            props = {}
        lname = ((getattr(sl, 'layerType', lambda: '')() or '') + ' ' + sl.__class__.__name__).lower()

        if 'simplefill' in lname:
            fc = _parse_qcolor_value(props.get('color'), fill_style.get('color'))
            oc = _parse_qcolor_value(props.get('outline_color'), fill_style.get('outline_color'))
            ow = _float_prop(props, ['outline_width', 'border_width', 'stroke_width'], fill_style.get('outline_width', default_width))
            ps = _pen_style_from_text(_str_prop(props, ['outline_style', 'line_style'], 'solid'), QC.Qt_PenStyle_SolidLine)
            fill_style = {
                'kind': 'simple',
                'color': QColor(fc or default_fill),
                'outline_color': QColor(oc or default_line),
                'outline_width': float(ow or default_width),
                'pen_style': ps,
                'style_name': _str_prop(props, 'style', 'solid'),
            }
            continue

        if 'gradientfill' in lname:
            c1 = _parse_qcolor_value(props.get('color1'), fill_style.get('color')) or QColor(default_fill)
            c2 = _parse_qcolor_value(props.get('color2'), QColor(max(0, c1.red()-40), max(0, c1.green()-40), max(0, c1.blue()-40), c1.alpha()))
            angle = _float_prop(props, ['angle', 'gradient_angle'], 0.0) or 0.0
            gtype = _str_prop(props, ['gradient_type', 'type'], 'linear').lower()
            fill_style = {
                'kind': 'gradient',
                'color1': QColor(c1),
                'color2': QColor(c2),
                'angle_deg': float(angle),
                'gradient_type': gtype,
                'outline_color': QColor(fill_style.get('outline_color', default_line)),
                'outline_width': float(fill_style.get('outline_width', default_width)),
                'pen_style': fill_style.get('pen_style', QC.Qt_PenStyle_SolidLine),
            }
            continue

        if 'linepatternfill' in lname:
            bg = QColor(fill_style.get('color', default_fill))
            try:
                subsym = sl.subSymbol() if hasattr(sl, 'subSymbol') else None
            except Exception:
                subsym = None
            sub_props = _extract_first_line_symbol_props(subsym)
            line_color = (_parse_qcolor_value(sub_props.get('line_color'), None) or
                          _parse_qcolor_value(sub_props.get('color'), None) or
                          _parse_qcolor_value(props.get('line_color'), None) or
                          _parse_qcolor_value(props.get('color'), fill_style.get('outline_color')) or QColor(default_line))
            angle = _float_prop(props, ['lineAngle', 'angle'], 45.0) or 45.0
            spacing = _float_prop(props, ['distance', 'lineDistance', 'spacing'], 2.0) or 2.0
            lw = (_float_prop(sub_props, ['line_width', 'width'], None) or
                  _float_prop(props, ['line_width', 'width', 'outline_width'], 0.26) or 0.26)
            pen_style = _pen_style_from_text(_str_prop(sub_props, ['line_style'], _str_prop(props, ['line_style'], 'solid')), QC.Qt_PenStyle_SolidLine)
            fill_style = {
                'kind': 'line_pattern',
                'bg_color': bg,
                'line_color': QColor(line_color),
                'spacing_px': _mm_to_px_estimate(spacing),
                'line_width_px': _mm_to_px_estimate(lw),
                'angle_deg': float(angle),
                'outline_color': QColor(fill_style.get('outline_color', default_line)),
                'outline_width': float(fill_style.get('outline_width', default_width)),
                'pen_style': pen_style,
            }
            continue

        if 'pointpatternfill' in lname:
            bg = QColor(fill_style.get('color', default_fill))
            try:
                subsym = sl.subSymbol() if hasattr(sl, 'subSymbol') else None
            except Exception:
                subsym = None
            sub_props = _extract_first_marker_symbol_props(subsym)
            pt_color = (_parse_qcolor_value(sub_props.get('color'), None) or
                        _parse_qcolor_value(props.get('color'), fill_style.get('outline_color')) or QColor(default_line))
            dx = _float_prop(props, ['distance_x', 'distanceX', 'horizontal_distance'], 3.0) or 3.0
            dy = _float_prop(props, ['distance_y', 'distanceY', 'vertical_distance'], dx) or dx
            size = (_float_prop(sub_props, ['size'], None) or _float_prop(props, ['point_size', 'size'], 1.2) or 1.2)
            shape = _str_prop(sub_props, ['name', 'shape'], _str_prop(props, ['name', 'shape', 'point_shape'], 'circle')).lower()
            fill_style = {
                'kind': 'point_pattern',
                'bg_color': bg,
                'point_color': QColor(pt_color),
                'spacing_x_px': _mm_to_px_estimate(dx),
                'spacing_y_px': _mm_to_px_estimate(dy),
                'point_size_px': _mm_to_px_estimate(size),
                'shape': shape,
                'outline_color': QColor(fill_style.get('outline_color', default_line)),
                'outline_width': float(fill_style.get('outline_width', default_width)),
                'pen_style': fill_style.get('pen_style', QC.Qt_PenStyle_SolidLine),
            }
            continue

    try:
        kind = str(fill_style.get('kind', 'simple') or 'simple').lower()
        style_name = str(fill_style.get('style_name', 'solid') or 'solid').lower()
        use_preview_texture = (kind in ('gradient', 'line_pattern', 'point_pattern')) or (kind == 'simple' and style_name not in ('solid', 'nobrush', 'no_brush', 'none'))
        if use_preview_texture:
            tex = _render_symbol_preview_tile(sym, size=112)
            if tex is not None and (not tex.isNull()):
                fill_style['texture_img'] = tex
                fill_style['texture_key'] = _texture_key_from_image(tex)
                fill_style['texture_mode'] = 'stretch' if kind == 'gradient' else 'tile'
    except Exception:
        pass

    return fill_style



def _renderer_name(renderer):
    try:
        return str(renderer.type() or '').lower()
    except Exception:
        return str(renderer.__class__.__name__).lower() if renderer is not None else ''


def _safe_symbol_from_renderer(renderer, feat=None, layer=None):
    
    if renderer is None:
        return None
    try:
        name = _renderer_name(renderer)

        if feat is not None and ('categorized' in name or isinstance(renderer, QgsCategorizedSymbolRenderer)):
            attr = None
            try:
                attr = renderer.classAttribute()
            except Exception:
                attr = None
            val = None
            if attr:
                try:
                    val = feat[attr]
                except Exception:
                    val = None
            try:
                for cat in renderer.categories():
                    cval = cat.value()
                    if val == cval or str(val) == str(cval):
                        sym = cat.symbol()
                        return sym.clone() if sym is not None and hasattr(sym, 'clone') else sym
            except Exception:
                pass

        if feat is not None and ('graduated' in name or isinstance(renderer, QgsGraduatedSymbolRenderer)):
            attr = None
            try:
                attr = renderer.classAttribute()
            except Exception:
                attr = None
            val = None
            if attr:
                try:
                    val = feat[attr]
                except Exception:
                    val = None
            if val is not None:
                try:
                    fval = float(val)
                    for rng in renderer.ranges():
                        try:
                            if rng.lowerValue() <= fval <= rng.upperValue():
                                sym = rng.symbol()
                                return sym.clone() if sym is not None and hasattr(sym, 'clone') else sym
                        except Exception:
                            continue
                except Exception:
                    pass

        if hasattr(renderer, 'symbol'):
            try:
                sym = renderer.symbol()
                return sym.clone() if sym is not None and hasattr(sym, 'clone') else sym
            except Exception:
                pass
    except Exception:
        pass
    return None

def _extract_qgis_layer_style(self, layer, feat=None, fallback=None):
    
    fallback = fallback or LayerStyle(layer=layer)
    color = QColor(getattr(fallback, 'color', QColor(0, 255, 0, 255)))
    fill_color = QColor(getattr(fallback, 'fill_color', color))
    width = float(getattr(fallback, 'width', 2.0) or 2.0)
    pen_style = _normalize_qt_pen_style(getattr(fallback, 'pen_style', QC.Qt_PenStyle_SolidLine))
    opacity = _opacity_factor(getattr(fallback, 'opacity', 1.0), 1.0)
    fill_polygons = bool(getattr(fallback, 'fill_polygons', True))
    fill_style = getattr(fallback, 'qgis_fill_style', None)

    if layer is None:
        return dict(color=color, fill_color=fill_color, width=width, pen_style=pen_style, opacity=opacity, fill_polygons=fill_polygons, fill_style=fill_style)

    try:
        renderer = layer.renderer()
    except Exception:
        renderer = None

    sym = None
    if renderer is not None:
        sym = _safe_symbol_from_renderer(renderer, feat=feat, layer=layer)

    if sym is None:
        return dict(color=color, fill_color=fill_color, width=width, pen_style=pen_style, opacity=opacity, fill_polygons=fill_polygons, fill_style=fill_style)

    opacity = _qgis_style_opacity(layer, renderer=renderer, symbol=sym, fallback=opacity)

    try:
        c0 = sym.color()
        if c0 is not None:
            color = QColor(c0)
    except Exception:
        pass

    try:
        if hasattr(sym, 'width') and callable(getattr(sym, 'width', None)):
            w = float(sym.width())
            if math.isfinite(w) and w > 0:
                width = w
    except Exception:
        pass

    try:
        if hasattr(sym, 'size') and callable(getattr(sym, 'size', None)):
            s = float(sym.size())
            if math.isfinite(s) and s > 0:
                width = max(width, s * 0.35)
    except Exception:
        pass

    try:
        nsl = int(sym.symbolLayerCount())
    except Exception:
        nsl = 0

    for i in range(nsl):
        try:
            sl = sym.symbolLayer(i)
        except Exception:
            sl = None
        if sl is None:
            continue
        try:
            if hasattr(sl, 'strokeColor') and callable(getattr(sl, 'strokeColor', None)):
                sc = sl.strokeColor()
                if sc is not None:
                    color = QColor(sc)
            elif hasattr(sl, 'color') and callable(getattr(sl, 'color', None)):
                sc = sl.color()
                if sc is not None:
                    color = QColor(sc)
        except Exception:
            pass
        try:
            if hasattr(sl, 'fillColor') and callable(getattr(sl, 'fillColor', None)):
                fc = sl.fillColor()
                if fc is not None:
                    fill_color = QColor(fc)
                    fill_polygons = True
        except Exception:
            pass
        try:
            if hasattr(sl, 'brushStyle') and callable(getattr(sl, 'brushStyle', None)):
                bsty = sl.brushStyle()
                if bsty == QC.Qt_BrushStyle_NoBrush:
                    fill_polygons = False
        except Exception:
            pass
        try:
            if hasattr(sl, 'strokeWidth') and callable(getattr(sl, 'strokeWidth', None)):
                w = float(sl.strokeWidth())
                if math.isfinite(w) and w >= 0:
                    width = max(0.0, w)
            elif hasattr(sl, 'width') and callable(getattr(sl, 'width', None)):
                w = float(sl.width())
                if math.isfinite(w) and w >= 0:
                    width = max(0.0, w)
        except Exception:
            pass
        try:
            if hasattr(sl, 'penStyle') and callable(getattr(sl, 'penStyle', None)):
                pen_style = _normalize_qt_pen_style(sl.penStyle())
        except Exception:
            pass

    try:
        fill_style = _extract_qgis_fill_style_from_symbol(sym, fill_color, color, width)
    except Exception:
        fill_style = fill_style

    try:
        fk = str((fill_style or {}).get('kind', 'simple')).lower()
        if fk in ('simple', 'gradient', 'line_pattern', 'point_pattern'):
            fill_polygons = True
    except Exception:
        pass

    return dict(color=color, fill_color=fill_color, width=width, pen_style=pen_style, opacity=opacity, fill_polygons=fill_polygons, fill_style=fill_style)


def _extract_qgis_label_settings(layer):
    out = {
        'enabled': False,
        'is_expression': False,
        'expr': '',
        'field_name': None,
        'size': 12,
        'text_color': QColor(20, 20, 20, 255),
        'bg': False,
        'bg_color': QColor(255, 255, 255, 220),
    }
    if layer is None:
        return out
    try:
        if not bool(layer.labelsEnabled()):
            return out
    except Exception:
        return out
    try:
        labeling = layer.labeling()
        settings = labeling.settings() if labeling is not None and hasattr(labeling, 'settings') else None
    except Exception:
        settings = None
    if settings is None:
        return out

    out['enabled'] = True
    try:
        expr = str(getattr(settings, 'fieldName', '') or '')
        out['expr'] = expr
    except Exception:
        expr = ''
    try:
        is_expr = bool(getattr(settings, 'isExpression', False))
    except Exception:
        is_expr = False
    out['is_expression'] = is_expr
    out['field_name'] = None if is_expr else (expr or None)

    try:
        fmt = settings.format() if hasattr(settings, 'format') else None
    except Exception:
        fmt = None
    if fmt is not None:
        try:
            c = fmt.color()
            if c is not None:
                out['text_color'] = QColor(c)
        except Exception:
            pass
        try:
            sz = float(fmt.size())
            if math.isfinite(sz) and sz > 0:
                out['size'] = max(6, int(round(sz)))
        except Exception:
            pass
        try:
            bg = fmt.background()
            if bg is not None and hasattr(bg, 'enabled') and bg.enabled():
                out['bg'] = True
                try:
                    fc = bg.fillColor()
                    if fc is not None:
                        out['bg_color'] = QColor(fc)
                except Exception:
                    pass
        except Exception:
            pass
    return out


def _sync_layer_labels_from_qgis(self, sty):
    if not bool(getattr(sty, 'use_qgis_labels', True)):
        return sty
    try:
        qlbl = _extract_qgis_label_settings(getattr(sty, 'layer', None))
        sty.show_labels = bool(qlbl.get('enabled', False))
        sty.qgis_label_is_expression = bool(qlbl.get('is_expression', False))
        sty.qgis_label_expr = str(qlbl.get('expr', '') or '')
        if sty.qgis_label_is_expression:
            sty.label_field = None
            sty.label_text = sty.qgis_label_expr
        else:
            sty.label_field = qlbl.get('field_name')
            sty.label_text = ''
        sty.label_size = int(qlbl.get('size', getattr(sty, 'label_size', 12)))
        sty.label_text_color = QColor(qlbl.get('text_color', getattr(sty, 'label_text_color', QColor(20,20,20,255))))
        sty.label_bg = bool(qlbl.get('bg', False))
        sty.label_bg_color = QColor(qlbl.get('bg_color', getattr(sty, 'label_bg_color', QColor(255,255,255,220))))
        sty.label_halo = False
    except Exception:
        pass
    return sty


def _sync_layer_style_from_qgis(self, sty, feat=None):
    if not bool(getattr(sty, 'use_qgis_style', True)):
        return sty
    try:
        lyr = getattr(sty, 'layer', None)
        theme_style_name = str(getattr(sty, 'qgis_theme_style_name', '') or '')
        if theme_style_name and '_temporarily_extract_style_from_named_qgis_style' in globals():
            qsty = _temporarily_extract_style_from_named_qgis_style(self, lyr, theme_style_name, sty, feat=feat)
        else:
            qsty = _extract_qgis_layer_style(self, lyr, feat=feat, fallback=sty)
        sty.color = qsty['color']
        sty.fill_color = qsty['fill_color']
        sty.width = qsty['width']
        sty.pen_style = qsty['pen_style']
        sty.opacity = _opacity_factor(qsty.get('opacity', getattr(sty, 'opacity', 1.0)), 1.0)
        sty.fill_polygons = qsty['fill_polygons']
        sty.qgis_fill_style = qsty.get('fill_style')
        try:
            if str((sty.qgis_fill_style or {}).get('kind', 'simple')).lower() in ('gradient', 'line_pattern', 'point_pattern', 'simple'):
                sty.fill_polygons = True
        except Exception:
            pass
    except Exception:
        pass
    return sty


def sync_all_layer_styles_from_qgis(self):
    changed = False
    for sty in getattr(self, 'layer_styles', []):
        before = (
            sty.color.rgba() if getattr(sty, 'color', None) else None,
            getattr(getattr(sty, 'fill_color', None), 'rgba', lambda: None)(),
            float(getattr(sty, 'width', 0.0) or 0.0),
            float(getattr(sty, 'opacity', 1.0) or 1.0),
            enum_int(getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine)),
            bool(getattr(sty, 'fill_polygons', True)),
            repr(getattr(sty, 'qgis_fill_style', None)),
            bool(getattr(sty, 'show_labels', False)),
            getattr(sty, 'label_field', None),
            getattr(sty, 'label_text', ''),
            int(getattr(sty, 'label_size', 0) or 0),
            getattr(getattr(sty, 'label_text_color', None), 'rgba', lambda: None)(),
            bool(getattr(sty, 'label_bg', False)),
            getattr(getattr(sty, 'label_bg_color', None), 'rgba', lambda: None)(),
        )
        if bool(getattr(sty, 'use_qgis_style', True)):
            _sync_layer_style_from_qgis(self, sty, feat=None)
        if bool(getattr(sty, 'use_qgis_labels', True)):
            _sync_layer_labels_from_qgis(self, sty)
        after = (
            sty.color.rgba() if getattr(sty, 'color', None) else None,
            getattr(getattr(sty, 'fill_color', None), 'rgba', lambda: None)(),
            float(getattr(sty, 'width', 0.0) or 0.0),
            float(getattr(sty, 'opacity', 1.0) or 1.0),
            enum_int(getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine)),
            bool(getattr(sty, 'fill_polygons', True)),
            repr(getattr(sty, 'qgis_fill_style', None)),
            bool(getattr(sty, 'show_labels', False)),
            getattr(sty, 'label_field', None),
            getattr(sty, 'label_text', ''),
            int(getattr(sty, 'label_size', 0) or 0),
            getattr(getattr(sty, 'label_text_color', None), 'rgba', lambda: None)(),
            bool(getattr(sty, 'label_bg', False)),
            getattr(getattr(sty, 'label_bg_color', None), 'rgba', lambda: None)(),
        )
        changed = changed or (before != after)
    return changed



def _qgis_theme_collection():
    try:
        return QgsProject.instance().mapThemeCollection()
    except Exception:
        return None


def refresh_qgis_themes(self):
    
    combo = getattr(self, 'cmb_qgis_theme', None)
    if combo is None:
        return
    current = combo.currentData()
    if current is None:
        try:
            current = combo.currentText()
        except Exception:
            current = ''
    combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(tr('— aucun thème —'), '')
        coll = _qgis_theme_collection()
        names = []
        if coll is not None:
            try:
                names = list(coll.mapThemes())
            except Exception:
                names = []
        for name in names:
            
            
            if str(name).startswith('__QCALVIEW_PDV__'):
                continue
            combo.addItem(tr(str(name)), str(name))
        idx = combo.findData(current)
        if idx < 0 and current:
            idx = combo.findText(str(current))
        combo.setCurrentIndex(idx if idx >= 0 else 0)
    finally:
        combo.blockSignals(False)


def _ordered_theme_vector_layers(theme_name, camera_layer=None):
    
    coll = _qgis_theme_collection()
    if coll is None or not theme_name:
        return [], {}
    try:
        visible_ids = set(str(x) for x in coll.mapThemeVisibleLayerIds(theme_name))
    except Exception:
        visible_ids = set()
        try:
            visible_layers = coll.mapThemeVisibleLayers(theme_name)
            visible_ids = set(str(lyr.id()) for lyr in visible_layers if lyr is not None)
        except Exception:
            pass
    try:
        style_overrides = dict(coll.mapThemeStyleOverrides(theme_name) or {})
    except Exception:
        style_overrides = {}
    try:
        ordered = list(coll.masterLayerOrder())
    except Exception:
        try:
            ordered = list(QgsProject.instance().layerTreeRoot().layerOrder())
        except Exception:
            try:
                ordered = [lyr for lyr in QgsProject.instance().mapLayers().values()]
            except Exception:
                ordered = []
    cam_id = None
    try:
        cam_id = camera_layer.id() if camera_layer is not None else None
    except Exception:
        cam_id = None
    out = []
    for lyr in ordered:
        if not isinstance(lyr, QgsVectorLayer):
            continue
        try:
            lid = lyr.id()
        except Exception:
            continue
        if lid not in visible_ids:
            continue
        if cam_id and lid == cam_id:
            continue
        try:
            if QgsWkbTypes.geometryType(lyr.wkbType()) == QC.QgsWkbTypes_GeometryType_NullGeometry:
                continue
        except Exception:
            pass
        out.append(lyr)
    return out, style_overrides


def _temporarily_extract_style_from_named_qgis_style(self, layer, style_name, fallback, feat=None):
    
    if not style_name:
        return _extract_qgis_layer_style(self, layer, feat=feat, fallback=fallback)
    sm = None
    prev = None
    try:
        sm = layer.styleManager()
        prev = sm.currentStyle()
        try:
            sm.setCurrentStyle(str(style_name))
        except Exception:
            pass
        return _extract_qgis_layer_style(self, layer, feat=feat, fallback=fallback)
    finally:
        try:
            if sm is not None and prev:
                sm.setCurrentStyle(prev)
        except Exception:
            pass


def apply_qgis_theme_to_overlays(self):
    
    combo = getattr(self, 'cmb_qgis_theme', None)
    theme_name = ''
    if combo is not None:
        try:
            theme_name = str(combo.currentData() or combo.currentText() or '').strip()
        except Exception:
            theme_name = ''
    if not theme_name or theme_name.startswith('—'):
        return

    try:
        self._settings.setValue('QCALVIEW/theme_name', theme_name)
    except Exception:
        pass

    camera_layer = None
    try:
        camera_layer = self.cmb_camera.currentLayer()
    except Exception:
        camera_layer = None

    layers, style_overrides = _ordered_theme_vector_layers(theme_name, camera_layer=camera_layer)
    try:
        max_layers = int(getattr(self, '_settings', None).value("QCALVIEW/limits/max_overlay_layers", 20))
        if max_layers > 0:
            layers = list(layers)[:max_layers]
    except Exception:
        pass
    new_styles = []
    for lyr in layers:
        try:
            sty = LayerStyle(layer=lyr)
            sty.qgis_theme_name = theme_name
            sty.qgis_theme_style_name = str(style_overrides.get(lyr.id(), '') or '')
            try:
                if sty.qgis_theme_style_name:
                    qsty = _temporarily_extract_style_from_named_qgis_style(self, lyr, sty.qgis_theme_style_name, sty)
                    sty.color = qsty['color']
                    sty.fill_color = qsty['fill_color']
                    sty.width = qsty['width']
                    sty.pen_style = qsty['pen_style']
                    sty.opacity = _opacity_factor(qsty.get('opacity', getattr(sty, 'opacity', 1.0)), 1.0)
                    sty.fill_polygons = qsty['fill_polygons']
                    sty.qgis_fill_style = qsty.get('fill_style')
                else:
                    _sync_layer_style_from_qgis(self, sty, feat=None)
            except Exception:
                _sync_layer_style_from_qgis(self, sty, feat=None)
            _sync_layer_labels_from_qgis(self, sty)
            new_styles.append(sty)
            _watch_overlay_layer(self, lyr)
        except Exception:
            continue

    self.layer_styles = new_styles
    try:
        self._refresh_layer_list_labels(0 if self.layer_styles else -1)
    except Exception:
        pass
    try:
        getattr(self, '_overlay_cache', {}).clear()
        getattr(self, '_geom_cache', {}).clear()
    except Exception:
        pass
    _queue_overlay_style_refresh(self)


def _queue_overlay_style_refresh(self, delay_ms=15):
    
    try:
        if hasattr(self, '_overlay_cache') and isinstance(self._overlay_cache, dict):
            self._overlay_cache.clear()
    except Exception:
        pass
    try:
        if hasattr(self, '_geom_cache') and isinstance(self._geom_cache, dict):
            self._geom_cache.clear()
    except Exception:
        pass

    try:
        timer = getattr(self, '_style_refresh_timer', None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.render_preview)
            self._style_refresh_timer = timer
        else:
            timer.stop()
        timer.start(max(0, int(delay_ms)))
    except Exception:
        
        
        try:
            QTimer.singleShot(max(0, int(delay_ms)), self.render_preview)
        except Exception:
            pass


def _on_qgis_theme_auto_sync_toggled(self, checked: bool):
    try:
        self._settings.setValue('QCALVIEW/theme_auto_sync', bool(checked))
    except Exception:
        pass
    coll = _qgis_theme_collection()
    if coll is None:
        return
    if not hasattr(self, '_qgis_theme_sync_connected'):
        self._qgis_theme_sync_connected = False

    def _sync_if_current(*_args):
        try:
            self.refresh_qgis_themes()
            if (not getattr(self, '_suspend_theme_auto_apply', False)
                    and bool(getattr(self, 'cb_theme_auto_sync', None) and self.cb_theme_auto_sync.isChecked())):
                self.apply_qgis_theme_to_overlays()
        except Exception:
            pass

    if checked and not getattr(self, '_ui_initializing', False) and not getattr(self, '_suspend_theme_auto_apply', False):
        try:
            QTimer.singleShot(0, self.apply_qgis_theme_to_overlays)
        except Exception:
            pass
    if checked and not self._qgis_theme_sync_connected:
        for sig_name in ('mapThemesChanged', 'mapThemeChanged'):
            try:
                getattr(coll, sig_name).connect(_sync_if_current)
            except Exception:
                pass
        self._qgis_theme_sync_connected = True


def _restore_qgis_theme_settings(self):
    try:
        auto = str(self._settings.value('QCALVIEW/theme_auto_sync', 'false')).lower() in ('1','true','yes','on')
    except Exception:
        auto = False
    try:
        theme_name = str(self._settings.value('QCALVIEW/theme_name', '') or '')
    except Exception:
        theme_name = ''
    try:
        self.cb_theme_auto_sync.setChecked(bool(auto))
    except Exception:
        pass
    try:
        if theme_name and getattr(self, 'cmb_qgis_theme', None) is not None:
            idx = self.cmb_qgis_theme.findData(theme_name)
            if idx >= 0:
                self.cmb_qgis_theme.setCurrentIndex(idx)
                if auto and not getattr(self, '_ui_initializing', False) and not getattr(self, '_suspend_theme_auto_apply', False):
                    QTimer.singleShot(0, self.apply_qgis_theme_to_overlays)
    except Exception:
        pass

def _prune_missing_overlay_layers(self):
    changed = False
    kept = []
    for sty in list(getattr(self, 'layer_styles', []) or []):
        lyr = getattr(sty, 'layer', None)
        try:
            lid = lyr.id() if lyr is not None else None
        except Exception:
            lid = None
        proj_layer = QgsProject.instance().mapLayer(lid) if lid else None
        if proj_layer is None:
            changed = True
            continue
        if proj_layer is not lyr:
            sty.layer = proj_layer
            changed = True
        kept.append(sty)
    if changed:
        self.layer_styles = kept
        _refresh_layer_list_labels(self)
    return changed


def _on_project_layers_removed(self, *args):
    changed = _prune_missing_overlay_layers(self)
    if changed:
        try:
            getattr(self, '_overlay_cache', {}).clear()
        except Exception:
            pass
        try:
            self.render_preview()
        except Exception:
            pass




def _overlay_layer_preflight(self, layer):
    try:
        count = int(layer.featureCount())
    except Exception:
        count = -1
    try:
        gtype = QgsWkbTypes.geometryType(layer.wkbType())
    except Exception:
        gtype = QC.QgsWkbTypes_GeometryType_UnknownGeometry

    warn_limit = 20000
    block_limit = 80000
    if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
        warn_limit = 15000
        block_limit = 60000
    elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
        warn_limit = 25000
        block_limit = 90000
    try:
        user_block = int(getattr(self, '_settings', None).value("QCALVIEW/limits/max_features_per_layer", block_limit))
        if user_block > 0:
            block_limit = user_block
            warn_limit = max(1, int(block_limit * 0.6))
    except Exception:
        pass

    level = 'ok'
    if count >= 0 and count >= warn_limit:
        level = 'warn'
    if count >= 0 and count >= block_limit:
        level = 'block'
    return {
        'count': count,
        'geom_type': gtype,
        'warn_limit': warn_limit,
        'block_limit': block_limit,
        'level': level,
    }


def _cache_keys_for_layer(layer_id):
    return [layer_id]


def _invalidate_overlay_layer_cache(self, layer_id):
    try:
        self._layer_cache_versions[layer_id] = int(self._layer_cache_versions.get(layer_id, 0)) + 1
    except Exception:
        pass
    try:
        to_drop = [k for k in getattr(self, '_geom_cache', {}).keys() if isinstance(k, tuple) and k and k[0] == layer_id]
        for k in to_drop:
            self._geom_cache.pop(k, None)
    except Exception:
        pass
    try:
        getattr(self, '_overlay_cache', {}).clear()
    except Exception:
        pass


def _watch_overlay_layer(self, layer):
    if not isinstance(layer, QgsVectorLayer):
        return
    hooks = getattr(self, '_overlay_layer_hooks', None)
    if hooks is None:
        self._overlay_layer_hooks = {}
        hooks = self._overlay_layer_hooks
    if layer.id() in hooks:
        return

    callbacks = []

    def _mk_cb():
        
        
        
        
        def _on_overlay_changed(*a, _lid=layer.id(), **k):
            _invalidate_overlay_layer_cache(self, _lid)
            if not bool(getattr(self, '_camera_live_enabled', False)):
                return
            try:
                timer = getattr(self, '_camera_live_refresh_timer', None)
                if timer is not None:
                    timer.start(250)
                else:
                    
                    QTimer.singleShot(250, lambda: getattr(self, '_camera_refresh_current_view', lambda **_: None)(force=False))
            except Exception:
                pass
        return _on_overlay_changed

    for sig_name in ('geometryChanged', 'featureAdded', 'featuresDeleted', 'attributeValueChanged', 'committedGeometriesChanges', 'committedFeaturesAdded', 'committedFeaturesRemoved', 'dataChanged', 'rendererChanged'):
        sig = getattr(layer, sig_name, None)
        if sig is None:
            continue
        cb = _mk_cb()
        try:
            sig.connect(cb)
            callbacks.append((sig, cb))
        except Exception:
            pass
    hooks[layer.id()] = callbacks


def _heavy_layer_message(name, count, level):
    if count < 0:
        return (
            f"La couche '{name}' peut être lourde pour un rendu interactif dans QCALVIEW.\n\n"
            "La profondeur auto limitera si possible la portée de rendu. "
            "En cas de ralentissement, filtrez la couche ou réduisez son emprise."
        )
    if level == 'block':
        return (
            f"La couche '{name}' contient environ {count:,} entités.\n\n"
            "QCALVIEW risque de devenir très lent ou de figer la machine en aperçu interactif.\n"
            "Réduisez l'emprise, appliquez un filtre ou simplifiez la couche avant de l'ajouter."
        ).replace(',', ' ')
    return (
        f"La couche '{name}' contient environ {count:,} entités.\n\n"
        "Elle sera ajoutée, mais l'aperçu pourra être limité automatiquement "
        "(profondeur auto, coupure des labels, simplification)."
    ).replace(',', ' ')


def _add_overlay_layer_object(self, lyr):
    
    if not lyr:
        return False
    
    try:
        for existing in getattr(self, 'layer_styles', []):
            if getattr(existing, 'layer', None) is lyr or (getattr(getattr(existing, 'layer', None), 'id', lambda: None)() == lyr.id()):
                self._refresh_layer_list_labels()
                return
    except Exception:
        pass

    try:
        max_layers = int(getattr(self, '_settings', None).value("QCALVIEW/limits/max_overlay_layers", 20))
        if max_layers > 0 and len(getattr(self, 'layer_styles', []) or []) >= max_layers:
            try:
                QMessageBox.warning(self, tr('QCALVIEW — limite atteinte'), tr(f"Nombre maximal de couches projetées atteint ({max_layers}). Réglez cette limite dans Paramètres > Limites de rendu."))
            except Exception:
                pass
            return
    except Exception:
        pass

    preflight = _overlay_layer_preflight(self, lyr)
    level = preflight.get('level', 'ok')
    if level == 'block' and bool(getattr(self, 'cb_block_heavy_layers', None) and self.cb_block_heavy_layers.isChecked()):
        try:
            QMessageBox.warning(self, tr('QCALVIEW — couche trop lourde'), tr(_heavy_layer_message(lyr.name(), preflight.get('count', -1), level)))
        except Exception:
            pass
        try:
            self.lbl_render_budget.setText(tr('Ajout bloqué : couche trop lourde pour l’aperçu interactif'))
        except Exception:
            pass
        return
    elif level == 'warn':
        try:
            QMessageBox.information(self, tr('QCALVIEW — couche volumineuse'), tr(_heavy_layer_message(lyr.name(), preflight.get('count', -1), level)))
        except Exception:
            pass

    sty = LayerStyle(layer=lyr)
    _sync_layer_style_from_qgis(self, sty, feat=None)
    _sync_layer_labels_from_qgis(self, sty)
    self.layer_styles.append(sty)
    _watch_overlay_layer(self, lyr)
    self._refresh_layer_list_labels(len(self.layer_styles)-1)
    try:
        _invalidate_overlay_layer_cache(self, lyr.id())
    except Exception:
        pass
    self.render_preview()
    return True

def add_layer(self):
    
    lyr = self.cmb_src.currentLayer()
    return _add_overlay_layer_object(self, lyr)

def add_layer_object(self, lyr):
    
    return _add_overlay_layer_object(self, lyr)

def _on_layer_table_double_clicked(self, row, column=0):
    
    try:
        row = int(row)
    except Exception:
        return
    if row < 0 or row >= len(getattr(self, 'layer_styles', []) or []):
        return
    try:
        self.list_layers.setCurrentCell(row, max(0, int(column)))
        self.list_layers.selectRow(row)
    except Exception:
        pass
    self.edit_style()

def remove_layer(self):
    row = self.list_layers.currentRow()
    if 0 <= row < len(self.layer_styles):
        self.layer_styles.pop(row)
        self._refresh_layer_list_labels(min(row, len(self.layer_styles)-1))
        self.render_preview()

def current_style(self):
    row = self.list_layers.currentRow()
    if row < 0 or row >= len(self.layer_styles): return None, -1
    return self.layer_styles[row], row

def move_up(self):
    sty, idx = self.current_style()
    if sty and idx > 0:
        self.layer_styles.insert(idx-1, self.layer_styles.pop(idx))
        self._refresh_layer_list_labels(idx-1); self.render_preview()

def move_down(self):
    sty, idx = self.current_style()
    if sty and idx < len(self.layer_styles)-1:
        self.layer_styles.insert(idx+1, self.layer_styles.pop(idx))
        self._refresh_layer_list_labels(idx+1); self.render_preview()











def _finite_uv(self, uv):
    if not uv:
        return None
    try:
        u, v = float(uv[0]), float(uv[1])
        if not (math.isfinite(u) and math.isfinite(v)):
            return None

        
        
        
        

        
        try:
            proj_txt = str(self.cmb_proj.currentText()).strip().upper()
            is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (bool(self.cb_360.isChecked()) and proj_txt in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL"))
        except Exception:
            is360 = False

        if is360:
            try:
                W = int(getattr(self, "_overlay_w", self.spin_w.value()))
                H = int(getattr(self, "_overlay_h", self.spin_h.value()))
                if W > 0:
                    u = u % W
                v = min(float(H), max(0.0, v))
            except Exception:
                pass

        return (u, v)
    except Exception:
        return None


def _safe_line(self, painter, uv1, uv2):
    if uv1 is None or uv2 is None:
        return False
    try:
        def _coords(uv):
            if hasattr(uv, "x") and callable(getattr(uv, "x", None)):
                return float(uv.x()), float(uv.y())
            return float(uv[0]), float(uv[1])

        dev = painter.device() if hasattr(painter, "device") else None
        W = int(dev.width())  if (dev is not None and hasattr(dev, "width"))  else int(getattr(self, "_last_overlay_w", 0) or 0)
        H = int(dev.height()) if (dev is not None and hasattr(dev, "height")) else int(getattr(self, "_last_overlay_h", 0) or 0)

        u1, v1 = _coords(uv1)
        u2, v2 = _coords(uv2)

        
        def _clip_y(u, v):
            if H > 0:
                if v < 0.0:   v = 0.0
                elif v > H:   v = float(H)
            return u, v

        u1, v1 = _clip_y(u1, v1)
        u2, v2 = _clip_y(u2, v2)

        if W <= 0:
            painter.drawLine(int(u1), int(v1), int(u2), int(v2))
            return True

        
        try:
            proj_txt = str(self.cmb_proj.currentText()).strip().upper() if hasattr(self, 'cmb_proj') else ''
            wrap360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (bool(getattr(self, 'cb_360', None) and self.cb_360.isChecked()) and proj_txt in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'))
        except Exception:
            wrap360 = False

        du = abs(u2 - u1)
        if wrap360 and du > (W * 0.5):
            if u2 > u1:
                segs = [(u1, v1, u2 - W, v2), (u1 + W, v1, u2, v2)]
            else:
                segs = [(u1, v1, u2 + W, v2), (u1 - W, v1, u2, v2)]
            for x1, y1, x2, y2 in segs:
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        else:
            painter.drawLine(int(u1), int(v1), int(u2), int(v2))
        return True
    except Exception:
        return False


def _azimuth_deg(self, dx, dy):
    ang = math.degrees(math.atan2(dx, dy))
    if ang < 0: ang += 360.0
    return ang

def _elev_deg(self, dz, horiz):
    return math.degrees(math.atan2(dz, max(horiz, 1e-9)))

def _make_z_sampler(self, dem_layer, cam_crs):
    
    if not isinstance(dem_layer, QgsRasterLayer):
        return None, None
    if not dem_layer.isValid():
        return None, None

    dem_crs = dem_layer.crs()
    tr = None if dem_crs == cam_crs else QgsCoordinateTransform(
        cam_crs, dem_crs, QgsProject.instance()
    )
    provider = dem_layer.dataProvider()

    
    
    
    
    
    
    
    
    
    cache = {}
    _CACHE_MAX = 65536
    _p_cam_reuse = QgsPointXY(0.0, 0.0)

    def _sample_xy_cam_uncached(x, y):
        x = float(x); y = float(y)
        try:
            _p_cam_reuse.setX(x)
            _p_cam_reuse.setY(y)
            p_dem = _p_cam_reuse if tr is None else tr.transform(_p_cam_reuse)
            val = provider.sample(p_dem, 1)
            z = val[0] if val else None
            z = float(z)
            if not math.isfinite(z):
                z = 0.0
        except Exception:
            z = 0.0
        return float(z)

    def _sample_xy_cam(x, y):
        x = float(x); y = float(y)
        key = (round(x, 1), round(y, 1))
        if key in cache:
            return float(cache[key])
        z = _sample_xy_cam_uncached(x, y)
        if len(cache) >= _CACHE_MAX:
            cache.clear()
        cache[key] = float(z)
        return float(z)

    def sample_z_single(pt_xy_cam):
        try:
            return _sample_xy_cam(pt_xy_cam.x(), pt_xy_cam.y())
        except Exception:
            try:
                return _sample_xy_cam(pt_xy_cam[0], pt_xy_cam[1])
            except Exception:
                return 0.0

    def _coerce_points_array(points_xy_cam):
        try:
            if isinstance(points_xy_cam, np.ndarray):
                pts = np.asarray(points_xy_cam, dtype=np.float64)
                if pts.ndim == 1:
                    pts = pts.reshape(1, -1)
            else:
                vals = []
                for pt in points_xy_cam:
                    try:
                        vals.append((float(pt.x()), float(pt.y())))
                    except Exception:
                        vals.append((float(pt[0]), float(pt[1])))
                pts = np.asarray(vals, dtype=np.float64)
        except Exception:
            return np.empty((0, 2), dtype=np.float64)
        if pts.size == 0:
            return np.empty((0, 2), dtype=np.float64)
        return pts

    def sample_z_batch(points_xy_cam):
        
        pts = _coerce_points_array(points_xy_cam)
        if pts.size == 0:
            return np.empty((0,), dtype=np.float64)
        out = np.empty(int(pts.shape[0]), dtype=np.float64)
        for i in range(int(pts.shape[0])):
            out[i] = _sample_xy_cam(pts[i, 0], pts[i, 1])
        return out

    def sample_z_batch_uncached(points_xy_cam):
        
        pts = _coerce_points_array(points_xy_cam)
        if pts.size == 0:
            return np.empty((0,), dtype=np.float64)
        out = np.empty(int(pts.shape[0]), dtype=np.float64)
        for i in range(int(pts.shape[0])):
            out[i] = _sample_xy_cam_uncached(pts[i, 0], pts[i, 1])
        return out

    sample_z_single.batch = sample_z_batch
    sample_z_single.batch_uncached = sample_z_batch_uncached
    sample_z_single.xy = _sample_xy_cam
    sample_z_single.xy_uncached = _sample_xy_cam_uncached
    sample_z_single.clear_cache = lambda: cache.clear()
    sample_z_single._qcv_unified_ground_sampler = True
    return sample_z_single, tr

def _style_item_text(sty):
    try:
        name = sty.layer.name() if getattr(sty, 'layer', None) else '(couche)'
    except Exception:
        name = '(couche)'
    tags = []
    if getattr(sty, 'enable_25d', True):
        tags.append('2,5D')
    if getattr(sty, 'show_labels', False):
        tags.append('labels QGIS' if getattr(sty, 'use_qgis_labels', True) else 'labels')
    if getattr(sty, 'use_qgis_style', True):
        tags.append('style QGIS')
    if bool(getattr(sty, 'schematic_enabled', False)) and str(getattr(sty, 'schematic_symbol_id', '') or ''):
        tags.append('AVR:' + str(getattr(sty, 'schematic_symbol_id', '')))
    if bool(getattr(sty, '_budget_warning', False)):
        tags.append('budget limité')
    return f"{name} — {', '.join(tags)}" if tags else name


def _refresh_layer_list_labels(self, current_row=None):
    try:
        if current_row is None:
            current_row = self.list_layers.currentRow()

        
        if hasattr(self.list_layers, 'setRowCount') and hasattr(self.list_layers, 'setItem'):
            rows = getattr(self, 'layer_styles', [])
            try:
                self.list_layers.blockSignals(True)
            except Exception:
                pass
            self.list_layers.setRowCount(0)
            for i, sty in enumerate(rows):
                try:
                    if not hasattr(sty, 'visible'):
                        sty.visible = True
                except Exception:
                    pass
                self.list_layers.insertRow(i)
                try:
                    lyr = sty.layer
                    name = lyr.name() if lyr else '(couche)'
                except Exception:
                    name = '(couche)'
                visible = QTableWidgetItem(tr(''))
                visible.setTextAlignment(QC.Qt_AlignmentFlag_AlignCenter)
                visible.setFlags((visible.flags() | QC.Qt_ItemFlag_ItemIsUserCheckable | QC.Qt_ItemFlag_ItemIsEnabled | QC.Qt_ItemFlag_ItemIsSelectable) & ~QC.Qt_ItemFlag_ItemIsEditable)
                visible.setCheckState(QC.Qt_CheckState_Checked if bool(getattr(sty, 'visible', True)) else QC.Qt_CheckState_Unchecked)
                visible.setToolTip(tr('Afficher / masquer temporairement cette couche dans QCALVIEW, sans modifier le thème QGIS'))
                self.list_layers.setItem(i, 0, visible)
                self.list_layers.setItem(i, 1, QTableWidgetItem(tr(str(name))))

                h = getattr(sty, 'default_height_override', None)
                if h is None:
                    try:
                        h = float(getattr(self, 'd_hdefault', None).value())
                    except Exception:
                        h = 0.0
                self.list_layers.setItem(i, 2, QTableWidgetItem(tr(f"{float(h):.2f} m")))

                mode = []
                if getattr(sty, 'fill_polygons', True):
                    mode.append('Remplissage')
                if getattr(sty, 'enable_25d', True):
                    mode.append('2,5D')
                if getattr(sty, 'show_labels', False):
                    mode.append('Labels')
                if bool(getattr(sty, 'schematic_enabled', False)) and str(getattr(sty, 'schematic_symbol_id', '') or ''):
                    mode.append('AVR:' + str(getattr(sty, 'schematic_symbol_id', '')))
                self.list_layers.setItem(i, 3, QTableWidgetItem(tr(' + '.join(mode) if mode else 'Arêtes')))

                op = getattr(sty, 'opacity', 1.0)
                try:
                    op_pct = int(round(float(op) * 100.0))
                except Exception:
                    op_pct = 100
                self.list_layers.setItem(i, 4, QTableWidgetItem(tr(f"{op_pct:d} %")))

                qstyle = 'QGIS' if getattr(sty, 'use_qgis_style', True) else 'Manuel'
                try:
                    sw = QWidget()
                    hb = QHBoxLayout(sw)
                    hb.setContentsMargins(2, 1, 2, 1)
                    hb.setSpacing(4)
                    def _rgba_css(col, fallback):
                        try:
                            c = col if isinstance(col, QColor) else QColor(col)
                            if not c.isValid():
                                c = QColor(fallback)
                            return f"rgba({c.red()},{c.green()},{c.blue()},{max(0.0, min(1.0, c.alphaF())):.3f})"
                        except Exception:
                            return fallback
                    for _label, _col in (("Rempl.", getattr(sty, 'fill_color', getattr(sty, 'color', QColor(0, 255, 0, 255)))), ("Trait", getattr(sty, 'color', QColor(0, 255, 0, 255)))):
                        box = QLabel()
                        box.setFixedSize(26, 12)
                        box.setToolTip(tr(_label))
                        box.setStyleSheet(f"background:{_rgba_css(_col, '#00ff00')}; border:1px solid rgba(120,120,120,160);")
                        hb.addWidget(box)
                    txt = QLabel(tr(qstyle))
                    txt.setMinimumWidth(0)
                    hb.addWidget(txt)
                    hb.addStretch(1)
                    self.list_layers.setCellWidget(i, 5, sw)
                except Exception:
                    self.list_layers.setItem(i, 5, QTableWidgetItem(tr(qstyle)))
            if current_row is not None and current_row >= 0 and current_row < len(rows):
                self.list_layers.selectRow(current_row)
            try:
                self.list_layers.blockSignals(False)
            except Exception:
                pass
            return

        
        self.list_layers.clear()
        for sty in getattr(self, 'layer_styles', []):
            self.list_layers.addItem(tr(_style_item_text(sty)))
        if current_row is not None and current_row >= 0 and current_row < self.list_layers.count():
            self.list_layers.setCurrentRow(current_row)
    except Exception:
        pass



def _on_layer_table_item_changed(self, item):
    
    try:
        if item is None or int(item.column()) != 0:
            return
        row = int(item.row())
        styles = getattr(self, 'layer_styles', []) or []
        if row < 0 or row >= len(styles):
            return
        styles[row].visible = (item.checkState() == QC.Qt_CheckState_Checked)
        try:
            getattr(self, '_overlay_cache', {}).clear()
            getattr(self, '_geom_cache', {}).clear()
        except Exception:
            pass
        try:
            self.render_preview()
        except Exception:
            pass
    except Exception:
        pass

def _pick_style_color(button, current, parent, title):
    col = QColorDialog.getColor(current, parent, title)
    if col.isValid():
        current = col
    button.setStyleSheet(f"background:{current.name(QC.QColor_NameFormat_HexArgb)};")
    return current


def edit_style(self):
    sty, idx = self.current_style()
    if not sty:
        return

    _plugin_dir = os.path.dirname(os.path.dirname(__file__))
    dlg = QDialog(self)
    ui_path = os.path.join(_plugin_dir, 'ui', 'layer_style_dialog.ui')
    uic.loadUi(ui_path, dlg)
    dlg.setWindowTitle(tr(f"Style — {sty.layer.name() if getattr(sty, 'layer', None) else 'couche'}"))

    
    
    cb_use_qgis_style=dlg.cbUseQgisStyle; btn_color=dlg.btnColor; sp_width=dlg.spWidth; sp_opacity=dlg.spOpacity
    cb_fill_poly=dlg.cbFillPoly; btn_fill_color=dlg.btnFillColor; cb_fill_walls=dlg.cbFillWalls
    cb_25d=dlg.cb25d; le_hfield=dlg.leHField; sp_hdef=dlg.spHDef
    cb_schematic=dlg.cbSchematic; cmb_schematic_type=dlg.cmbSchematicType; cmb_schematic_family=dlg.cmbSchematicFamily
    cmb_schematic=dlg.cmbSchematic; btn_library=dlg.btnLibrary; btn_params=dlg.btnParams; btn_models=dlg.btnModels; btn_occ_height=dlg.btnOccHeight

    btn_color.setStyleSheet(f"background:{sty.color.name(QC.QColor_NameFormat_HexArgb)};")
    sp_width.setValue(float(sty.width))
    sp_opacity.setValue(int(round(100.0 * _opacity_factor(getattr(sty, 'opacity', 1.0), 1.0))))
    cb_fill_poly.setChecked(bool(getattr(sty, 'fill_polygons', True)))
    _fill_col = getattr(sty, 'fill_color', sty.color)
    btn_fill_color.setStyleSheet(f"background:{_fill_col.name(QC.QColor_NameFormat_HexArgb)};")
    cb_fill_walls.setChecked(bool(getattr(sty, 'fill_walls', True)))
    cb_use_qgis_style.setChecked(bool(getattr(sty, 'use_qgis_style', True)))
    cb_25d.setChecked(bool(getattr(sty, 'enable_25d', True)))
    le_hfield.setText(tr(getattr(sty, 'height_field_override', '') or ''))
    sp_hdef.setValue(float(getattr(sty, 'default_height_override', 0.0) or 0.0))
    cb_schematic.setChecked(bool(getattr(sty, 'schematic_enabled', False)))

    _gtype_current = QgsWkbTypes.geometryType(sty.layer.wkbType()) if getattr(sty, 'layer', None) is not None else None
    _geom_name = {QC.QgsWkbTypes_GeometryType_PointGeometry:'point', QC.QgsWkbTypes_GeometryType_LineGeometry:'line', QC.QgsWkbTypes_GeometryType_PolygonGeometry:'polygon'}.get(_gtype_current, '')
    try:
        dlg.lblGeometryHint.setText(tr({
            'point': 'Couche de points : objets ponctuels et motifs compatibles uniquement.',
            'line': 'Couche de lignes : végétation en alignement/haie et objets linéaires (dont clôtures).',
            'polygon': 'Couche de polygones : dispersion surfacique pour les objets compatibles ; les clôtures suivent uniquement le périmètre extérieur.',
        }.get(_geom_name, 'Les familles proposées sont filtrées selon la géométrie de la couche.')))
    except Exception:
        pass

    
    _initial_symbol_id = str(getattr(sty, 'schematic_symbol_id', '') or '')
    _lib0 = get_symbol_library(_plugin_dir)
    _def0 = _lib0.get(_initial_symbol_id) if _initial_symbol_id else None
    _derived_type, _derived_family = symbol_taxonomy(_def0)
    _initial_type = str(getattr(sty, 'schematic_type', '') or _derived_type or 'vegetation')
    _initial_family = str(getattr(sty, 'schematic_family', '') or _derived_family or 'all')
    populate_type_combo(cmb_schematic_type, _initial_type, _geom_name)
    populate_family_combo(cmb_schematic_family, str(cmb_schematic_type.currentData() or _initial_type), _initial_family, _geom_name)
    _sym_lib = populate_symbol_combo(cmb_schematic, _plugin_dir, _initial_symbol_id, _geom_name, str(cmb_schematic_type.currentData() or ''), str(cmb_schematic_family.currentData() or 'all'))
    _schematic_params_work = dict(getattr(sty, 'schematic_params', {}) or {})
    _schematic_assets_work = list(getattr(sty, 'schematic_asset_paths', []) or [])[:3]
    _schematic_params_symbol_id = str(getattr(sty, 'schematic_symbol_id', '') or '')
    _schematic_params_by_symbol = {_schematic_params_symbol_id: dict(_schematic_params_work)} if _schematic_params_symbol_id else {}

    def _current_type():
        return str(cmb_schematic_type.currentData() or '')

    def _current_family():
        return str(cmb_schematic_family.currentData() or 'all')

    def _current_symbol_def():
        try:
            return _sym_lib.get(str(cmb_schematic.currentData() or ''))
        except Exception:
            return None

    def _update_model_button():
        count = len(_schematic_assets_work or [])
        btn_models.setText(tr(f"Modèles ({count}/3)…" if count else "Modèles (0/3)…"))

    def _update_schematic_controls():
        enabled = bool(cb_schematic.isChecked())
        for w in (cmb_schematic_type, cmb_schematic_family, cmb_schematic, btn_library):
            w.setEnabled(enabled)
        has_symbol = bool(cmb_schematic.currentData())
        btn_params.setEnabled(enabled and has_symbol)
        d = _current_symbol_def()
        _gen = str(d.get('generator','')) if d else ''
        
        
        
        btn_models.setEnabled(enabled and _gen in ('billboard_svg','vegetation_adaptive'))
        is_vegetation_barrier = bool(d and _gtype_current == QC.QgsWkbTypes_GeometryType_LineGeometry and _gen in ('vegetation_ribbon', 'vegetation_adaptive'))
        btn_occ_height.setEnabled(enabled and is_vegetation_barrier)
        _update_model_button()

    def _switch_schematic_params():
        nonlocal _schematic_params_work, _schematic_params_symbol_id
        new_id = str(cmb_schematic.currentData() or '')
        if new_id == _schematic_params_symbol_id:
            _update_schematic_controls()
            return
        if _schematic_params_symbol_id:
            _schematic_params_by_symbol[_schematic_params_symbol_id] = dict(_schematic_params_work or {})
        _schematic_params_symbol_id = new_id
        _schematic_params_work = dict(_schematic_params_by_symbol.get(new_id, {}) or {})
        _update_schematic_controls()

    def _repopulate_symbols(prefer_id=''):
        nonlocal _sym_lib
        current_id = str(prefer_id or cmb_schematic.currentData() or '')
        _sym_lib = populate_symbol_combo(
            cmb_schematic, _plugin_dir, current_id, _geom_name, _current_type(), _current_family()
        )
        _switch_schematic_params()

    def _on_type_changed(*_):
        nonlocal _schematic_assets_work
        old_family = _current_family()
        populate_family_combo(cmb_schematic_family, _current_type(), old_family, _geom_name)
        _schematic_assets_work = filter_assets_for_context(
            _schematic_assets_work, _plugin_dir, _current_type(), _current_family()
        )
        _repopulate_symbols()

    def _on_family_changed(*_):
        nonlocal _schematic_assets_work
        _schematic_assets_work = filter_assets_for_context(
            _schematic_assets_work, _plugin_dir, _current_type(), _current_family()
        )
        _repopulate_symbols()

    def _browse_schematic():
        browse_symbol_library(
            dlg, cmb_schematic, _plugin_dir, _geom_name, _current_type(), _current_family()
        )
        _switch_schematic_params()

    def _edit_schematic_params():
        nonlocal _schematic_params_work
        d = _current_symbol_def()
        if d:
            _schematic_params_work = edit_symbol_params(dlg, d, _schematic_params_work)
            if _schematic_params_symbol_id:
                _schematic_params_by_symbol[_schematic_params_symbol_id] = dict(_schematic_params_work or {})

    def _select_models():
        nonlocal _schematic_assets_work
        _schematic_assets_work = select_symbol_assets(
            dlg, _plugin_dir, _schematic_assets_work, _current_type(), _current_family()
        )[:3]
        _update_model_button()

    def _calc_occ_height():
        
        
        
        
        self._render_suspend_count = int(getattr(self, '_render_suspend_count', 0) or 0) + 1
        self._render_resume_requested = False
        try:
            try:
                if hasattr(self, '_hq_render_timer'):
                    self._hq_render_timer.stop()
            except Exception:
                pass
            try:
                scheduler = getattr(self, 'render_scheduler', None)
                if scheduler is not None:
                    for timer_name in ('debounce_timer', 'quality_restore_timer'):
                        timer = getattr(scheduler, timer_name, None)
                        if timer is not None:
                            timer.stop()
            except Exception:
                pass
            try:
                if hasattr(self, 'debounce'):
                    self.debounce.stop()
            except Exception:
                pass

            result_field = calculate_hedge_occlusion_dialog(
                self, getattr(sty, 'layer', None), le_hfield.text().strip() or 'qcv_h_req',
                hedge_symbol_id=str(cmb_schematic.currentData() or ''),
                hedge_params=dict(_schematic_params_work or {}),
            )
            if result_field:
                le_hfield.setText(tr(result_field))
                cb_25d.setChecked(True)
        finally:
            self._render_suspend_count = max(0, int(getattr(self, '_render_suspend_count', 1) or 1) - 1)
            if self._render_suspend_count == 0:
                self._render_resume_requested = False
                try:
                    QTimer.singleShot(0, self.render_preview)
                except Exception:
                    pass

    cb_schematic.toggled.connect(_update_schematic_controls)
    cmb_schematic_type.currentIndexChanged.connect(_on_type_changed)
    cmb_schematic_family.currentIndexChanged.connect(_on_family_changed)
    cmb_schematic.currentIndexChanged.connect(_switch_schematic_params)
    btn_library.clicked.connect(_browse_schematic)
    btn_params.clicked.connect(_edit_schematic_params)
    btn_models.clicked.connect(_select_models)
    btn_occ_height.clicked.connect(_calc_occ_height)
    _update_schematic_controls()

    cb_labels=dlg.cbLabels; cb_use_qgis_labels=dlg.cbUseQgisLabels; le_field=dlg.leField; le_text=dlg.leText
    sp_size=dlg.spSize; cmb_pos=dlg.cmbPos; cmb_anchor=dlg.cmbAnchor; sp_offx=dlg.spOffX; sp_offy=dlg.spOffY
    btn_txt_color=dlg.btnTxtColor; cb_bg=dlg.cbBg; btn_bg_color=dlg.btnBgColor; sp_bg_pad=dlg.spBgPad; sp_bg_rad=dlg.spBgRad
    cb_callout=dlg.cbCallout; btn_callout_color=dlg.btnCalloutColor; sp_callout=dlg.spCallout
    cb_labels.setChecked(bool(sty.show_labels)); cb_use_qgis_labels.setChecked(bool(getattr(sty, 'use_qgis_labels', True)))
    le_field.setText(tr(sty.label_field or '')); le_text.setText(tr(sty.label_text or '')); sp_size.setValue(int(sty.label_size))
    positions=["N","NE","E","SE","S","SW","W","NW","C"]; cmb_pos.clear(); cmb_pos.addItems(tr(positions)); cmb_pos.setCurrentIndex(positions.index(sty.label_pos if sty.label_pos in positions else "NE"))
    anchors=["AUTO","CENTROID","IMAGE_CENTER"]; cmb_anchor.clear(); cmb_anchor.addItems(tr(anchors)); cmb_anchor.setCurrentIndex(anchors.index(sty.label_anchor_mode if sty.label_anchor_mode in anchors else "AUTO"))
    sp_offx.setValue(int(sty.label_offset.x())); sp_offy.setValue(int(sty.label_offset.y()))
    btn_txt_color.setStyleSheet(f"background:{sty.label_text_color.name(QC.QColor_NameFormat_HexArgb)};")
    cb_bg.setChecked(bool(sty.label_bg)); btn_bg_color.setStyleSheet(f"background:{sty.label_bg_color.name(QC.QColor_NameFormat_HexArgb)};")
    sp_bg_pad.setValue(int(sty.label_bg_padding)); sp_bg_rad.setValue(int(sty.label_bg_radius))
    cb_callout.setChecked(bool(sty.label_callout)); btn_callout_color.setStyleSheet(f"background:{sty.label_callout_color.name(QC.QColor_NameFormat_HexArgb)};"); sp_callout.setValue(int(sty.label_callout_width))
    bb=dlg.buttonBox; bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)

    colors = {
        'layer': sty.color,
        'fill': getattr(sty, 'fill_color', sty.color),
        'text': sty.label_text_color,
        'bg': sty.label_bg_color,
        'callout': sty.label_callout_color,
    }
    btn_color.clicked.connect(lambda: colors.__setitem__('layer', _pick_style_color(btn_color, colors['layer'], dlg, 'Couleur de la couche')))
    btn_fill_color.clicked.connect(lambda: colors.__setitem__('fill', _pick_style_color(btn_fill_color, colors['fill'], dlg, 'Couleur du remplissage')))
    btn_txt_color.clicked.connect(lambda: colors.__setitem__('text', _pick_style_color(btn_txt_color, colors['text'], dlg, 'Couleur du texte')))
    btn_bg_color.clicked.connect(lambda: colors.__setitem__('bg', _pick_style_color(btn_bg_color, colors['bg'], dlg, 'Couleur du fond')))
    btn_callout_color.clicked.connect(lambda: colors.__setitem__('callout', _pick_style_color(btn_callout_color, colors['callout'], dlg, 'Couleur du callout')))

    def _toggle_manual_style_fields():
        manual = not cb_use_qgis_style.isChecked()
        for w in [btn_color, sp_width, sp_opacity, cb_fill_poly, btn_fill_color, cb_fill_walls]:
            w.setEnabled(manual)
        if cb_use_qgis_style.isChecked():
            try:
                qsty = _extract_qgis_layer_style(self, getattr(sty, 'layer', None), feat=None, fallback=sty)
                colors['layer'] = qsty['color']
                colors['fill'] = qsty['fill_color']
                btn_color.setStyleSheet(f"background:{colors['layer'].name(QC.QColor_NameFormat_HexArgb)};")
                btn_fill_color.setStyleSheet(f"background:{colors['fill'].name(QC.QColor_NameFormat_HexArgb)};")
                sp_width.setValue(float(qsty['width']))
                sp_opacity.setValue(int(round(100.0 * _opacity_factor(qsty.get('opacity', 1.0), 1.0))))
                cb_fill_poly.setChecked(bool(qsty['fill_polygons']))
            except Exception:
                pass

    cb_use_qgis_style.toggled.connect(_toggle_manual_style_fields)
    _toggle_manual_style_fields()

    def _toggle_manual_label_fields():
        manual = not cb_use_qgis_labels.isChecked()
        for w in [le_field, le_text, sp_size, cmb_pos, cmb_anchor, sp_offx, sp_offy, btn_txt_color, cb_bg, btn_bg_color, sp_bg_pad, sp_bg_rad, cb_callout, btn_callout_color, sp_callout]:
            w.setEnabled(manual)
        if cb_use_qgis_labels.isChecked():
            try:
                qlbl = _extract_qgis_label_settings(getattr(sty, 'layer', None))
                cb_labels.setChecked(bool(qlbl.get('enabled', False)))
                le_field.setText(tr(qlbl.get('field_name') or ''))
                le_text.setText(tr(qlbl.get('expr') if bool(qlbl.get('is_expression', False)) else ''))
                sp_size.setValue(int(qlbl.get('size', max(6, int(sty.label_size)))))
                colors['text'] = QColor(qlbl.get('text_color', colors['text']))
                btn_txt_color.setStyleSheet(f"background:{colors['text'].name(QC.QColor_NameFormat_HexArgb)};")
                cb_bg.setChecked(bool(qlbl.get('bg', False)))
                colors['bg'] = QColor(qlbl.get('bg_color', colors['bg']))
                btn_bg_color.setStyleSheet(f"background:{colors['bg'].name(QC.QColor_NameFormat_HexArgb)};")
            except Exception:
                pass

    cb_use_qgis_labels.toggled.connect(_toggle_manual_label_fields)
    _toggle_manual_label_fields()

    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted:
        return

    sty.use_qgis_style = bool(cb_use_qgis_style.isChecked())
    sty.color = colors['layer']
    sty.width = float(sp_width.value())
    sty.opacity = float(sp_opacity.value()) / 100.0
    sty.fill_polygons = bool(cb_fill_poly.isChecked())
    sty.fill_color = colors['fill']
    sty.fill_walls = bool(cb_fill_walls.isChecked())
    if sty.use_qgis_style:
        _sync_layer_style_from_qgis(self, sty, feat=None)
    sty.enable_25d = bool(cb_25d.isChecked())
    sty.height_field_override = le_hfield.text().strip()
    sty.default_height_override = None if float(sp_hdef.value()) <= 0.0 else float(sp_hdef.value())
    
    
    _requested_schematic = bool(cb_schematic.isChecked())
    _selected_symbol_id = str(cmb_schematic.currentData() or '').strip()
    _selected_definition = _sym_lib.get(_selected_symbol_id) if _selected_symbol_id else None
    sty.schematic_enabled = bool(_requested_schematic and _selected_definition is not None)
    if _selected_definition is not None:
        sty.schematic_symbol_id = _selected_symbol_id
        _tax_type, _tax_family = symbol_taxonomy(_selected_definition)
        sty.schematic_type = str(_tax_type or cmb_schematic_type.currentData() or '')
        sty.schematic_family = str(_tax_family or cmb_schematic_family.currentData() or 'all')
        sty.schematic_params = dict(_schematic_params_work or {})
        sty.schematic_asset_paths = filter_assets_for_context(list(_schematic_assets_work or [])[:3], _plugin_dir, sty.schematic_type, sty.schematic_family)
    else:
        sty.schematic_symbol_id = ''
        sty.schematic_type = ''
        sty.schematic_family = ''
        sty.schematic_params = {}
        sty.schematic_asset_paths = []
        if _requested_schematic:
            qcv_log(f"Style couche {sty.layer.name() if getattr(sty,'layer',None) else '?'} : AVR demandé sans motif valide, représentation schématique désactivée", 'SCHEMATIC/STATE', 'WARNING')
    _motif_log = f"{sty.schematic_type}/{sty.schematic_family}/{sty.schematic_symbol_id}" if sty.schematic_enabled else 'désactivé'
    qcv_log(f"Style couche {sty.layer.name() if getattr(sty,'layer',None) else '?'} : géométrie={_geom_name}, motif={_motif_log}, modèles={len(sty.schematic_asset_paths)}", 'SCHEMATIC', 'INFO')
    sty.use_qgis_labels = bool(cb_use_qgis_labels.isChecked())
    sty.show_labels = bool(cb_labels.isChecked())
    if sty.use_qgis_labels:
        _sync_layer_labels_from_qgis(self, sty)
    else:
        sty.label_field = (le_field.text().strip() or None)
        sty.label_text = le_text.text().strip()
        sty.label_size = int(sp_size.value())
        sty.label_text_color = colors['text']
        sty.label_bg = bool(cb_bg.isChecked())
        sty.label_bg_color = colors['bg']
    sty.label_pos = cmb_pos.currentText()
    sty.label_anchor_mode = cmb_anchor.currentText()
    sty.label_offset = QPoint(int(sp_offx.value()), int(sp_offy.value()))
    sty.label_bg_padding = int(sp_bg_pad.value())
    sty.label_bg_radius = int(sp_bg_rad.value())
    sty.label_halo = False
    sty.label_callout = bool(cb_callout.isChecked())
    sty.label_callout_color = colors['callout']
    sty.label_callout_width = int(sp_callout.value())

    try:
        self._refresh_layer_list_labels(idx)
    except Exception:
        pass
    _queue_overlay_style_refresh(self)

def _get_base_scaled(self, W, H):
    
    if self.image is None or self.image.isNull():
        try:
            transparent = bool(self._schematic_background_transparent())
            color = self._schematic_background_color().name()
            key = ('__schematic__', int(W), int(H), color, transparent)
            img = self._base_cache.get(key)
            if img is None or img.isNull():
                img = self._make_schematic_base(int(W), int(H), for_export=False)
                self._base_cache[key] = img
            return img
        except Exception:
            return None

    
    
    try:
        full_w = int(self.spin_w.value())
        full_h = int(self.spin_h.value())
    except Exception:
        full_w, full_h = int(W), int(H)

    is_full_res = (int(W) == full_w and int(H) == full_h)
    use_fast = False
    try:
        use_fast = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked() and not is_full_res)
    except Exception:
        use_fast = False

    transform_mode = QC.Qt_TransformationMode_FastTransformation if use_fast else QC.Qt_TransformationMode_SmoothTransformation
    key = (self.photo_path, int(W), int(H), 'fast' if use_fast else 'smooth')
    img = self._base_cache.get(key)
    if img is None:
        try:
            for_export = bool(getattr(self, '_qcv_export_in_progress', False))
            if bool(getattr(self, '_photo_is_proxy', False)):
                img = read_scaled_for_owner(self, int(W), int(H), for_export=for_export)
            else:
                img = self.image.scaled(int(W), int(H), QC.Qt_AspectRatioMode_IgnoreAspectRatio, transform_mode)
        except Exception as e:
            qcv_log(f"Base photo {int(W)}×{int(H)} indisponible: {e}", 'PHOTO/IO', 'CRITICAL')
            if bool(getattr(self, '_qcv_export_in_progress', False)):
                raise
            img = self.image.scaled(int(W), int(H), QC.Qt_AspectRatioMode_IgnoreAspectRatio, transform_mode)
        self._base_cache[key] = img
    return img



def _refresh_preview_and_viewer(self):
    try: self.render_preview()
    except Exception: pass
    try:
        if getattr(self, "viewer", None):
            ov = getattr(self, "overlay_image", None) or getattr(self, "overlay_path", None)
            if ov is not None: self.viewer.update_overlay(ov)
            else:              self.viewer.clear_overlay()
    except Exception: pass

def _mb(self):
    
    return self.iface.messageBar()

def _on_toggle_center_axis(self, checked: bool):
    
    self.show_center_axis = bool(checked)
    try:
        getattr(self, '_overlay_cache', {}).clear()
    except Exception:
        pass
    _refresh_preview_and_viewer(self)

def _on_toggle_pdv_axis(self, checked: bool):
    
    self.show_pdv_axis = bool(checked)
    try:
        getattr(self, '_overlay_cache', {}).clear()
    except Exception:
        pass
    if checked:
        try:
            self.start_pick_pdv_center()  
            self._mb().pushMessage(tr("Centre image"), tr("Cliquez sur le canevas le point correspondant au centre de l'image."), level=QC.Qgis_MessageLevel_Info, duration=5)
        except Exception as e:
            self._mb().pushWarning(tr("Centre image"), tr(f"Impossible d'armer le clic canevas: {e}"))
    else:
        try:
            
            if hasattr(self, "_cancel_maptool"):
                self._cancel_maptool()
        except Exception:
            pass
    _refresh_preview_and_viewer(self)

def install_global_shortcuts(self):
    
    if getattr(self, "_shortcuts_installed", False): return
    self._shortcuts_installed = True

    parent = self.iface.mainWindow()

    self._sc_center = QShortcut(QKeySequence("C"), parent)
    self._sc_center.setContext(QC.Qt_ShortcutContext_ApplicationShortcut)
    self._sc_center.activated.connect(lambda: (
        hasattr(self, "cb_show_center_axis") and
        self.cb_show_center_axis.setChecked(not self.cb_show_center_axis.isChecked())
    ))

    self._sc_pdv = QShortcut(QKeySequence("P"), parent)
    self._sc_pdv.setContext(QC.Qt_ShortcutContext_ApplicationShortcut)
    self._sc_pdv.activated.connect(lambda: (
        hasattr(self, "cb_show_pdv_axis") and
        self.cb_show_pdv_axis.setChecked(not self.cb_show_pdv_axis.isChecked())
    ))

        
def _cancel_maptool(self):
    canvas = self.iface.mapCanvas()
    if self._maptool_backup:
        try: canvas.setMapTool(self._maptool_backup)
        except Exception: pass
    self._maptool_backup = None
