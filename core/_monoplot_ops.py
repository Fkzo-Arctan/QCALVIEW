# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
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
from ..projector import project_point, _validated_vfov_for_cylindrical
from ._render_ops import _make_pov_curved_sampler, _apply_pov_curvature_to_z

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
    except Exception:
        pass


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
            except Exception:
                pass
        try:
            if not pdv_name:
                pdv_name = str(feat.id())
        except Exception:
            pass
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
    proj = ctx['proj']; is360 = ctx['is360']; HFOV = float(ctx['hfov']); VFOV = float(ctx['vfov'])
    x = float(u); y = float(v)
    theta = 0.0; beta = 0.0
    if proj == 'PINHOLE':
        hf = math.radians(HFOV); vf = math.radians(VFOV)
        fx = (W * 0.5) / max(1e-9, math.tan(hf * 0.5))
        fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        theta = math.degrees(math.atan2(x - W * 0.5, fx))
        beta = math.degrees(math.atan2(H * 0.5 - y, fy))
    elif proj == 'CYLINDRICAL':
        hf = math.radians(HFOV)
        vf = _validated_vfov_for_cylindrical(VFOV, HFOV, W, H)
        fx = W / max(1e-9, hf)
        fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        if is360:
            theta = ((x / W) * 360.0) - 180.0
        else:
            theta = math.degrees((x - W * 0.5) / max(1e-9, fx))
        beta = math.degrees(math.atan((H * 0.5 - y) / max(1e-9, fy)))
    else:
        if is360:
            theta = ((x / W) * 360.0) - 180.0
            beta = 90.0 - ((y / H) * 180.0)
        else:
            theta = ((x / W) - 0.5) * HFOV
            beta = (0.5 - (y / H)) * VFOV
    az_abs = (ctx['yaw_eff'] + theta) % 360.0
    pitch_abs = ctx['pitch'] + beta
    return az_abs, pitch_abs


def _monoplot_point_visibility(self, rec, width=None, height=None):
    ctx = _monoplot_current_camera_context(self)
    if not ctx:
        return None
    width = int(width or ctx['width']); height = int(height or ctx['height'])
    if rec.get('src_mode') == 'image_to_ground' and rec.get('src_pdv_id') == ctx.get('pdv_id') and rec.get('_click_uv'):
        u, v = float(rec['_click_uv'][0]), float(rec['_click_uv'][1])
        if 0.0 <= u <= width and 0.0 <= v <= height:
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
    if not ctx['is360']:
        if u < 0 or u >= width or v < 0 or v > height:
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
    except Exception:
        pass
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
    mode_label = 'terrain apparent' if terrain_mode == 'view' else 'terrain brut'
    for rec in records:
        status = 'visible' if rec.get('_visible_uv') else ('PDV source' if rec.get('src_pdv_id') == cur_pdv else 'hors vue')
        if rec.get('_visible_uv'):
            visible_count += 1
        lbl_txt = rec.get('label') or rec['mp_id']
        txt = f"{lbl_txt} · D={rec['dist_m']:.1f} m · Az={rec['az_deg']:.1f}°"
        lw.addItem(tr(QListWidgetItem(txt)))
    try:
        lbl = getattr(self, 'lbl_monoplot_status', None)
        if lbl is not None:
            if not records:
                lbl.setText(tr(f'Aucun repère monoplotting. Mode: {mode_label}.'))
            else:
                lbl.setText(tr(f"{len(records)} repère(s) · {visible_count} visible(s) · mode {mode_label}"))
    except Exception:
        pass



def _monoplot_sync_from_layers(self):
    points = getattr(self, '_monoplot_points_layer', None)
    if not isinstance(points, QgsVectorLayer) or not points.isValid():
        return
    idx_id = points.fields().indexOf('mp_id')
    idx_label = points.fields().indexOf('label')
    if idx_id < 0:
        return
    labels = {}
    for f in points.getFeatures():
        try:
            mp_id = str(f[idx_id])
        except Exception:
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
    points = getattr(self, '_monoplot_points_layer', None)
    if not isinstance(points, QgsVectorLayer) or not points.isValid():
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
    rays = getattr(self, '_monoplot_rays_layer', None)
    if not isinstance(rays, QgsVectorLayer) or not rays.isValid():
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
    except Exception:
        pass

def _monoplot_add_record(self, rec, add_ray=True):
    records = getattr(self, '_monoplot_records', None)
    if records is None:
        self._monoplot_records = []
        records = self._monoplot_records
    records.append(rec)
    points, rays = _monoplot_ensure_layers(self)
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
    canvas = self.iface.mapCanvas()
    self._maptool_backup = canvas.mapTool()
    canvas.setMapTool(MonoplotMapTool(canvas, lambda pt: _monoplot_on_map_click(self, pt)))
    mode_label = 'terrain apparent' if _monoplot_active_terrain_mode(self) == 'view' else 'terrain brut'
    _mp_log(self, f'Repère depuis la carte actif : cliquez sur le canevas. Mode {mode_label}.')


def _monoplot_on_map_click(self, map_pt):
    picked = _monoplot_pick_map_z(self, map_pt)
    if not picked:
        _mp_log(self, 'Échec altitude : Z géométrique absent et aucun MNT/MNS actif.')
        return
    pt_cam, z_raw, z_view, ctx = picked
    rec = _monoplot_make_record(self, 'map_to_image', pt_cam.x(), pt_cam.y(), z_raw, ctx, z_view=z_view)
    uv = _monoplot_point_visibility(self, rec)
    if uv is None:
        try:
            self.iface.messageBar().pushMessage(tr('QCALVIEW'), tr('Point non visible dans la vue courante'), level=QC.Qgis_MessageLevel_Warning, duration=4)
        except Exception:
            pass
        _mp_log(self, 'Point non visible dans la vue courante')
        return
    rec['_visible_uv'] = uv
    _monoplot_add_record(self, rec, add_ray=False)
    try:
        self.render_preview()
    except Exception:
        pass


def start_monoplot_image_to_ground(self):
    self._monoplot_active_tool = 'image_to_ground'
    self._image_pick_mode = 'monoplot_ground'
    mode_label = 'terrain apparent' if _monoplot_active_terrain_mode(self) == 'view' else 'terrain brut'
    _mp_log(self, f'Interroger le terrain depuis l’image actif : cliquez dans l’image. Mode {mode_label}.')


def _monoplot_intersect_image_uv(self, u, v):
    ctx = _monoplot_current_camera_context(self)
    z_sampler_active = ctx.get('z_sampler_active') if ctx else None
    if not ctx or z_sampler_active is None:
        return None, 'pas de topographie pour ce point'
    az_abs, pitch_abs = _monoplot_angles_from_uv(self, u, v, ctx)
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
            return None, 'pas de topographie pour ce point'
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
        return None, 'aucune intersection terrain trouvée'
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
    rec['_click_uv'] = (float(u), float(v))
    rec['_visible_uv'] = (float(u), float(v))
    return rec, None


def _monoplot_handle_image_click_uv(self, u, v):
    rec, err = _monoplot_intersect_image_uv(self, u, v)
    if rec is None:
        try:
            self.iface.messageBar().pushMessage(tr('QCALVIEW'), tr(err or 'Aucune intersection terrain trouvée'), level=QC.Qgis_MessageLevel_Warning, duration=4)
        except Exception:
            pass
        _mp_log(self, err or 'Aucune intersection terrain trouvée')
        return
    _monoplot_add_record(self, rec, add_ray=True)
    try:
        self.render_preview()
    except Exception:
        pass


def clear_monoplot_reperes(self):
    self._monoplot_records = []
    self._monoplot_visible = []
    self._monoplot_counter = 0
    _monoplot_invalidate_overlay(self)
    for attr in ('_monoplot_points_layer', '_monoplot_rays_layer'):
        lyr = getattr(self, attr, None)
        if isinstance(lyr, QgsVectorLayer) and lyr.isValid():
            ids = [f.id() for f in lyr.getFeatures()]
            if ids:
                lyr.dataProvider().deleteFeatures(ids)
                lyr.triggerRepaint()
    try:
        self.list_monoplot.clear()
    except Exception:
        pass
    try:
        self.render_preview()
    except Exception:
        pass
    _mp_log(self, 'Repères monoplotting effacés')


def stop_monoplot_tools(self):
    self._monoplot_active_tool = None
    self._image_pick_mode = None
    try:
        self._cancel_maptool()
    except Exception:
        pass


def _draw_monoplot_overlay(self, painter, width, height):
    _monoplot_sync_from_layers(self)
    vis = _monoplot_rebuild_visible(self, width=width, height=height)
    if not vis:
        return
    painter.save()
    pen = QPen(BLUE); pen.setWidth(2)
    painter.setPen(pen)
    font = QFont('Arial', 9)
    painter.setFont(font)
    for rec, uv in vis:
        if uv is None:
            continue
        u, v = float(uv[0]), float(uv[1])
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


def _monoplot_on_pdv_changed(self):
    try:
        _monoplot_rebuild_visible(self)
        _monoplot_invalidate_overlay(self)
        self.render_preview()
    except Exception:
        pass
