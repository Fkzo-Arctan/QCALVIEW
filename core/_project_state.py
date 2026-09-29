from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._log import qcv_log
import json
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsProject

PROJECT_STATE_KEY = '/project_state_v5'
PROJECT_STATE_VERSION = 5


def project_state_read(self):
    try:
        raw, found = QgsProject.instance().readEntry('QCALVIEW', PROJECT_STATE_KEY, '')
        if not found or not raw:
            return {}
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        qcv_log(f"Unreadable QCALVIEW project state: {exc}", 'PROJECT/STATE', 'WARNING')
        return {}


def project_state_write(self, data):
    try:
        payload = dict(data or {})
        payload['version'] = PROJECT_STATE_VERSION
        ok = bool(QgsProject.instance().writeEntry(
            'QCALVIEW', PROJECT_STATE_KEY,
            json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        ))
        if ok:
            QgsProject.instance().setDirty(True)
        return ok
    except Exception as exc:
        qcv_log(f"Unable to write QCALVIEW project state: {exc}", 'PROJECT/STATE', 'WARNING')
        return False


def project_state_exists(self):
    return bool(project_state_read(self))


def project_base_theme_name(self):
    try:
        return str(project_state_read(self).get('base_theme') or '')
    except Exception:
        return ''


def project_base_layer_ids(self):
    try:
        vals = list(project_state_read(self).get('base_layers') or [])
    except Exception:
        vals = []
    out = []
    for val in vals:
        lid = str(val or '').strip()
        if lid and lid not in out:
            out.append(lid)
    return out


def project_set_base_theme(self, theme_name, layer_ids, styles_initialized=True):
    state = project_state_read(self)
    state['base_theme'] = str(theme_name or '').strip()
    clean = []
    for val in list(layer_ids or []):
        lid = str(val or '').strip()
        if lid and lid not in clean:
            clean.append(lid)
    state['base_layers'] = clean
    state['base_styles_initialized'] = bool(styles_initialized)
    return project_state_write(self, state)


def _color_hex(color, fallback='#ffffff00'):
    try:
        r, g, b, a = color.getRgb()
        return f'#{int(a):02x}{int(r):02x}{int(g):02x}{int(b):02x}'
    except Exception:
        try:
            return str(color.name())
        except Exception:
            return fallback


def _widget_bool(self, name, default=False):
    try:
        w = getattr(self, name, None)
        return bool(w.isChecked()) if w is not None else bool(default)
    except Exception:
        return bool(default)


def _widget_float(self, name, default=0.0):
    try:
        w = getattr(self, name, None)
        return float(w.value()) if w is not None else float(default)
    except Exception:
        return float(default)


def _widget_int(self, name, default=0):
    try:
        w = getattr(self, name, None)
        return int(w.value()) if w is not None else int(default)
    except Exception:
        return int(default)


def project_capture_global_relief(self):
    out = {
        'dem_layer_id': '',
        'relief_mode': 'none',
        'use_dem_z': _widget_bool(self, 'cb_use_dem_z', False),
        'dem_step': _widget_float(self, 'spin_dem_step', 150.0),
        'dem_width': _widget_int(self, 'spin_dem_width', 1),
        'wire_dashed': _widget_bool(self, 'cb_wire_dashed', False),
        'wire_mode': 0,
        'curvature': _widget_bool(self, 'cb_curvature', True),
        'earth_radius_km': _widget_float(self, 'd_earth_radius_km', 6370.0),
        'ridge_gap': _widget_float(self, 'd_ridge_gap', 250.0),
        'ridge_prom': _widget_float(self, 'd_ridge_prom', 2.0),
        'eps': _widget_float(self, 'd_eps', 0.05),
        'az_step': _widget_float(self, 'd_az_step', 1.0),
        'rad_step': _widget_float(self, 'd_rad_step', 25.0),
        'sky_dashed': _widget_bool(self, 'cb_sky_dashed', False),
        'drape_enabled': _widget_bool(self, 'cb_drape_rasters', False),
        'drape_layer_id': '',
    }
    try:
        lyr = self.cmb_dem.currentLayer()
        out['dem_layer_id'] = str(lyr.id()) if lyr is not None else ''
    except Exception:
        pass
    try:
        out['relief_mode'] = str(self._relief_mode_id() or 'none')
    except Exception:
        pass
    try:
        out['wire_mode'] = int(self.combo_wire_mode.currentData()) if self.combo_wire_mode.currentData() is not None else int(self.combo_wire_mode.currentIndex())
    except Exception:
        pass
    try:
        out['dem_color'] = _color_hex(getattr(self, '_dem_color', QColor(255, 255, 0, 220)))
    except Exception:
        out['dem_color'] = '#dcffff00'
    try:
        lyr = self.cmb_drape_raster.currentLayer()
        out['drape_layer_id'] = str(lyr.id()) if lyr is not None else ''
    except Exception:
        pass
    return out


def project_save_global_relief(self):
    if getattr(self, '_restoring_project_state', False):
        return False
    state = project_state_read(self)
    previous = dict(state.get('terrain') or {})
    current = project_capture_global_relief(self)
    # A PDV-specific mode must not overwrite the project default mode.
    if _widget_bool(self, 'cb_relief_specific_pdv', False):
        current['relief_mode'] = str(previous.get('relief_mode') or current.get('relief_mode') or 'none')
    state['terrain'] = current
    return project_state_write(self, state)


def _set_checked(widget, value):
    if widget is None:
        return
    old = widget.blockSignals(True)
    try:
        widget.setChecked(bool(value))
    finally:
        widget.blockSignals(old)


def _set_value(widget, value):
    if widget is None:
        return
    old = widget.blockSignals(True)
    try:
        widget.setValue(value)
    finally:
        widget.blockSignals(old)


def project_apply_global_relief(self, terrain, include_mode=True):
    terrain = dict(terrain or {})
    if not terrain:
        return False
    prev = bool(getattr(self, '_restoring_project_state', False))
    self._restoring_project_state = True
    try:
        lid = str(terrain.get('dem_layer_id') or '')
        try:
            layer = QgsProject.instance().mapLayer(lid) if lid else None
            if getattr(self, 'cmb_dem', None) is not None:
                self.cmb_dem.setLayer(layer)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_project_state.py:restore-dem')
        if include_mode:
            try:
                self._set_relief_mode_id(str(terrain.get('relief_mode') or 'none'))
            except Exception as exc:
                _qcv_suppress(exc, 'core/_project_state.py:restore-mode')
        bools = {
            'cb_use_dem_z': 'use_dem_z',
            'cb_wire_dashed': 'wire_dashed',
            'cb_curvature': 'curvature',
            'cb_sky_dashed': 'sky_dashed',
        }
        for name, key in bools.items():
            if key in terrain:
                try:
                    _set_checked(getattr(self, name, None), terrain[key])
                except Exception:
                    pass
        nums = {
            'spin_dem_step': 'dem_step',
            'spin_dem_width': 'dem_width',
            'd_earth_radius_km': 'earth_radius_km',
            'd_ridge_gap': 'ridge_gap',
            'd_ridge_prom': 'ridge_prom',
            'd_eps': 'eps',
            'd_az_step': 'az_step',
            'd_rad_step': 'rad_step',
        }
        for name, key in nums.items():
            if key in terrain:
                try:
                    _set_value(getattr(self, name, None), terrain[key])
                except Exception:
                    pass
        try:
            wm = terrain.get('wire_mode', None)
            combo = getattr(self, 'combo_wire_mode', None)
            if combo is not None and wm is not None:
                idx = combo.findData(int(wm))
                if idx < 0:
                    idx = int(wm)
                old = combo.blockSignals(True)
                combo.setCurrentIndex(max(0, min(idx, combo.count() - 1)))
                combo.blockSignals(old)
        except Exception:
            pass
        try:
            if terrain.get('dem_color'):
                self._dem_color = QColor(str(terrain['dem_color']))
                self._sky_color = QColor(self._dem_color)
        except Exception:
            pass
        # Raster drape is project-global as well.
        try:
            drape_lid = str(terrain.get('drape_layer_id') or '')
            drape_layer = QgsProject.instance().mapLayer(drape_lid) if drape_lid else None
            if getattr(self, 'cmb_drape_raster', None) is not None:
                self.cmb_drape_raster.setLayer(drape_layer)
            _set_checked(getattr(self, 'cb_drape_rasters', None), bool(terrain.get('drape_enabled', False) and drape_layer is not None))
        except Exception:
            pass
        try:
            self._sync_relief_mode_controls()
        except Exception:
            pass
        return True
    finally:
        self._restoring_project_state = prev


def project_camera_layer_changed(self, layer=None):
    if getattr(self, '_restoring_project_state', False):
        return False
    try:
        if layer is None and getattr(self, 'cmb_camera', None) is not None:
            layer = self.cmb_camera.currentLayer()
        lid = str(layer.id()) if layer is not None else ''
        state = project_state_read(self)
        state['camera_layer_id'] = lid
        return project_state_write(self, state)
    except Exception as exc:
        _qcv_suppress(exc, 'core/_project_state.py:camera-layer')
        return False


def project_restore_global_state(self):
    state = project_state_read(self)
    if not state:
        return False
    prev = bool(getattr(self, '_restoring_project_state', False))
    self._restoring_project_state = True
    try:
        camera_lid = str(state.get('camera_layer_id') or '')
        if camera_lid:
            try:
                camera_layer = QgsProject.instance().mapLayer(camera_lid)
                if camera_layer is not None and getattr(self, 'cmb_camera', None) is not None:
                    self.cmb_camera.setLayer(camera_layer)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_project_state.py:restore-camera-layer')
        project_apply_global_relief(self, state.get('terrain') or {}, include_mode=True)
        theme = str(state.get('base_theme') or '')
        combo = getattr(self, 'cmb_qgis_theme', None)
        if combo is not None and theme:
            try:
                if not getattr(self, '_theme_combo_loaded', False):
                    self._theme_combo_loaded = True
                    self.refresh_qgis_themes()
                idx = combo.findData(theme)
                if idx < 0:
                    idx = combo.findText(theme)
                if idx >= 0:
                    old = combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(old)
                # Migration path: old projects had a remembered theme but no
                # project-level base layer snapshot yet. Build it once.
                if (not bool(state.get('base_styles_initialized', False)) or not list(state.get('base_layers') or [])) and hasattr(self, 'apply_qgis_theme_to_overlays'):
                    self.apply_qgis_theme_to_overlays(scope='base')
            except Exception as exc:
                _qcv_suppress(exc, 'core/_project_state.py:restore-theme')
        return True
    finally:
        self._restoring_project_state = prev


def project_seed_from_legacy_pdv(self, legacy_settings=None, legacy_theme=''):
    """One-time migration when opening a project that uses the legacy state schema.

    A partial v5 state may already exist because the camera combo emitted a
    layerChanged signal during dock construction. Merge missing legacy values
    instead of treating that partial state as a completed migration.
    """
    existing = project_state_read(self)
    if existing.get('terrain') and ('base_theme' in existing):
        return False
    state = dict(existing or {})
    state.setdefault('version', PROJECT_STATE_VERSION)
    state.setdefault('base_theme', str(legacy_theme or ''))
    state.setdefault('base_layers', [])
    state.setdefault('base_styles_initialized', False)
    terrain = project_capture_global_relief(self)
    old = dict(legacy_settings or {})
    if old.get('dem_layer_id'):
        terrain['dem_layer_id'] = str(old.get('dem_layer_id') or '')
    if old.get('relief_mode'):
        terrain['relief_mode'] = str(old.get('relief_mode') or 'none')
    for key_src, key_dst in (
        ('cb_use_dem_z', 'use_dem_z'), ('cb_curvature', 'curvature'),
        ('d_earth_radius_km', 'earth_radius_km'), ('d_rad_step', 'dem_step'),
    ):
        if key_src in old:
            terrain[key_dst] = old[key_src]
    state['terrain'] = terrain
    return project_state_write(self, state)


def project_relief_specific_toggled(self, checked):
    if getattr(self, '_restoring_project_state', False):
        return
    try:
        if not bool(checked):
            terrain = project_state_read(self).get('terrain') or {}
            if terrain:
                prev = bool(getattr(self, '_restoring_project_state', False))
                self._restoring_project_state = True
                try:
                    self._set_relief_mode_id(str(terrain.get('relief_mode') or 'none'))
                finally:
                    self._restoring_project_state = prev
        fid = getattr(self, '_camera_current_fid', None)
        if fid is not None and hasattr(self, '_camera_capture_visual_state'):
            self._camera_capture_visual_state(int(fid))
        self.render_preview()
    except Exception as exc:
        _qcv_suppress(exc, 'core/_project_state.py:relief-specific')


def project_relief_control_changed(self, *_args):
    if getattr(self, '_restoring_project_state', False):
        return
    project_save_global_relief(self)
    # The relief mode itself may be a PDV override. Persist it immediately so
    # switching viewpoint or closing/reopening the dock never loses it.
    if _widget_bool(self, 'cb_relief_specific_pdv', False):
        try:
            fid = getattr(self, '_camera_current_fid', None)
            if fid is not None and hasattr(self, '_camera_capture_visual_state'):
                self._camera_capture_visual_state(int(fid))
        except Exception as exc:
            _qcv_suppress(exc, 'core/_project_state.py:save-relief-override')
