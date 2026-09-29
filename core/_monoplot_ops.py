from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._compat import QC
import math, datetime
from qgis.PyQt.QtCore import Qt, QPointF, QMetaType
from qgis.PyQt.QtGui import QColor, QPen, QBrush, QFont
from qgis.PyQt.QtWidgets import QListWidgetItem
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateTransform, QgsFeature,
    QgsGeometry, QgsPointXY, QgsPoint, QgsField, QgsFields, QgsWkbTypes
)
from qgis.gui import QgsMapTool, QgsVertexMarker, QgsRubberBand
from ..projector import project_point, _validated_vfov_for_cylindrical, _basis_from_yaw_pitch_roll
from ._render_ops import _make_pov_curved_sampler, _apply_pov_curvature_to_z, _overlay_offset_pixels

BLUE = QColor(50, 120, 255, 235)

class MonoplotMapTool(QgsMapTool):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback
    def canvasReleaseEvent(self, ev):
        pt = self.canvas.getCoordinateTransform().toMapCoordinates(ev.pos().x(), ev.pos().y())
        self.callback(pt)

def _mp_log(self, msg):
    try:
        if hasattr(self, 'lbl_info'):
            self.lbl_info.setText(tr(str(msg)))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:33")


def _monoplot_next_id(self):
    cur = int(getattr(self, '_monoplot_counter', 0) or 0) + 1
    self._monoplot_counter = cur
    return f"MP{cur:03d}"


def _monoplot_current_pdv_info(self):
    layer = self.cmb_camera.currentLayer() if hasattr(self, 'cmb_camera') else None
    feat = self._camera_current_feature() if hasattr(self, '_camera_current_feature') else None
    if feat is None and isinstance(layer, QgsVectorLayer):
        feat = next(layer.getFeatures(), None)
    pdv_id = ''
    pdv_name = ''
    if feat is not None:
        for name in ('IDPTV', 'idptv', 'name', 'nom', 'qcv_id'):
            try:
                if feat.fields().indexOf(name) >= 0:
                    val = feat[name]
                    if val not in (None, ''):
                        if not pdv_id:
                            pdv_id = str(val)
                        if not pdv_name:
                            pdv_name = str(val)
                        if pdv_id and pdv_name:
                            break
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:62")
        try:
            if not pdv_name:
                pdv_name = str(feat.id())
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:67")
    return {'layer': layer, 'feat': feat, 'pdv_id': pdv_id or '', 'pdv_name': pdv_name or ''}


def _monoplot_current_camera_context(self):
    info = _monoplot_current_pdv_info(self)
    layer = info['layer']; feat = info['feat']
    if feat is None or feat.geometry() is None:
        return None
    try:
        cam_pt, cam_crs = self._camera_point_in_work_crs(feat)
    except Exception:
        cam_pt, cam_crs = None, None
    if cam_pt is None or cam_crs is None:
        return None
    dem_layer = self.cmb_dem.currentLayer() if hasattr(self, 'cmb_dem') else None
    z_sampler_raw = None
    z_sampler_view = None
    cam_ground_z = 0.0
    curvature_enabled = bool(getattr(self, 'cb_curvature', None) and self.cb_curvature.isChecked())
    if isinstance(dem_layer, QgsRasterLayer) and dem_layer.isValid():
        try:
            z_sampler_raw, _ = self._make_z_sampler(dem_layer, cam_crs)
            z_sampler_view = _make_pov_curved_sampler(self, z_sampler_raw, cam_pt, curvature_enabled=True, k_refraction=0.0)
            cam_ground_z = float(z_sampler_raw(QgsPointXY(cam_pt.x(), cam_pt.y())))
        except Exception:
            z_sampler_raw = None
            z_sampler_view = None
            cam_ground_z = 0.0
    z_sampler_active = z_sampler_view if curvature_enabled else z_sampler_raw
    cam_z = cam_ground_z + float(self.d_camheight.value())
    proj = str(self.cmb_proj.currentText()).strip().upper()
    is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
    yaw_eff = float(self.d_yaw.value()) + float(self.d_yaw_offset.value())
    pitch = float(self.d_pitch.value())
    roll = float(self.d_roll.value())
    hfov = float(self.d_hfov.value())
    vfov = float(self.d_vfov.value())
    width = int(self.spin_w.value()); height = int(self.spin_h.value())
    maxdist = float(self.d_maxdist.value()) if float(self.d_maxdist.value()) > 0 else 5000.0
    return {
        'cam_pt': cam_pt, 'cam_crs': cam_crs, 'cam_z': cam_z, 'cam_ground_z': cam_ground_z,
        'z_sampler': z_sampler_active, 'z_sampler_active': z_sampler_active,
        'z_sampler_raw': z_sampler_raw, 'z_sampler_view': z_sampler_view,
        'curvature_enabled': curvature_enabled, 'terrain_mode': 'view' if curvature_enabled else 'raw',
        'proj': proj, 'is360': is360, 'yaw_eff': yaw_eff,
        'pitch': pitch, 'roll': roll, 'hfov': hfov, 'vfov': vfov, 'width': width, 'height': height,
        'maxdist': maxdist, **info
    }


def _monoplot_active_terrain_mode(self, ctx=None):
    ctx = ctx or _monoplot_current_camera_context(self)
    if not ctx:
        return 'raw'
    return 'view' if bool(ctx.get('curvature_enabled')) else 'raw'


def _monoplot_overlay_shift(self, width, height):
    try:
        dx, dy = _overlay_offset_pixels(self, int(width), int(height))
        return float(int(round(float(dx)))), float(int(round(float(dy))))
    except Exception:
        return 0.0, 0.0


def _monoplot_compute_view_z(self, ctx, x, y, z_raw):
    try:
        z_val = float(z_raw)
    except Exception:
        return None
    if not ctx:
        return z_val
    try:
        arr = _apply_pov_curvature_to_z(
            self, [[float(x), float(y)]], [z_val], ctx['cam_pt'],
            curvature_enabled=True, k_refraction=0.0
        )
        return float(arr[0])
    except Exception:
        return z_val


def _monoplot_record_active_z(self, rec, ctx=None):
    mode = _monoplot_active_terrain_mode(self, ctx=ctx)
    if mode == 'view':
        val = rec.get('z_view', rec.get('z'))
        if val is not None:
            return float(val)
    val = rec.get('z_raw', rec.get('z'))
    return float(val) if val is not None else None


def _monoplot_angles_from_uv(self, u, v, ctx):
    W = max(1.0, float(ctx['width'])); H = max(1.0, float(ctx['height']))
    proj = str(ctx['proj']).upper(); is360 = bool(ctx['is360'])
    HFOV = float(ctx['hfov']); VFOV = float(ctx['vfov'])
    x = float(u); y = float(v)
    hf = math.radians(max(1e-6, HFOV)); vf = math.radians(max(1e-6, VFOV))
    if proj == 'PINHOLE':
        fx = (W * 0.5) / max(1e-9, math.tan(hf * 0.5))
        fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        xc = (x - W * 0.5) / max(1e-9, fx)
        yc = 1.0
        zc = (H * 0.5 - y) / max(1e-9, fy)
    elif proj == 'CYLINDRICAL':
        vf = _validated_vfov_for_cylindrical(VFOV, HFOV, W, H)
        if is360:
            alpha = (x / W) * (2.0 * math.pi) - math.pi
        else:
            fx = W / max(1e-9, hf)
            alpha = (x - W * 0.5) / max(1e-9, fx)
        fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        beta = math.atan((H * 0.5 - y) / max(1e-9, fy))
        cb = math.cos(beta)
        xc = math.sin(alpha) * cb
        yc = math.cos(alpha) * cb
        zc = math.sin(beta)
    else:
        if is360:
            alpha = (x / W) * (2.0 * math.pi) - math.pi
            beta = (0.5 * math.pi) - (y / H) * math.pi
        else:
            alpha = ((x / W) - 0.5) * hf
            beta = (0.5 - (y / H)) * vf
        cb = math.cos(beta)
        xc = math.sin(alpha) * cb
        yc = math.cos(alpha) * cb
        zc = math.sin(beta)
    r, up, f = _basis_from_yaw_pitch_roll(float(ctx['yaw_eff']), float(ctx['pitch']), float(ctx['roll']))
    wx = xc * float(r[0]) + yc * float(f[0]) + zc * float(up[0])
    wy = xc * float(r[1]) + yc * float(f[1]) + zc * float(up[1])
    wz = xc * float(r[2]) + yc * float(f[2]) + zc * float(up[2])
    az_abs = math.degrees(math.atan2(wx, wy)) % 360.0
    pitch_abs = math.degrees(math.atan2(wz, max(1e-12, math.hypot(wx, wy))))
    return az_abs, pitch_abs


def _monoplot_image_probe_info(self, u, v):
    """Return the exact native image pixel and viewing angles under an image pick.

    Monoplotting picks are snapped to the centre of a source pixel.  This keeps the
    magnifier reticle, the displayed X/Y pixel values and the terrain ray consistent,
    including when the full viewer itself is showing a reduced proxy.
    """
    try:
        width = max(1, int(self.spin_w.value()))
        height = max(1, int(self.spin_h.value()))
        proj = str(self.cmb_proj.currentText()).strip().upper()
        is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
        ctx = {
            'proj': proj,
            'is360': is360,
            'yaw_eff': float(self.d_yaw.value()) + float(self.d_yaw_offset.value()),
            'pitch': float(self.d_pitch.value()),
            'roll': float(self.d_roll.value()),
            'hfov': float(self.d_hfov.value()),
            'vfov': float(self.d_vfov.value()),
            'width': width,
            'height': height,
        }
    except Exception:
        return None
    try:
        x = float(u)
        y = float(v)
    except Exception:
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    if x < 0.0 or y < 0.0 or x >= float(width) or y >= float(height):
        return None

    pixel_x = max(0, min(width - 1, int(math.floor(x))))
    pixel_y = max(0, min(height - 1, int(math.floor(y))))
    sample_u = float(pixel_x) + 0.5
    sample_v = float(pixel_y) + 0.5

    angle_u = sample_u
    angle_v = sample_v
    off_x, off_y = _monoplot_overlay_shift(self, width, height)
    angle_u -= float(off_x)
    angle_v -= float(off_y)
    if bool(ctx.get('is360')):
        angle_u %= max(1.0, float(width))
    az_abs, pitch_abs = _monoplot_angles_from_uv(self, angle_u, angle_v, ctx)
    return {
        'pixel_x': int(pixel_x),
        'pixel_y': int(pixel_y),
        'u': float(sample_u),
        'v': float(sample_v),
        'az_deg': float(az_abs),
        'elev_deg': float(pitch_abs),
    }


def _monoplot_point_visibility(self, rec, width=None, height=None):
    ctx = _monoplot_current_camera_context(self)
    if not ctx:
        return None
    width = int(width or ctx['width']); height = int(height or ctx['height'])
    shift_x, shift_y = _monoplot_overlay_shift(self, width, height)
    if rec.get('src_mode') == 'image_to_ground' and rec.get('src_pdv_id') == ctx.get('pdv_id') and rec.get('_click_uv'):
        src_w = max(1.0, float(ctx['width'])); src_h = max(1.0, float(ctx['height']))
        u = float(rec['_click_uv'][0]) * float(width) / src_w
        v = float(rec['_click_uv'][1]) * float(height) / src_h
        shown_u = u + shift_x
        shown_v = v + shift_y
        if bool(ctx['is360']):
            shown_u %= max(1.0, float(width))
        if (bool(ctx['is360']) or 0.0 <= shown_u < width) and 0.0 <= shown_v <= height:
            return (u, v)
    z_tgt = _monoplot_record_active_z(self, rec, ctx=ctx)
    if z_tgt is None:
        return None
    uv = self._finite_uv(project_point(ctx['cam_pt'], ctx['cam_z'], QgsPointXY(float(rec['x']), float(rec['y'])), None,
                                      ctx['proj'], width, height, ctx['yaw_eff'], ctx['pitch'], ctx['roll'], ctx['hfov'], ctx['vfov'], ctx['is360'],
                                      dist_max=ctx['maxdist'], z_tgt=float(z_tgt), z_sampler=None))
    if uv is None:
        return None
    u, v = float(uv[0]), float(uv[1])
    shown_u = u + shift_x
    shown_v = v + shift_y
    if bool(ctx['is360']):
        shown_u %= max(1.0, float(width))
    if (not bool(ctx['is360']) and (shown_u < 0 or shown_u >= width)) or shown_v < 0 or shown_v > height:
        return None
    return (u, v)


def _monoplot_rebuild_visible(self, width=None, height=None):
    vis = []
    for rec in getattr(self, '_monoplot_records', []) or []:
        uv = _monoplot_point_visibility(self, rec, width=width, height=height)
        rec['_visible_uv'] = uv
        vis.append((rec, uv))
    self._monoplot_visible = vis
    try:
        self._monoplot_refresh_list()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:219")
    return vis


def _monoplot_refresh_list(self):
    lw = getattr(self, 'list_monoplot', None)
    if lw is None:
        return
    lw.clear()
    _monoplot_sync_from_layers(self)
    records = getattr(self, '_monoplot_records', []) or []
    cur_pdv = _monoplot_current_pdv_info(self).get('pdv_id','')
    visible_count = 0
    terrain_mode = _monoplot_active_terrain_mode(self)
    mode_label = tr('apparent terrain') if terrain_mode == 'view' else tr('raw terrain')
    for rec in records:
        if rec.get('_visible_uv'):
            visible_count += 1
        lbl_txt = rec.get('label') or rec['mp_id']
        txt = f"{lbl_txt} · D={rec['dist_m']:.1f} m · Az={rec['az_deg']:.1f}°"
        lw.addItem(QListWidgetItem(txt))
    try:
        lbl = getattr(self, 'lbl_monoplot_status', None)
        if lbl is not None:
            if not records:
                lbl.setText(tr(f'No monoplotting reference. Mode: {mode_label}.'))
            else:
                lbl.setText(tr(f"{len(records)} reference(s) · {visible_count} visible · mode {mode_label}"))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:249")

def _monoplot_live_project_layer(layer):
    if not isinstance(layer, QgsVectorLayer):
        return None
    try:
        layer_id = layer.id()
    except Exception:
        return None
    try:
        current = QgsProject.instance().mapLayer(layer_id)
    except Exception:
        return None
    if not isinstance(current, QgsVectorLayer):
        return None
    try:
        if not current.isValid():
            return None
    except Exception:
        return None
    return current


def _monoplot_reset_runtime_state(self):
    self._monoplot_records = []
    self._monoplot_visible = []
    self._monoplot_counter = 0
    self._monoplot_points_layer = None
    self._monoplot_rays_layer = None
    try:
        self.list_monoplot.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:reset_runtime")


def _monoplot_sync_from_layers(self):
    points = _monoplot_live_project_layer(getattr(self, '_monoplot_points_layer', None))
    if points is None:
        return
    self._monoplot_points_layer = points
    idx_id = points.fields().indexOf('mp_id')
    idx_label = points.fields().indexOf('label')
    if idx_id < 0:
        return
    labels = {}
    for f in points.getFeatures():
        try:
            mp_id = str(f[idx_id])
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:266")
            continue
        lbl = None
        if idx_label >= 0:
            try:
                lbl = f[idx_label]
            except Exception:
                lbl = None
        labels[mp_id] = str(lbl) if lbl not in (None, '') else mp_id
    for rec in getattr(self, '_monoplot_records', []) or []:
        rec['label'] = labels.get(rec.get('mp_id'), rec.get('label') or rec.get('mp_id'))


def _monoplot_ensure_layers(self):
    crs = None
    try:
        ctx = _monoplot_current_camera_context(self)
        crs = ctx['cam_crs'] if ctx else None
    except Exception:
        crs = None
    authid = crs.authid() if crs is not None and crs.isValid() else self.iface.mapCanvas().mapSettings().destinationCrs().authid()
    proj = QgsProject.instance()
    raw_points = getattr(self, '_monoplot_points_layer', None)
    raw_rays = getattr(self, '_monoplot_rays_layer', None)
    points = _monoplot_live_project_layer(raw_points)
    rays = _monoplot_live_project_layer(raw_rays)
    if (raw_points is not None and points is None) or (raw_rays is not None and rays is None):
        _monoplot_reset_runtime_state(self)
        points = None
        rays = None
    if points is None:
        points = QgsVectorLayer(f'Point?crs={authid}', tr('QCV_MONOPLOT_POINTS'), 'memory')
        pr = points.dataProvider()
        pr.addAttributes([
            QgsField('mp_id', QMetaType.Type.QString), QgsField('src_mode', QMetaType.Type.QString), QgsField('src_pdv', QMetaType.Type.QString),
            QgsField('src_name', QMetaType.Type.QString), QgsField('dist_m', QMetaType.Type.Double), QgsField('dist_h_m', QMetaType.Type.Double), QgsField('az_deg', QMetaType.Type.Double),
            QgsField('z', QMetaType.Type.Double), QgsField('z_raw', QMetaType.Type.Double), QgsField('z_view', QMetaType.Type.Double),
            QgsField('z_mode', QMetaType.Type.QString), QgsField('curv_on', QMetaType.Type.Int),
            QgsField('proj', QMetaType.Type.QString), QgsField('hfov', QMetaType.Type.Double), QgsField('vfov', QMetaType.Type.Double),
            QgsField('yaw', QMetaType.Type.Double), QgsField('pitch', QMetaType.Type.Double), QgsField('roll', QMetaType.Type.Double),
            QgsField('created', QMetaType.Type.QString), QgsField('label', QMetaType.Type.QString), QgsField('is_ctrl', QMetaType.Type.Int)
        ])
        points.updateFields(); proj.addMapLayer(points)
        self._monoplot_points_layer = points
    else:
        extra_attrs = []
        for name, typ in (
            ('dist_h_m', QMetaType.Type.Double),
            ('z_raw', QMetaType.Type.Double),
            ('z_view', QMetaType.Type.Double),
            ('z_mode', QMetaType.Type.QString),
            ('curv_on', QMetaType.Type.Int),
        ):
            if points.fields().indexOf(name) < 0:
                extra_attrs.append(QgsField(name, typ))
        if extra_attrs:
            points.dataProvider().addAttributes(extra_attrs)
            points.updateFields()
    if rays is None:
        rays = QgsVectorLayer(f'LineString?crs={authid}', tr('QCV_MONOPLOT_RAYS'), 'memory')
        pr = rays.dataProvider()
        pr.addAttributes([
            QgsField('mp_id', QMetaType.Type.QString), QgsField('src_pdv', QMetaType.Type.QString), QgsField('dist_m', QMetaType.Type.Double), QgsField('az_deg', QMetaType.Type.Double)
        ])
        rays.updateFields(); proj.addMapLayer(rays)
        self._monoplot_rays_layer = rays
    return points, rays


def _monoplot_invalidate_overlay(self):
    try:
        if hasattr(self, '_overlay_cache') and isinstance(self._overlay_cache, dict):
            self._overlay_cache.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:335")

def _monoplot_add_record(self, rec, add_ray=True):
    points, rays = _monoplot_ensure_layers(self)
    records = getattr(self, '_monoplot_records', None)
    if records is None:
        self._monoplot_records = []
        records = self._monoplot_records
    records.append(rec)
    feat = QgsFeature(points.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(rec['x']), float(rec['y']))))
    feat['mp_id'] = rec['mp_id']; feat['src_mode'] = rec['src_mode']; feat['src_pdv'] = rec['src_pdv_id']; feat['src_name'] = rec['src_pdv_name']
    feat['dist_m'] = float(rec['dist_m']); feat['dist_h_m'] = float(rec.get('dist_h_m', 0.0)); feat['az_deg'] = float(rec['az_deg']); feat['z'] = float(rec['z']); feat['proj'] = rec['proj_mode']
    if points.fields().indexOf('z_raw') >= 0:
        feat['z_raw'] = float(rec.get('z_raw', rec['z']))
    if points.fields().indexOf('z_view') >= 0:
        feat['z_view'] = float(rec.get('z_view', rec['z']))
    if points.fields().indexOf('z_mode') >= 0:
        feat['z_mode'] = str(rec.get('terrain_mode', 'raw'))
    if points.fields().indexOf('curv_on') >= 0:
        feat['curv_on'] = 1 if bool(rec.get('curvature_enabled', False)) else 0
    feat['hfov'] = float(rec['hfov']); feat['vfov'] = float(rec['vfov']); feat['yaw'] = float(rec['yaw']); feat['pitch'] = float(rec['pitch']); feat['roll'] = float(rec['roll'])
    feat['created'] = rec['created_at']; feat['label'] = rec.get('label') or rec['mp_id']; feat['is_ctrl'] = 0
    points.dataProvider().addFeatures([feat]); points.updateExtents(); points.triggerRepaint()
    if add_ray and rec.get('cam_x') is not None:
        fr = QgsFeature(rays.fields())
        fr.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(float(rec['cam_x']), float(rec['cam_y'])), QgsPointXY(float(rec['x']), float(rec['y']))]))
        fr['mp_id'] = rec['mp_id']; fr['src_pdv'] = rec['src_pdv_id']; fr['dist_m'] = float(rec['dist_m']); fr['az_deg'] = float(rec['az_deg'])
        rays.dataProvider().addFeatures([fr]); rays.updateExtents(); rays.triggerRepaint()
    _monoplot_rebuild_visible(self)
    _monoplot_invalidate_overlay(self)


def _monoplot_make_record(self, src_mode, x, y, z_raw, ctx, z_view=None):
    if z_view is None:
        z_view = _monoplot_compute_view_z(self, ctx, x, y, z_raw)
    z_active = float(z_view) if bool(ctx.get('curvature_enabled')) else float(z_raw)
    dx = float(x) - float(ctx['cam_pt'].x()); dy = float(y) - float(ctx['cam_pt'].y()); dz = float(z_active) - float(ctx['cam_z'])
    dist_h = math.hypot(dx, dy)
    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    az = self._azimuth_deg(dx, dy)
    return {
        'mp_id': _monoplot_next_id(self), 'src_mode': src_mode,
        'src_pdv_id': ctx['pdv_id'], 'src_pdv_name': ctx['pdv_name'],
        'x': float(x), 'y': float(y), 'z': float(z_active), 'z_raw': float(z_raw), 'z_view': float(z_view),
        'terrain_mode': _monoplot_active_terrain_mode(self, ctx=ctx), 'curvature_enabled': bool(ctx.get('curvature_enabled', False)),
        'dist_m': float(dist), 'dist_h_m': float(dist_h), 'az_deg': float(az),
        'proj_mode': ctx['proj'], 'hfov': float(ctx['hfov']), 'vfov': float(ctx['vfov']), 'yaw': float(ctx['yaw_eff']),
        'pitch': float(ctx['pitch']), 'roll': float(ctx['roll']),
        'cam_x': float(ctx['cam_pt'].x()), 'cam_y': float(ctx['cam_pt'].y()), 'cam_z': float(ctx['cam_z']),
        'created_at': datetime.datetime.now().isoformat(timespec='seconds'), 'label': ''
    }


def _monoplot_pick_map_z(self, map_pt):
    ctx = _monoplot_current_camera_context(self)
    if not ctx:
        return None
    src = self.iface.mapCanvas().mapSettings().destinationCrs()
    tr = QgsCoordinateTransform(src, ctx['cam_crs'], QgsProject.instance())
    pt_cam = tr.transform(map_pt)
    z_raw = None
    try:
        z_raw = float(getattr(map_pt, 'z', lambda: float('nan'))())
        if not math.isfinite(z_raw):
            z_raw = None
    except Exception:
        z_raw = None
    if z_raw is None and ctx.get('z_sampler_raw') is not None:
        try:
            z_raw = float(ctx['z_sampler_raw'](QgsPointXY(pt_cam.x(), pt_cam.y())))
        except Exception:
            z_raw = None
    if z_raw is None:
        return None
    z_view = _monoplot_compute_view_z(self, ctx, pt_cam.x(), pt_cam.y(), z_raw)
    return QgsPointXY(pt_cam.x(), pt_cam.y()), z_raw, z_view, ctx


def start_monoplot_map_to_image(self):
    self._monoplot_active_tool = 'map_to_image'
    self._image_pick_mode = None
    viewer = getattr(self, "viewer", None)
    if viewer is not None and hasattr(viewer, "set_image_pick_active"):
        viewer.set_image_pick_active(False)
    canvas = self.iface.mapCanvas()
    self._maptool_backup = canvas.mapTool()
    canvas.setMapTool(MonoplotMapTool(canvas, lambda pt: _monoplot_on_map_click(self, pt)))
    mode_label = 'apparent terrain' if _monoplot_active_terrain_mode(self) == 'view' else 'raw terrain'
    _mp_log(self, f'Map reference active: click the map canvas. Mode {mode_label}.')


def _monoplot_on_map_click(self, map_pt):
    picked = _monoplot_pick_map_z(self, map_pt)
    if not picked:
        _mp_log(self, 'Elevation failed: no geometry Z and no active DEM/DSM.')
        return
    pt_cam, z_raw, z_view, ctx = picked
    rec = _monoplot_make_record(self, 'map_to_image', pt_cam.x(), pt_cam.y(), z_raw, ctx, z_view=z_view)
    uv = _monoplot_point_visibility(self, rec)
    if uv is None:
        try:
            self.iface.messageBar().pushMessage(tr('QCALVIEW'), tr('Point not visible in the current view'), level=QC.Qgis_MessageLevel_Warning, duration=4)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:435")
        _mp_log(self, 'Point not visible in the current view')
        return
    rec['_visible_uv'] = uv
    _monoplot_add_record(self, rec, add_ray=False)
    try:
        _monoplot_refresh_viewer_markers(self)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:map_click_viewer_markers")
    try:
        self.render_preview()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:443")


def start_monoplot_image_to_ground(self):
    self._monoplot_active_tool = 'image_to_ground'
    self._image_pick_mode = 'monoplot_ground'
    viewer = getattr(self, "viewer", None)
    if viewer is not None and hasattr(viewer, "set_image_pick_active"):
        viewer.set_image_pick_active(True)
    mode_label = 'apparent terrain' if _monoplot_active_terrain_mode(self) == 'view' else 'raw terrain'
    _mp_log(self, f'Query terrain from image active: click in the image. Mode {mode_label}.')


def _monoplot_intersect_image_uv(self, u, v):
    ctx = _monoplot_current_camera_context(self)
    z_sampler_active = ctx.get('z_sampler_active') if ctx else None
    if not ctx or z_sampler_active is None:
        return None, 'no terrain data for this point'
    raw_u = float(u)
    raw_v = float(v)
    off_x, off_y = _monoplot_overlay_shift(self, int(ctx['width']), int(ctx['height']))
    raw_u -= float(off_x)
    raw_v -= float(off_y)
    if bool(ctx.get('is360')):
        raw_u %= max(1.0, float(ctx['width']))
    az_abs, pitch_abs = _monoplot_angles_from_uv(self, raw_u, raw_v, ctx)
    maxdist = max(50.0, float(ctx['maxdist']))
    tanp = math.tan(math.radians(pitch_abs))
    prev = None
    step = max(2.0, min(25.0, maxdist / 300.0))
    hit = None
    for i in range(1, int(maxdist / step) + 1):
        d = min(maxdist, i * step)
        x = float(ctx['cam_pt'].x()) + d * math.sin(math.radians(az_abs))
        y = float(ctx['cam_pt'].y()) + d * math.cos(math.radians(az_abs))
        try:
            zt = float(z_sampler_active(QgsPointXY(x, y)))
        except Exception:
            return None, 'no terrain data for this point'
        zr = float(ctx['cam_z']) + tanp * d
        if prev is not None and zr <= zt:
            lo, hi = prev[0], d
            best = (x, y, zt, d)
            for _ in range(16):
                mid = 0.5 * (lo + hi)
                xm = float(ctx['cam_pt'].x()) + mid * math.sin(math.radians(az_abs))
                ym = float(ctx['cam_pt'].y()) + mid * math.cos(math.radians(az_abs))
                ztm = float(z_sampler_active(QgsPointXY(xm, ym)))
                zrm = float(ctx['cam_z']) + tanp * mid
                if zrm <= ztm:
                    hi = mid; best = (xm, ym, ztm, mid)
                else:
                    lo = mid
            hit = best
            break
        prev = (d, x, y, zt, zr)
    if hit is None:
        return None, 'no terrain intersection found'
    x, y, _zt_active, d = hit
    z_raw = None
    z_view = None
    try:
        if ctx.get('z_sampler_raw') is not None:
            z_raw = float(ctx['z_sampler_raw'](QgsPointXY(x, y)))
    except Exception:
        z_raw = None
    if z_raw is None:
        z_raw = float(_zt_active)
    try:
        if ctx.get('z_sampler_view') is not None:
            z_view = float(ctx['z_sampler_view'](QgsPointXY(x, y)))
    except Exception:
        z_view = None
    if z_view is None:
        z_view = _monoplot_compute_view_z(self, ctx, x, y, z_raw)
    rec = _monoplot_make_record(self, 'image_to_ground', x, y, z_raw, ctx, z_view=z_view)
    rec['az_deg'] = float(az_abs)
    rec['_click_uv'] = (float(raw_u), float(raw_v))
    rec['_visible_uv'] = (float(raw_u), float(raw_v))
    return rec, None


def _monoplot_handle_image_click_uv(self, u, v):
    rec, err = _monoplot_intersect_image_uv(self, u, v)
    if rec is None:
        try:
            self.iface.messageBar().pushMessage(tr('QCALVIEW'), tr(err or 'No terrain intersection found'), level=QC.Qgis_MessageLevel_Warning, duration=4)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:521")
        _mp_log(self, err or 'No terrain intersection found')
        return False
    _monoplot_add_record(self, rec, add_ray=True)
    if not bool(getattr(self, '_monoplot_viewer_click_active', False)):
        try:
            self.render_preview()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:528")
    return True


def clear_monoplot_reperes(self):
    self._monoplot_records = []
    self._monoplot_visible = []
    self._monoplot_counter = 0
    _monoplot_invalidate_overlay(self)
    for attr in ('_monoplot_points_layer', '_monoplot_rays_layer'):
        lyr = _monoplot_live_project_layer(getattr(self, attr, None))
        if lyr is not None:
            setattr(self, attr, lyr)
            ids = [f.id() for f in lyr.getFeatures()]
            if ids:
                lyr.dataProvider().deleteFeatures(ids)
                lyr.triggerRepaint()
        else:
            setattr(self, attr, None)
    try:
        self.list_monoplot.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:546")
    try:
        viewer = getattr(self, 'viewer', None)
        if viewer is not None and hasattr(viewer, 'clear_pick_markers'):
            viewer.clear_pick_markers()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:clear_viewer_markers")
    try:
        self.render_preview()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:550")
    _mp_log(self, 'Monoplotting references cleared')


def stop_monoplot_tools(self):
    self._monoplot_active_tool = None
    self._image_pick_mode = None
    viewer = getattr(self, "viewer", None)
    if viewer is not None and hasattr(viewer, "set_image_pick_active"):
        viewer.set_image_pick_active(False)
    try:
        self._cancel_maptool()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:560")


def _draw_monoplot_overlay(self, painter, width, height, apply_display_shift=False):
    """Draw monoplot markers into a raster target.

    ``_monoplot_point_visibility`` returns coordinates in the unshifted projection
    frame because the normal QCALVIEW overlay is shifted as a whole afterwards.
    When markers are drawn directly into an already composed image (preview/export),
    ``apply_display_shift`` applies that final display offset here instead.
    """
    _monoplot_sync_from_layers(self)
    vis = _monoplot_rebuild_visible(self, width=width, height=height)
    if not vis:
        return
    shift_x = shift_y = 0.0
    is360 = False
    if apply_display_shift:
        shift_x, shift_y = _monoplot_overlay_shift(self, width, height)
        try:
            ctx = _monoplot_current_camera_context(self)
            is360 = bool(ctx and ctx.get('is360'))
        except Exception:
            is360 = False
    painter.save()
    pen = QPen(BLUE); pen.setWidth(2)
    painter.setPen(pen)
    font = QFont('Arial', 9)
    painter.setFont(font)
    for rec, uv in vis:
        if uv is None:
            continue
        u, v = float(uv[0]), float(uv[1])
        if apply_display_shift:
            u += float(shift_x)
            v += float(shift_y)
            if is360:
                u %= max(1.0, float(width))
            elif u < 0.0 or u >= float(width):
                continue
            if v < 0.0 or v > float(height):
                continue
        painter.drawLine(QPointF(u-7, v), QPointF(u+7, v))
        painter.drawLine(QPointF(u, v-7), QPointF(u, v+7))
        lbl_txt = rec.get('label') or rec['mp_id']
        txt = f"{lbl_txt} | D={rec['dist_m']:.1f} m | Az={rec['az_deg']:.1f}°"
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(txt) + 8
        rect_w = min(max(120, tw), max(120, width - 8))
        rect_x = min(max(4.0, u + 10.0), max(4.0, width - rect_w - 4.0))
        rect_y = min(max(4.0, v - 28.0), max(4.0, height - 24.0))
        painter.fillRect(int(rect_x), int(rect_y), int(rect_w), 18, QColor(255,255,255,180))
        painter.drawText(int(rect_x)+4, int(rect_y)+13, txt)
    painter.restore()


def _monoplot_refresh_viewer_markers(self):
    """Rebuild crisp viewer-only markers at the exact raster-overlay position.

    The raster overlay is rendered at the current display quality
    (25/50/100 %) and QCALVIEW then scales that pixmap to the viewer base image.
    To preserve exact marker placement, markers are projected in that *same*
    overlay coordinate system and only their final drawing is kept vectorial.
    No monoplot geometry, terrain intersection or projection behaviour is changed.
    """
    viewer = getattr(self, 'viewer', None)
    if viewer is None or not hasattr(viewer, 'clear_pick_markers') or not hasattr(viewer, 'add_pick_marker'):
        return
    try:
        pix_item = getattr(viewer, '_pix', None)
        base_pm = pix_item.pixmap() if pix_item is not None else None
        if base_pm is None or base_pm.isNull() or base_pm.width() <= 0 or base_pm.height() <= 0:
            return

        overlay_item = getattr(viewer, '_overlay_pix', None)
        overlay_pm = overlay_item.pixmap() if overlay_item is not None else None
        if overlay_pm is not None and not overlay_pm.isNull() and overlay_pm.width() > 0 and overlay_pm.height() > 0:
            render_w = int(overlay_pm.width())
            render_h = int(overlay_pm.height())
        else:
            # Safe fallback for a viewer opened before its first overlay refresh.
            # Once update_overlay() runs, this function is called again using the
            # real 25/50/100 % raster dimensions.
            render_w = max(1, int(base_pm.width()))
            render_h = max(1, int(base_pm.height()))

        vis = _monoplot_rebuild_visible(self, width=render_w, height=render_h)
        shift_x, shift_y = _monoplot_overlay_shift(self, render_w, render_h)
        ctx = _monoplot_current_camera_context(self)
        is360 = bool(ctx and ctx.get('is360'))

        # This is exactly the transform used by _ImageViewer.update_overlay().
        sx = float(base_pm.width()) / float(render_w)
        sy = float(base_pm.height()) / float(render_h)

        viewer.clear_pick_markers()
        for rec, uv in vis:
            if uv is None:
                continue
            u = float(uv[0]) + float(shift_x)
            v = float(uv[1]) + float(shift_y)
            if is360:
                u %= max(1.0, float(render_w))
            elif u < 0.0 or u >= float(render_w):
                continue
            if v < 0.0 or v > float(render_h):
                continue
            lbl_txt = rec.get('label') or rec.get('mp_id') or ''
            text = f"{lbl_txt} | D={float(rec.get('dist_m', 0.0)):.1f} m | Az={float(rec.get('az_deg', 0.0)):.1f}°"
            viewer.add_pick_marker(u * sx, v * sy, text)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:refresh_viewer_markers")


def _monoplot_on_pdv_changed(self):
    try:
        _monoplot_rebuild_visible(self)
        _monoplot_invalidate_overlay(self)
        self.render_preview()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_monoplot_ops.py:597")
