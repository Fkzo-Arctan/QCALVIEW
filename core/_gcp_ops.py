


from ._i18n import tr
from ._compat import QC, dialog_exec
"""SPLIT-ONLY extracted implementations from qcalview_window.QCalViewDock.
Attached to the class via setattr after class definition.
"""
import os, sys, math, json, re, pathlib, functools, itertools, typing
import numpy as np
from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.core import *
from qgis.gui import *
from ..projector import (project_point, hfov_from_focal_sensor, vfov_from_hfov_ratio, _validated_vfov_for_cylindrical)


try:
    from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QFont, QPixmap, QTransform
except Exception:
    QImage = QtGui.QImage; QPainter = QtGui.QPainter; QPen = QtGui.QPen
    QColor = QtGui.QColor; QFont = QtGui.QFont; QPixmap = QtGui.QPixmap
    QTransform = getattr(QtGui, 'QTransform', None)
try:
    from qgis.PyQt.QtCore import Qt, QSize, QRect, QPointF, QPoint
except Exception:
    Qt = getattr(QtCore, 'Qt', None); QSize = QtCore.QSize; QRect = QtCore.QRect
    QPointF = QtCore.QPointF; QPoint = QtCore.QPoint
try:
    from qgis.PyQt.QtWidgets import QWidget, QLabel
except Exception:
    QWidget = QtWidgets.QWidget; QLabel = QtWidgets.QLabel


def _normalize_azimuth_360(value, fallback=0.0):
    try:
        out = float(value) % 360.0
        if out < 0.0:
            out += 360.0
        return out
    except Exception:
        return float(fallback)

def _proj_grid_uv(proj_name, W, H, HFOV, VFOV, is360, alpha_deg, beta_deg):
    try:
        W = max(1, int(W)); H = max(1, int(H))
        proj_name = str(proj_name or "PINHOLE").upper()
        a = math.radians(float(alpha_deg))
        b = math.radians(float(beta_deg))
        if proj_name == "PINHOLE":
            hf = math.radians(max(1e-6, float(HFOV)))
            vf = math.radians(max(1e-6, float(VFOV)))
            if abs(a) >= (0.5 * hf - 1e-9) or abs(b) >= (0.5 * vf - 1e-9):
                return None
            ca = math.cos(a)
            if abs(ca) <= 1e-9:
                return None
            fx = (W * 0.5) / math.tan(hf * 0.5)
            fy = (H * 0.5) / math.tan(vf * 0.5)
            u = W * 0.5 + fx * math.tan(a)
            v = H * 0.5 - fy * (math.tan(b) / ca)
            return (u, v) if (math.isfinite(u) and math.isfinite(v)) else None
        if proj_name == "CYLINDRICAL":
            hf = math.radians(max(1e-6, float(HFOV)))
            vf = _validated_vfov_for_cylindrical(VFOV, HFOV, W, H)
            if not bool(is360) and abs(a) > (0.5 * hf + 1e-9):
                return None
            if abs(b) > (0.5 * vf + 1e-9):
                return None
            fx = W / max(1e-9, hf)
            fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
            if bool(is360):
                u = (a + math.pi) / (2.0 * math.pi) * W
            else:
                u = W * 0.5 + fx * a
            
            v = H * 0.5 - fy * math.tan(b)
            return (u, v) if (math.isfinite(u) and math.isfinite(v)) else None
        
        if bool(is360):
            u = (a + math.pi) / (2.0 * math.pi) * W
            v = (math.pi * 0.5 - b) / math.pi * H
            return (u, v) if (math.isfinite(u) and math.isfinite(v)) else None
        hf = math.radians(max(1e-6, float(HFOV)))
        vf = math.radians(max(1e-6, float(VFOV)))
        if abs(a) > (0.5 * hf + 1e-9) or abs(b) > (0.5 * vf + 1e-9):
            return None
        u = W * 0.5 * (1.0 + a / (hf * 0.5))
        v = H * 0.5 * (1.0 - b / (vf * 0.5))
        return (u, v) if (math.isfinite(u) and math.isfinite(v)) else None
    except Exception:
        return None




def _nice_rounded_step(span_value, target_ticks=10):
    try:
        span = abs(float(span_value))
    except Exception:
        span = 0.0
    if span <= 0.0:
        return 5.0
    raw = max(5.0, span / max(1.0, float(target_ticks)))
    base = 10.0 ** math.floor(math.log10(raw))
    for mult in (0.5, 1.0, 2.0, 5.0, 10.0):
        step = 5.0 * mult * base
        if step >= raw - 1e-9:
            return step
    return 5.0 * base * 10.0

def _draw_projection_grid_overlay(self, p, proj, W, H, HFOV, VFOV, is360):
    if not bool(getattr(self, 'cb_proj_grid_enable', None) and self.cb_proj_grid_enable.isChecked()):
        return
    step_deg = float(getattr(self, 'd_proj_grid_step', None).value()) if hasattr(self, 'd_proj_grid_step') else 5.0
    step_deg = max(5.0, min(45.0, step_deg))
    proj_name = str(proj or 'PINHOLE').upper()
    color = QColor(255, 165, 0, 185)
    major_color = QColor(255, 128, 0, 235)
    viewer_scale = 1.0
    try:
        if getattr(self, 'viewer', None) is not None and self.viewer.isVisible():
            viewer_scale = max(1.0, float(getattr(self.viewer, '_scale', 1.0)))
    except Exception:
        viewer_scale = 1.0
    base_w = float(int(getattr(self, 'spin_calib_width', None).value()) if hasattr(self, 'spin_calib_width') else 1)
    base_w = max(1.5, base_w + min(2.5, 0.6 * max(0.0, viewer_scale - 1.0)))

    def draw_curve(samples, pen_obj):
        prev = None
        p.setPen(pen_obj)
        for uv in samples:
            if uv is None:
                prev = None
                continue
            q = QPointF(float(uv[0]), float(uv[1]))
            if prev is not None:
                p.drawLine(prev, q)
            prev = q

    if proj_name == 'EQUIRECT' and bool(is360):
        alpha_vals = list(range(-180, 181, int(step_deg)))
        beta_vals = list(range(-90, 91, int(step_deg)))
        sample_beta = [b for b in range(-90, 91, max(1, int(step_deg/2.0)))]
        sample_alpha = [a for a in range(-180, 181, max(1, int(step_deg/2.0)))]
    else:
        a_half = float(HFOV) * 0.5
        b_half = float(VFOV) * 0.5
        alpha_vals = [x for x in np.arange(-a_half, a_half + 0.5 * step_deg, step_deg)]
        beta_vals = [y for y in np.arange(-b_half, b_half + 0.5 * step_deg, step_deg)]
        sample_beta = [y for y in np.arange(-b_half, b_half + 0.25 * step_deg, max(0.5, step_deg / 4.0))]
        sample_alpha = [x for x in np.arange(-a_half, a_half + 0.25 * step_deg, max(0.5, step_deg / 4.0))]

    p.save()
    for a_deg in alpha_vals:
        is_major = (abs((float(a_deg) / 15.0) - round(float(a_deg) / 15.0)) < 1e-6) or abs(float(a_deg)) < 1e-6
        pen = QPen(major_color if is_major else color)
        pen.setWidthF(base_w + (1.0 if is_major else 0.0))
        pen.setStyle(QC.Qt_PenStyle_SolidLine)
        curve = [_proj_grid_uv(proj_name, W, H, HFOV, VFOV, is360, a_deg, b_deg) for b_deg in sample_beta]
        draw_curve(curve, pen)
    for b_deg in beta_vals:
        is_major = (abs((float(b_deg) / 15.0) - round(float(b_deg) / 15.0)) < 1e-6) or abs(float(b_deg)) < 1e-6
        pen = QPen(major_color if is_major else color)
        pen.setWidthF(base_w + (1.0 if is_major else 0.0))
        pen.setStyle(QC.Qt_PenStyle_SolidLine)
        curve = [_proj_grid_uv(proj_name, W, H, HFOV, VFOV, is360, a_deg, b_deg) for a_deg in sample_alpha]
        draw_curve(curve, pen)
    p.restore()


def _draw_calib_grid(self, p, cam_pt, cam_z, cam_crs, proj, W, H, yaw_eff, pitch, roll, HFOV, VFOV, is360, z_sampler):
    
    _draw_projection_grid_overlay(self, p, proj, W, H, HFOV, VFOV, is360)
    if not bool(getattr(self, 'cb_calib_enable', None) and self.cb_calib_enable.isChecked()):
        return

    
    typ = self.cmb_calib_type.currentText()
    S   = float(self.d_calib_spacing.value())
    Lx  = float(self.d_calib_width.value())
    Ly  = float(self.d_calib_depth.value())
    Hz  = float(self.d_calib_height.value())
    D   = float(self.d_calib_dist.value())
    dz  = float(self.d_calib_elev.value())

    
    pen = QPen(self._calib_color); pen.setWidth(int(self.spin_calib_width.value()))
    p.setPen(pen)

    
    yr = math.radians(yaw_eff)
    fxy = (math.sin(yr), math.cos(yr))          
    rxy = (math.cos(yr), -math.sin(yr))         

    
    Cx = cam_pt.x() + D * fxy[0]
    Cy = cam_pt.y() + D * fxy[1]

    
    
    cam_ground_z = cam_z - float(self.d_camheight.value())
    Z0 = cam_ground_z
    if self.cb_calib_snap_dem.isChecked() and z_sampler is not None:
        try:
            Z0 = float(z_sampler(QgsPointXY(Cx, Cy)))
        except Exception:
            pass
    Z0 += dz  

    
    def proj_xy_z(x, y, z):
        return self._finite_uv(project_point(cam_pt, cam_z, QgsPointXY(x, y), None,
                                             proj, W, H, yaw_eff, pitch, roll, HFOV, VFOV, is360,
                                             dist_max=self.d_maxdist.value() if self.d_maxdist.value() > 0 else None,
                                             z_tgt=z, z_sampler=None))

    def draw_seg(P, Q):
        try:
            seg_len = math.sqrt((Q[0]-P[0])**2 + (Q[1]-P[1])**2 + (Q[2]-P[2])**2)
        except Exception:
            seg_len = 0.0
        proj_name = str(proj or '').upper()
        if proj_name in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
            samples = max(12, min(144, int(max(1.0, seg_len) / max(0.5, S) * 8.0)))
        else:
            samples = max(2, min(24, int(max(1.0, seg_len) / max(0.5, S) * 2.0)))
        prev = None
        for t in np.linspace(0.0, 1.0, samples):
            x = P[0] + (Q[0] - P[0]) * float(t)
            y = P[1] + (Q[1] - P[1]) * float(t)
            z = P[2] + (Q[2] - P[2]) * float(t)
            uv = proj_xy_z(x, y, z)
            if uv is None:
                prev = None
                continue
            q = QPointF(float(uv[0]), float(uv[1]))
            if prev is not None:
                if bool(is360) and abs(q.x() - prev.x()) > (0.45 * float(W)):
                    prev = q
                    continue
                p.drawLine(prev, q)
            prev = q

    
    if S <= 0.0 or Lx <= 0.0:
        return
    max_lines = 400

    if typ == "Plan au sol":
        
        half_x = Lx * 0.5
        half_y = max(Ly, S) * 0.5
        
        n1 = int(math.floor(Lx / S))
        n2 = int(math.floor(Ly / S))
        n1 = max(1, min(n1, max_lines))
        n2 = max(1, min(n2, max_lines))
        for i in range(-n1//2, n1//2 + 1):
            ox = i * S * rxy[0]; oy = i * S * rxy[1]
            P = (Cx - half_y * fxy[0] + ox, Cy - half_y * fxy[1] + oy, Z0)
            Q = (Cx + half_y * fxy[0] + ox, Cy + half_y * fxy[1] + oy, Z0)
            draw_seg(P, Q)
        
        for j in range(-n2//2, n2//2 + 1):
            oy = j * S * fxy[1]; ox = j * S * fxy[0]
            P = (Cx - half_x * rxy[0] + ox, Cy - half_x * rxy[1] + oy, Z0)
            Q = (Cx + half_x * rxy[0] + ox, Cy + half_x * rxy[1] + oy, Z0)
            draw_seg(P, Q)

        
        if self.cb_calib_axes.isChecked():
            
            ax_len = max(5.0, min(Lx, Ly, Hz if Hz>0 else 100.0) * 0.25)
            uvC = proj_xy_z(Cx, Cy, Z0)
            if uvC:
                
                p.setPen(QPen(QColor(255,80,80,220), int(self.spin_calib_width.value()+1)))
                draw_seg((Cx, Cy, Z0), (Cx + ax_len*rxy[0], Cy + ax_len*rxy[1], Z0))
                
                p.setPen(QPen(QColor(80,220,80,220), int(self.spin_calib_width.value()+1)))
                draw_seg((Cx, Cy, Z0), (Cx + ax_len*fxy[0], Cy + ax_len*fxy[1], Z0))
                
                p.setPen(QPen(QColor(80,120,255,220), int(self.spin_calib_width.value()+1)))
                draw_seg((Cx, Cy, Z0), (Cx, Cy, Z0 + ax_len))
                p.setPen(pen)

        if self.cb_calib_labels.isChecked():
            p.save()
            p.setFont(QFont("Arial", max(8, int(12 * max(0.5, W/4000.0)))))
            p.setPen(QPen(self._calib_color))
            span = max(Ly, S)
            label_step = _nice_rounded_step(span, target_ticks=10)
            start_val = math.ceil((-half_y) / label_step) * label_step
            end_val = half_y + 1e-9
            val = start_val
            while val <= end_val:
                uv = proj_xy_z(Cx + val * fxy[0], Cy + val * fxy[1], Z0)
                if uv:
                    p.drawText(int(uv[0] + 6), int(uv[1] - 6), f"{int(round(val / 5.0) * 5)} m")
                val += label_step
            p.restore()

    elif typ == "Plan vertical":
        
        half_x = Lx * 0.5
        z0 = Z0
        nX = max(1, min(int(math.floor(Lx / S)), max_lines))
        nZ = max(1, min(int(math.floor(max(Hz, S) / S)), max_lines))
        
        for i in range(-nX//2, nX//2 + 1):
            ox = i * S * rxy[0]; oy = i * S * rxy[1]
            P = (Cx + ox, Cy + oy, z0)
            Q = (Cx + ox, Cy + oy, z0 + Hz)
            draw_seg(P, Q)
        
        for k in range(0, nZ + 1):
            z = z0 + k * S
            P = (Cx - half_x * rxy[0], Cy - half_x * rxy[1], z)
            Q = (Cx + half_x * rxy[0], Cy + half_x * rxy[1], z)
            draw_seg(P, Q)

    else:  
        
        half_x = Lx * 0.5; half_y = max(Ly, S) * 0.5; half_z = Hz * 0.5
        zc = Z0 + half_z
        
        def V(sx, sy, sz):
            return (Cx + sx*half_x*rxy[0] + sy*half_y*fxy[0],
                    Cy + sx*half_x*rxy[1] + sy*half_y*fxy[1],
                    zc + sz*half_z)
        V000 = V(-1,-1,-1); V100 = V(1,-1,-1); V110 = V(1,1,-1); V010 = V(-1,1,-1)
        V001 = V(-1,-1, 1); V101 = V(1,-1, 1); V111 = V(1,1, 1); V011 = V(-1,1, 1)
        edges = [
            (V000, V100), (V100, V110), (V110, V010), (V010, V000),  
            (V001, V101), (V101, V111), (V111, V011), (V011, V001),  
            (V000, V001), (V100, V101), (V110, V111), (V010, V011)   
        ]
        for P, Q in edges:
            draw_seg(P, Q)

def _on_map_pick_for_gcp(self, map_pt):
    try:
        cam_layer = self.cmb_camera.currentLayer()
        if not cam_layer or cam_layer.featureCount() < 1:
            self._cancel_maptool(); self._adding_gcp_uv = None; return
        cam_crs = cam_layer.crs()
        
        src = self.iface.mapCanvas().mapSettings().destinationCrs()
        tr = QgsCoordinateTransform(src, cam_crs, QgsProject.instance())
        pt_cam = tr.transform(map_pt)

        
        z = 0.0
        dem_layer = self.cmb_dem.currentLayer()
        if isinstance(dem_layer, QgsRasterLayer) and dem_layer.isValid():
            z_sampler, _ = self._make_z_sampler(dem_layer, cam_crs)
            try:
                z = float(z_sampler(QgsPointXY(pt_cam.x(), pt_cam.y())))
            except Exception:
                z = 0.0

        if self._adding_gcp_uv is None:
            self._cancel_maptool(); return
        u, v = self._adding_gcp_uv
        self.gcps.append({'u': float(u), 'v': float(v), 'x': float(pt_cam.x()), 'y': float(pt_cam.y()), 'z': float(z)})
        self.list_gcp.addItem(tr(f"UV=({int(u)},{int(v)})  XY=({pt_cam.x():.2f},{pt_cam.y():.2f},{z:.2f})"))
        self._adding_gcp_uv = None
        self._cancel_maptool()
        self._refresh_gcp_markers()
        self.render_preview()
    except Exception as e:
        self._cancel_maptool()
        self._adding_gcp_uv = None
        self.lbl_info.setText(tr(f"Erreur ajout GCP: {e}"))

def start_pick_pdv_center(self):
    
    canvas = self.iface.mapCanvas()
    self._maptool_backup = canvas.mapTool()
    
    canvas.setMapTool(MapPointTool(canvas, self._on_map_pick_pdv_center))
    if hasattr(self, "lbl_info"):
        self.lbl_info.setText(tr("clic on canvas"))


def _on_map_pick_pdv_center(self, map_pt):
    
    try:
        cam_layer = self.cmb_camera.currentLayer()
        if not isinstance(cam_layer, QgsVectorLayer) or cam_layer.featureCount() < 1:
            if hasattr(self, "lbl_info"):
                self.lbl_info.setText(tr("Aucune couche caméra valide."))
            self._cancel_maptool()
            return

        try:
            cam_feat = self._camera_current_feature()
        except Exception:
            cam_feat = None
        if cam_feat is None or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            cam_feat = next(cam_layer.getFeatures(), None)
        if cam_feat is None:
            self._cancel_maptool()
            return

        cam_pt, work_crs = self._camera_point_in_work_crs(cam_feat)
        if cam_pt is None or work_crs is None:
            self._camera_warn_if_non_metric_project(notify=True)
            self._cancel_maptool()
            return

        src = self.iface.mapCanvas().mapSettings().destinationCrs()
        pt_work = QgsCoordinateTransform(src, work_crs, QgsProject.instance()).transform(map_pt)
        dx = float(pt_work.x() - cam_pt.x())
        dy = float(pt_work.y() - cam_pt.y())
        az_deg = self._azimuth_deg(dx, dy)

        if hasattr(self, "set_pdv_azimuth"):
            self.set_pdv_azimuth(az_deg)
        else:
            self.current_pdv_azimuth = float(az_deg)
            try:
                self.render_preview()
            except Exception:
                pass
        try:
            self.iface.messageBar().pushMessage(tr("Centre image"), tr(f"Azimut PDV ≈ {az_deg:.2f}°"), level=QC.Qgis_MessageLevel_Info, duration=4)
        except Exception:
            pass
        try:
            self._cancel_maptool()
        except Exception:
            pass
    except Exception as e:
        if hasattr(self, "lbl_info"):
            self.lbl_info.setText(tr(f"Erreur centre image : {e}"))
        try:
            self._cancel_maptool()
        except Exception:
            pass


def _start_add_gcp(self):
    
    self._adding_gcp_uv = (0, 0)  
    self.lbl_info.setText(tr("Ajout GCP : cliquez d'abord dans la photo, puis sur la carte…"))

def _delete_gcp(self):
    row = self.list_gcp.currentRow()
    if 0 <= row < len(self.gcps):
        self.gcps.pop(row)
        self.list_gcp.takeItem(row)
        self._refresh_gcp_markers()
        self.render_preview()

def _clear_gcps(self):
    self.gcps.clear()
    self.list_gcp.clear()
    self._clear_gcp_markers()
    self.render_preview()

def _clear_gcp_markers(self):
    canvas = self.iface.mapCanvas()
    for m in self._gcp_markers:
        try:
            canvas.scene().removeItem(m)
        except Exception:
            try: m.setVisible(False)
            except Exception: pass
    self._gcp_markers = []

def _ensure_fov_rubberbands(self):
    
    canvas = self.iface.mapCanvas()
    
    if not hasattr(self, "_rb_dir") or self._rb_dir is None:
        self._rb_dir = QgsRubberBand(canvas, QC.QgsWkbTypes_GeometryType_LineGeometry)
        self._rb_dir.setColor(QColor(200, 60, 60, 220))  
        self._rb_dir.setWidth(2)
    
    if not hasattr(self, "_rb_fov") or self._rb_fov is None:
        self._rb_fov = QgsRubberBand(canvas, QC.QgsWkbTypes_GeometryType_PolygonGeometry)
        self._rb_fov.setColor(QColor(60, 120, 220, 80))  
        self._rb_fov.setWidth(2)

def _connect_fov_signals(self):
    
    widgets = [self.d_yaw, self.d_hfov, self.cb_360, self.d_maxdist, self.cmb_camera]
    for w in widgets:
        for sig in ('valueChanged', 'currentIndexChanged', 'toggled'):
            if hasattr(w, sig):
                try:
                    getattr(w, sig).connect(self._update_canvas_fov)
                except Exception:
                    pass
    
    try:
        self.iface.mapCanvas().destinationCrsChanged.connect(self._update_canvas_fov)
    except Exception:
        pass

    
    layer = self.cmb_camera.currentLayer()
    if isinstance(layer, QgsVectorLayer):
        try: layer.geometryChanged.connect(self._update_canvas_fov)
        except Exception: pass
        try: layer.committedGeometriesChanges.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass
        try: layer.featureAdded.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass
        try: layer.featuresDeleted.connect(lambda *a, **k: self._update_canvas_fov())
        except Exception: pass


def _refresh_gcp_markers(self):
    self._clear_gcp_markers()
    canvas = self.iface.mapCanvas()
    for g in self.gcps:
        m = QgsVertexMarker(canvas)
        m.setCenter(QgsPointXY(g['x'], g['y']))
        m.setIconType(QC.QgsVertexMarker_IconType_ICON_CROSS)
        m.setColor(QColor(255, 80, 80, 220))
        m.setPenWidth(2)
        m.setIconSize(12)
        self._gcp_markers.append(m)

def _draw_gcps_overlay(self, painter, W, H):
    if not self.gcps:
        return
    pen = QPen(QColor(255, 80, 80, 220)); pen.setWidth(2)
    painter.setPen(pen)
    
    W_full, H_full = float(self.spin_w.value()), float(self.spin_h.value())
    sx = W / max(1.0, W_full); sy = H / max(1.0, H_full)
    for g in self.gcps:
        u = int(g['u'] * sx); v = int(g['v'] * sy)
        painter.drawLine(u-6, v,   u+6, v)
        painter.drawLine(u,   v-6, u,   v+6)

def _solve_camera(self):
    
    if len(self.gcps) < 4:
        self.lbl_info.setText(tr("Besoin d’au moins 4 GCP pour une résolution stable."))
        return
    cam_layer = self.cmb_camera.currentLayer()
    if not cam_layer or cam_layer.featureCount() < 1:
        self.lbl_info.setText(tr("Aucune caméra (couche point) sélectionnée."))
        return
    sel = cam_layer.selectedFeatures()
    cam_feat = sel[0] if sel else next(cam_layer.getFeatures(), None)
    if cam_feat is None:
        self.lbl_info.setText(tr("Aucune entité caméra trouvée (sélectionnée ou non)."))
        return
    cam_pt = cam_feat.geometry().asPoint()
    cam_crs = cam_layer.crs()


    
    cam_ground_z = 0.0
    dem_layer = self.cmb_dem.currentLayer()
    z_sampler = None
    need_dem = isinstance(dem_layer, QgsRasterLayer) and dem_layer.isValid()
    if need_dem:
        z_sampler, _ = self._make_z_sampler(dem_layer, cam_crs)
    if z_sampler and self.grp_dem.isChecked() and self.cb_use_dem_z.isChecked():
        try:
            cam_ground_z = float(z_sampler(QgsPointXY(cam_pt.x(), cam_pt.y())))
        except Exception:
            cam_ground_z = 0.0
    cam_z = cam_ground_z + float(self.d_camheight.value())

    
    W_full, H_full = int(self.spin_w.value()), int(self.spin_h.value())
    proj = self.cmb_proj.currentText()
    is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
    proj_upper = str(proj).strip().upper()
    is_proj_360 = bool(
        proj_upper in ("EQUIRECT", "EQUIRECTANGULAR")
        and self.cb_360.isChecked()
    )
    is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (
        is_proj_360 or (proj_upper == 'CYLINDRICAL' and float(self.d_hfov.value()) >= 359.999)
    )
    maxdist = None if float(self.d_maxdist.value()) <= 0 else float(self.d_maxdist.value())

    
    params0 = [float(self.d_yaw.value()), float(self.d_pitch.value()),
               float(self.d_roll.value()), float(self.d_hfov.value())]
    mask = [self.cb_sol_yaw.isChecked(), self.cb_sol_pitch.isChecked(),
            self.cb_sol_roll.isChecked(), self.cb_sol_hfov.isChecked()]
    idxs = [i for i, m in enumerate(mask) if m]
    if not idxs:
        self.lbl_info.setText(tr("Choisissez au moins un paramètre (Yaw/Pitch/Roll/HFOV)."))
        return
    if is_proj_360:
        params0[3] = 360.0  
        
        
        if 3 in idxs:
            idxs.remove(3)
        
    def params_full(vec):
        out = params0[:]
        for k, i in enumerate(idxs): out[i] = float(vec[k])
        return out
    x0_vec = [params0[i] for i in idxs]

    def reproj_rms(vec):
        yaw, pitch, roll, HFOV = params_full(vec)
        VFOV = 40.0 if proj_upper == 'CYLINDRICAL' else vfov_from_hfov_ratio(HFOV, W_full, H_full)
        sse = 0.0; wsum = 0.0; n = 0
        for g in self.gcps:
            uv = project_point(cam_pt, cam_z, QgsPointXY(g['x'], g['y']), None,
                               proj, W_full, H_full, yaw, pitch, roll, HFOV, VFOV, is360,
                               dist_max=maxdist, z_tgt=g['z'], z_sampler=None)
            if uv is None:
                continue
            du = (uv[0]-g['u']); dv = (uv[1]-g['v'])
            dx = g['x'] - cam_pt.x(); dy = g['y'] - cam_pt.y()
            dist = (dx*dx + dy*dy) ** 0.5
            w = max(1e-6, dist)      
            sse  += w * (du*du + dv*dv)
            wsum += w
            n += 1
        return 1e9 if n == 0 else (sse / max(1e-6, wsum)) ** 0.5


    def nelder_mead(f, x_init, step=2.0, max_iter=250, tol=0.5):
        import numpy as np
        x = np.array(x_init, dtype=float)   
        n = x.size
        simplex = [x]
        for i in range(n):
            xi = x.copy()
            xi[i] += (step if i < 3 else max(1.0, step))  
            simplex.append(xi)
        simplex = np.array(simplex)
        vals = np.array([f(s) for s in simplex])
    
        for _ in range(max_iter):
            order = np.argsort(vals)
            simplex = simplex[order]; vals = vals[order]
            if np.std(vals) < tol:
                break
            x_best = simplex[0]
            x_cent = simplex[:-1].mean(axis=0)
            
            xr = x_cent + (x_cent - simplex[-1])
            fr = f(xr)
            if fr < vals[0]:
                
                xe = x_cent + 2.0*(x_cent - simplex[-1])
                fe = f(xe)
                simplex[-1] = (xe if fe < fr else xr)
                vals[-1] = min(fe, fr)
            else:
                
                xc = x_cent + 0.5*(simplex[-1] - x_cent)
                fc = f(xc)
                if fc < vals[-1]:
                    simplex[-1] = xc; vals[-1] = fc
                else:
                    
                    for i in range(1, n+1):
                        simplex[i] = simplex[0] + 0.5*(simplex[i] - simplex[0])
                    vals = np.array([f(s) for s in simplex])
        order = np.argsort(vals)
        simplex = simplex[order]; vals = vals[order]
        return simplex[0], vals[0]


    
    x_opt, err = nelder_mead(reproj_rms, x_init=x0_vec, step=2.0, max_iter=250, tol=0.5)

    yaw, pitch, roll, HFOV = params_full(x_opt)
    self.d_yaw.blockSignals(True); self.d_yaw.setValue(_normalize_azimuth_360(yaw, fallback=self.d_yaw.value())); self.d_yaw.blockSignals(False)
    self.d_pitch.blockSignals(True); self.d_pitch.setValue(float(pitch)); self.d_pitch.blockSignals(False)
    self.d_roll.blockSignals(True); self.d_roll.setValue(float(roll)); self.d_roll.blockSignals(False)
    self.d_hfov.blockSignals(True); self.d_hfov.setValue(float(HFOV)); self.d_hfov.blockSignals(False)
    vfov_out = 40.0 if proj_upper == 'CYLINDRICAL' else vfov_from_hfov_ratio(float(HFOV), W_full, H_full)
    self.d_vfov.blockSignals(True); self.d_vfov.setValue(vfov_out); self.d_vfov.blockSignals(False)
    self.lbl_info.setText(tr(f"Résolution OK — RMS ≈ {err:.2f} px"))
    self._update_canvas_fov()
    
    if is_proj_360:
        HFOV = 360.0
        self.d_hfov.blockSignals(True)
        self.d_hfov.setValue(360.0)
        self.d_hfov.setEnabled(False)  
        self.d_hfov.blockSignals(False)
    else:
        self.d_hfov.blockSignals(True)
        self.d_hfov.setEnabled(True)
        self.d_hfov.setValue(float(HFOV))
        self.d_hfov.blockSignals(False)

    
    self.d_vfov.blockSignals(True)
    self.d_vfov.setValue(40.0 if proj_upper == 'CYLINDRICAL' else vfov_from_hfov_ratio(float(HFOV), W_full, H_full))
    self.d_vfov.blockSignals(False)
    self.render_preview()

def _update_canvas_fov(self):
    
    try:
        canvas = self.iface.mapCanvas()
        self._ensure_fov_rubberbands()

        cam_layer = self.cmb_camera.currentLayer()
        if not isinstance(cam_layer, QgsVectorLayer) or cam_layer.featureCount() < 1:
            return

        try:
            cam_feat = self._camera_current_feature()
        except Exception:
            cam_feat = None
        if cam_feat is None or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            sel = cam_layer.selectedFeatures()
            cam_feat = sel[0] if sel else next(cam_layer.getFeatures(), None)
        if cam_feat is None or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            return

        try:
            cam_pt, work_crs = self._camera_point_in_work_crs(cam_feat)
        except Exception:
            cam_pt, work_crs = None, None
        if cam_pt is None or work_crs is None:
            return

        dst = canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        to_canvas = QgsCoordinateTransform(work_crs, dst, project)
        p0 = to_canvas.transform(QgsPointXY(cam_pt.x(), cam_pt.y()))

        proj_upper = ""
        if hasattr(self, "cmb_proj") and self.cmb_proj is not None:
            try:
                proj_upper = str(self.cmb_proj.currentText()).strip().upper()
            except Exception:
                pass
        is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (
            bool(self.cb_360.isChecked()) and proj_upper in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL")
        )

        az = math.radians(float(self.d_yaw.value()))
        hfov_deg = 360.0 if is360 else float(self.d_hfov.value())
        hfov = math.radians(hfov_deg)

        
        L = 200.0
        md = float(self.d_maxdist.value())
        if md > 0:
            L = max(50.0, min(500.0, md / 5.0))

        if self._rb_dir:
            self._rb_dir.reset(False)
            p1_work = QgsPointXY(cam_pt.x() + L * math.sin(az), cam_pt.y() + L * math.cos(az))
            p1 = to_canvas.transform(p1_work)
            self._rb_dir.addPoint(QgsPointXY(p0.x(), p0.y()), False)
            self._rb_dir.addPoint(QgsPointXY(p1.x(), p1.y()), True)
            self._rb_dir.show()

        if self._rb_fov:
            pitch_deg = float(self.d_pitch.value())
            col = QColor(60, 120, 220, 80)
            if pitch_deg > 10:
                col = QColor(60, 180, 80, 80)
            elif pitch_deg < -10:
                col = QColor(220, 160, 60, 80)
            self._rb_fov.setColor(col)

            self._rb_fov.reset(True)
            n = 64
            a0 = az - hfov * 0.5
            a1 = az + hfov * 0.5
            self._rb_fov.addPoint(QgsPointXY(p0.x(), p0.y()), False)
            for k in range(n + 1):
                a = a0 + (a1 - a0) * k / n
                pk_work = QgsPointXY(cam_pt.x() + L * math.sin(a), cam_pt.y() + L * math.cos(a))
                pk = to_canvas.transform(pk_work)
                self._rb_fov.addPoint(QgsPointXY(pk.x(), pk.y()), False)
            self._rb_fov.addPoint(QgsPointXY(p0.x(), p0.y()), True)
            self._rb_fov.show()
    except Exception:
        pass

        
def _ensure_nav_overlays(self):
    canvas = self.iface.mapCanvas()
    if not hasattr(self, "_rb_pick") or self._rb_pick is None:
        self._rb_pick = QgsRubberBand(canvas, QC.QgsWkbTypes_GeometryType_LineGeometry)
        self._rb_pick.setColor(QColor(20, 220, 120, 220))
        self._rb_pick.setWidth(2)
    if not hasattr(self, "_vm_pick") or self._vm_pick is None:
        self._vm_pick = QgsVertexMarker(canvas)
        self._vm_pick.setColor(QColor(20, 220, 120, 220))
        self._vm_pick.setIconType(QC.QgsVertexMarker_IconType_ICON_CROSS)
        self._vm_pick.setIconSize(14)
        self._vm_pick.setPenWidth(3)
        self._vm_pick.hide()


def start_pick_view_from_canvas(self):
    canvas = self.iface.mapCanvas()
    self._maptool_backup = canvas.mapTool()
    self._image_pick_mode = None
    canvas.setMapTool(MapPointTool(canvas, self._on_map_pick_set_view))
    if hasattr(self, "lbl_nav_state"):
        self.lbl_nav_state.setText(tr("Mode navigation : cliquez un point dans le canevas pour orienter la vue"))
    if hasattr(self, "lbl_info"):
        self.lbl_info.setText(tr("Visée carte → image active"))


def _on_map_pick_set_view(self, map_pt):
    try:
        cam_layer = self.cmb_camera.currentLayer()
        if not isinstance(cam_layer, QgsVectorLayer) or cam_layer.featureCount() < 1:
            if hasattr(self, "lbl_info"):
                self.lbl_info.setText(tr("Aucune couche caméra valide."))
            return
        try:
            cam_feat = self._camera_current_feature()
        except Exception:
            cam_feat = None
        if cam_feat is None or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            cam_feat = next(cam_layer.getFeatures(), None)
        if cam_feat is None:
            return

        cam_pt, work_crs = self._camera_point_in_work_crs(cam_feat)
        if cam_pt is None or work_crs is None:
            self._camera_warn_if_non_metric_project(notify=True)
            return
        src = self.iface.mapCanvas().mapSettings().destinationCrs()
        pt_work = QgsCoordinateTransform(src, work_crs, QgsProject.instance()).transform(map_pt)
        dx = float(pt_work.x() - cam_pt.x())
        dy = float(pt_work.y() - cam_pt.y())
        az_deg = self._azimuth_deg(dx, dy)
        try:
            self.d_yaw.blockSignals(True)
            self.d_yaw.setValue(_normalize_azimuth_360(float(az_deg) - float(self.d_yaw_offset.value()), fallback=self.d_yaw.value()))
        finally:
            self.d_yaw.blockSignals(False)
        if hasattr(self, "set_pdv_azimuth"):
            self.set_pdv_azimuth(az_deg)
        else:
            self.current_pdv_azimuth = float(az_deg)
        pitch_done = False
        if getattr(self, "cb_pick_sets_pitch", None) is not None and self.cb_pick_sets_pitch.isChecked():
            try:
                dem_layer = self.cmb_dem.currentLayer()
                if isinstance(dem_layer, QgsRasterLayer) and dem_layer.isValid():
                    z_sampler, _ = self._make_z_sampler(dem_layer, work_crs)
                    z_cam_ground = float(z_sampler(QgsPointXY(cam_pt.x(), cam_pt.y())))
                    z_tgt = float(z_sampler(QgsPointXY(pt_work.x(), pt_work.y())))
                    cam_z = z_cam_ground + float(self.d_camheight.value())
                    dist_xy = math.hypot(dx, dy)
                    if dist_xy > 1e-6:
                        pitch_deg = math.degrees(math.atan2(z_tgt - cam_z, dist_xy))
                        self.d_pitch.blockSignals(True)
                        self.d_pitch.setValue(float(pitch_deg))
                        self.d_pitch.blockSignals(False)
                        pitch_done = True
            except Exception:
                pitch_done = False
        self._update_canvas_fov()
        self.render_preview()
        msg = f"Visée mise à jour : azimut {az_deg:.2f}°"
        if pitch_done:
            msg += f" · tangage {float(self.d_pitch.value()):.2f}°"
        if hasattr(self, "lbl_info"):
            self.lbl_info.setText(tr(msg))
        if hasattr(self, "lbl_nav_state"):
            self.lbl_nav_state.setText(tr("Mode navigation : visée carte → image appliquée"))
        try:
            self.iface.messageBar().pushMessage(tr("QCALVIEW"), tr(msg), level=QC.Qgis_MessageLevel_Info, duration=4)
        except Exception:
            pass
    except Exception as e:
        if hasattr(self, "lbl_info"):
            self.lbl_info.setText(tr(f"Erreur visée carte → image : {e}"))
    finally:
        try:
            self._cancel_maptool()
        except Exception:
            pass


def start_image_to_canvas_pick(self):
    self._image_pick_mode = "mapray"
    if hasattr(self, "lbl_nav_state"):
        self.lbl_nav_state.setText(tr("Mode navigation : cliquez dans l’image pour tracer un rayon sur la carte"))
    if hasattr(self, "lbl_info"):
        self.lbl_info.setText(tr("Cliquez dans l’aperçu ou la visionneuse pour viser dans le canevas"))
    try:
        self.iface.messageBar().pushMessage(tr("QCALVIEW"), tr("Cliquez dans l’image pour viser dans le canevas."), level=QC.Qgis_MessageLevel_Info, duration=4)
    except Exception:
        pass


def stop_interaction_tools(self):
    self._image_pick_mode = None
    try:
        self._cancel_maptool()
    except Exception:
        pass
    try:
        self._ensure_nav_overlays()
        if getattr(self, "_rb_pick", None):
            self._rb_pick.reset(False)
            self._rb_pick.hide()
        if getattr(self, "_vm_pick", None):
            self._vm_pick.hide()
    except Exception:
        pass
    if hasattr(self, "lbl_nav_state"):
        self.lbl_nav_state.setText(tr("Mode navigation : inactif"))


def _draw_canvas_pick_ray(self, az_deg, target_point=None):
    try:
        self._ensure_nav_overlays()
        cam_layer = self.cmb_camera.currentLayer()
        if not isinstance(cam_layer, QgsVectorLayer) or cam_layer.featureCount() < 1:
            return
        try:
            cam_feat = self._camera_current_feature()
        except Exception:
            cam_feat = None
        if cam_feat is None or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            cam_feat = next(cam_layer.getFeatures(), None)
        if cam_feat is None:
            return

        cam_pt, work_crs = self._camera_point_in_work_crs(cam_feat)
        if cam_pt is None or work_crs is None:
            return
        canvas = self.iface.mapCanvas()
        dst = canvas.mapSettings().destinationCrs()
        project = QgsProject.instance()
        to_canvas = QgsCoordinateTransform(work_crs, dst, project)
        p0 = to_canvas.transform(QgsPointXY(cam_pt.x(), cam_pt.y()))
        if target_point is None:
            L = float(self.d_maxdist.value()) if float(self.d_maxdist.value()) > 0 else 1000.0
            target_work = QgsPointXY(
                cam_pt.x() + L * math.sin(math.radians(az_deg)),
                cam_pt.y() + L * math.cos(math.radians(az_deg)),
            )
            p1 = to_canvas.transform(target_work)
        else:
            
            src = cam_layer.crs()
            target_work = QgsCoordinateTransform(src, work_crs, project).transform(target_point)
            p1 = to_canvas.transform(target_work)
        self._rb_pick.reset(False)
        self._rb_pick.addPoint(QgsPointXY(p0.x(), p0.y()), False)
        self._rb_pick.addPoint(QgsPointXY(p1.x(), p1.y()), True)
        self._rb_pick.show()
        if getattr(self, "_vm_pick", None):
            self._vm_pick.setCenter(QgsPointXY(p1.x(), p1.y()))
            self._vm_pick.show()
        if getattr(self, "cb_center_canvas_on_pick", None) is not None and self.cb_center_canvas_on_pick.isChecked():
            canvas.setCenter(QgsPointXY(p1.x(), p1.y()))
            canvas.refresh()
    except Exception:
        pass


def _handle_image_navigation_click_uv(self, u, v):
    try:
        W = max(1.0, float(self.spin_w.value()))
        H = max(1.0, float(self.spin_h.value()))
        proj = str(self.cmb_proj.currentText()).strip().upper()
        is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
        yaw_eff = float(self.d_yaw.value()) + float(self.d_yaw_offset.value())
        pitch = float(self.d_pitch.value())
        HFOV = max(1e-6, float(self.d_hfov.value()))
        VFOV = max(1e-6, float(self.d_vfov.value()))
        x = float(u); y = float(v)
        theta = 0.0
        beta = 0.0
        if proj == "PINHOLE":
            hf = math.radians(HFOV)
            vf = math.radians(VFOV)
            fx = (W * 0.5) / max(1e-9, math.tan(hf * 0.5))
            fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
            theta = math.degrees(math.atan2(x - W * 0.5, fx))
            beta = math.degrees(math.atan2(H * 0.5 - y, fy))
        elif proj == "CYLINDRICAL":
            hf = math.radians(HFOV)
            vf = math.radians(VFOV)
            fx_cc = W / max(1e-9, hf)
            fy_cc = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
            theta = math.degrees((x - W * 0.5) / max(1e-9, fx_cc))
            beta = math.degrees(math.atan((H * 0.5 - y) / max(1e-9, fy_cc)))
        else:
            if is360:
                theta = ((x / W) * 360.0) - 180.0
                beta = 90.0 - ((y / H) * 180.0)
            else:
                theta = ((x / W) - 0.5) * HFOV
                beta = (0.5 - (y / H)) * VFOV
        az_abs = (yaw_eff + theta) % 360.0
        pitch_abs = pitch + beta
        self.current_pdv_azimuth = float(az_abs)
        self._draw_canvas_pick_ray(az_abs)
        self.render_preview()
        if hasattr(self, "lbl_info"):
            self.lbl_info.setText(tr(f"Image → carte : azimut {az_abs:.2f}° · élévation relative {beta:+.2f}°"))
        if hasattr(self, "lbl_nav_state"):
            self.lbl_nav_state.setText(tr(f"Mode navigation : cible image → carte (az. {az_abs:.2f}°, pitch estimé {pitch_abs:.2f}°)"))
    except Exception as e:
        if hasattr(self, "lbl_info"):
            self.lbl_info.setText(tr(f"Erreur image → carte : {e}"))


class MapPointTool(QgsMapTool):
    def __init__(self, canvas, on_pick):
        super().__init__(canvas)
        self.canvas = canvas
        self.on_pick = on_pick
    def canvasReleaseEvent(self, ev):
        pt = self.canvas.getCoordinateTransform().toMapCoordinates(ev.pos().x(), ev.pos().y())
        self.on_pick(pt)        

def _image_click_to_full_uv(self, x, y):
    if self.last_preview is None:
        return None
    label_w = max(1, self.preview.width()); label_h = max(1, self.preview.height())
    img_w = self.last_preview.width(); img_h = self.last_preview.height()
    scale = min(label_w / float(img_w), label_h / float(img_h))
    disp_w = int(img_w * scale); disp_h = int(img_h * scale)
    off_x = (label_w - disp_w) // 2; off_y = (label_h - disp_h) // 2
    ix = (x - off_x) / float(scale); iy = (y - off_y) / float(scale)
    if ix < 0 or iy < 0 or ix >= img_w or iy >= img_h:
        return None
    W_full, H_full = float(self.spin_w.value()), float(self.spin_h.value())
    scale_x = W_full / float(img_w); scale_y = H_full / float(img_h)
    return (ix * scale_x, iy * scale_y)


def _dispatch_image_uv_click(self, u, v):
    mode = getattr(self, "_image_pick_mode", None)
    if mode == "mapray":
        self._handle_image_navigation_click_uv(u, v)
        return
    if mode == "monoplot_ground":
        try:
            self._monoplot_handle_image_click_uv(u, v)
        except Exception as e:
            try:
                self.lbl_info.setText(tr(f"Erreur image → terrain : {e}"))
            except Exception:
                pass
        return
    if self._adding_gcp_uv is None:
        return
    self._adding_gcp_uv = (u, v)
    canvas = self.iface.mapCanvas()
    self._maptool_backup = canvas.mapTool()
    canvas.setMapTool(MapPointTool(canvas, self._on_map_pick_for_gcp))
    self.lbl_info.setText(tr("Choisissez maintenant le point correspondant sur la carte…"))


def _on_preview_click(self, x, y):
    uv = self._image_click_to_full_uv(x, y)
    if uv is None:
        return
    self._dispatch_image_uv_click(*uv)


def _on_viewer_image_clicked(self, u, v):
    try:
        self._dispatch_image_uv_click(float(u), float(v))
    except Exception:
        pass

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

        
        du = abs(u2 - u1)
        if du > (W * 0.5):
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


def _ensure_fov_rubberbands(self):
    canvas = self.iface.mapCanvas()
    if not hasattr(self, "_rb_dir") or self._rb_dir is None:
        self._rb_dir = QgsRubberBand(canvas, QC.QgsWkbTypes_GeometryType_LineGeometry)
        self._rb_dir.setColor(QColor(200, 60, 60, 220))
        self._rb_dir.setWidth(2)
    if not hasattr(self, "_rb_fov") or self._rb_fov is None:
        self._rb_fov = QgsRubberBand(canvas, QC.QgsWkbTypes_GeometryType_PolygonGeometry)
        self._rb_fov.setColor(QColor(60, 120, 220, 80))
        self._rb_fov.setWidth(2)
