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
        'qgis_label_is_expression','qgis_label_expr','qgis_dash_pattern'
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



def _overlay_scope_token(self):
    """Stable project-entry scope for overlay state, isolated per camera layer."""
    try:
        layer = _camera_layer(self)
        lid = str(layer.id()) if layer is not None else 'default'
    except Exception:
        lid = 'default'
    return ''.join(ch if (ch.isalnum() or ch in '_-') else '_' for ch in lid)


def _overlay_global_layers_key(self):
    return f'/overlay_state/{_overlay_scope_token(self)}/global_layers'


def _overlay_global_styles_key(self):
    return f'/overlay_state/{_overlay_scope_token(self)}/global_styles'


def _overlay_read_global_layer_ids(self):
    try:
        raw, found = QgsProject.instance().readEntry('QCALVIEW', _overlay_global_layers_key(self), '')
        if not found or not raw:
            return []
        vals = json.loads(str(raw))
        if not isinstance(vals, list):
            return []
        out = []
        for v in vals:
            sv = str(v or '').strip()
            if sv and sv not in out:
                out.append(sv)
        return out
    except Exception as exc:
        qcv_log(f"Unable to read global layers: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return []


def _overlay_write_global_layer_ids(self, layer_ids):
    out = []
    for v in list(layer_ids or []):
        sv = str(v or '').strip()
        if sv and sv not in out:
            out.append(sv)
    try:
        ok = bool(QgsProject.instance().writeEntry(
            'QCALVIEW', _overlay_global_layers_key(self),
            json.dumps(out, ensure_ascii=False, separators=(',', ':'))
        ))
        if ok:
            QgsProject.instance().setDirty(True)
        return ok
    except Exception as exc:
        qcv_log(f"Unable to write global layers: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return False


def _overlay_read_global_styles(self):
    try:
        raw, found = QgsProject.instance().readEntry('QCALVIEW', _overlay_global_styles_key(self), '')
        if not found or not raw:
            return {}
        vals = json.loads(str(raw))
        return vals if isinstance(vals, dict) else {}
    except Exception as exc:
        qcv_log(f"Unable to read global styles: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return {}


def _overlay_write_global_styles(self, styles):
    try:
        clean = styles if isinstance(styles, dict) else {}
        ok = bool(QgsProject.instance().writeEntry(
            'QCALVIEW', _overlay_global_styles_key(self),
            json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
        ))
        if ok:
            QgsProject.instance().setDirty(True)
        return ok
    except Exception as exc:
        qcv_log(f"Unable to write global styles: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return False


def _overlay_style_payload_global(sty):
    data = _qcv_style_to_dict(sty)
    # Visibility and native-theme references are viewpoint-scoped. Everything
    # else is the QCALVIEW layer style and is shared between viewpoints.
    for key in ('visible', 'qgis_theme_name', 'qgis_theme_style_name'):
        data.pop(key, None)
    return data


def _overlay_store_global_style(self, sty):
    try:
        layer = getattr(sty, 'layer', None)
        lid = str(layer.id()) if layer is not None else ''
        if not lid:
            return False
        styles = _overlay_read_global_styles(self)
        styles[lid] = _overlay_style_payload_global(sty)
        return _overlay_write_global_styles(self, styles)
    except Exception as exc:
        qcv_log(f"Global style not saved: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return False


def _overlay_apply_global_style(self, sty, fallback_data=None):
    """Apply the global style of sty.layer while keeping PDV visibility/theme refs."""
    try:
        layer = getattr(sty, 'layer', None)
        lid = str(layer.id()) if layer is not None else ''
        if not lid:
            return sty
        styles = _overlay_read_global_styles(self)
        gdata = styles.get(lid)
        if not isinstance(gdata, dict):
            if isinstance(fallback_data, dict):
                # Migrate the currently loaded layer style when it is first encountered.
                # to the new global-style model.
                seed = dict(fallback_data)
                for key in ('visible', 'qgis_theme_name', 'qgis_theme_style_name'):
                    seed.pop(key, None)
                styles[lid] = seed
                _overlay_write_global_styles(self, styles)
                gdata = seed
            else:
                _overlay_store_global_style(self, sty)
                return sty
        visible = bool(getattr(sty, 'visible', True))
        theme_name = str(getattr(sty, 'qgis_theme_name', '') or '')
        theme_style = str(getattr(sty, 'qgis_theme_style_name', '') or '')
        _qcv_style_from_dict(sty, gdata)
        sty.visible = visible
        sty.qgis_theme_name = theme_name
        sty.qgis_theme_style_name = theme_style
    except Exception as exc:
        qcv_log(f"Unable to apply global style: {exc}", 'PDV/OVERLAYS', 'WARNING')
    return sty


def _overlay_is_layer_global(self, layer_or_id):
    try:
        lid = str(layer_or_id.id()) if hasattr(layer_or_id, 'id') else str(layer_or_id or '')
        return lid in _overlay_read_global_layer_ids(self)
    except Exception:
        return False


def _overlay_set_layer_global(self, sty_or_layer, enabled=True):
    layer = getattr(sty_or_layer, 'layer', None) or sty_or_layer
    try:
        lid = str(layer.id()) if layer is not None else ''
    except Exception:
        lid = ''
    if not lid:
        return False
    ids = _overlay_read_global_layer_ids(self)
    if enabled:
        if lid not in ids:
            ids.append(lid)
        sty = sty_or_layer if hasattr(sty_or_layer, 'layer') else None
        if sty is not None:
            _overlay_store_global_style(self, sty)
    else:
        ids = [x for x in ids if x != lid]
    return _overlay_write_global_layer_ids(self, ids)


def _overlay_remove_layer_from_all_saved_states(self, layer_id):
    """Remove a layer from every saved PDV visual state for the active camera layer."""
    lid = str(layer_id or '')
    if not lid:
        return
    layer = _camera_layer(self)
    if layer is None:
        return
    project = QgsProject.instance()
    changed = False
    try:
        fids = [int(f.id()) for f in layer.getFeatures()]
    except Exception:
        fids = []
    for fid in fids:
        keys = [_camera_visual_state_key(self, fid), _camera_legacy_visual_state_key(self, fid)]
        for key in [k for k in keys if k]:
            try:
                raw, found = project.readEntry('QCALVIEW', key, '')
                if not found or not raw:
                    continue
                state = json.loads(str(raw))
                if not isinstance(state, dict):
                    continue
                if int(state.get('version', 0) or 0) >= 5 or 'overrides' in state:
                    ov = dict(state.get('overrides') or {})
                    local_add = [str(x) for x in list(ov.get('local_add') or []) if str(x) != lid]
                    removed = [str(x) for x in list(ov.get('removed') or []) if str(x) != lid]
                    visibility = {str(k): bool(v) for k, v in dict(ov.get('visibility') or {}).items() if str(k) != lid}
                    order = [str(x) for x in list(ov.get('order') or []) if str(x) != lid]
                    ov.update({'local_add': local_add, 'removed': removed, 'visibility': visibility, 'order': order})
                    state['overrides'] = ov
                else:
                    overlays = list(state.get('overlays') or [])
                    state['overlays'] = [d for d in overlays if str((d or {}).get('layer_id') or '') != lid]
                project.writeEntry('QCALVIEW', key, json.dumps(state, ensure_ascii=False, separators=(',', ':')))
                changed = True
            except Exception as exc:
                qcv_log(f"Viewpoint cleanup {fid} for layer {lid}: {exc}", 'PDV/OVERLAYS', 'WARNING')
    if changed:
        project.setDirty(True)


def _overlay_remove_layer_globally(self, layer_id):
    lid = str(layer_id or '')
    if not lid:
        return False
    ids = [x for x in _overlay_read_global_layer_ids(self) if x != lid]
    _overlay_write_global_layer_ids(self, ids)
    styles = _overlay_read_global_styles(self)
    if lid in styles:
        styles.pop(lid, None)
        _overlay_write_global_styles(self, styles)
    _overlay_remove_layer_from_all_saved_states(self, lid)
    return True


def _overlay_autosave_current_visual_state(self):
    """Persist layer-stack changes without requiring the camera Save button."""
    try:
        fid = _camera_current_fid(self)
        if fid is None:
            return False
        return bool(_camera_capture_visual_state(self, int(fid)))
    except Exception as exc:
        qcv_log(f"Unable to auto-save visual state: {exc}", 'PDV/OVERLAYS', 'WARNING')
        return False

def _camera_legacy_visual_state_key(self, fid):
    layer = _camera_layer(self)
    if layer is None or fid is None:
        return ''
    safe_layer = ''.join(ch if (ch.isalnum() or ch in '_-') else '_' for ch in str(layer.id()))
    return f'/pdv_visual_state/{safe_layer}/{int(fid)}'


def _camera_pdv_identity(self, fid):
    """Stable PDV identity: qcv_uid, then qcv_id, then FID."""
    layer = _camera_layer(self)
    if layer is None or fid is None:
        return ''
    feat = None
    try:
        feat = layer.getFeature(int(fid))
    except Exception:
        feat = None
    if feat is not None:
        try:
            names = {str(n).lower(): n for n in layer.fields().names()}
        except Exception:
            names = {}
        for candidate in ('qcv_uid', 'qcv_id'):
            real = names.get(candidate)
            if not real:
                continue
            try:
                value = str(feat[real] or '').strip()
            except Exception:
                value = ''
            if value:
                safe = ''.join(ch if (ch.isalnum() or ch in '_-.') else '_' for ch in value)
                return f'{candidate}_{safe}'
    return f'fid_{int(fid)}'


def _camera_visual_state_key(self, fid):
    layer = _camera_layer(self)
    ident = _camera_pdv_identity(self, fid)
    if layer is None or not ident:
        return ''
    safe_layer = ''.join(ch if (ch.isalnum() or ch in '_-') else '_' for ch in str(layer.id()))
    return f'/pdv_state_v5/{safe_layer}/{ident}'


def _camera_layer_tree_model(self):
    try:
        view = self.iface.layerTreeView()
        return view.layerTreeModel() if view is not None else None
    except Exception:
        return None


def _qcv_widget_bool(self, name, default=False):
    try:
        w = getattr(self, name, None)
        return bool(w.isChecked()) if w is not None else bool(default)
    except Exception:
        return bool(default)


def _qcv_widget_float(self, name, default=0.0):
    try:
        w = getattr(self, name, None)
        return float(w.value()) if w is not None else float(default)
    except Exception:
        return float(default)


def _camera_capture_plugin_settings(self):
    """PDV-only settings. Terrain/MNT defaults live in project_state_v5."""
    out = {
        'cb_occ_objects': _qcv_widget_bool(self, 'cb_occ_objects', True),
        'cb_transparent_objects': _qcv_widget_bool(self, 'cb_transparent_objects'),
        'cb_debug_no_occ': _qcv_widget_bool(self, 'cb_debug_no_occ'),
        'cb_force_horizontal_25d': _qcv_widget_bool(self, 'cb_force_horizontal_25d'),
        'cb_draw_2p5d': _qcv_widget_bool(self, 'cb_draw_2p5d'),
        'cb_show_labels': _qcv_widget_bool(self, 'cb_show_labels', True),
        'd_hdefault': _qcv_widget_float(self, 'd_hdefault', 0.0),
        'height_field': '',
        'relief_specific': _qcv_widget_bool(self, 'cb_relief_specific_pdv', False),
        'relief_mode_override': '',
    }
    try:
        out['height_field'] = str(self.txt_hfield.text() or '')
    except Exception:
        pass
    if out['relief_specific']:
        try:
            out['relief_mode_override'] = str(self._relief_mode_id() or 'none')
        except Exception:
            out['relief_mode_override'] = 'none'
    return out


def _camera_restore_plugin_settings(self, data):
    data = dict(data or {})
    for name in ('cb_occ_objects', 'cb_transparent_objects', 'cb_debug_no_occ',
                 'cb_force_horizontal_25d', 'cb_draw_2p5d', 'cb_show_labels'):
        if name in data:
            try:
                w = getattr(self, name)
                old = w.blockSignals(True)
                w.setChecked(bool(data[name]))
                w.blockSignals(old)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, 'core/_camera_layer_ops.py:restore-pdv-bool')
    if 'd_hdefault' in data:
        try:
            w = self.d_hdefault; old = w.blockSignals(True); w.setValue(float(data['d_hdefault'])); w.blockSignals(old)
        except Exception:
            pass
    if 'height_field' in data:
        try:
            w = self.txt_hfield; old = w.blockSignals(True); w.setText(str(data.get('height_field') or '')); w.blockSignals(old)
        except Exception:
            pass

    specific = bool(data.get('relief_specific', False))
    try:
        cb = getattr(self, 'cb_relief_specific_pdv', None)
        if cb is not None:
            old = cb.blockSignals(True); cb.setChecked(specific); cb.blockSignals(old)
    except Exception:
        pass
    try:
        if specific:
            self._set_relief_mode_id(str(data.get('relief_mode_override') or 'none'))
        else:
            state = self._project_state_read() if hasattr(self, '_project_state_read') else {}
            terrain = dict(state.get('terrain') or {})
            self._set_relief_mode_id(str(terrain.get('relief_mode') or 'none'))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, 'core/_camera_layer_ops.py:restore-relief-mode')


def _camera_base_layer_ids(self):
    out = []
    try:
        vals = self._project_base_layer_ids() if hasattr(self, '_project_base_layer_ids') else []
    except Exception:
        vals = []
    for lid in list(vals or []) + list(_overlay_read_global_layer_ids(self) or []):
        lid = str(lid or '').strip()
        if lid and lid not in out:
            out.append(lid)
    return out


def _camera_collect_visual_state_snapshot(self):
    """Version 5: store only PDV deltas relative to project/base theme."""
    effective = []
    visibility = {}
    for sty in list(getattr(self, 'layer_styles', []) or []):
        try:
            lid = str(sty.layer.id())
        except Exception:
            continue
        if not lid or lid in effective:
            continue
        effective.append(lid)
        visibility[lid] = bool(getattr(sty, 'visible', True))
        try:
            _overlay_store_global_style(self, sty)
        except Exception:
            pass
    base_ids = _camera_base_layer_ids(self)
    local_add = [lid for lid in effective if lid not in base_ids]
    removed = [lid for lid in base_ids if lid not in effective]
    # Default inherited visibility is True. Persist only actual visibility
    # overrides so an untouched PDV remains clean and follows later base changes.
    visibility = {lid: val for lid, val in visibility.items() if not bool(val)}
    default_order = [lid for lid in base_ids if lid not in removed] + [lid for lid in local_add if lid not in base_ids]
    order_override = effective if effective != default_order else []
    return {
        'version': 5,
        'pdv_uid': _camera_pdv_identity(self, getattr(self, '_camera_current_fid', None)),
        'overrides': {
            'local_add': local_add,
            'removed': removed,
            'visibility': visibility,
            'order': order_override,
        },
        'settings': _camera_capture_plugin_settings(self),
    }


def _camera_state_effective_compare(state):
    state = dict(state or {})
    ov = dict(state.get('overrides') or {})
    return {
        'local_add': [str(x) for x in list(ov.get('local_add') or [])],
        'removed': [str(x) for x in list(ov.get('removed') or [])],
        'visibility': {str(k): bool(v) for k, v in dict(ov.get('visibility') or {}).items()},
        'order': [str(x) for x in list(ov.get('order') or [])],
        'settings': dict(state.get('settings') or {}),
    }


def _camera_read_saved_state(self, fid):
    project = QgsProject.instance()
    key = _camera_visual_state_key(self, fid)
    if key:
        try:
            raw, found = project.readEntry('QCALVIEW', key, '')
            if found and raw:
                data = json.loads(str(raw))
                if isinstance(data, dict):
                    return data, True, False
        except Exception:
            pass
    legacy_key = _camera_legacy_visual_state_key(self, fid)
    if legacy_key:
        try:
            raw, found = project.readEntry('QCALVIEW', legacy_key, '')
            if found and raw:
                data = json.loads(str(raw))
                if isinstance(data, dict):
                    return data, True, True
        except Exception:
            pass
    return {}, False, False


def _camera_theme_layer_ids(self, theme_name):
    name = str(theme_name or '').strip()
    if not name:
        return []
    try:
        coll = QgsProject.instance().mapThemeCollection()
        if coll is None or not coll.hasMapTheme(name):
            return []
        visible = {str(x) for x in coll.mapThemeVisibleLayerIds(name)}
        try:
            ordered_layers = list(coll.masterLayerOrder())
        except Exception:
            ordered_layers = list(QgsProject.instance().mapLayers().values())
        camera = _camera_layer(self)
        cam_id = str(camera.id()) if camera is not None else ''
        out = []
        for lyr in ordered_layers:
            try:
                lid = str(lyr.id())
            except Exception:
                continue
            if lid not in visible or lid == cam_id or not isinstance(lyr, QgsVectorLayer):
                continue
            out.append(lid)
        return out
    except Exception:
        return []


def _camera_migrate_legacy_state(self, legacy):
    legacy = dict(legacy or {})
    # Seed project-global terrain/theme from the first legacy PDV encountered.
    try:
        if hasattr(self, '_project_seed_from_legacy_pdv'):
            self._project_seed_from_legacy_pdv(legacy.get('settings') or {}, legacy.get('theme') or '')
    except Exception:
        pass
    legacy_theme = str(legacy.get('theme') or '')
    old_theme_ids = _camera_theme_layer_ids(self, legacy_theme)
    try:
        if legacy_theme and old_theme_ids and hasattr(self, '_project_base_layer_ids') and not self._project_base_layer_ids():
            self._project_set_base_theme(legacy_theme, old_theme_ids, styles_initialized=False)
    except Exception:
        pass
    base_ids = old_theme_ids if old_theme_ids else _camera_base_layer_ids(self)
    effective = []
    visibility = {}
    legacy_styles = {}
    for d in list(legacy.get('overlays') or []):
        if not isinstance(d, dict):
            continue
        lid = str(d.get('layer_id') or '')
        if not lid or lid in effective:
            continue
        effective.append(lid)
        if not bool(d.get('visible', True)):
            visibility[lid] = False
        legacy_styles[lid] = d
    local_add = [lid for lid in effective if lid not in base_ids]
    removed = [lid for lid in base_ids if lid not in effective]
    settings = dict(legacy.get('settings') or {})
    specific = bool(settings.get('relief_specific', False))
    # Legacy project states stored relief mode per viewpoint; treat it as the project default during migration.
    pdv_settings = {
        'cb_occ_objects': bool(settings.get('cb_occ_objects', True)),
        'cb_transparent_objects': bool(settings.get('cb_transparent_objects', False)),
        'cb_debug_no_occ': bool(settings.get('cb_debug_no_occ', False)),
        'cb_force_horizontal_25d': bool(settings.get('cb_force_horizontal_25d', False)),
        'cb_draw_2p5d': bool(settings.get('cb_draw_2p5d', True)),
        'cb_show_labels': bool(settings.get('cb_show_labels', True)),
        'd_hdefault': float(settings.get('d_hdefault', 0.0) or 0.0),
        'height_field': str(settings.get('height_field') or ''),
        'relief_specific': specific,
        'relief_mode_override': str(settings.get('relief_mode') or 'none') if specific else '',
    }
    return {
        'version': 5,
        'overrides': {
            'local_add': local_add,
            'removed': removed,
            'visibility': visibility,
            'order': [],
        },
        'settings': pdv_settings,
        '_legacy_styles': legacy_styles,
    }


def _camera_visual_state_dirty(self, fid):
    try:
        current = _camera_collect_visual_state_snapshot(self)
        saved, found, legacy = _camera_read_saved_state(self, fid)
        if legacy:
            saved = _camera_migrate_legacy_state(self, saved)
        if not found:
            saved = {'version': 5, 'overrides': {'local_add': [], 'removed': [], 'visibility': {}, 'order': []}, 'settings': {}}
        a = json.dumps(_camera_state_effective_compare(current), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        b = json.dumps(_camera_state_effective_compare(saved), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return a != b
    except Exception as exc:
        qcv_log(f"PDV {fid}: visual-state comparison failed: {exc}", 'EXPORT/PREFLIGHT', 'WARNING')
        return True


def _camera_capture_visual_state(self, fid):
    key = _camera_visual_state_key(self, fid)
    if not key:
        return False
    project = QgsProject.instance()
    state = _camera_collect_visual_state_snapshot(self)
    try:
        state['pdv_uid'] = _camera_pdv_identity(self, fid)
        payload = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        ok = bool(project.writeEntry('QCALVIEW', key, payload))
        if ok:
            project.setDirty(True)
    except Exception as exc:
        qcv_log(f"Failed to save viewpoint {fid}: {exc}", 'PDV/SAVE', 'CRITICAL')
        ok = False
    self._camera_last_visual_capture_fid = int(fid)
    local_count = len(list((state.get('overrides') or {}).get('local_add') or []))
    self._camera_last_visual_capture_summary = (
        f"QCALVIEW v5 state saved ({local_count} local addition(s))"
        if ok else 'failed to save QCALVIEW state'
    )
    if ok:
        qcv_log(f"PDV {fid}: {self._camera_last_visual_capture_summary}", 'PDV/SAVE', 'SUCCESS')
    return ok


def _camera_restore_visual_state(self, fid):
    project = QgsProject.instance()
    saved, state_found, was_legacy = _camera_read_saved_state(self, fid)
    if was_legacy:
        saved = _camera_migrate_legacy_state(self, saved)
    if not isinstance(saved, dict):
        saved = {}
    ov = dict(saved.get('overrides') or {})
    base_ids = _camera_base_layer_ids(self)
    removed = {str(x) for x in list(ov.get('removed') or []) if str(x)}
    local_add = [str(x) for x in list(ov.get('local_add') or []) if str(x)]
    effective = [lid for lid in base_ids if lid not in removed]
    for lid in local_add:
        if lid not in effective:
            effective.append(lid)
    order = [str(x) for x in list(ov.get('order') or []) if str(x)]
    if order:
        ranked = [lid for lid in order if lid in effective]
        for lid in effective:
            if lid not in ranked:
                ranked.append(lid)
        effective = ranked
    visibility = {str(k): bool(v) for k, v in dict(ov.get('visibility') or {}).items()}
    legacy_styles = dict(saved.get('_legacy_styles') or {})

    restored = []
    try:
        from ._layerstyle import LayerStyle
        global_styles = _overlay_read_global_styles(self)
        changed = False
        for lid in effective:
            lyr = project.mapLayer(lid)
            if lyr is None:
                continue
            sty = LayerStyle(lyr)
            fallback = legacy_styles.get(lid)
            gdata = global_styles.get(lid)
            if isinstance(gdata, dict):
                _qcv_style_from_dict(sty, gdata)
            elif isinstance(fallback, dict):
                _qcv_style_from_dict(sty, fallback)
                global_styles[lid] = _overlay_style_payload_global(sty)
                changed = True
            else:
                global_styles[lid] = _overlay_style_payload_global(sty)
                changed = True
            sty.visible = bool(visibility.get(lid, True))
            restored.append(sty)
        if changed:
            _overlay_write_global_styles(self, global_styles)
        self.layer_styles = restored
        try:
            self._refresh_layer_list_labels(0 if restored else -1)
        except Exception:
            pass
    except Exception as exc:
        qcv_log(f"PDV {fid}: incomplete v5 overlay restore: {exc}", 'PDV/LOAD', 'WARNING')

    try:
        _camera_restore_plugin_settings(self, saved.get('settings') or {})
    except Exception as exc:
        qcv_log(f"PDV {fid}: incomplete v5 settings restore: {exc}", 'PDV/LOAD', 'WARNING')

    # The top theme selector is project-global; never change it per viewpoint.
    try:
        getattr(self, '_overlay_cache', {}).clear()
        getattr(self, '_geom_cache', {}).clear()
    except Exception:
        pass
    try:
        self._refresh_preview_and_viewer()
    except Exception:
        try:
            self.render_preview()
        except Exception:
            pass
    self._camera_last_visual_restore_fid = int(fid)
    self._camera_last_visual_restore_summary = (
        f"QCALVIEW v5 state restored ({len(restored)} layers; project base theme unchanged)"
    )
    qcv_log(f"PDV {fid}: {self._camera_last_visual_restore_summary}", 'PDV/LOAD', 'SUCCESS')
    if was_legacy:
        try:
            _camera_capture_visual_state(self, fid)
        except Exception:
            pass
    return bool(state_found or restored)

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
        qcv_log(f"Unable to transform viewpoint to project CRS: {exc}", 'PDV/CRS', 'WARNING')
        return None, work


def _camera_warn_if_non_metric_project(self, notify=False):
    crs = _camera_metric_project_crs(self)
    if crs is not None:
        return True
    msg = 'The project CRS must be projected and metric for QCALVIEW calculations. Source layers may use WGS84: QCALVIEW reprojects them to the project CRS.'
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
                self.lbl_current_pdv.setText(tr(f"Viewpoint&nbsp;<b>{clean}</b>"))
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
    return f"Viewpoint {int(feat.id())}"


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
        _camera_set_status(self, 'No camera layer selected.')
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
    default_label = _camera_first_existing_name(names, ('name', 'nom', 'label', 'libelle', 'label', 'description'))
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
    _camera_set_status(self, f"Camera layer ready: {layer.name()} ({layer.featureCount()} points approximately).")


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
        _camera_set_status(self, 'No viewpoint found in the camera layer (is a QGIS filter active?).', "#aa6600")
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
        suffix = 'image specified' if img_path else 'schematic view'
        _camera_update_title(self, title)
        _camera_set_status(self, f"Current viewpoint: {title} — {suffix}.", "#666" if img_path else "#aa6600")
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
    if (
        getattr(self, '_camera_internal_write', False)
        or getattr(self, '_camera_loading_feature', False)
        or getattr(self, '_qcv_fov_cache_write', False)
    ):
        return

    try:
        layer = _camera_layer(self)
        if layer is not None and len(args) >= 2:
            fid = int(args[0])
            field_index = int(args[1])
            field_name = str(layer.fields().field(field_index).name()).lower()



            if field_name == 'qcv_uid' or 'qcv_fov_' in field_name:
                return
            if field_name in {'qcv_proj', 'qcv_360', 'qcv_yaw', 'qcv_hfov', 'qcv_mdst'}:
                update_fov = getattr(self, '_qcv_fov_update_feature', None)
                if callable(update_fov):
                    update_fov(layer, fid, save=True)
    except Exception as _qcv_exc:
        _qcv_suppress(
            _qcv_exc,
            'core/_camera_layer_ops.py:_camera_on_layer_data_changed:fov',
        )

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
    try:
        fid = int(fid)
    except Exception:
        return

    current = _camera_current_fid(self)
    is_current = current is not None and fid == int(current)

    try:
        if is_current:
            self._sync_pdv_qml()
        else:
            update_fov = getattr(self, '_qcv_fov_update_feature', None)
            if callable(update_fov):
                update_fov(_camera_layer(self), fid, save=True)
    except Exception as _qcv_exc:
        _qcv_suppress(
            _qcv_exc,
            'core/_camera_layer_ops.py:_camera_on_geometry_changed:fov',
        )

    if not is_current or not bool(getattr(self, '_camera_live_enabled', False)):
        return

    self._camera_live_pending_fid = fid
    try:
        self._camera_live_refresh_timer.start(250)
    except Exception as _qcv_exc:
        _qcv_suppress(
            _qcv_exc,
            'core/_camera_layer_ops.py:_camera_on_geometry_changed:timer',
        )


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
    if photo_path:
        state['qcv_img'] = photo_path
    elif mode == 'SCHEMA':

        try:
            layer = _camera_layer(self)
            feat = _camera_current_feature(self)
            stored_img = _camera_feature_value(layer, feat, 'qcv_img', None) if layer is not None and feat is not None else None
            if stored_img not in (None, NULL, ''):
                state['qcv_img'] = stored_img
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:_camera_collect_ui_state:qcv_img")
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
        _camera_set_status(self, 'No viewpoint to load.', "#aa6600")
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
                        QMessageBox.warning(self, tr('QCALVIEW'), tr(f"Unable to load image:\n{e}"))
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

    # The first QML sync above occurs while the feature-loading guard is active.
    # Run it once more after loading so the live FOV geometry and current-PDV
    # styling are updated immediately, without waiting for a camera control change.
    try:
        self._sync_pdv_qml()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:_camera_load_current_feature:post_load_pdv_sync")

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
    status = f"Viewpoint loaded: {title}"
    mode_now = _camera_normalize_view_mode(getattr(self, '_camera_current_view_mode', view_mode))
    if path:
        status += f" — {os.path.basename(path)}"
    elif mode_now == 'PHOTO':
        status += " — associated photo not found: temporary schematic display"
    else:
        status += " — schematic view (without photo)"
    try:
        if int(getattr(self, '_camera_last_visual_restore_fid', -999999)) == int(feat.id()):
            vs = str(getattr(self, '_camera_last_visual_restore_summary', '') or '')
            if vs and vs != 'no saved visual state':
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
    if not getattr(self, '_camera_loading_feature', False):
        try:
            self._sync_pdv_qml()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:photo_path_sync")
    return True


def _camera_write_source_fields(self, mode, image_path=None, silent=False):

    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    if layer is None or feat is None:
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
        return False
    if not _camera_ensure_fields(self):
        return False
    fid = int(feat.id())
    mode = _camera_normalize_view_mode(mode)
    stored = None
    if mode == 'PHOTO' and image_path:
        stored = _camera_make_storable_image_path(layer, image_path)



    fields_to_write = [('qcv_mode', mode)]
    if mode == 'PHOTO':
        fields_to_write.append(('qcv_img', stored))

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
        for key, value in fields_to_write:
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
    if mode == 'PHOTO' and image_path:
        draft['qcv_img'] = image_path
    elif mode in ('SCHEMA', 'AUTO'):

        existing = draft.get('qcv_img')
        if existing in (None, ''):
            existing = _camera_feature_value(layer, feat, 'qcv_img', None)
        if existing not in (None, NULL, ''):
            draft['qcv_img'] = existing
    drafts[fid] = draft
    self._camera_drafts = drafts
    if not silent:
        _camera_set_status(self,
            'Saved source: photograph.' if mode == 'PHOTO' else
            ('Saved source: schematic view (associated photo retained).' if mode == 'SCHEMA' else
             'Saved source: automatic detection from the image field.'),
            "#2b6" if mode == 'PHOTO' else "#386a8a")
    return changed


def _camera_set_current_schematic(self):

    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
        return
    layer = _camera_layer(self)
    fid = int(feat.id())
    associated_img = getattr(self, '_camera_current_photo_path', None)
    if not associated_img and layer is not None:
        associated_img = _camera_feature_value(layer, feat, 'qcv_img', None)

    self._camera_current_view_mode = 'SCHEMA'
    try:
        self._activate_schematic_view()
    except Exception:
        self.image = None
        self.photo_path = None
        self._camera_current_photo_path = None

    self._camera_current_view_mode = 'SCHEMA'
    draft = _camera_capture_current_draft(self, feat.id()) or {}
    if associated_img not in (None, NULL, ''):
        draft['qcv_img'] = associated_img
        self._camera_drafts[fid] = draft
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
    _camera_set_status(self, f"{title} — schematic view saved (associated photo kept).", "#386a8a")


def _camera_use_auto_image_source(self):

    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    if layer is None or feat is None:
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
        return
    self._camera_current_view_mode = 'AUTO'
    drafts = getattr(self, '_camera_drafts', {}) or {}
    draft = dict(drafts.get(int(feat.id())) or _camera_collect_ui_state(self))
    draft['qcv_mode'] = 'AUTO'
    if draft.get('qcv_img') in (None, ''):
        stored_img = _camera_feature_value(layer, feat, 'qcv_img', None)
        if stored_img not in (None, NULL, ''):
            draft['qcv_img'] = stored_img
    drafts[int(feat.id())] = draft
    self._camera_drafts = drafts
    _camera_write_source_fields(self, 'AUTO', None, silent=True)
    _camera_load_current_feature(self, auto=False)


def _camera_associate_photo(self):

    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
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
    _camera_set_status(self, f"{title} — associated photograph: {os.path.basename(after)}", "#2b6")


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
        QMessageBox.information(self, tr('QCALVIEW'), tr('Select a viewpoint layer first.'))
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
        _camera_set_status(self, 'QCALVIEW fields already exist in the camera layer.', "#2b6")
        return True
    ok = False
    try:
        ok = bool(layer.dataProvider().addAttributes(new_fields))
        if ok:
            layer.updateFields()
            self._camera_refresh_field_combos(layer)
            _camera_set_status(self, 'QCALVIEW fields added to the camera layer.', "#2b6")
        else:
            _camera_set_status(self, 'Unable to add QCALVIEW fields.', "#b33")
    except Exception as e:
        QMessageBox.warning(self, tr('QCALVIEW'), tr(f"Unable to add QCALVIEW fields:\n{e}"))
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

        if not img_path:
            img_path = _camera_feature_value(layer, feat, 'qcv_img', None)
        if img_path not in (None, NULL, ''):
            state['qcv_img'] = _camera_make_storable_image_path(layer, img_path)
        else:
            state.pop('qcv_img', None)
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
        _camera_set_status(self, f"Parameters saved for {title}.", "#2b6")

    try:
        update_fov = getattr(self, '_qcv_fov_update_feature', None)
        if callable(update_fov):
            update_fov(layer, fid, save=True)
    except Exception as _qcv_exc:
        _qcv_suppress(
            _qcv_exc,
            'core/_camera_layer_ops.py:_camera_save_feature_by_fid:fov',
        )

    try:
        self._sync_pdv_qml()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1824")
    return changed


def _camera_save_current_feature(self):
    feat = _camera_current_feature(self)
    if feat is None:
        _camera_set_status(self, 'No viewpoint to save.', "#aa6600")
        return
    _camera_capture_current_draft(self, feat.id())
    _camera_save_feature_by_fid(self, int(feat.id()), silent=False)
    try:
        ok_state = _camera_capture_visual_state(self, int(feat.id()))
        title = _camera_resolve_title(self, _camera_layer(self), feat)
        summary = str(getattr(self, '_camera_last_visual_capture_summary', '') or '')
        if ok_state:
            _camera_set_status(self, f"Parameters and {summary} for {title}.", "#2b6")
        else:
            _camera_set_status(self, f"Parameters saved for {title}, but {summary}.", "#b36b00")
    except Exception:
        _camera_set_status(self, 'Settings saved, but the visual state could not be captured.', "#b36b00")


def _camera_on_feature_changed(self, *_):
    layer = _camera_layer(self)
    if layer is None:
        _camera_set_status(self, 'No camera layer selected.', "#aa6600")
        _camera_update_title(self, "")
        return
    combo = getattr(self, 'cmb_cam_feature', None)
    if combo is None or combo.currentIndex() < 0:
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
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
        _camera_set_status(self, 'No viewpoint active.', "#aa6600")
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
        source_txt, color = 'image specified', '#666'
    elif mode == 'PHOTO':
        source_txt, color = 'photo not found', '#b36b00'
    else:
        source_txt, color = 'schematic view', '#386a8a'
    _camera_set_status(self, f"Current viewpoint: {title} — {source_txt}.", color)
    self._camera_load_current_feature(auto=True)
    try:
        if getattr(self, 'viewer', None):
            self.viewer.sync_pdv_controls()
            self.viewer.update_info()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_camera_layer_ops.py:1902")
