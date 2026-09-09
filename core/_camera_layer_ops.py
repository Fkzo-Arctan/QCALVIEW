


from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._compat import QC
import os, json
from datetime import datetime

from qgis.PyQt.QtGui import QImage, QColor
from qgis.PyQt.QtCore import QPoint
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsProject, QgsField, QgsVectorLayer, QgsMapLayerStyle, QgsMapThemeCollection, QgsCoordinateTransform, QgsPointXY, Qgis, NULL
from ._log import qcv_log
from ._image_io import load_working_image, probe_image



QCV_CAMERA_FIELDS = [
    ("qcv_id", QC.QMetaType_Type_QString, 64, 0, ["qcv_id"]),
    ("qcv_img", QC.QMetaType_Type_QString, 512, 0, ["qcv_img"]),
    ("qcv_mode", QC.QMetaType_Type_QString, 12, 0, ["qcv_mode", "qcv_source", "qcv_src"]),
    ("qcv_proj", QC.QMetaType_Type_QString, 24, 0, ["qcv_proj"]),
    ("qcv_360", QC.QMetaType_Type_Int, 0, 0, ["qcv_360"]),
    ("qcv_yaw", QC.QMetaType_Type_Double, 20, 6, ["qcv_yaw"]),
    ("qcv_pitch", QC.QMetaType_Type_Double, 20, 6, ["qcv_pitch"]),
    ("qcv_roll", QC.QMetaType_Type_Double, 20, 6, ["qcv_roll"]),
    ("qcv_hfov", QC.QMetaType_Type_Double, 20, 6, ["qcv_hfov"]),
    ("qcv_vfov", QC.QMetaType_Type_Double, 20, 6, ["qcv_vfov"]),
    ("qcv_ahf", QC.QMetaType_Type_Int, 0, 0, ["qcv_ahf"]),
    ("qcv_alt", QC.QMetaType_Type_Double, 20, 6, ["qcv_alt", "qcv_cam_h"]),
    ("qcv_ofh", QC.QMetaType_Type_Double, 20, 6, ["qcv_ofh", "qcv_off_h"]),
    ("qcv_ofv", QC.QMetaType_Type_Double, 20, 6, ["qcv_ofv", "qcv_off_v"]),
    ("qcv_ofmd", QC.QMetaType_Type_Int, 0, 0, ["qcv_ofmd", "qcv_off_mode", "qcv_ofmod"]),
    ("qcv_mdst", QC.QMetaType_Type_Double, 20, 6, ["qcv_mdst", "qcv_maxdist", "qcv_maxdst"]),
    ("qcv_iw", QC.QMetaType_Type_Int, 0, 0, ["qcv_iw", "qcv_img_w"]),
    ("qcv_ih", QC.QMetaType_Type_Int, 0, 0, ["qcv_ih", "qcv_img_h"]),
    ("qcv_foc", QC.QMetaType_Type_Double, 20, 6, ["qcv_foc", "qcv_focal"]),
    ("qcv_sens", QC.QMetaType_Type_Double, 20, 6, ["qcv_sens", "qcv_sensorw", "qcv_sensw"]),
    ("qcv_stat", QC.QMetaType_Type_QString, 64, 0, ["qcv_stat", "qcv_status"]),
    ("qcv_note", QC.QMetaType_Type_QString, 255, 0, ["qcv_note"]),
    ("qcv_upd", QC.QMetaType_Type_QString, 32, 0, ["qcv_upd", "qcv_updated", "qcv_updatd"]),
]

FIELD_ALIASES = {key: aliases for key, _typ, _len, _prec, aliases in QCV_CAMERA_FIELDS}
FIELD_SPECS = {key: (typ, length, precision, aliases) for key, typ, length, precision, aliases in QCV_CAMERA_FIELDS}

IMAGE_HINTS = ("qcv_img", "photo", "image", "img", "pano", "path", "file", "fichier", "url")
TITLE_HINTS = ("qcv_id", "IDPTV", "idptv", "name", "nom")


def _qcv_color_hex(c):
    try:
        return str(c.name(QC.QColor_NameFormat_HexArgb))
    except Exception:
        return '#ff00ff00'

def _qcv_style_to_dict(sty):
    
    keys = (
        'visible','opacity','width','show_labels','label_field','label_size','label_pos',
        'label_bg','label_bg_padding','label_bg_radius','label_halo','label_halo_width',
        'label_callout','label_callout_width','label_anchor_mode','label_text',
        'enable_25d','height_field_override','default_height_override','fill_polygons','fill_walls',
        'use_qgis_style','qgis_theme_name','qgis_theme_style_name','schematic_enabled',
        'schematic_symbol_id','schematic_type','schematic_family','schematic_params','schematic_asset_paths','use_qgis_labels',
        'qgis_label_is_expression','qgis_label_expr'
    )
    out = {'layer_id': sty.layer.id() if getattr(sty, 'layer', None) is not None else ''}
    for k in keys:
        try:
            v=getattr(sty,k)
            if isinstance(v, (str,int,float,bool)) or v is None or isinstance(v,(dict,list)):
                out[k]=v
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:74")
    for k in ('color','fill_color','label_text_color','label_bg_color','label_halo_color','label_callout_color'):
        try: out[k]=_qcv_color_hex(getattr(sty,k))
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:78")
    try: out['label_offset']=[int(sty.label_offset.x()), int(sty.label_offset.y())]
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:80")
    try: out['pen_style']=int(sty.pen_style)
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:82")
    return out

def _qcv_style_from_dict(sty, data):
    for k,v in (data or {}).items():
        if k in ('layer_id','label_offset','pen_style') or k.endswith('_color') or k == 'color':
            continue
        if hasattr(sty,k):
            try: setattr(sty,k,v)
            except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:91")
    for k in ('color','fill_color','label_text_color','label_bg_color','label_halo_color','label_callout_color'):
        if k in data:
            try: setattr(sty,k,QColor(str(data[k])))
            except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:95")
    if 'label_offset' in data:
        try: sty.label_offset=QPoint(int(data['label_offset'][0]),int(data['label_offset'][1]))
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:98")
    if 'pen_style' in data:
        try: sty.pen_style=QC.Qt_PenStyle(int(data['pen_style']))
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:101")

def _camera_visual_state_key(self, fid):
    layer=_camera_layer(self)
    if layer is None or fid is None: return ''
    
    safe_layer=''.join(ch if (ch.isalnum() or ch in '_-') else '_' for ch in str(layer.id()))
    return f'/pdv_visual_state/{safe_layer}/{int(fid)}'


def _camera_layer_tree_model(self):
    try:
        view = self.iface.layerTreeView()
        return view.layerTreeModel() if view is not None else None
    except Exception:
        return None


def _qcv_widget_bool(self, name, default=False):
    try:
        w=getattr(self,name,None)
        return bool(w.isChecked()) if w is not None else bool(default)
    except Exception: return bool(default)


def _qcv_widget_float(self, name, default=0.0):
    try:
        w=getattr(self,name,None)
        return float(w.value()) if w is not None else float(default)
    except Exception: return float(default)


def _camera_capture_plugin_settings(self):
    out={
        'cb_occ_terrain':_qcv_widget_bool(self,'cb_occ_terrain'),
        'cb_occ_layers':_qcv_widget_bool(self,'cb_occ_layers'),
        'cb_occ_objects':_qcv_widget_bool(self,'cb_occ_objects'),
        'cb_transparent_objects':_qcv_widget_bool(self,'cb_transparent_objects'),
        'cb_debug_no_occ':_qcv_widget_bool(self,'cb_debug_no_occ'),
        'cb_use_dem_z':_qcv_widget_bool(self,'cb_use_dem_z'),
        'cb_force_horizontal_25d':_qcv_widget_bool(self,'cb_force_horizontal_25d'),
        'cb_draw_2p5d':_qcv_widget_bool(self,'cb_draw_2p5d'),
        'cb_show_labels':_qcv_widget_bool(self,'cb_show_labels',True),
        'cb_curvature':_qcv_widget_bool(self,'cb_curvature',True),
        'd_eps':_qcv_widget_float(self,'d_eps',0.05),
        'd_az_step':_qcv_widget_float(self,'d_az_step',1.0),
        'd_rad_step':_qcv_widget_float(self,'d_rad_step',25.0),
        'd_hdefault':_qcv_widget_float(self,'d_hdefault',0.0),
        'd_earth_radius_km':_qcv_widget_float(self,'d_earth_radius_km',6370.0),
        'height_field': '', 'dem_layer_id':'', 'relief_mode':'none',
    }
    try: out['height_field']=str(self.txt_hfield.text() or '')
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:153")
    try:
        lyr=self.cmb_dem.currentLayer(); out['dem_layer_id']=str(lyr.id()) if lyr is not None else ''
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:156")
    try: out['relief_mode']=str(self._relief_mode_id())
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:158")
    return out


def _camera_restore_plugin_settings(self, data):
    data=data or {}
    for name in ('cb_occ_terrain','cb_occ_layers','cb_transparent_objects','cb_debug_no_occ','cb_use_dem_z','cb_force_horizontal_25d','cb_draw_2p5d','cb_show_labels','cb_curvature'):
        if name in data:
            try: getattr(self,name).setChecked(bool(data[name]))
            except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:167")
    
    
    
    try:
        self.cb_occ_objects.setChecked(True)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:173")
    for name in ('d_eps','d_az_step','d_rad_step','d_hdefault','d_earth_radius_km'):
        if name in data:
            try: getattr(self,name).setValue(float(data[name]))
            except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:178")
    if 'height_field' in data:
        try: self.txt_hfield.setText(tr(str(data.get('height_field') or '')))
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:181")
    lid=str(data.get('dem_layer_id') or '')
    if lid:
        try:
            lyr=QgsProject.instance().mapLayer(lid)
            if lyr is not None: self.cmb_dem.setLayer(lyr)
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:187")
    mode=str(data.get('relief_mode') or '')
    if mode:
        
        
        try:
            if hasattr(self,'_set_relief_mode_id'): self._set_relief_mode_id(mode)
            elif hasattr(self,'combo_relief_mode'):
                mapping={'none':0,'transparent':1,'opaque':2,'wireframe':3,'ridgelines':4,'skyline':5}
                self.combo_relief_mode.setCurrentIndex(int(mapping.get(mode.lower(),0)))
                if hasattr(self,'_sync_relief_mode_controls'): self._sync_relief_mode_controls()
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:198")


def _camera_collect_visual_state_snapshot(self):
    
    state={'version':3,'theme':'','overlays':[],'settings':_camera_capture_plugin_settings(self)}
    try:
        combo=getattr(self,'cmb_qgis_theme',None)
        state['theme']=str(combo.currentData() or combo.currentText() or '') if combo is not None else ''
        if state['theme'].startswith('—'):
            state['theme']=''
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:209")
    try:
        state['overlays']=[_qcv_style_to_dict(sty) for sty in list(getattr(self,'layer_styles',[]) or [])]
    except Exception:
        state['overlays']=[]
    return state


def _camera_visual_state_dirty(self, fid):
    
    key=_camera_visual_state_key(self,fid)
    if not key:
        return False
    try:
        current=_camera_collect_visual_state_snapshot(self)
        raw,found=QgsProject.instance().readEntry('QCALVIEW', key, '')
        if not found or not raw:
            return True
        saved=json.loads(str(raw))
        a=json.dumps(current,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        b=json.dumps(saved,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return a != b
    except Exception as exc:
        qcv_log(f"PDV {fid}: comparaison état visuel impossible: {exc}", 'EXPORT/PREFLIGHT', 'WARNING')
        
        return True


def _camera_capture_visual_state(self, fid):
    
    key=_camera_visual_state_key(self,fid)
    if not key: return False
    project=QgsProject.instance()
    state=_camera_collect_visual_state_snapshot(self)
    try:
        payload=json.dumps(state,ensure_ascii=False,separators=(',',':'))
        ok=bool(project.writeEntry('QCALVIEW', key, payload))
        if ok:
            raw,found=project.readEntry('QCALVIEW', key, '')
            ok=bool(found and raw)
        project.setDirty(True)
    except Exception as exc:
        qcv_log(f"Échec enregistrement PDV {fid}: {exc}", 'PDV/SAVE', 'CRITICAL')
        ok=False
    self._camera_last_visual_capture_fid=int(fid)
    self._camera_last_visual_capture_summary=(
        f"état QCALVIEW enregistré ({len(state['overlays'])} couches, thème natif={state['theme'] or 'aucun'})"
        if ok else "échec d’enregistrement de l’état QCALVIEW"
    )
    if ok: qcv_log(f"PDV {fid}: {self._camera_last_visual_capture_summary}", 'PDV/SAVE', 'SUCCESS')
    return ok


def _camera_restore_visual_state(self, fid):
    key=_camera_visual_state_key(self,fid)
    if not key:return False
    project=QgsProject.instance()
    try:
        raw,found=project.readEntry('QCALVIEW', key, '')
        if not found or not raw:
            self._camera_last_visual_restore_fid=int(fid); self._camera_last_visual_restore_summary='aucun état QCALVIEW enregistré'
            return False
        state=json.loads(str(raw))
    except Exception as exc:
        qcv_log(f"PDV {fid}: état illisible: {exc}", 'PDV/LOAD', 'WARNING')
        self._camera_last_visual_restore_fid=int(fid); self._camera_last_visual_restore_summary='état QCALVIEW illisible'
        return False

    
    theme=str(state.get('theme') or '')
    theme_applied=False
    if theme:
        try:
            coll=project.mapThemeCollection(); root=project.layerTreeRoot(); model=_camera_layer_tree_model(self)
            if coll is not None and model is not None and coll.hasMapTheme(theme):
                prev=getattr(self,'_suspend_theme_auto_apply',False); self._suspend_theme_auto_apply=True
                try: coll.applyTheme(theme,root,model); theme_applied=True
                finally: self._suspend_theme_auto_apply=prev
        except Exception as exc:
            qcv_log(f"PDV {fid}: thème {theme} non appliqué: {exc}", 'PDV/LOAD', 'WARNING')

    
    _camera_restore_plugin_settings(self,state.get('settings') or {})
    restored=[]
    try:
        from ._layerstyle import LayerStyle
        for d in (state.get('overlays') or []):
            lyr=project.mapLayer(str(d.get('layer_id') or ''))
            if lyr is None: continue
            sty=LayerStyle(lyr); _qcv_style_from_dict(sty,d)
            
            try:
                if bool(getattr(sty, 'schematic_enabled', False)):
                    sid = str(getattr(sty, 'schematic_symbol_id', '') or '').strip()
                    from ._schematic_symbols import get_symbol_library
                    plugin_dir = os.path.dirname(os.path.dirname(__file__))
                    if (not sid) or get_symbol_library(plugin_dir).get(sid) is None:
                        sty.schematic_enabled = False
                        sty.schematic_symbol_id = ''
                        sty.schematic_type = ''
                        sty.schematic_family = ''
                        sty.schematic_params = {}
                        sty.schematic_asset_paths = []
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:313")
            restored.append(sty)
        self.layer_styles=restored
        try: self._refresh_layer_list_labels(0 if restored else -1)
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:318")
    except Exception as exc:
        qcv_log(f"PDV {fid}: restauration overlays incomplète: {exc}", 'PDV/LOAD', 'WARNING')

    combo=getattr(self,'cmb_qgis_theme',None)
    if combo is not None:
        try:
            prev=getattr(self,'_suspend_theme_auto_apply',False); self._suspend_theme_auto_apply=True
            try:
                idx=combo.findData(theme) if theme else combo.findData('')
                if idx<0 and theme: idx=combo.findText(theme)
                if idx>=0: combo.setCurrentIndex(idx)
            finally:self._suspend_theme_auto_apply=prev
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:331")
    try: getattr(self,'_overlay_cache',{}).clear(); getattr(self,'_geom_cache',{}).clear()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:333")
    try:self.iface.mapCanvas().refresh()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:335")
    try:self._refresh_preview_and_viewer()
    except Exception:
        try:self.render_preview()
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:339")
    self._camera_last_visual_restore_fid=int(fid)
    self._camera_last_visual_restore_summary=f"état QCALVIEW restauré ({len(restored)} couches, thème {'appliqué' if theme_applied else (theme or 'aucun')})"
    qcv_log(f"PDV {fid}: {self._camera_last_visual_restore_summary}", 'PDV/LOAD', 'SUCCESS')
    return True


def _camera_metric_project_crs(self):
    
    try:
        crs = QgsProject.instance().crs()
        if crs is None or not crs.isValid() or crs.isGeographic():
            return None
        
        
        try:
            meters = getattr(getattr(Qgis, 'DistanceUnit', None), 'Meters', None)
            if meters is None:
                meters = getattr(Qgis, 'DistanceMeters', None)
            if meters is not None and crs.mapUnits() != meters:
                return None
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:360")
        return crs
    except Exception:
        return None


def _camera_point_in_work_crs(self, feat=None):
    layer = _camera_layer(self)
    feat = feat or _camera_current_feature(self)
    work = _camera_metric_project_crs(self)
    if layer is None or feat is None or work is None:
        return None, work
    try:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return None, work
        pt = QgsPointXY(geom.asPoint())
        src = layer.crs()
        if src.isValid() and src != work:
            pt = QgsCoordinateTransform(src, work, QgsProject.instance()).transform(pt)
        return pt, work
    except Exception as exc:
        qcv_log(f"Transformation du PDV vers le CRS projet impossible: {exc}", 'PDV/CRS', 'WARNING')
        return None, work


def _camera_warn_if_non_metric_project(self, notify=False):
    crs = _camera_metric_project_crs(self)
    if crs is not None:
        return True
    msg = "Le CRS du projet doit être projeté et métrique pour les calculs QCALVIEW. Les couches source peuvent être en WGS84 : QCALVIEW les reprojette vers le CRS du projet."
    _camera_set_status(self, msg, '#b36b00')
    if notify:
        try: self.iface.messageBar().pushWarning(tr('QCALVIEW — CRS'), tr(msg))
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:395")
    return False


def _normalize_azimuth_360(value, fallback=0.0):
    try:
        out = float(value) % 360.0
        if out < 0.0:
            out += 360.0
        return out
    except Exception:
        return float(fallback)


def _camera_layer(self):
    try:
        layer = self.cmb_camera.currentLayer()
    except Exception:
        layer = None
    return layer if isinstance(layer, QgsVectorLayer) else None


def _camera_lowered_names(layer):
    try:
        return {str(n).lower(): n for n in layer.fields().names()}
    except Exception:
        return {}


def _camera_find_existing_field(layer, canonical_name):
    lowered = _camera_lowered_names(layer)
    aliases = FIELD_ALIASES.get(canonical_name, [canonical_name])
    for alias in aliases:
        real = lowered.get(str(alias).lower())
        if real:
            return real
    return None


def _camera_field_name(layer, canonical_name):
    return _camera_find_existing_field(layer, canonical_name) or canonical_name


def _camera_feature_value(layer, feat, canonical_name, default=None):
    try:
        actual = _camera_find_existing_field(layer, canonical_name)
        if not actual or actual not in feat.fields().names():
            return default
        val = feat[actual]
        return default if val in (None, "") else val
    except Exception:
        return default







def _camera_normalize_view_mode(value, default='AUTO'):
    raw = str(value or '').strip().upper()
    aliases = {
        'SCHEMA': 'SCHEMA', 'SCHEMATIC': 'SCHEMA', 'SANS_PHOTO': 'SCHEMA',
        'NO_PHOTO': 'SCHEMA', 'NONE': 'SCHEMA',
        'PHOTO': 'PHOTO', 'IMAGE': 'PHOTO',
        'AUTO': 'AUTO', 'LEGACY': 'AUTO',
    }
    return aliases.get(raw, str(default or 'AUTO').strip().upper() or 'AUTO')


def _camera_feature_view_mode(self, layer, feat):
    try:
        fid = int(feat.id())
        draft = (getattr(self, '_camera_drafts', {}) or {}).get(fid)
    except Exception:
        draft = None
    if isinstance(draft, dict) and 'qcv_mode' in draft:
        return _camera_normalize_view_mode(draft.get('qcv_mode'))
    return _camera_normalize_view_mode(_camera_feature_value(layer, feat, 'qcv_mode', 'AUTO'))


def _camera_pick_state_value(state, key, fallback):
    try:
        val = state.get(key, None)
    except Exception:
        val = None
    return fallback if val in (None, "") else val



def _camera_read_photo_metadata(path):
    meta = {}
    try:
        from ._utils_ops import read_exif
        meta = dict(read_exif(path) or {})
    except Exception:
        meta = {}
    
    try:
        inf = probe_image(str(path))
        meta.setdefault('ImageWidth', int(inf.get('width', 0) or 0))
        meta.setdefault('ImageHeight', int(inf.get('height', 0) or 0))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:497")
    return meta


def _camera_projection_from_meta(meta, fallback='PINHOLE'):
    proj = str((meta or {}).get('Projection') or '').strip().lower()
    if proj == 'equirectangular':
        return 'EQUIRECT'
    if proj == 'cylindrical':
        return 'CYLINDRICAL'
    if proj in ('rectilinear', 'pinhole'):
        return 'PINHOLE'
    return str(fallback or 'PINHOLE')


def _camera_apply_photo_metadata_to_ui(self, meta):
    meta = meta or {}
    widgets = []
    for name in ('cmb_proj', 'spin_w', 'spin_h', 'd_focal', 'd_sensorw'):
        w = getattr(self, name, None)
        if w is not None:
            try:
                w.blockSignals(True)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:521")
            widgets.append(w)
    try:
        w = meta.get('ImageWidth')
        h = meta.get('ImageHeight')
        if isinstance(w, (int, float)) and int(w) > 0:
            self.spin_w.setValue(int(w))
        if isinstance(h, (int, float)) and int(h) > 0:
            self.spin_h.setValue(int(h))
        foc = meta.get('FocalLength')
        sens = meta.get('SensorWidthMM')
        f35 = meta.get('FocalLength35mmEq')
        if isinstance(foc, (int, float)) and float(foc) > 0 and isinstance(sens, (int, float)) and float(sens) > 0:
            self.d_focal.setValue(float(foc))
            self.d_sensorw.setValue(float(sens))
        elif isinstance(f35, (int, float)) and float(f35) > 0:
            foc = float(f35)
            sens = 36.0
            self.d_focal.setValue(foc)
            self.d_sensorw.setValue(sens)
        mode = _camera_projection_from_meta(meta, getattr(self, 'cmb_proj', None).currentText() if getattr(self, 'cmb_proj', None) else 'PINHOLE')
        try:
            idx = self.cmb_proj.findText(mode)
            if idx >= 0:
                self.cmb_proj.setCurrentIndex(idx)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:547")
        try:
            info = []
            if isinstance(w, (int, float)) and isinstance(h, (int, float)) and int(w) > 0 and int(h) > 0:
                info.append(f"{int(w)}×{int(h)} px")
            if isinstance(foc, (int, float)) and float(foc) > 0:
                info.append(f"Focale {float(foc):.2f} mm")
            self.lbl_info.setText(tr(" | ".join(info) if info else os.path.basename(str(getattr(self, 'photo_path', '') or ''))))
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:556")
    finally:
        for w in widgets:
            try:
                w.blockSignals(False)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:562")

def _camera_set_status(self, text, color="#666"):
    try:
        self.lbl_cam_status.setText(tr(str(text)))
        self.lbl_cam_status.setStyleSheet(f"color:{color};")
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:569")


def _camera_update_title(self, label=None):
    try:
        base = "QCALVIEW"
        clean = str(label or "").strip()
        if clean:
            self.setWindowTitle(tr(f"{base} — {clean}"))
            if hasattr(self, 'lbl_current_pdv'):
                self.lbl_current_pdv.setText(tr(f"PDV&nbsp;<b>{clean}</b>"))
        else:
            self.setWindowTitle(tr(base))
            if hasattr(self, 'lbl_current_pdv'):
                self.lbl_current_pdv.setText(tr(""))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:585")


def _camera_first_existing_name(names, candidates):
    lowered = {str(n).lower(): n for n in names}
    for cand in candidates:
        real = lowered.get(str(cand).lower())
        if real:
            return real
    return ""


def _camera_default_state(self):
    defaults = {
        'qcv_proj': str(self.cmb_proj.currentText()).strip(),
        'qcv_360': 1 if (str(self.cmb_proj.currentText()).strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR') and self.cb_360.isChecked()) else 0,
        'qcv_yaw': _normalize_azimuth_360(self.d_yaw.value()),
        'qcv_pitch': float(self.d_pitch.value()),
        'qcv_roll': float(self.d_roll.value()),
        'qcv_hfov': float(self.d_hfov.value()),
        'qcv_vfov': float(self.d_vfov.value()),
        'qcv_ahf': 1 if self.cb_auto_hfov.isChecked() else 0,
        'qcv_alt': float(self.d_camheight.value()) if hasattr(self, 'd_camheight') else 1.7,
        'qcv_ofh': float(self.spin_off_h.value()),
        'qcv_ofv': float(self.spin_off_v.value()),
        'qcv_ofmd': int(self.cmb_off_mode.currentIndex()),
        'qcv_mdst': float(self.d_maxdist.value()),
        'qcv_iw': int(self.spin_w.value()),
        'qcv_ih': int(self.spin_h.value()),
        'qcv_foc': float(self.d_focal.value()),
        'qcv_sens': float(self.d_sensorw.value()),
    }
    stored = getattr(self, '_camera_point_defaults', None) or {}
    if stored:
        defaults.update(stored)
    defaults['qcv_alt'] = float(defaults.get('qcv_alt', 1.7) or 1.7)
    return defaults


def _camera_geom_z(feat):
    try:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return None
        pt = geom.asPoint()
        z = getattr(pt, 'z', lambda: None)()
        if z is None:
            return None
        z = float(z)
        return z if abs(z) < 1e12 else None
    except Exception:
        return None


def _camera_resolve_title(self, layer, feat):
    names = layer.fields().names()
    id_field = ''
    label_field = ''
    try: id_field = str(self.cmb_cam_id_field.currentText() or '').strip()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:645")
    try: label_field = str(self.cmb_cam_label_field.currentText() or '').strip()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:647")

    def value(field):
        if field and field in names:
            try:
                v = feat[field]
                return '' if v in (None, NULL, '') else str(v).strip()
            except Exception:
                return ''
        return ''

    ident = value(id_field)
    label = value(label_field)
    if ident and label and label != ident:
        return f"{ident} — {label}"
    if ident or label:
        return ident or label
    for field_name in ('qcv_id', 'IDPTV', 'idptv', 'name', 'nom'):
        val = _camera_feature_value(layer, feat, field_name, None) if field_name.startswith('qcv_') else None
        if val in (None, '') and field_name.lower() in _camera_lowered_names(layer):
            try: val = feat[_camera_lowered_names(layer)[field_name.lower()]]
            except Exception: val = None
        if val not in (None, ''):
            return str(val).strip()
    return f"PDV {int(feat.id())}"


def _camera_list_label(self, layer, feat):
    label = _camera_resolve_title(self, layer, feat)
    return label or f"FID {feat.id()}"


def _camera_current_fid(self):
    fid = getattr(self, '_camera_current_fid', None)
    if fid is not None:
        try:
            return int(fid)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:684")
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or combo.currentIndex() < 0:
        return None
    try:
        data = combo.currentData()
        return None if data is None else int(data)
    except Exception:
        return None


def _camera_current_feature(self):
    layer = _camera_layer(self)
    fid = _camera_current_fid(self)
    if layer is None or fid is None:
        return None
    try:
        feat = layer.getFeature(int(fid))
        return feat if feat and feat.isValid() else None
    except Exception:
        return None


def _camera_disconnect_layer_runtime_signals(self):
    conns = getattr(self, '_camera_bound_connections', []) or []
    for sig, handler in conns:
        try:
            sig.disconnect(handler)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:713")
    self._camera_bound_connections = []
    self._camera_bound_layer = None


def _camera_connect_layer_runtime_signals(self, layer):
    _camera_disconnect_layer_runtime_signals(self)
    if not isinstance(layer, QgsVectorLayer):
        return
    self._camera_bound_layer = layer
    self._camera_bound_connections = []
    for signal_name, handler in (
        ('selectionChanged', self._camera_on_layer_selection_changed),
        ('geometryChanged', self._camera_on_geometry_changed),
        ('subsetStringChanged', self._camera_on_layer_subset_changed),
        ('attributeValueChanged', self._camera_on_layer_data_changed),
        ('featureAdded', self._camera_on_layer_data_changed),
        ('featuresDeleted', self._camera_on_layer_data_changed),
        ('committedFeaturesAdded', self._camera_on_layer_data_changed),
        ('committedFeaturesRemoved', self._camera_on_layer_data_changed),
        ('committedAttributeValuesChanges', self._camera_on_layer_data_changed),
    ):
        sig = getattr(layer, signal_name, None)
        if sig is None:
            continue
        try:
            sig.connect(handler)
            self._camera_bound_connections.append((sig, handler))
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:742")


def _camera_on_layer_changed(self, layer):
    self._camera_current_fid = None
    self._camera_drafts = {}
    _camera_connect_layer_runtime_signals(self, layer)
    _camera_warn_if_non_metric_project(self, notify=False)
    self._camera_refresh_field_combos(layer)
    self._camera_refresh_feature_list(autoload=True)


def _camera_refresh_field_combos(self, layer=None):
    layer = layer or _camera_layer(self)
    combos = [getattr(self, 'cmb_cam_id_field', None), getattr(self, 'cmb_cam_label_field', None),
              getattr(self, 'cmb_cam_order_field', None), getattr(self, 'cmb_cam_image_field', None)]
    if layer is None:
        for combo in combos:
            if combo is not None:
                combo.blockSignals(True)
                combo.clear()
                combo.blockSignals(False)
        combo_feat = getattr(self, 'cmb_cam_feature', None)
        if combo_feat is not None:
            combo_feat.blockSignals(True)
            combo_feat.clear()
            combo_feat.blockSignals(False)
        _camera_set_status(self, "Aucune couche caméra sélectionnée.")
        _camera_update_title(self, "")
        return

    names = [f.name() for f in layer.fields()]
    prev_id = self.cmb_cam_id_field.currentText() if self.cmb_cam_id_field.count() else ""
    prev_label = self.cmb_cam_label_field.currentText() if self.cmb_cam_label_field.count() else ""
    prev_order = self.cmb_cam_order_field.currentText() if self.cmb_cam_order_field.count() else ""
    prev_img = self.cmb_cam_image_field.currentText() if self.cmb_cam_image_field.count() else ""

    for combo in combos:
        combo.blockSignals(True)
        combo.clear()
        
        if combo in (getattr(self, 'cmb_cam_label_field', None), getattr(self, 'cmb_cam_order_field', None)):
            combo.addItem(tr(''))
        combo.addItems(tr(names))
        combo.blockSignals(False)

    default_id = _camera_first_existing_name(names, ('qcv_id', 'IDPTV', 'idptv', 'id', 'numero', 'num', 'filename', 'name', 'nom'))
    default_label = _camera_first_existing_name(names, ('name', 'nom', 'label', 'libelle', 'libellé', 'description'))
    default_order = _camera_first_existing_name(names, ('numero', 'num', 'ordre', 'order', 'qcv_id', 'IDPTV', 'idptv'))
    default_img = _camera_first_existing_name(names, IMAGE_HINTS)
    id_name = prev_id if prev_id in names else default_id
    label_name = prev_label if prev_label in names else default_label
    order_name = prev_order if prev_order in names else default_order
    img_name = prev_img if prev_img in names else default_img
    
    
    for combo, value in ((self.cmb_cam_id_field, id_name), (self.cmb_cam_label_field, label_name),
                         (self.cmb_cam_order_field, order_name), (self.cmb_cam_image_field, img_name)):
        if value:
            try:
                old = combo.blockSignals(True)
                combo.setCurrentText(value)
                combo.blockSignals(old)
            except Exception:
                try:
                    combo.blockSignals(False)
                except Exception as _qcv_exc:
                    _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:809")
    _camera_set_status(self, f"Couche caméra prête : {layer.name()} ({layer.featureCount()} points environ).")


def _camera_refresh_feature_list(self, autoload=None):
    if autoload is None:
        autoload = bool(getattr(self, '_camera_feature_refresh_autoload', True))
    self._camera_feature_refresh_autoload = False
    layer = _camera_layer(self)
    combo = getattr(self, 'cmb_cam_feature', None)
    if layer is None or combo is None:
        return

    prev_fid = _camera_current_fid(self)
    try:
        selected_ids = [int(fid) for fid in layer.selectedFeatureIds()]
    except Exception:
        selected_ids = []

    features = []
    try:
        order_field = ''
        try: order_field = str(self.cmb_cam_order_field.currentText() or '').strip()
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:833")
        id_field = ''
        try: id_field = str(self.cmb_cam_id_field.currentText() or '').strip()
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:836")
        names = layer.fields().names()
        for feat in layer.getFeatures():
            label = _camera_list_label(self, layer, feat)
            raw = None
            for fld in (order_field, id_field):
                if fld and fld in names:
                    try:
                        raw = feat[fld]
                        if raw not in (None, NULL, ''): break
                    except Exception:
                        raw = None
            if raw in (None, NULL, ''):
                raw = int(feat.id())
            try:
                sort_key = (0, float(raw), int(feat.id()))
            except Exception:
                sort_key = (1, str(raw).lower(), int(feat.id()))
            features.append((sort_key, label, int(feat.id())))
    except Exception:
        features = []

    combo.blockSignals(True)
    combo.clear()
    for _sort_key, label, fid in sorted(features, key=lambda x: x[0]):
        combo.addItem(tr(label), fid)
    combo.blockSignals(False)

    if combo.count() <= 0:
        self._camera_current_fid = None
        _camera_set_status(self, "Aucun point de vue trouvé dans la couche caméra (filtre QGIS actif ?).", "#aa6600")
        _camera_update_title(self, "")
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:870")
        return

    preferred_fid = selected_ids[0] if selected_ids else prev_fid
    idx = combo.findData(int(preferred_fid)) if preferred_fid is not None else -1
    if idx < 0:
        idx = 0
    combo.blockSignals(True)
    combo.setCurrentIndex(idx)
    combo.blockSignals(False)
    try:
        self._camera_current_fid = int(combo.itemData(idx))
    except Exception:
        self._camera_current_fid = None

    if autoload:
        self._camera_on_feature_changed(idx)
    else:
        feat = _camera_current_feature(self)
        if feat is None:
            _camera_update_title(self, "")
            return
        title = _camera_resolve_title(self, layer, feat)
        img_path = _camera_feature_image_path(self, layer, feat)
        suffix = "image renseignée" if img_path else "vue schématique"
        _camera_update_title(self, title)
        _camera_set_status(self, f"Point courant : {title} — {suffix}.", "#666" if img_path else "#aa6600")
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:900")
    try:
        if getattr(self, 'viewer', None):
            self.viewer.sync_pdv_controls()
            self.viewer.update_info()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:906")
    
    
    
    try:
        if (not getattr(self, '_ui_initializing', False)
                and bool(getattr(self, '_export_tab_loaded', False))
                and hasattr(self, '_refresh_batch_pdv_table')):
            self._refresh_batch_pdv_table()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:916")


def _camera_select_combo_feature_by_fid(self, fid, autoload=True):
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or fid is None:
        return False
    idx = combo.findData(int(fid))
    if idx < 0:
        return False
    combo.blockSignals(True)
    combo.setCurrentIndex(idx)
    combo.blockSignals(False)
    self._camera_current_fid = int(fid)
    if autoload:
        self._camera_on_feature_changed(idx)
    else:
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:936")
    return True


def _camera_select_layer_feature(self, fid):
    
    return


def _camera_on_layer_selection_changed(self, *args):
    if getattr(self, '_camera_sync_guard', False):
        return
    layer = _camera_layer(self)
    if layer is None:
        return
    try:
        selected = [int(fid) for fid in layer.selectedFeatureIds()]
    except Exception:
        selected = []
    if not selected:
        return
    prev = getattr(self, '_camera_sync_guard', False)
    self._camera_sync_guard = True
    try:
        _camera_select_combo_feature_by_fid(self, int(selected[0]), autoload=True)
        try:
            layer.removeSelection()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:964")
    finally:
        self._camera_sync_guard = prev


def _camera_on_layer_subset_changed(self, *args):
    self._camera_refresh_feature_list(autoload=True)


def _camera_schedule_feature_refresh(self, autoload=False):
    if getattr(self, '_camera_internal_write', False) or getattr(self, '_camera_loading_feature', False):
        return
    try:
        self._camera_feature_refresh_autoload = bool(autoload) or bool(getattr(self, '_camera_feature_refresh_autoload', False))
        self._camera_feature_refresh_timer.start()
    except Exception:
        self._camera_refresh_feature_list(autoload=autoload)


def _camera_on_layer_data_changed(self, *args):
    if getattr(self, '_camera_internal_write', False) or getattr(self, '_camera_loading_feature', False):
        return
    self._camera_schedule_feature_refresh(autoload=False)


def _resolve_image_path(layer, raw_path):
    raw = str(raw_path or "").strip().strip('"')
    if not raw:
        return None
    if os.path.isabs(raw) and os.path.exists(raw):
        return os.path.normpath(raw)
    candidates = []
    try:
        home = QgsProject.instance().homePath()
        if home:
            candidates.append(os.path.join(home, raw))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1001")
    try:
        src = str(layer.source()).split('|')[0]
        if src:
            candidates.append(os.path.join(os.path.dirname(src), raw))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1007")
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.normpath(candidate)
    return raw if os.path.exists(raw) else None




def _camera_set_live_enabled(self, enabled):
    self._camera_live_enabled = bool(enabled)
    try:
        if getattr(self, 'viewer', None) and getattr(self.viewer, '_chk_live', None):
            self.viewer._chk_live.blockSignals(True)
            self.viewer._chk_live.setChecked(bool(enabled))
            self.viewer._chk_live.blockSignals(False)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1024")


def _camera_on_geometry_changed(self, fid, *args):
    
    if not bool(getattr(self, '_camera_live_enabled', False)):
        return
    try:
        fid = int(fid)
        current = _camera_current_fid(self)
        if current is None or fid != int(current):
            return
    except Exception:
        return
    self._camera_live_pending_fid = fid
    try: self._camera_live_refresh_timer.start(250)
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1041")


def _camera_live_refresh_current(self):
    if not bool(getattr(self, '_camera_live_enabled', False)):
        return
    _camera_refresh_current_view(self, force=False)


def _camera_refresh_current_view(self, force=False):
    
    try:
        getattr(self, '_overlay_cache', {}).clear()
        getattr(self, '_geom_cache', {}).clear()
        self._horizon = None
        self._horizon_params = None
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1057")
    try: self._update_canvas_fov()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1060")
    
    
    try: self.render_preview()
    except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1064")
    try:
        if getattr(self, 'viewer', None):
            self.viewer.update_info()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1068")


def _camera_prev_feature(self):
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or combo.count() <= 0:
        return
    combo.setCurrentIndex(max(0, combo.currentIndex() - 1))


def _camera_next_feature(self):
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or combo.count() <= 0:
        return
    combo.setCurrentIndex(min(combo.count() - 1, combo.currentIndex() + 1))


def _camera_feature_image_path(self, layer, feat):
    
    mode = _camera_feature_view_mode(self, layer, feat)
    if mode == 'SCHEMA':
        return None

    names = feat.fields().names()
    candidates = []
    draft = None
    try:
        fid = int(feat.id())
        draft = (getattr(self, '_camera_drafts', {}) or {}).get(fid)
    except Exception:
        draft = None

    
    
    if isinstance(draft, dict) and draft.get('qcv_img') not in (None, ''):
        candidates.append(draft.get('qcv_img'))

    val_qcv = _camera_feature_value(layer, feat, 'qcv_img', None)
    if val_qcv not in (None, ''):
        candidates.append(val_qcv)

    combo = getattr(self, 'cmb_cam_image_field', None)
    img_field = combo.currentText().strip() if combo is not None else ""
    if img_field and img_field in names:
        try:
            val = feat[img_field]
            if val not in (None, ''):
                candidates.append(val)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1117")

    
    
    for fld in ('photo', 'image', 'img', 'path', 'file'):
        if fld in names:
            try:
                val = feat[fld]
                if val not in (None, ''):
                    candidates.append(val)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1128")
    if 'directory' in names and 'filename' in names:
        try:
            directory = feat['directory']
            filename = feat['filename']
            if directory and filename:
                candidates.extend([
                    os.path.join(str(directory), str(filename)),
                    os.path.join(str(directory), str(filename) + '.JPG'),
                    os.path.join(str(directory), str(filename) + '.jpg'),
                    os.path.join(str(directory), str(filename) + '.JPEG'),
                    os.path.join(str(directory), str(filename) + '.jpeg'),
                    os.path.join(str(directory), str(filename) + '.PNG'),
                    os.path.join(str(directory), str(filename) + '.png'),
                ])
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1144")

    seen = set()
    for cand in candidates:
        key = str(cand or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        path = _resolve_image_path(layer, cand)
        if path:
            return path
    return None


def _camera_load_photo_from_path(self, path):
    path = str(path)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path)
    working_image, source_info = load_working_image(path)
    self.photo_path = path
    self.image = working_image
    self._photo_source_info = dict(source_info or {})
    self._photo_is_proxy = bool(self._photo_source_info.get('proxy', False))
    self._camera_current_photo_path = path
    photo_meta = _camera_read_photo_metadata(path)
    try:
        self._camera_last_photo_meta = dict(photo_meta)
    except Exception:
        self._camera_last_photo_meta = photo_meta
    try:
        self._base_cache.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1176")
    try:
        self._z_cache.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1180")
    try:
        self._horizon = None
        self._horizon_params = None
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1185")
    try:
        getattr(self, '_overlay_cache', {}).clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1189")
    try:
        getattr(self, '_geom_cache', {}).clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1193")
    try:
        self._layer_cache_versions = {}
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1197")
    try:
        _camera_apply_photo_metadata_to_ui(self, photo_meta)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1201")
    try:
        if getattr(self, 'viewer', None):
            
            self.viewer.update_image(self.image)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1207")
    return photo_meta


def _camera_state_from_feature(self, layer, feat, photo_meta=None):
    
    defaults = _camera_default_state(self)
    photo_meta = photo_meta or {}
    z_geom = _camera_geom_z(feat)
    alt_default = defaults.get('qcv_alt', 1.7)

    img_w = _camera_feature_value(layer, feat, 'qcv_iw', None)
    img_h = _camera_feature_value(layer, feat, 'qcv_ih', None)
    stored_foc = _camera_feature_value(layer, feat, 'qcv_foc', None)
    stored_sens = _camera_feature_value(layer, feat, 'qcv_sens', None)
    auto_hfov = int(_camera_feature_value(layer, feat, 'qcv_ahf', defaults.get('qcv_ahf', 1)) or 0)

    proj_default = _camera_projection_from_meta(photo_meta, defaults.get('qcv_proj'))
    img_w = photo_meta.get('ImageWidth') if img_w in (None, '') else img_w
    img_h = photo_meta.get('ImageHeight') if img_h in (None, '') else img_h

    def _pos(v):
        try:
            return float(v) > 0.0
        except Exception:
            return False

    
    
    meta_foc = photo_meta.get('FocalLength')
    meta_sens = photo_meta.get('SensorWidthMM')
    meta_f35 = photo_meta.get('FocalLength35mmEq')
    photo_foc = photo_sens = None
    if _pos(meta_foc) and _pos(meta_sens):
        photo_foc, photo_sens = float(meta_foc), float(meta_sens)
    elif _pos(meta_f35):
        photo_foc, photo_sens = float(meta_f35), 36.0

    if auto_hfov and _pos(photo_foc) and _pos(photo_sens):
        
        foc, sens = photo_foc, photo_sens
    else:
        foc = float(stored_foc) if _pos(stored_foc) else (photo_foc if _pos(photo_foc) else None)
        sens = float(stored_sens) if _pos(stored_sens) else (photo_sens if _pos(photo_sens) else None)

    state = {
        'qcv_mode': _camera_feature_view_mode(self, layer, feat),
        'qcv_proj': _camera_feature_value(layer, feat, 'qcv_proj', proj_default),
        'qcv_360': int(_camera_feature_value(layer, feat, 'qcv_360', defaults.get('qcv_360', 0)) or 0),
        'qcv_yaw': _normalize_azimuth_360(_camera_feature_value(layer, feat, 'qcv_yaw', defaults.get('qcv_yaw', 0.0)) or 0.0),
        'qcv_pitch': float(_camera_feature_value(layer, feat, 'qcv_pitch', defaults.get('qcv_pitch', 0.0)) or 0.0),
        'qcv_roll': float(_camera_feature_value(layer, feat, 'qcv_roll', defaults.get('qcv_roll', 0.0)) or 0.0),
        'qcv_hfov': float(_camera_feature_value(layer, feat, 'qcv_hfov', defaults.get('qcv_hfov', 60.0)) or 60.0),
        'qcv_vfov': float(_camera_feature_value(layer, feat, 'qcv_vfov', defaults.get('qcv_vfov', 40.0)) or 40.0),
        'qcv_ahf': auto_hfov,
        'qcv_alt': float(_camera_feature_value(layer, feat, 'qcv_alt', z_geom if z_geom is not None else alt_default) or (z_geom if z_geom is not None else alt_default)),
        'qcv_ofh': float(_camera_feature_value(layer, feat, 'qcv_ofh', defaults.get('qcv_ofh', 0.0)) or 0.0),
        'qcv_ofv': float(_camera_feature_value(layer, feat, 'qcv_ofv', defaults.get('qcv_ofv', 0.0)) or 0.0),
        'qcv_ofmd': int(_camera_feature_value(layer, feat, 'qcv_ofmd', defaults.get('qcv_ofmd', 0)) or 0),
        'qcv_mdst': float(_camera_feature_value(layer, feat, 'qcv_mdst', defaults.get('qcv_mdst', 0.0)) or 0.0),
    }
    if isinstance(img_w, (int, float)) and int(img_w) > 0:
        state['qcv_iw'] = int(img_w)
    if isinstance(img_h, (int, float)) and int(img_h) > 0:
        state['qcv_ih'] = int(img_h)
    if _pos(foc):
        state['qcv_foc'] = float(foc)
    if _pos(sens):
        state['qcv_sens'] = float(sens)

    if auto_hfov and (not _pos(foc) or not _pos(sens)):
        
        state['qcv_ahf'] = 0

    img_path = _camera_feature_image_path(self, layer, feat)
    if img_path:
        state['qcv_img'] = img_path
    return state


def _camera_apply_state(self, state):
    widgets = [
        self.cmb_proj, self.cb_360, self.d_yaw, self.d_pitch, self.d_roll,
        self.cb_auto_hfov, self.d_hfov, self.d_vfov, self.d_camheight, self.spin_off_h, self.spin_off_v,
        self.cmb_off_mode, self.d_maxdist, self.spin_w, self.spin_h, self.d_focal, self.d_sensorw,
    ]
    for w in widgets:
        try:
            w.blockSignals(True)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1297")
    try:
        proj = str(state.get('qcv_proj', self.cmb_proj.currentText()) or self.cmb_proj.currentText())
        idx = self.cmb_proj.findText(proj)
        if idx >= 0:
            self.cmb_proj.setCurrentIndex(idx)
        proj_upper = str(proj).strip().upper()
        full_equirect = bool(
            proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR')
            and int(_camera_pick_state_value(state, 'qcv_360', 0) or 0)
        )
        self.cb_360.setEnabled(proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR'))
        self.cb_360.setChecked(full_equirect)
        self.d_yaw.setValue(_normalize_azimuth_360(_camera_pick_state_value(state, 'qcv_yaw', self.d_yaw.value()), fallback=self.d_yaw.value()))
        self.d_pitch.setValue(float(_camera_pick_state_value(state, 'qcv_pitch', self.d_pitch.value())))
        self.d_roll.setValue(float(_camera_pick_state_value(state, 'qcv_roll', self.d_roll.value())))
        self.cb_auto_hfov.setChecked(bool(int(_camera_pick_state_value(state, 'qcv_ahf', 1) or 0)))
        self.d_hfov.setValue(float(_camera_pick_state_value(state, 'qcv_hfov', self.d_hfov.value())))
        self.d_vfov.setValue(float(_camera_pick_state_value(state, 'qcv_vfov', self.d_vfov.value())))
        self.d_camheight.setValue(float(_camera_pick_state_value(state, 'qcv_alt', self.d_camheight.value())))
        self.spin_off_h.setValue(float(_camera_pick_state_value(state, 'qcv_ofh', self.spin_off_h.value())))
        self.spin_off_v.setValue(float(_camera_pick_state_value(state, 'qcv_ofv', self.spin_off_v.value())))
        self.cmb_off_mode.setCurrentIndex(int(_camera_pick_state_value(state, 'qcv_ofmd', self.cmb_off_mode.currentIndex())))
        self.d_maxdist.setValue(float(_camera_pick_state_value(state, 'qcv_mdst', self.d_maxdist.value())))
        self.spin_w.setValue(int(_camera_pick_state_value(state, 'qcv_iw', self.spin_w.value())))
        self.spin_h.setValue(int(_camera_pick_state_value(state, 'qcv_ih', self.spin_h.value())))
        self.d_focal.setValue(float(_camera_pick_state_value(state, 'qcv_foc', self.d_focal.value())))
        self.d_sensorw.setValue(float(_camera_pick_state_value(state, 'qcv_sens', self.d_sensorw.value())))
        self._camera_current_view_mode = _camera_normalize_view_mode(
            state.get('qcv_mode', getattr(self, '_camera_current_view_mode', 'AUTO'))
        )
    finally:
        for w in widgets:
            try:
                w.blockSignals(False)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1333")
    try:
        self._toggle_hfov_enable(self.cb_auto_hfov.isChecked())
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1337")
    
    
    
    try:
        proj_upper = str(self.cmb_proj.currentText()).strip().upper()
        if proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR'):
            full_equirect = bool(self.cb_360.isChecked())
            self.d_hfov.setEnabled(not full_equirect)
            self.d_vfov.setEnabled(not full_equirect)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1348")


def _camera_collect_ui_state(self):
    state = {
        'qcv_proj': str(self.cmb_proj.currentText()).strip(),
        'qcv_360': 1 if (str(self.cmb_proj.currentText()).strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR') and self.cb_360.isChecked()) else 0,
        'qcv_yaw': _normalize_azimuth_360(self.d_yaw.value()),
        'qcv_pitch': float(self.d_pitch.value()),
        'qcv_roll': float(self.d_roll.value()),
        'qcv_hfov': float(self.d_hfov.value()),
        'qcv_vfov': float(self.d_vfov.value()),
        'qcv_ahf': 1 if self.cb_auto_hfov.isChecked() else 0,
        'qcv_alt': float(self.d_camheight.value()),
        'qcv_ofh': float(self.spin_off_h.value()),
        'qcv_ofv': float(self.spin_off_v.value()),
        'qcv_ofmd': int(self.cmb_off_mode.currentIndex()),
        'qcv_mdst': float(self.d_maxdist.value()),
        'qcv_iw': int(self.spin_w.value()),
        'qcv_ih': int(self.spin_h.value()),
        'qcv_foc': float(self.d_focal.value()),
        'qcv_sens': float(self.d_sensorw.value()),
        'qcv_upd': datetime.now().isoformat(timespec='seconds'),
    }
    mode = _camera_normalize_view_mode(getattr(self, '_camera_current_view_mode', 'AUTO'))
    state['qcv_mode'] = mode
    photo_path = getattr(self, '_camera_current_photo_path', None)
    if mode == 'SCHEMA':
        
        
        state['qcv_img'] = None
    elif photo_path:
        state['qcv_img'] = photo_path
    return state


def _camera_capture_current_draft(self, fid=None):
    fid = _camera_current_fid(self) if fid is None else fid
    if fid is None:
        return None
    try:
        fid = int(fid)
    except Exception:
        return None
    drafts = getattr(self, '_camera_drafts', None)
    if drafts is None:
        self._camera_drafts = {}
        drafts = self._camera_drafts
    state = _camera_collect_ui_state(self)
    drafts[fid] = dict(state)
    return drafts[fid]


def _camera_load_current_feature(self, auto=False):
    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    if layer is None or feat is None:
        _camera_set_status(self, "Aucun point de vue à charger.", "#aa6600")
        _camera_update_title(self, "")
        return

    self._camera_loading_feature = True
    try:
        view_mode = _camera_feature_view_mode(self, layer, feat)
        self._camera_current_view_mode = view_mode
        path = _camera_feature_image_path(self, layer, feat)
        photo_meta = {}
        if path:
            try:
                cur = os.path.normcase(os.path.normpath(str(getattr(self, 'photo_path', '') or '')))
                tgt = os.path.normcase(os.path.normpath(str(path)))
                same_path = cur == tgt
            except Exception:
                same_path = False
            image_missing = getattr(self, 'image', None) is None or getattr(self, 'image', None).isNull()
            if (not auto) or (not same_path) or image_missing:
                try:
                    photo_meta = _camera_load_photo_from_path(self, path) or {}
                except Exception as e:
                    if not auto:
                        QMessageBox.warning(self, tr('QCALVIEW'), tr(f"Impossible de charger l’image :\n{e}"))
            else:
                photo_meta = dict(getattr(self, '_camera_last_photo_meta', {}) or {})
            self._camera_current_photo_path = path
        else:
            
            
            
            try:
                self._activate_schematic_view()
            except Exception:
                self._camera_current_photo_path = None
                self.photo_path = None
                self.image = None

        base_state = _camera_state_from_feature(self, layer, feat, photo_meta=photo_meta)
        draft = (getattr(self, '_camera_drafts', {}) or {}).get(int(feat.id()))
        state = dict(base_state)
        if draft:
            state.update(draft)
        state['qcv_mode'] = _camera_normalize_view_mode(state.get('qcv_mode', view_mode))
        if path and not state.get('qcv_img'):
            state['qcv_img'] = path

        _camera_apply_state(self, state)
        try:
            self._camera_last_loaded_fid = int(feat.id())
        except Exception:
            self._camera_last_loaded_fid = None
        title = _camera_resolve_title(self, layer, feat)
        _camera_update_title(self, title)
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1462")
    finally:
        self._camera_loading_feature = False

    
    
    
    try:
        self.render_preview()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1472")
    try:
        if getattr(self, 'viewer', None):
            if path:
                try:
                    cur = getattr(getattr(self.viewer, '_pix', None), 'pixmap', lambda: None)()
                    cur = cur if cur is not None else None
                    needs_image = (cur is None) or cur.isNull()
                except Exception:
                    needs_image = True
                if needs_image:
                    self.viewer.update_image(path)
            else:
                try:
                    self.viewer.update_image(self._make_schematic_base(
                        int(self.spin_w.value()), int(self.spin_h.value()), for_export=False))
                except Exception as _qcv_exc:
                    _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1489")
            ov = getattr(self, 'overlay_image', None) or getattr(self, 'overlay_path', None)
            if ov is not None:
                self.viewer.update_overlay(ov)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1494")
    status = f"Point chargé : {title}"
    mode_now = _camera_normalize_view_mode(getattr(self, '_camera_current_view_mode', view_mode))
    if path:
        status += f" — {os.path.basename(path)}"
    elif mode_now == 'PHOTO':
        status += " — photo associée introuvable : affichage schématique provisoire"
    else:
        status += " — vue schématique (sans photo)"
    try:
        if int(getattr(self, '_camera_last_visual_restore_fid', -999999)) == int(feat.id()):
            vs = str(getattr(self, '_camera_last_visual_restore_summary', '') or '')
            if vs and vs != 'aucun état visuel enregistré':
                status += f" — {vs}"
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1509")
    _camera_set_status(self, status, "#2b6" if path else ("#b36b00" if mode_now == 'PHOTO' else "#386a8a"))


def _camera_make_storable_image_path(layer, path):
    path = os.path.normpath(str(path or '').strip())
    if not path:
        return ""
    bases = []
    try:
        home = QgsProject.instance().homePath()
        if home:
            bases.append(os.path.normpath(home))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1523")
    try:
        src = str(layer.source()).split('|')[0]
        if src:
            bases.append(os.path.normpath(os.path.dirname(src)))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1529")
    for base in bases:
        try:
            rel = os.path.relpath(path, base)
            if rel and not rel.startswith('..'):
                return rel.replace('\\', '/')
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1536")
    return path


def _camera_assign_current_photo_path(self, path):
    if not path:
        return False
    self._camera_current_view_mode = 'PHOTO'
    self._camera_current_photo_path = str(path)
    _camera_capture_current_draft(self)
    try:
        self._sync_pdv_qml()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1549")
    return True


def _camera_write_source_fields(self, mode, image_path=None, silent=False):
    
    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    if layer is None or feat is None:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        return False
    if not _camera_ensure_fields(self):
        return False
    fid = int(feat.id())
    mode = _camera_normalize_view_mode(mode)
    stored = None
    if mode == 'PHOTO' and image_path:
        stored = _camera_make_storable_image_path(layer, image_path)

    started = False
    if not layer.isEditable():
        try:
            started = bool(layer.startEditing())
        except Exception:
            started = False

    changed = False
    prev_write = getattr(self, '_camera_internal_write', False)
    self._camera_internal_write = True
    try:
        for key, value in (('qcv_mode', mode), ('qcv_img', stored)):
            name = _camera_field_name(layer, key)
            if name not in layer.fields().names():
                continue
            idx = layer.fields().indexOf(name)
            write_value = NULL if (key == 'qcv_img' and value in (None, '')) else value
            try:
                ok = layer.changeAttributeValue(fid, idx, write_value)
            except Exception:
                
                
                ok = layer.changeAttributeValue(fid, idx, '' if key == 'qcv_img' else value)
            changed = bool(ok) or changed
        if started:
            try:
                changed = bool(layer.commitChanges()) or changed
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1596")
    finally:
        self._camera_internal_write = prev_write

    drafts = getattr(self, '_camera_drafts', {}) or {}
    draft = dict(drafts.get(fid) or _camera_collect_ui_state(self))
    draft['qcv_mode'] = mode
    draft['qcv_img'] = image_path if mode == 'PHOTO' and image_path else None
    drafts[fid] = draft
    self._camera_drafts = drafts
    if not silent:
        _camera_set_status(self,
            "Source enregistrée : photographie." if mode == 'PHOTO' else
            ("Source enregistrée : vue schématique sans photo." if mode == 'SCHEMA' else
             "Source enregistrée : détection automatique depuis le champ image."),
            "#2b6" if mode == 'PHOTO' else "#386a8a")
    return changed


def _camera_set_current_schematic(self):
    
    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        return
    self._camera_current_view_mode = 'SCHEMA'
    try:
        self._activate_schematic_view()
    except Exception:
        self.image = None
        self.photo_path = None
        self._camera_current_photo_path = None
    
    self._camera_current_view_mode = 'SCHEMA'
    _camera_capture_current_draft(self, feat.id())
    _camera_write_source_fields(self, 'SCHEMA', None, silent=True)
    try:
        if getattr(self, 'viewer', None):
            self.viewer.update_image(self._make_schematic_base(
                int(self.spin_w.value()), int(self.spin_h.value()), for_export=False))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1637")
    try:
        self.render_preview()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1641")
    title = _camera_resolve_title(self, _camera_layer(self), feat)
    _camera_set_status(self, f"{title} — vue schématique enregistrée (sans photo).", "#386a8a")


def _camera_use_auto_image_source(self):
    
    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    if layer is None or feat is None:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        return
    self._camera_current_view_mode = 'AUTO'
    drafts = getattr(self, '_camera_drafts', {}) or {}
    draft = dict(drafts.get(int(feat.id())) or _camera_collect_ui_state(self))
    draft['qcv_mode'] = 'AUTO'
    draft['qcv_img'] = None
    drafts[int(feat.id())] = draft
    self._camera_drafts = drafts
    _camera_write_source_fields(self, 'AUTO', None, silent=True)
    _camera_load_current_feature(self, auto=False)


def _camera_associate_photo(self):
    
    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        return
    before = getattr(self, '_camera_current_photo_path', None)
    try:
        ok = self.load_photo()
    except Exception:
        ok = False
    after = getattr(self, '_camera_current_photo_path', None)
    if ok is False or not after:
        
        
        if not after or after == before:
            return
    self._camera_current_view_mode = 'PHOTO'
    _camera_capture_current_draft(self, feat.id())
    _camera_write_source_fields(self, 'PHOTO', after, silent=True)
    title = _camera_resolve_title(self, _camera_layer(self), feat)
    _camera_set_status(self, f"{title} — photographie associée : {os.path.basename(after)}", "#2b6")


def _camera_schedule_autosave(self, *_args):
    
    if getattr(self, '_camera_loading_feature', False):
        return
    
    
    if bool(getattr(self, '_render_edit_widgets', set())):
        self._camera_autosave_dirty = True
        return
    try:
        _camera_capture_current_draft(self)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1700")
    self._camera_autosave_dirty = False
    try:
        self._camera_autosave_timer.stop()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1705")
    try:
        self._sync_pdv_qml()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1709")


def _camera_autosave_timeout(self):
    self._camera_autosave_dirty = False
    return


def _camera_ensure_fields(self):
    layer = _camera_layer(self)
    if layer is None:
        QMessageBox.information(self, tr('QCALVIEW'), tr("Choisissez d’abord une couche caméra."))
        return False
    existing_lower = {f.name().lower() for f in layer.fields()}
    new_fields = []
    for key, (typ, length, precision, aliases) in FIELD_SPECS.items():
        if any(alias.lower() in existing_lower for alias in aliases):
            continue
        fld = QgsField(key, typ)
        if length:
            fld.setLength(int(length))
        if precision:
            fld.setPrecision(int(precision))
        new_fields.append(fld)
    if not new_fields:
        _camera_set_status(self, "Les champs QCALVIEW existent déjà pour la couche caméra.", "#2b6")
        return True
    ok = False
    try:
        ok = bool(layer.dataProvider().addAttributes(new_fields))
        if ok:
            layer.updateFields()
            self._camera_refresh_field_combos(layer)
            _camera_set_status(self, "Champs QCALVIEW ajoutés à la couche caméra.", "#2b6")
        else:
            _camera_set_status(self, "Impossible d’ajouter les champs QCALVIEW.", "#b33")
    except Exception as e:
        QMessageBox.warning(self, tr('QCALVIEW'), tr(f"Impossible d’ajouter les champs QCALVIEW :\n{e}"))
        ok = False
    return ok


def _camera_save_feature_by_fid(self, fid, silent=False):
    layer = _camera_layer(self)
    if layer is None or fid is None:
        return False
    try:
        fid = int(fid)
        feat = layer.getFeature(fid)
    except Exception:
        feat = None
    if feat is None or not feat.isValid():
        return False
    
    if not _camera_ensure_fields(self):
        return False

    drafts = getattr(self, '_camera_drafts', {}) or {}
    state = dict(drafts.get(fid) or _camera_collect_ui_state(self))
    title = _camera_resolve_title(self, layer, feat)
    if not _camera_feature_value(layer, feat, 'qcv_id', None):
        state['qcv_id'] = title
    mode = _camera_normalize_view_mode(state.get('qcv_mode', getattr(self, '_camera_current_view_mode', 'AUTO')))
    state['qcv_mode'] = mode
    img_path = state.get('qcv_img') or getattr(self, '_camera_current_photo_path', None)
    if mode == 'SCHEMA':
        state['qcv_img'] = None
    elif img_path:
        state['qcv_img'] = _camera_make_storable_image_path(layer, img_path)
    else:
        
        
        state['qcv_img'] = None
    state['qcv_upd'] = datetime.now().isoformat(timespec='seconds')

    started = False
    if not layer.isEditable():
        try:
            started = bool(layer.startEditing())
        except Exception:
            started = False

    changed = False
    prev_write = getattr(self, '_camera_internal_write', False)
    self._camera_internal_write = True
    try:
        for key, value in state.items():
            name = _camera_field_name(layer, key)
            if name not in layer.fields().names():
                continue
            try:
                idx = layer.fields().indexOf(name)
                write_value = NULL if (key == 'qcv_img' and value in (None, '')) else value
                ok = layer.changeAttributeValue(fid, idx, write_value)
                changed = bool(ok) or changed
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1805")
        if started:
            try:
                changed = bool(layer.commitChanges()) or changed
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1810")
    finally:
        self._camera_internal_write = prev_write

    
    
    
    drafts.pop(fid, None)
    self._camera_drafts = drafts
    if changed and not silent:
        _camera_set_status(self, f"Paramètres enregistrés pour {title}.", "#2b6")
    try:
        self._sync_pdv_qml()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1824")
    return changed


def _camera_save_current_feature(self):
    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, "Aucun point de vue à enregistrer.", "#aa6600")
        return
    _camera_capture_current_draft(self, feat.id())
    _camera_save_feature_by_fid(self, int(feat.id()), silent=False)
    try:
        ok_state = _camera_capture_visual_state(self, int(feat.id()))
        title = _camera_resolve_title(self, _camera_layer(self), feat)
        summary = str(getattr(self, '_camera_last_visual_capture_summary', '') or '')
        if ok_state:
            _camera_set_status(self, f"Paramètres et {summary} pour {title}.", "#2b6")
        else:
            _camera_set_status(self, f"Paramètres enregistrés pour {title}, mais {summary}.", "#b36b00")
    except Exception:
        _camera_set_status(self, "Paramètres enregistrés, mais l’état visuel n’a pas pu être capturé.", "#b36b00")


def _camera_on_feature_changed(self, *_):
    layer = _camera_layer(self)
    if layer is None:
        _camera_set_status(self, "Aucune couche caméra sélectionnée.", "#aa6600")
        _camera_update_title(self, "")
        return
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or combo.currentIndex() < 0:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        _camera_update_title(self, "")
        return

    new_fid = combo.currentData()
    try:
        new_fid = int(new_fid) if new_fid is not None else None
    except Exception:
        new_fid = None
    old_fid = getattr(self, '_camera_current_fid', None)
    if old_fid is not None and new_fid is not None and old_fid != new_fid and not getattr(self, '_camera_loading_feature', False):
        try:
            _camera_capture_current_draft(self, old_fid)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1869")
    self._camera_current_fid = new_fid
    if new_fid is not None:
        try: _camera_restore_visual_state(self, new_fid)
        except Exception as _qcv_exc: _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1874")

    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, "Aucun point de vue actif.", "#aa6600")
        _camera_update_title(self, "")
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1882")
        return

    title = _camera_resolve_title(self, layer, feat)
    mode = _camera_feature_view_mode(self, layer, feat)
    path = _camera_feature_image_path(self, layer, feat)
    _camera_update_title(self, title)
    if path:
        source_txt, color = 'image renseignée', '#666'
    elif mode == 'PHOTO':
        source_txt, color = 'photo introuvable', '#b36b00'
    else:
        source_txt, color = 'vue schématique', '#386a8a'
    _camera_set_status(self, f"Point courant : {title} — {source_txt}.", color)
    self._camera_load_current_feature(auto=True)
    try:
        if getattr(self, 'viewer', None):
            self.viewer.sync_pdv_controls()
            self.viewer.update_info()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1902")
