


from ._i18n import tr
from ._compat import QC, dialog_exec
import os, json, math, copy
import math as _math
import numpy as np
from qgis.PyQt.QtCore import Qt, QSize, QPoint, QTimer, QElapsedTimer, QRect, QRectF, pyqtSignal, QPointF
from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QFont, QPixmap, QTransform, QBrush, QPolygonF, QPainterPath
try:
    from qgis.PyQt.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QColorDialog, QGroupBox, QFormLayout, QLineEdit, QScrollArea
)
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsWkbTypes, QgsPointXY, QgsFeature, QgsGeometry, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsVectorLayer, QgsFeatureRequest, QgsRectangle, QgsFeedback
)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand, QgsVertexMarker, QgsMapTool
from ..projector import (project_point, hfov_from_focal_sensor, vfov_from_hfov_ratio, build_camera_context, project_points_batch, _basis_from_yaw_pitch_roll, _validated_vfov_for_cylindrical)
from ._topography_ops import (
    draw_dem_wireframe as topo_draw_dem_wireframe,
    draw_dem_horizon as topo_draw_dem_horizon,
    draw_dem_ridgelines as topo_draw_dem_ridgelines,
    build_radial_distances as topo_build_radial_distances,
)
from ._render_budget import (
    LayerEstimate, RenderDecision, get_budget_profile, geom_factor_from_gtype,
    style_factor_from_style, extrusion_factor_for_style, default_interactive_distance,
    estimate_score,
)
from ._schematic_symbols import (
    render_schematic_feature, style_uses_schematic, geometry_parts_in_camera_crs,
    build_schematic_feature_primitives, schematic_role_colors, billboard_world_quad,
    _safe_svg_renderer,
    Polygon3D, Polyline3D, Billboard3D
)
from ._log import qcv_log
from ._panorama_primitives import (
    PanoramicPrimitive2D, PanoramicFace, is_panorama_context, project_panorama_primitive,
    unwrap_x_continuous, unwrap_closed_ring, iter_viewport_copies,
    surface_faces_from_primitive, wall_faces_from_primitives,
    panorama_faces_from_world_mesh, project_panorama_path_safe,
)
from ._memory_guard import (
    prepare_panorama_preview, format_guard_status, release_stale_panorama_buffers, prune_base_cache_for_size,
    memory_snapshot,
)





def _effective_curvature_radius(self, curvature_enabled=None, earth_radius_m=None, k_refraction=0.0):
    
    if curvature_enabled is None:
        try:
            curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked())
        except Exception:
            curvature_enabled = True
    if not bool(curvature_enabled):
        return float('inf')
    if earth_radius_m is None:
        try:
            earth_radius_m = float(getattr(self, 'd_earth_radius_km', None).value()) * 1000.0
        except Exception:
            earth_radius_m = 6370000.0
    try:
        R_earth = float(earth_radius_m)
    except Exception:
        R_earth = 6370000.0
    if not math.isfinite(R_earth) or R_earth <= 0.0:
        return float('inf')
    try:
        k_ref = max(-0.5, min(0.49, float(k_refraction)))
    except Exception:
        k_ref = 0.0
    denom = max(1e-9, 1.0 - k_ref)
    return R_earth / denom


def _curvature_drop_from_cam_xy(self, pts_xy, cam_pt, curvature_enabled=None, earth_radius_m=None, k_refraction=0.0):
    
    pts = np.asarray(pts_xy, dtype=np.float64)
    if pts.ndim == 1:
        pts = pts.reshape(1, -1)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError('pts_xy must be an array of shape (N,2)')
    R_eff = _effective_curvature_radius(self, curvature_enabled=curvature_enabled, earth_radius_m=earth_radius_m, k_refraction=k_refraction)
    if not math.isfinite(R_eff):
        return np.zeros(pts.shape[0], dtype=np.float64)
    try:
        cx = float(cam_pt.x()) if hasattr(cam_pt, 'x') else float(cam_pt[0])
        cy = float(cam_pt.y()) if hasattr(cam_pt, 'y') else float(cam_pt[1])
    except Exception:
        return np.zeros(pts.shape[0], dtype=np.float64)
    dx = pts[:, 0] - cx
    dy = pts[:, 1] - cy
    d2 = dx * dx + dy * dy
    return d2 / (2.0 * R_eff)


def _apply_pov_curvature_to_z(self, pts_xy, z_values, cam_pt, curvature_enabled=None, earth_radius_m=None, k_refraction=0.0):
    
    pts = np.asarray(pts_xy, dtype=np.float64)
    if pts.ndim == 1:
        pts = pts.reshape(1, -1)
    z = np.asarray(z_values, dtype=np.float64)
    if z.ndim == 0:
        z = np.full(pts.shape[0], float(z), dtype=np.float64)
    elif z.shape[0] != pts.shape[0]:
        z = np.resize(z, pts.shape[0]).astype(np.float64, copy=False)
    drops = _curvature_drop_from_cam_xy(self, pts, cam_pt, curvature_enabled=curvature_enabled, earth_radius_m=earth_radius_m, k_refraction=k_refraction)
    return z - drops


def _make_pov_curved_sampler(self, z_sampler, cam_pt, curvature_enabled=None, earth_radius_m=None, k_refraction=0.0):
    
    if z_sampler is None:
        return None
    if curvature_enabled is None:
        try:
            curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked())
        except Exception:
            curvature_enabled = True
    if not bool(curvature_enabled):
        return z_sampler

    def _pts_array(points_xy_cam):
        if isinstance(points_xy_cam, np.ndarray):
            pts = np.asarray(points_xy_cam, dtype=np.float64)
            if pts.ndim == 1:
                pts = pts.reshape(1, -1)
            return pts
        pts = []
        for p in points_xy_cam:
            try:
                pts.append((float(p.x()), float(p.y())))
            except Exception:
                pts.append((float(p[0]), float(p[1])))
        if not pts:
            return np.empty((0, 2), dtype=np.float64)
        return np.asarray(pts, dtype=np.float64)

    def _sample_single(pt_xy_cam):
        try:
            x = float(pt_xy_cam.x()); y = float(pt_xy_cam.y())
        except Exception:
            x = float(pt_xy_cam[0]); y = float(pt_xy_cam[1])
        z_raw = float(z_sampler(QgsPointXY(x, y)))
        z_app = _apply_pov_curvature_to_z(
            self, np.asarray([[x, y]], dtype=np.float64), np.asarray([z_raw], dtype=np.float64),
            cam_pt, curvature_enabled=curvature_enabled, earth_radius_m=earth_radius_m, k_refraction=k_refraction
        )
        return float(z_app[0])

    def _sample_batch(points_xy_cam):
        pts = _pts_array(points_xy_cam)
        if pts.size == 0:
            return np.empty((0,), dtype=np.float64)
        batch = getattr(z_sampler, 'batch', None)
        if callable(batch):
            try:
                raw = np.asarray(batch(pts), dtype=np.float64)
            except Exception:
                raw = np.asarray([float(z_sampler(QgsPointXY(float(x), float(y)))) for x, y in pts], dtype=np.float64)
        else:
            raw = np.asarray([float(z_sampler(QgsPointXY(float(x), float(y)))) for x, y in pts], dtype=np.float64)
        return _apply_pov_curvature_to_z(
            self, pts, raw, cam_pt,
            curvature_enabled=curvature_enabled, earth_radius_m=earth_radius_m, k_refraction=k_refraction
        )

    _sample_single.batch = _sample_batch
    if hasattr(z_sampler, 'clear_cache') and callable(getattr(z_sampler, 'clear_cache')):
        _sample_single.clear_cache = getattr(z_sampler, 'clear_cache')
    _sample_single._raw_sampler = z_sampler
    _sample_single._pov_curvature = True
    try:
        _sample_single._cam_xy = (float(cam_pt.x()), float(cam_pt.y()))
    except Exception:
        _sample_single._cam_xy = None
    return _sample_single



def _wrap180_deg(a_deg: float) -> float:
    return ((float(a_deg) + 180.0) % 360.0) - 180.0

def _proj_key(projection: str) -> str:
    pj = (projection or "").strip().lower()
    if "equirect" in pj or "sph" in pj: return "equirect"
    if "cyl"     in pj:                 return "cylindrical"
    
    return "rectilinear"

def _unwrap_az_for_range(az_deg: float, az_min: float, az_max: float) -> float:
    
    az = float(az_deg)
    center = 0.5 * (float(az_min) + float(az_max))
    while az - center > 180.0:
        az -= 360.0
    while az - center < -180.0:
        az += 360.0
    return az

def _az_to_x_proj(W: int, yaw_deg: float, az_deg: float,
                  hfov_deg: float, projection: str, full360: bool=False):
    
    pj = _proj_key(projection)
    cx = W * 0.5

    
    
    
    if (pj == "equirect" and full360) or (pj == "cylindrical" and full360):
        ddeg = _wrap180_deg(float(az_deg) - float(yaw_deg))
        scale = float(W) / 360.0
        return int(round((W * 0.5) + scale * ddeg))
    
    
    if pj == "equirect" and not full360:
        ddeg = _wrap180_deg(float(az_deg) - float(yaw_deg))  
        if abs(ddeg) > 0.5 * float(hfov_deg):
            return None  
        scale = float(W) / float(hfov_deg)  
        return int(round((W * 0.5) + scale * ddeg))

    
    hfov_rad = _math.radians(float(hfov_deg))
    theta_cyl = _math.radians(_wrap180_deg(float(yaw_deg) - float(az_deg)))

    if pj == "cylindrical":
        if abs(theta_cyl) > 0.5 * hfov_rad:
            return None
        f_cyl = W / hfov_rad
        return int(round(cx + f_cyl * theta_cyl))

    
    
    theta_rect = _math.radians(_wrap180_deg(float(az_deg) - float(yaw_deg)))
    if abs(theta_rect) > 0.5 * hfov_rad:
        return None
    f_rect = (W * 0.5) / _math.tan(0.5 * hfov_rad)
    return int(round(cx + f_rect * _math.tan(theta_rect)))




def _normalize_ruler_x(x: int, width: int, full360: bool=False):
    try:
        W = int(width)
        xi = int(x)
    except Exception:
        return None
    if W <= 0:
        return None
    if bool(full360):
        return xi % W
    if xi < 0 or xi >= W:
        return None
    return xi


def _draw_azimuth_rule(self, painter, width: int, height: int, yaw_deg: float, hfov_deg: float, projection: str, full360: bool=False):
    if not (getattr(self, 'cb_az_rule_enable', None) and self.cb_az_rule_enable.isChecked()):
        return
    W = max(1, int(width))
    H = max(1, int(height))
    try:
        band_pct = float(self.d_az_rule_band_pct.value())
    except Exception:
        band_pct = 4.0
    band_pct = max(1.0, min(10.0, band_pct))
    band_h = max(18, int(round(H * (band_pct / 100.0))))
    band_h = min(band_h, max(18, int(H * 0.10)))

    try:
        text_pct = float(self.d_az_rule_text_pct.value())
    except Exception:
        text_pct = 38.0
    text_pct = max(20.0, min(80.0, text_pct))
    font_px = max(8, int(round(band_h * (text_pct / 100.0))))

    rect = QRect(0, 0, W, band_h)
    painter.save()
    painter.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_SourceOver)
    painter.fillRect(rect, QColor(255, 255, 255, 128))
    painter.setRenderHint(QC.QPainter_RenderHint_TextAntialiasing, True)

    y_base = band_h - 1
    y_short = max(3, int(round(band_h * 0.68)))
    y_long = max(2, int(round(band_h * 0.50)))
    text_y = max(font_px + 1, int(round(band_h * 0.42)))

    font = QFont('Arial')
    try:
        font.setPixelSize(font_px)
    except Exception:
        font.setPointSize(max(8, int(round(font_px * 0.75))))
    painter.setFont(font)

    pen_short = QPen(QColor(0, 0, 0, 165)); pen_short.setWidthF(1.0)
    pen_long = QPen(QColor(0, 0, 0, 220)); pen_long.setWidthF(1.2)
    pen_base = QPen(QColor(0, 0, 0, 140)); pen_base.setWidthF(1.0)
    pen_north = QPen(QColor(220, 20, 20, 235)); pen_north.setWidthF(1.4)

    painter.setPen(pen_base)
    painter.drawLine(0, y_base, W - 1, y_base)

    cardinals = {0: 'N', 90: 'E', 180: 'S', 270: 'O'}
    seen_x = set()
    for deg in range(0, 360, 5):
        x = _az_to_x_proj(W, yaw_deg, float(deg), hfov_deg, projection, bool(full360))
        x = _normalize_ruler_x(x, W, bool(full360))
        if x is None:
            continue
        key = int(x)
        if key in seen_x:
            continue
        seen_x.add(key)

        is_long = (deg % 10 == 0)
        is_card = deg in cardinals
        if is_card and deg == 0:
            painter.setPen(pen_north)
            painter.drawLine(key, y_long, key, y_base)
        else:
            painter.setPen(pen_long if is_long else pen_short)
            painter.drawLine(key, y_long if is_long else y_short, key, y_base)

        if deg % 30 == 0 or is_card:
            label = cardinals.get(deg, f"{deg}°")
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(label)
            th = fm.height()
            tx = int(round(key - tw / 2.0))
            ty = max(th, text_y)
            if bool(full360):
                tx = max(0, min(W - tw, tx))
            else:
                if tx < 0 or tx + tw > W:
                    continue
            painter.setPen(QColor(220, 20, 20) if (is_card and deg == 0) else QColor(0, 0, 0))
            painter.drawText(tx, ty, label)

    painter.restore()


def _style_opacity_factor(sty):
    try:
        v = float(getattr(sty, 'opacity', 1.0))
        if v > 1.0 and v <= 100.0:
            v /= 100.0
        if not math.isfinite(v):
            v = 1.0
        return max(0.0, min(1.0, v))
    except Exception:
        return 1.0


def _color_with_opacity(color, opacity_factor=1.0):
    c = QColor(color)
    try:
        f = max(0.0, min(1.0, float(opacity_factor)))
        c.setAlpha(max(0, min(255, int(round(c.alpha() * f)))))
    except Exception:
        pass
    return c


def _style_has_transparency(sty):
    if _style_opacity_factor(sty) < 0.999:
        return True
    for attr in ('color', 'fill_color'):
        try:
            c = QColor(getattr(sty, attr))
            if c.isValid() and c.alpha() < 250:
                return True
        except Exception:
            pass
    try:
        spec = getattr(sty, 'qgis_fill_style', None) or {}
        for key in ('color', 'color1', 'color2', 'bg_color', 'line_color', 'point_color', 'outline_color'):
            if key in spec:
                c = QColor(spec.get(key))
                if c.isValid() and c.alpha() < 250:
                    return True
    except Exception:
        pass
    return False


def _make_pen_for_style(color, width_value, scale_factor, pen_style=QC.Qt_PenStyle_SolidLine, opacity_factor=1.0):
    try:
        style = pen_style
    except Exception:
        style = QC.Qt_PenStyle_SolidLine
    try:
        wv = float(width_value or 0.0)
    except Exception:
        wv = 0.0
    if (style == QC.Qt_PenStyle_NoPen) or (wv <= 0.0):
        return QPen(QC.Qt_PenStyle_NoPen)
    pen = QPen(_color_with_opacity(color, opacity_factor))
    pen.setWidthF(max(1.0, float(wv) * float(scale_factor)))
    try:
        pen.setStyle(style)
    except Exception:
        pass
    try:
        pen.setJoinStyle(QC.Qt_PenJoinStyle_RoundJoin)
        pen.setCapStyle(QC.Qt_PenCapStyle_RoundCap)
        pen.setCosmetic(True)
    except Exception:
        pass
    return pen

def _draw_label(self, painter, text, anchor_uv, sty):
    if not text or anchor_uv is None: return
    painter.save()
    fm = painter.fontMetrics()
    tw = fm.horizontalAdvance(text); th = fm.height()
    pos_dx, pos_dy = self._label_offset_from_pos(sty.label_pos, base_px=8)
    x = int(anchor_uv[0] + pos_dx + sty.label_offset.x())
    y = int(anchor_uv[1] + pos_dy + sty.label_offset.y())
    pad = int(sty.label_bg_padding)
    rect = QRect(x - pad, y - th - pad, tw + 2*pad, th + 2*pad)

    if sty.label_callout:
        pen_call = QPen(sty.label_callout_color); pen_call.setWidth(int(sty.label_callout_width))
        painter.setPen(pen_call)
        tgt = rect.center()
        self._safe_line(painter, anchor_uv, (tgt.x(), tgt.y()))

    if sty.label_bg:
        painter.setPen(QC.Qt_PenStyle_NoPen); painter.setBrush(sty.label_bg_color)
        painter.drawRoundedRect(rect, sty.label_bg_radius, sty.label_bg_radius)

    if sty.label_halo and sty.label_halo_width > 0:
        painter.setPen(QPen(sty.label_halo_color))
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                if dx==0 and dy==0: continue
                painter.drawText(x + dx*sty.label_halo_width, y + dy*sty.label_halo_width, text)

    painter.setPen(QPen(sty.label_text_color))
    painter.drawText(x, y, text)
    painter.restore()

def _schedule_deferred_hq_render(self, scheduler):
    
    try:
        if scheduler is None or not bool(getattr(self,'cb_lowlat',None) and self.cb_lowlat.isChecked()):
            return
        if _is_panorama_ui(self):
            state=getattr(self,'_memory_guard_last_state',None)
            if isinstance(state,dict) and str(state.get('level','normal')) in ('critical','dangerous'):
                
                return
        scheduler.schedule_render(quality='high')
    except Exception:
        return


def _render_debounce_timeout(self):
    
    try:
        if bool(getattr(self, '_render_edit_widgets', set())):
            self._render_edit_pending = True
            return
        scheduled = int(getattr(self, '_render_scheduled_generation', 0) or 0)
        current = int(getattr(self, '_render_request_generation', 0) or 0)
        if scheduled != current:
            
            return
    except Exception:
        pass
    self._render_preview_now()


def _render_debounce_delay_ms(self, sender=None):
    
    try:
        override = getattr(self, '_render_delay_override_ms', None)
        if override is not None:
            return max(0, int(override))
    except Exception:
        pass

    try:
        lowlat = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked())
    except Exception:
        lowlat = False

    
    
    expensive_names = (
        'd_yaw', 'd_yaw_offset', 'd_pitch', 'd_roll', 'd_camheight',
        'd_hfov', 'd_vfov', 'd_maxdist', 'd_focal', 'd_sensorw',
        'spin_dem_step', 'd_earth_radius_km', 'd_proj_grid_step',
        'd_az_step', 'd_rad_step', 'd_eps',
    )
    try:
        expensive = any(sender is getattr(self, n, None) for n in expensive_names)
    except Exception:
        expensive = False

    if expensive:
        return 180 if lowlat else 380
    if isinstance(sender, (QSpinBox, QDoubleSpinBox)):
        return 140 if lowlat else 260
    
    
    return 70 if lowlat else 120


def render_preview(self):
    
    
    
    if int(getattr(self, '_render_suspend_count', 0) or 0) > 0:
        self._render_resume_requested = True
        return
    if getattr(self, '_ui_initializing', False):
        return

    
    
    
    try:
        if bool(getattr(self, '_render_edit_widgets', set())):
            self._render_edit_pending = True
            try:
                self.debounce.stop()
            except Exception:
                pass
            return
    except Exception:
        pass

    
    
    
    try:
        self._render_request_generation = int(getattr(self, '_render_request_generation', 0)) + 1
    except Exception:
        self._render_request_generation = 1
    try:
        self._render_scheduled_generation = int(self._render_request_generation)
    except Exception:
        self._render_scheduled_generation = 0

    scheduler = getattr(self, "render_scheduler", None)
    if scheduler is not None:
        try:
            lowlat = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked())
            quality = 'low' if lowlat else 'high'
            scheduler.schedule_render(quality=quality)
            if not hasattr(self, '_hq_render_timer'):
                self._hq_render_timer = QTimer(self)
                self._hq_render_timer.setSingleShot(True)
                self._hq_render_timer.timeout.connect(lambda: _schedule_deferred_hq_render(self, getattr(self, 'render_scheduler', scheduler)))
            self._hq_render_timer.stop()
            if lowlat:
                self._hq_render_timer.setInterval(500)
                self._hq_render_timer.start()
            return
        except Exception:
            pass

    try:
        sender = self.sender()
    except Exception:
        sender = None
    try:
        delay = int(_render_debounce_delay_ms(self, sender))
    except Exception:
        delay = 250
    try:
        self.debounce.stop()
        self.debounce.setInterval(max(0, delay))
        self.debounce.start()
    except Exception:
        pass

def _clear_base_cache_and_render(self):
    self._base_cache.clear()
    if not getattr(self, '_ui_initializing', False):
        self.render_preview()

def _draw_fov_frame(self, p, W, H):
    pen = QPen(QColor(255,255,255,120)); pen.setWidth(1); p.setPen(pen)
    p.drawRect(1,1,W-2,H-2)

def _draw_axes_debug(self, p, W, H):
    cx, cy = W//2, H//2
    L = max(30, min(W,H)//12)
    p.setPen(QPen(QColor(220,60,60,200), 2)); p.drawLine(cx, cy, cx + L, cy)   
    p.setPen(QPen(QColor(60,200,60,200), 2)); p.drawLine(cx, cy, cx, cy - L)   
    p.setPen(QPen(QColor(60,120,240,200), 2, QC.Qt_PenStyle_DashLine)); p.drawLine(cx, cy, cx, cy + L)  

def _draw_center_and_pdv_guides(self, painter, W: int, H: int,
                                yaw_deg: float, hfov_deg: float,
                                projection: str, is360: bool):
    
    
    painter.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
    show_center = (getattr(self, "cb_show_center_axis", None).isChecked() if hasattr(self, "cb_show_center_axis")
                   else bool(getattr(self, "show_center_axis", True)))
    show_pdv    = (getattr(self, "cb_show_pdv_axis", None).isChecked() if hasattr(self, "cb_show_pdv_axis")
                   else bool(getattr(self, "show_pdv_axis", False)))
    
    az_pdv_deg  = getattr(self, "current_pdv_azimuth", None)
    if az_pdv_deg is None:
        try:
            az_pdv_deg = float(self.d_yaw.value() + self.d_yaw_offset.value())
        except Exception:
            az_pdv_deg = None


    
    
    center_w = int(getattr(self, "center_axis_px", 2))  
    pdv_w    = int(getattr(self, "pdv_axis_px",    2))  

    center_color = QColor(255,255,255,200)
    try:
        if self.image is None or self.image.isNull():
            bg = self._schematic_background_color()
            lum = 0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()
            center_color = QColor(55,55,55,210) if lum > 145 else QColor(245,245,245,220)
    except Exception:
        pass
    pen_center = QPen(center_color); pen_center.setWidth(max(1, center_w))
    pen_pdv    = QPen(QColor(220,20,20,230));   pen_pdv.setWidth(max(1, pdv_w))


    painter.save()
    painter.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_SourceOver)
    
    if show_center:
        painter.setPen(pen_center)
        x = int(W // 2)
        painter.drawLine(x, 0, x, H-1)

    if show_pdv and (az_pdv_deg is not None):
        xpdv = _az_to_x_proj(W, yaw_deg, float(az_pdv_deg), float(hfov_deg), projection, bool(is360))
        if xpdv is None:
            
            pj = (projection or "").strip().lower()
            full360 = (("equirect" in pj or "cyl" in pj) and bool(is360))
            if not full360:
                
                ddeg = ((float(az_pdv_deg) - float(yaw_deg) + 180.0) % 360.0) - 180.0
                xpdv = 0 if ddeg < 0 else (W - 1)
        
        if xpdv is not None:
            painter.setPen(pen_pdv)
            painter.drawLine(int(xpdv), 0, int(xpdv), H-1)
            painter.fillRect(int(xpdv)-3, 0, 6, 6, QColor(220,20,20,220))

    painter.restore()


def _is_panorama_ui(self):
    try:
        return str(self.cmb_proj.currentText()).strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
    except Exception:
        return False


def _active_panorama_guard(self):
    
    if not bool(getattr(self, '_memory_guard_in_preview', False)):
        return {}
    state = getattr(self, '_memory_guard_runtime', None)
    return state if isinstance(state, dict) and bool(state.get('safe_mode', False)) else {}


def _render_preview_now(self):
    if int(getattr(self, '_render_suspend_count', 0) or 0) > 0:
        self._render_resume_requested = True
        return
    requested_generation = int(getattr(self, '_render_request_generation', 0))
    if getattr(self, '_rendering_now', False):
        
        self._render_pending_generation = max(int(getattr(self, '_render_pending_generation', 0)), requested_generation)
        return
    self._rendering_now = True
    self._render_active_generation = requested_generation
    try:
        
        
        

        
        render_quality = getattr(self, '_current_render_quality', 'high')
        scale = {0:0.25, 1:0.5, 2:1.0}.get(self.cmb_quality.currentIndex(), 0.25)
        W_full, H_full = self.spin_w.value(), self.spin_h.value()
        if render_quality == 'low':
            scale = min(scale, 0.20)
        elif render_quality == 'normal' and bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked()):
            scale = min(scale, 0.35)
        W = max(192 if render_quality == 'low' else 256, int(W_full * scale))
        H = max(128 if render_quality == 'low' else 256, int(H_full * scale))

        
        
        
        
        panorama_ui = _is_panorama_ui(self)
        self._memory_guard_in_preview = bool(panorama_ui)
        if panorama_ui:
            guard_state = prepare_panorama_preview(
                self, W, H, W_full, H_full, render_quality=render_quality
            )
            self._memory_guard_runtime = guard_state
            self._memory_guard_last_state = guard_state
            if bool(guard_state.get('cancel', False)):
                _label_budget_text(self, 'Rendu annulé — charge mémoire critique')
                return
            W = max(192 if render_quality == 'low' else 256, int(guard_state.get('width', W)))
            H = max(128 if render_quality == 'low' else 256, int(guard_state.get('height', H)))
            try:
                prune_base_cache_for_size(self, W, H)
            except Exception:
                pass
            
            
            try:
                self.preview.setPixmap(QPixmap())
            except Exception:
                pass
        else:
            self._memory_guard_runtime = {'active': False, 'safe_mode': False, 'level': 'normal'}

        
        
        if panorama_ui:
            self._suppress_overlay_viewer_sync = True
            try:
                overlay = self._render_overlay(width=W, height=H)
            finally:
                self._suppress_overlay_viewer_sync = False
        else:
            if not hasattr(self, "_overlay_cache"):
                self._overlay_cache = {}
            preview_key = self._overlay_params_key(W, H)
            overlay = self._overlay_cache.get(preview_key)
            if overlay is None or overlay.isNull():
                overlay = self._render_overlay(width=W, height=H)
                self._overlay_cache[preview_key] = overlay
                
                if len(self._overlay_cache) > 8:
                    try:
                        oldest_key = next(iter(self._overlay_cache.keys()))
                        if oldest_key != preview_key:
                            self._overlay_cache.pop(oldest_key, None)
                    except Exception:
                        pass
        base = self._get_base_scaled(W, H)
        if base is None or base.isNull():
            raise RuntimeError("Fond de vue indisponible")
        composed = QImage(base)
        qp = QPainter(composed); qp.drawImage(0, 0, overlay); qp.end()
        self.last_preview = composed
        preview_transform = QC.Qt_TransformationMode_FastTransformation if render_quality == 'low' else QC.Qt_TransformationMode_SmoothTransformation
        self.preview.setPixmap(QPixmap.fromImage(composed).scaled(self.preview.size(), QC.Qt_AspectRatioMode_KeepAspectRatio, preview_transform))
        try:
            if hasattr(self, '_monoplot_refresh_list'):
                self._monoplot_refresh_list()
        except Exception:
            pass

        
        if self.viewer is not None and self.viewer.isVisible():
            prev_pm = self.viewer._pix.pixmap() if hasattr(self.viewer, "_pix") else QPixmap()
            viewer_full_res = bool(getattr(self, '_viewer_full_res', False)) and render_quality == 'high'
            if panorama_ui and bool((getattr(self, '_memory_guard_runtime', {}) or {}).get('disable_viewer_full_res', False)):
                viewer_full_res = False

            if viewer_full_res:
                base_view = self._get_base_scaled(W_full, H_full)
                vw, vh = base_view.width(), base_view.height()
                if panorama_ui:
                    
                    
                    if vw == W and vh == H:
                        ov_view = overlay
                    else:
                        self._suppress_overlay_viewer_sync = True
                        try:
                            ov_view = self._render_overlay(width=vw, height=vh)
                        finally:
                            self._suppress_overlay_viewer_sync = False
                else:
                    if not hasattr(self, "_overlay_cache"):
                        self._overlay_cache = {}
                    key = self._overlay_params_key(vw, vh)
                    ov_view = self._overlay_cache.get(key)
                    if ov_view is None or ov_view.isNull():
                        ov_view = self._render_overlay(width=vw, height=vh)
                        self._overlay_cache[key] = ov_view
                self.viewer.update_image(base_view)
            else:
                target_w = prev_pm.width() if not prev_pm.isNull() else W_full
                target_h = prev_pm.height() if not prev_pm.isNull() else H_full
                vw, vh = max(1, int(target_w)), max(1, int(target_h))
                base_view = None
                ov_view = overlay
                
                
                if prev_pm.isNull() or prev_pm.width() != vw or prev_pm.height() != vh:
                    
                    try:
                        if self.image is not None and not self.image.isNull():
                            self.viewer.update_image(self.image)
                        else:
                            base_view = self._get_base_scaled(vw, vh)
                            self.viewer.update_image(base_view)
                    except Exception:
                        base_view = self._get_base_scaled(vw, vh)
                        self.viewer.update_image(base_view)

            if ov_view is not None and not ov_view.isNull():
                self.viewer.update_overlay(ov_view)
            else:
                self.viewer.clear_overlay()


    except Exception as e:
        err = QImage(820, 60, QC.QImage_Format_Format_ARGB32_Premultiplied)
        err.fill(QColor(0,0,0,0))
        p = QPainter(err); p.setPen(QPen(QColor(255,80,80,255))); p.setFont(QFont("Arial", 10))
        p.drawText(10, 35, f"Erreur rendu: {e}"); p.end()
        self.preview.setPixmap(QPixmap.fromImage(err))
    finally:
        
        
        self._memory_guard_in_preview = False
        active_generation = int(getattr(self, '_render_active_generation', requested_generation))
        pending_generation = max(
            int(getattr(self, '_render_pending_generation', 0)),
            int(getattr(self, '_render_request_generation', 0))
        )
        self._rendering_now = False
        self._render_pending_generation = 0
        
        
        
        
        if pending_generation > active_generation:
            try:
                self._render_scheduled_generation = int(getattr(self, '_render_request_generation', pending_generation))
                delay = 380 if _is_panorama_ui(self) else 120
                self.debounce.stop()
                self.debounce.setInterval(int(delay))
                self.debounce.start()
            except Exception:
                pass


def _render_overlay(self, width, height):
    try:
        if hasattr(self, '_prune_missing_overlay_layers'):
            self._prune_missing_overlay_layers()
        if hasattr(self, 'sync_all_layer_styles_from_qgis'):
            self.sync_all_layer_styles_from_qgis()
    except Exception:
        pass
    _panorama_depth_error = None
    overlay = QImage(width, height, QC.QImage_Format_Format_ARGB32_Premultiplied)
        
    try:
        self._overlay_w = int(width)
        self._overlay_h = int(height)
    except Exception:
        self._overlay_w = width
        self._overlay_h = height

    overlay.fill(QColor(0,0,0,0))
    p = QPainter(overlay)
    render_quality = getattr(self, '_current_render_quality', 'high')
    fast_preview = bool(self.cb_lowlat.isChecked()) or (render_quality == 'low')
    p.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
    try:
        p.setRenderHint(QC.QPainter_RenderHint_TextAntialiasing, True)
    except Exception:
        pass
    try:
        p.setRenderHint(QC.QPainter_RenderHint_SmoothPixmapTransform, True)
    except Exception:
        pass
    _begin_feature_feedback(self)
    self._budget_snapshots = []
    self._feature_style_cache = {}
    
    try:
        cam_layer = self.cmb_camera.currentLayer()
        if not cam_layer or cam_layer.featureCount() < 1:
            
            try:
                yaw_eff = self.d_yaw.value() + self.d_yaw_offset.value()
                HFOV    = self.d_hfov.value()
                proj    = self.cmb_proj.currentText()
                is360   = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
                p.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_SourceOver)
                self._draw_center_and_pdv_guides(p, width, height, yaw_eff, HFOV, proj, is360)
            except Exception:
                pass
            p.end()
            overlay = _shift_overlay_image(self, overlay)
            self.overlay_image = overlay
            return overlay

        cam_feat = None
        try:
            cam_feat = self._camera_current_feature()
        except Exception:
            cam_feat = None
        if cam_feat is None or (not cam_feat.isValid()) or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            try:
                cam_feat = next(cam_layer.getFeatures())
            except Exception:
                cam_feat = None
        if cam_feat is None or (not cam_feat.isValid()) or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
            raise RuntimeError("Aucun point caméra valide pour le rendu")
        
        
        
        try:
            cam_pt, cam_crs = self._camera_point_in_work_crs(cam_feat)
        except Exception:
            cam_pt, cam_crs = None, None
        if cam_pt is None or cam_crs is None:
            try: self._camera_warn_if_non_metric_project(notify=False)
            except Exception: pass
            raise RuntimeError("CRS projet non métrique : choisissez un CRS projeté (par ex. Lambert-93) pour QCALVIEW")

        yaw = self.d_yaw.value()
        yaw_eff = yaw + self.d_yaw_offset.value()
        pitch = self.d_pitch.value()
        roll  = self.d_roll.value()
        proj  = self.cmb_proj.currentText()
        proj_upper = str(proj).strip().upper()
        full_equirect = bool(
            proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR')
            and self.cb_360.isChecked()
        )
        is360 = full_equirect
        maxdist = self.d_maxdist.value(); maxdist = None if maxdist <= 0.0 else maxdist
        
        if not hasattr(self, "show_center_axis"): self.show_center_axis = True
        if not hasattr(self, "show_pdv_axis"):    self.show_pdv_axis    = True

        auto_hfov_allowed = proj_upper in ('PINHOLE', 'RECTILINEAR')
        if self.cb_auto_hfov.isChecked() and auto_hfov_allowed and self.d_focal.value() > 0 and self.d_sensorw.value() > 0:
            hfov = hfov_from_focal_sensor(self.d_focal.value(), self.d_sensorw.value())
            self.d_hfov.blockSignals(True); self.d_hfov.setValue(hfov); self.d_hfov.blockSignals(False)
            vfov = vfov_from_hfov_ratio(hfov, width, height)
            self.d_vfov.blockSignals(True); self.d_vfov.setValue(vfov); self.d_vfov.blockSignals(False)
        HFOV = self.d_hfov.value(); VFOV = self.d_vfov.value()
        
        self._last_overlay_w, self._last_overlay_h = int(width), int(height)

        
        
        
        panoramic_proj = proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
        
        
        
        if panoramic_proj:
            _guard = _active_panorama_guard(self)
            try:
                _cap = _guard.get('maxdist_cap')
                if _cap is not None and float(_cap) > 0.0:
                    maxdist = float(_cap) if maxdist is None else min(float(maxdist), float(_cap))
            except Exception:
                pass
        
        
        
        
        is360 = bool(
            full_equirect
            or (proj_upper == 'CYLINDRICAL' and float(HFOV) >= 359.999)
        )
        if full_equirect:
            HFOV = 360.0
            VFOV = 180.0
            try:
                self.d_hfov.blockSignals(True); self.d_hfov.setValue(HFOV); self.d_hfov.blockSignals(False)
                self.d_vfov.blockSignals(True); self.d_vfov.setValue(VFOV); self.d_vfov.blockSignals(False)
            except Exception:
                pass
        elif proj_upper == 'CYLINDRICAL' and is360:
            HFOV = 360.0
            try:
                self.d_hfov.blockSignals(True); self.d_hfov.setValue(HFOV); self.d_hfov.blockSignals(False)
            except Exception:
                pass

        
        z_sampler = None; cam_ground_z = 0.0
        
        
        dem_group_ok = True
        
        
        
        occ_group_ok = True
        
        relief_mode = self._relief_mode_id() if hasattr(self, '_relief_mode_id') else ('wireframe' if self.cb_show_dem.isChecked() else ('skyline' if self.cb_draw_skyline.isChecked() else 'none'))
        relief_active = relief_mode != 'none'
        need_dem = (
            dem_group_ok and (
                self.cb_use_dem_z.isChecked() or
                relief_active
            )
        ) or (occ_group_ok and self.cb_occ_terrain.isChecked())

        if need_dem:
            dem_layer = self.cmb_dem.currentLayer()
            if isinstance(dem_layer, QgsRasterLayer) and dem_layer.isValid():
                z_sampler, _ = self._make_z_sampler(dem_layer, cam_crs)
        if z_sampler and self.cb_use_dem_z.isChecked():
            try: cam_ground_z = z_sampler(cam_pt)
            except Exception: cam_ground_z = 0.0

        cam_height = float(self.d_camheight.value())
        cam_z = cam_ground_z + cam_height
        vector_z_sampler_raw = z_sampler if bool(getattr(self, "cb_use_dem_z", None) and self.cb_use_dem_z.isChecked()) else None
        vector_z_sampler = _make_pov_curved_sampler(self, vector_z_sampler_raw, cam_pt, k_refraction=0.0)

        
        self._draw_gcps_overlay(p, width, height)

        
        need_horizon_for_anything = bool(relief_active and z_sampler is not None and not cam_crs.isGeographic())
        if need_horizon_for_anything:
            curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked()) if hasattr(self, 'cb_curvature') else True
            earth_radius_m = float(getattr(self, 'd_earth_radius_km', None).value() * 1000.0) if hasattr(self, 'd_earth_radius_km') else 6370000.0
            _az_step_eff = float(self.d_az_step.value())
            _rad_step_eff = float(self.d_rad_step.value())
            if panoramic_proj:
                try:
                    _guard_rad = _active_panorama_guard(self).get('rad_step_min')
                    if _guard_rad is not None:
                        _rad_step_eff = max(_rad_step_eff, float(_guard_rad))
                except Exception:
                    pass
            horizon_view_key = (
                round(float(cam_pt.x()), 3), round(float(cam_pt.y()), 3), round(float(cam_z), 3),
                str(proj), int(width), int(height),
                round(float(yaw_eff), 6), round(float(pitch), 6), round(float(roll), 6),
                round(float(HFOV), 6), round(float(VFOV), 6), bool(is360),
                round(float(maxdist or 2000.0), 3),
                round(float(_az_step_eff), 4), round(float(_rad_step_eff), 4),
                bool(curvature_enabled), round(float(earth_radius_m), 3),
            )
            horizon_occ_key = (
                round(float(cam_pt.x()), 3), round(float(cam_pt.y()), 3), round(float(cam_z), 3),
                round(float(maxdist or 2000.0), 3),
                round(float(_az_step_eff), 4), round(float(_rad_step_eff), 4),
                bool(curvature_enabled), round(float(earth_radius_m), 3),
            )
            existing = getattr(self, "_horizon", None) or {}
            if existing.get("view_key") != horizon_view_key or existing.get("occ_key") != horizon_occ_key:
                
                
                if panoramic_proj:
                    try:
                        self._horizon = None
                        self._horizon_params = None
                    except Exception:
                        pass
                self._build_horizon_cache(
                    z_sampler, cam_pt, cam_z, cam_crs,
                    proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist,
                    float(_az_step_eff), float(_rad_step_eff)
                )
        else:
            self._horizon = None

        
        
        
        occ_relief = (relief_active
                      and self._horizon is not None
                      and not (self.cb_debug_no_occ.isChecked()))
        eps = float(self.d_eps.value())
        
        
        
        occ_objects = True
        transparent_objects = bool(getattr(self, "cb_transparent_objects", None) and self.cb_transparent_objects.isChecked())
        force_horizontal_25d = bool(getattr(self, 'cb_force_horizontal_25d', None) and self.cb_force_horizontal_25d.isChecked())

        
        topo_draw_before_vectors = True
        panoramic_overlay_mode = str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
        
        
        
        
        _pano_zbuffer_enabled = bool(panoramic_overlay_mode)
        _pano_deferred_edges = []
        _pano_deferred_labels = []
        _pano_defer_calib_grid = bool(_pano_zbuffer_enabled)
        def _emit_feature_label(text, anchor_uv, sty_label):
            if not text or anchor_uv is None or sty_label is None:
                return
            if _pano_zbuffer_enabled:
                try:
                    _pano_deferred_labels.append((str(text), (float(anchor_uv[0]), float(anchor_uv[1])), sty_label))
                except Exception:
                    pass
            else:
                self._draw_label(p, text, anchor_uv, sty_label)

        def _queue_physical_edge(pen_obj, path_obj, wrap_w=0.0):
            if path_obj is None:
                return False
            try:
                uv = path_obj.uv
                dep = path_obj.radial_depth
                xyz = getattr(path_obj, 'world_xyz', None)
                if uv is None or dep is None or len(uv) < 2 or len(dep) < 2:
                    return False
                _pano_deferred_edges.append((QPen(pen_obj), uv, dep, xyz, float(wrap_w or 0.0)))
                return True
            except Exception:
                return False

        
        
        _pano_frame_budget = {'remaining': (45000 if render_quality == 'low' else 90000 if render_quality == 'normal' else 140000)} if panoramic_overlay_mode else None
        
        
        
        
        _pano_schematic_budget = None
        if panoramic_overlay_mode and bool(getattr(self, '_memory_guard_in_preview', False)):
            _base_instances = 14000 if render_quality == 'low' else 35000 if render_quality == 'normal' else 60000
            _guard_state = getattr(self, '_memory_guard_runtime', None)
            if isinstance(_guard_state, dict):
                _cap = _guard_state.get('schematic_instance_budget')
                if _cap is not None:
                    try: _base_instances = min(_base_instances, max(1000, int(_cap)))
                    except Exception: pass
                _min_px = _guard_state.get('min_billboard_px')
            else:
                _min_px = None
            if _min_px is None:
                _min_px = 1.10 if render_quality == 'low' else 0.75 if render_quality == 'normal' else 0.55
            _pano_schematic_budget = {
                'initial': int(_base_instances), 'remaining': int(_base_instances),
                'generated': 0, 'culled_lod': 0, 'culled_distance': 0,
                'dropped_budget': 0, 'exhausted': False,
                'min_billboard_px': float(_min_px),
            }

        def _draw_topography_group():
            if not dem_group_ok or not z_sampler:
                return
            _tp = p
            if relief_mode == 'opaque' and self._horizon is not None:
                self._draw_dem_opaque(_tp, cam_pt, cam_z, cam_crs, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist)
            elif relief_mode == 'wireframe':
                self._draw_dem_wireframe(_tp, cam_pt, cam_z, cam_crs, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist, z_sampler, eps=eps)
            elif relief_mode == 'skyline' and self._horizon is not None:
                self._draw_skyline(_tp, cam_pt, cam_z, cam_crs, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist)
            elif relief_mode == 'ridgelines':
                self._draw_dem_ridgelines(_tp, cam_pt, cam_z, cam_crs, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist, z_sampler, eps=eps)

        if topo_draw_before_vectors and not _pano_zbuffer_enabled:
            _draw_topography_group()

        
        
        
        if not _pano_defer_calib_grid:
            self._draw_calib_grid(p, cam_pt, cam_z, cam_crs, proj, width, height,
                                  yaw_eff, pitch, roll, HFOV, VFOV, is360, z_sampler)
        

        
        labels_hidden = (not self.cb_show_labels.isChecked()) or (render_quality == 'low')
        budget_notes = []
        height_field = (self.txt_hfield.text().strip() or None)
        h_default = self.d_hdefault.value()
        current_layer_maxdist = maxdist

        def point_visibility(pt_cam, z_tgt):
            dx = pt_cam.x() - cam_pt.x(); dy = pt_cam.y() - cam_pt.y()
            r = math.hypot(dx, dy)
            az = self._azimuth_deg(dx, dy)
            el = self._elev_deg(z_tgt - cam_z, r)
            if not occ_relief:
                vis = True
            elif panoramic_overlay_mode and self._horizon is not None:
                vis = _terrain_point_visible_fast_40192(
                    self._horizon,float(pt_cam.x()),float(pt_cam.y()),float(z_tgt),
                    float(cam_pt.x()),float(cam_pt.y()),float(cam_z),float(eps)
                )
            else:
                vis = self._is_visible_by_horizon(az, el, eps, r)
            return vis, az, el

        
        
        _pano_terrain_cull_stats = {
            'objects_tested': 0, 'objects_culled': 0, 'objects_mixed': 0,
            'objects_visible': 0, 'schematic_preculled': 0, 'edge_segments_culled': 0
        }

        def draw_polyline(points_cam, z_mode, pen, is_top_edge=False, label_text=None, label_sty=None, pre_densified=False):
            if len(points_cam) < 2: return
            p.setPen(pen)
            
            
            
            base_samples = 1 if pre_densified else (6 if (occ_relief or panoramic_overlay_mode) else 1)
            prev_uv = None; prev_vis = None; prev_az = None; prev_el = None
            label_uv = None
            for i in range(len(points_cam)-1):
                a = points_cam[i]; b = points_cam[i+1]
                N = base_samples
                for s in range(N+1):
                    t = s/float(N)
                    x = a.x()*(1-t) + b.x()*t
                    y = a.y()*(1-t) + b.y()*t
                    pt = QgsPointXY(x,y)
                    if z_mode == "ground":
                        ground_z = vector_z_sampler(pt) if vector_z_sampler else 0.0
                        zt = ground_z
                    else:
                        ground_z = vector_z_sampler(pt) if vector_z_sampler else 0.0
                        zt = ground_z + current_h
                    vis, az, el = point_visibility(pt, zt)
                    uv = self._finite_uv(project_point(cam_pt, cam_z, pt, None, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360,
                                                       dist_max=current_layer_maxdist, z_tgt=zt, z_sampler=None if z_mode=="top" else vector_z_sampler))
                    draw_ok = (uv is not None)
                    prev_draw_ok = (prev_uv is not None)
                    if (label_uv is None) and vis and draw_ok: label_uv = uv
                    if prev_draw_ok and draw_ok and (prev_vis is not None):
                        if vis and prev_vis:
                            _draw_uv_segments(self, p, np.asarray([prev_uv, uv], dtype=np.float64))
                        elif vis and (not prev_vis):
                            _draw_uv_segments(self, p, np.asarray([prev_uv, uv], dtype=np.float64))
                        if occ_objects and is_top_edge and vis:
                            self._update_horizon_by_segment(prev_az, az, max(prev_el, el))
                    prev_uv, prev_vis, prev_az, prev_el = uv, vis, az, el
            if (not labels_hidden) and label_text and label_sty and label_uv:
                _emit_feature_label(label_text, label_uv, label_sty)

        
        
        
        _pano_ctx_common = (build_camera_context(cam_pt, cam_z, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360)
                            if panoramic_overlay_mode else None)
        
        _pano_zfaces = []
        
        
        if panoramic_overlay_mode:
            _extra = 3000 if render_quality == 'low' else 8000 if render_quality == 'normal' else 16000
            if not bool(getattr(self,'_memory_guard_in_preview',False)):
                _extra = 30000
            _pano_surface_extra_budget = {'remaining': int(_extra)}
        else:
            _pano_surface_extra_budget = None

        
        
        
        
        if _pano_zbuffer_enabled and isinstance(_pano_schematic_budget, dict):
            _zb_cap = 4000 if render_quality == 'low' else 9000 if render_quality == 'normal' else 18000
            try:
                _pano_schematic_budget['initial'] = min(int(_pano_schematic_budget.get('initial',_zb_cap)), int(_zb_cap))
                _pano_schematic_budget['remaining'] = min(int(_pano_schematic_budget.get('remaining',_zb_cap)), int(_zb_cap))
            except Exception:
                pass

        fast_cpu_mode = (str(proj).upper() == 'PINHOLE') and (not panoramic_overlay_mode)
        if fast_cpu_mode:
            self._render_vector_layers_fast(
                p, cam_pt, cam_z, cam_crs, proj, width, height,
                yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist,
                vector_z_sampler, labels_hidden, height_field, h_default, transparent_objects,
                terrain_horizon=(self._horizon if occ_relief else None), terrain_eps=eps
            )
        else:
            for sty in self.layer_styles:
                if not bool(getattr(sty, 'visible', True)):
                    continue
                lyr = sty.layer
                if not lyr: continue
                src_crs = lyr.crs()
                tr = None if src_crs == cam_crs else QgsCoordinateTransform(src_crs, cam_crs, QgsProject.instance())
                pen = _make_pen_for_style(sty.color, getattr(sty, 'width', 0.0), (width/4000.0 if width<4000 else width/6000.0), getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine), _style_opacity_factor(sty))
                p.setPen(pen)
                font = QFont("Arial", max(6, int(sty.label_size * max(0.5, width/4000.0))))
                p.setFont(font)
                gtype = QgsWkbTypes.geometryType(lyr.wkbType())

                decision = _decide_layer_render(self, lyr, sty, cam_pt, cam_crs, yaw_eff, HFOV, proj, is360, render_quality=render_quality)
                effective_maxdist = float(decision.effective_maxdist)
                current_layer_maxdist = effective_maxdist
                layer_labels_hidden = bool(labels_hidden or decision.degrade_labels)
                sty._force_simple_fill = bool(decision.degrade_style)
                sty._budget_warning = bool(decision.accepted and (decision.degrade_labels or decision.degrade_style or decision.simplify_geometry or effective_maxdist < (_requested_maxdist_for_layer(self, sty, gtype) - 1.0)))
                self._budget_snapshots.append({
                    'name': lyr.name(), 'accepted': bool(decision.accepted), 'distance': effective_maxdist,
                    'count': int(decision.candidate_count), 'score': float(decision.score),
                    'labels_off': bool(decision.degrade_labels), 'style_light': bool(decision.degrade_style),
                    'simplify': bool(decision.simplify_geometry),
                    'schematic': bool(style_uses_schematic(sty)),
                    'reason': str(getattr(decision, 'reason', 'ok'))
                })
                if not decision.accepted:
                    continue

                _hard_limit = max(600, int(get_budget_profile(_perf_profile_name(self)).hard_features * 1.6))
                if panoramic_overlay_mode:
                    try:
                        _guard_limit = _active_panorama_guard(self).get('entity_limit_per_layer')
                        if _guard_limit is not None and int(_guard_limit) > 0:
                            _hard_limit = min(int(_hard_limit), int(_guard_limit))
                    except Exception:
                        pass
                _features = list(_iter_candidate_features(
                    self, lyr, sty, cam_pt, cam_crs, effective_maxdist, yaw_eff, HFOV, proj, is360,
                    render_quality=render_quality, feedback=getattr(self, '_feature_feedback', None), need_attrs=True,
                    simplify_geometry=decision.simplify_geometry, hard_limit=_hard_limit, width=width
                ))
                try:
                    _features.sort(key=lambda ft: _geometry_depth_key(ft.geometry(), tr, cam_pt), reverse=True)
                except Exception:
                    pass

                
                
                
                for feat in _features:
                    geom = feat.geometry()
                    sty_eff = _feature_local_style(self, sty, feat)
                    pen = _make_pen_for_style(sty_eff.color, getattr(sty_eff, 'width', 0.0), (width/4000.0 if width<4000 else width/6000.0), getattr(sty_eff, 'pen_style', QC.Qt_PenStyle_SolidLine), _style_opacity_factor(sty_eff))
                    font = QFont("Arial", max(6, int(getattr(sty_eff, 'label_size', sty.label_size) * max(0.5, width/4000.0))))
                    p.setPen(pen)
                    p.setFont(font)
                    draw_2p5d = bool(self.cb_draw_2p5d.isChecked()) and bool(getattr(sty_eff, 'enable_25d', True))
                    sty_height_field = getattr(sty_eff, 'height_field_override', '') or height_field
                    sty_h_default = getattr(sty_eff, 'default_height_override', None)
                    if sty_h_default is None:
                        sty_h_default = h_default
                    h = None
                    if sty_height_field and (sty_height_field in feat.fields().names()):
                        try: h = float(feat[sty_height_field])
                        except: h = None
                    if h is None: h = sty_h_default
                    current_h = h

                    anchor_uv_global, text_global = self._label_anchor_uv(sty_eff, feat, geom, proj, width, height,
                                                                          cam_pt, cam_z, tr, yaw_eff, pitch, roll, HFOV, VFOV,
                                                                          is360, effective_maxdist, vector_z_sampler, h)
                    if layer_labels_hidden:
                        text_global = None

                    if style_uses_schematic(sty_eff):
                        _schematic_parts = geometry_parts_in_camera_crs(geom, gtype, tr)
                        
                        
                        def _schematic_visibility(x, y, z):
                            try:
                                v, _az, _el = point_visibility(QgsPointXY(float(x), float(y)), float(z))
                                return bool(v)
                            except Exception:
                                return True
                        _plugin_dir = os.path.dirname(os.path.dirname(__file__))
                        handled = False
                        if _pano_zbuffer_enabled:
                            try:
                                camera_xy=(float(cam_pt.x()),float(cam_pt.y()))
                                _runtime_overrides=None
                                if isinstance(_pano_schematic_budget,dict):
                                    _vf=math.radians(max(1e-6,float(VFOV))); _hf=math.radians(max(1e-6,float(HFOV)))
                                    _runtime_overrides={
                                        '_billboard_budget_state':_pano_schematic_budget,
                                        '_pixels_per_rad':max(float(width)/max(1e-9,_hf),float(height)/max(1e-9,_vf))*1.15,
                                        '_min_billboard_px':float(_pano_schematic_budget.get('min_billboard_px',0.0) or 0.0),
                                        '_maxdist_m':float(effective_maxdist) if effective_maxdist is not None else None,
                                    }
                                _definition,_primitives=build_schematic_feature_primitives(
                                    feat,_schematic_parts,gtype,sty_eff,vector_z_sampler,h,_plugin_dir,camera_xy,
                                    runtime_overrides=_runtime_overrides
                                )
                                if _definition is not None:
                                    handled=True
                                    _append_schematic_primitives_for_panorama_zbuffer_419(
                                        _pano_zfaces,_primitives,sty_eff,_definition,_pano_ctx_common,
                                        effective_maxdist,camera_xy,width,render_quality=render_quality,
                                        transparent_objects=transparent_objects,extra_budget=_pano_surface_extra_budget,
                                        visibility_test=None,painter=p,
                                        terrain_face_culler=None,
                                        deferred_edges=_pano_deferred_edges
                                    )
                            except Exception as _exc:
                                try: qcv_log(f"{lyr.name()} | FID {feat.id()} | PANORAMA z-buffer AVR : {_exc}",'SCHEMATIC/RENDER','WARNING')
                                except Exception: pass
                                
                                
                                handled=True
                        else:
                            handled = render_schematic_feature(
                                p, feat, _schematic_parts, gtype, sty_eff, cam_pt, cam_z, proj, width, height,
                                yaw_eff, pitch, roll, HFOV, VFOV, is360, effective_maxdist, vector_z_sampler, h,
                                _plugin_dir, visibility_test=(_schematic_visibility if occ_relief else None),
                                runtime_budget=_pano_schematic_budget
                            )
                        if handled:
                            if (not layer_labels_hidden) and sty_eff.show_labels and anchor_uv_global is not None and text_global:
                                _emit_feature_label(text_global, anchor_uv_global, sty_eff)
                            continue

                    if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
                        pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
                        for pt in pts:
                            pt_cam = tr.transform(pt) if tr else pt
                            ground_z = vector_z_sampler(pt_cam) if vector_z_sampler else 0.0
                            vis_base, azb, elb = point_visibility(pt_cam, ground_z)
                            base_uv = self._finite_uv(project_point(cam_pt, cam_z, pt, tr, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360,
                                                                    dist_max=effective_maxdist, z_tgt=None, z_sampler=vector_z_sampler))
                            top_uv = None; vis_top = False; elt = None
                            if draw_2p5d and h > 0:
                                zt = ground_z + h
                                vis_top, azt, elt = point_visibility(pt_cam, zt)
                                top_uv = self._finite_uv(project_point(cam_pt, cam_z, pt, tr, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360,
                                                                       dist_max=effective_maxdist, z_tgt=zt, z_sampler=None))
                            if _pano_zbuffer_enabled:
                                if draw_2p5d and h > 0:
                                    xyz = np.asarray(((pt_cam.x(), pt_cam.y(), ground_z),
                                                      (pt_cam.x(), pt_cam.y(), ground_z + h)))
                                    path = project_panorama_path_safe(
                                        _pano_ctx_common, xyz, effective_maxdist,
                                        render_quality=render_quality,
                                        wrap_width=(float(width) if is360 else 0.0))
                                    _queue_physical_edge(pen, path, float(width) if is360 else 0.0)
                                elif base_uv:
                                    dep = math.sqrt((pt_cam.x()-cam_pt.x())**2 +
                                                    (pt_cam.y()-cam_pt.y())**2 + (ground_z-cam_z)**2)
                                    ring = [(base_uv[0]+2.0*math.cos(t), base_uv[1]+2.0*math.sin(t))
                                            for t in np.linspace(0.0, 2.0*math.pi, 17)]
                                    _pano_deferred_edges.append((QPen(pen), ring, [dep]*17, None,
                                                                 float(width) if is360 else 0.0))
                                if sty_eff.show_labels and not layer_labels_hidden and (vis_base or vis_top):
                                    _emit_feature_label(text_global, anchor_uv_global, sty_eff)
                                continue
                            if base_uv and (vis_base or not occ_relief):
                                if top_uv and (vis_top or not occ_relief):
                                    self._safe_line(p, base_uv, top_uv)
                                    if occ_objects and vis_top and elt is not None:
                                        self._update_horizon_by_segment(azb, azb, max(elb, elt))
                                else:
                                    try: p.drawEllipse(int(base_uv[0])-2, int(base_uv[1])-2, 4, 4)
                                    except Exception: pass
                                if sty_eff.show_labels and not layer_labels_hidden:
                                    if anchor_uv_global is not None and text_global:
                                        _emit_feature_label(text_global, anchor_uv_global, sty_eff)
                                    else:
                                        anchor = top_uv if (top_uv and vis_top) else base_uv
                                        txt = text_global
                                        if txt and anchor: _emit_feature_label(txt, anchor, sty_eff)

                    elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
                        _cached_parts=list(_panorama_feature_parts_cached(
                            self,lyr,feat,gtype,tr,fast_preview=(render_quality=='low' or bool(self.cb_lowlat.isChecked())),
                            simplify_geometry=decision.simplify_geometry
                        )) if panoramic_overlay_mode else []
                        if _cached_parts:
                            _line_arrays=_cached_parts
                        else:
                            _line_arrays=[]
                            lines=geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
                            for line in lines:
                                if not line: continue
                                step_idx=1 if not self.cb_lowlat.isChecked() else max(1,int(len(line)/500))
                                pts_cam=[]
                                for idx,pxy in enumerate(line):
                                    if (idx%step_idx)!=0: continue
                                    pts_cam.append(tr.transform(pxy) if tr else pxy)
                                if len(pts_cam)>=2: _line_arrays.append(_transform_points_array(pts_cam,None))

                        for arr_line in _line_arrays:
                            try: arr_line=np.asarray(arr_line,dtype=np.float64)
                            except Exception: continue
                            if arr_line.ndim!=2 or arr_line.shape[0]<2 or arr_line.shape[1]!=2: continue
                            base_z_line,_base_z_mean,_raw_ground_z=_effective_base_z_array(
                                self,arr_line,vector_z_sampler,geometry_kind='line',force_horizontal=False
                            )
                            ctx_line=_pano_ctx_common
                            xyz_base=np.column_stack([arr_line,base_z_line])
                            xyz_top=(np.column_stack([arr_line,base_z_line+float(h)]) if (draw_2p5d and h>0) else None)
                            wrap_width=float(width) if bool(is360) else 0.0

                            base_path=project_panorama_path_safe(
                                ctx_line,xyz_base,effective_maxdist,closed=False,render_quality=render_quality,
                                wrap_width=wrap_width,max_points=(1000 if render_quality=='low' else 2200),
                                budget_state=_pano_frame_budget,pole_guard_px=2.5
                            )
                            top_path=None
                            if xyz_top is not None:
                                top_path=project_panorama_path_safe(
                                    ctx_line,xyz_top,effective_maxdist,closed=False,render_quality=render_quality,
                                    wrap_width=wrap_width,max_points=(1000 if render_quality=='low' else 2200),
                                    budget_state=_pano_frame_budget,pole_guard_px=2.5
                                )
                                
                                
                                n=int(xyz_base.shape[0]); wall_xyz=np.vstack([xyz_base,xyz_top]); wall_tri=[]
                                for ii in range(n-1):
                                    jj=ii+1; wall_tri.append((ii,jj,n+jj)); wall_tri.append((ii,n+jj,n+ii))
                                wall_faces=panorama_faces_from_world_mesh(
                                    ctx_line,wall_xyz,wall_tri,effective_maxdist,wrap_width=(wrap_width or None),
                                    role='line_wall',metadata={'projection_family':'PANORAMA','layer_id':lyr.id(),'fid':int(feat.id())},
                                    render_quality=render_quality,extra_face_budget=_pano_surface_extra_budget,pole_guard_px=2.5
                                )
                                if wall_faces:
                                    _top_fill=_normalized_fill_spec_for_sty(sty_eff,transparent_objects=transparent_objects)
                                    _wall_spec=_panorama_wall_fill_spec_419(sty_eff,_top_fill,transparent_objects=transparent_objects)
                                    if _pano_zbuffer_enabled:
                                        _append_panorama_faces_for_zbuffer_419(
                                            _pano_zfaces,wall_faces,_wall_spec,
                                            terrain_culler=None
                                        )
                                    else:
                                        _fill_panorama_wall_faces(p,wall_faces,QColor(_wall_spec['color']))

                            _line_has_depth_surface = bool(_pano_zbuffer_enabled)
                            if transparent_objects or top_path is None:
                                if _line_has_depth_surface:
                                    _queue_physical_edge(pen, base_path, wrap_width)
                                else:
                                    p.setPen(pen); _draw_uv_segments(self,p,base_path.uv)
                            if top_path is not None:
                                if _line_has_depth_surface:
                                    _queue_physical_edge(pen, top_path, wrap_width)
                                else:
                                    p.setPen(pen); _draw_uv_segments(self,p,top_path.uv)
                                edge_step=1 if transparent_objects else max(1,int(len(arr_line)/32))
                                for ii in range(0,len(arr_line),edge_step):
                                    try:
                                        ep=project_panorama_path_safe(ctx_line,np.asarray([xyz_base[ii],xyz_top[ii]],dtype=np.float64),effective_maxdist,
                                                                      closed=False,render_quality=render_quality,wrap_width=wrap_width,max_points=24,
                                                                      budget_state=_pano_frame_budget,pole_guard_px=2.5)
                                        if _line_has_depth_surface:
                                            _queue_physical_edge(pen, ep, wrap_width)
                                        else:
                                            p.setPen(pen); _draw_uv_segments(self,p,ep.uv)
                                    except Exception: pass
                            if (not layer_labels_hidden) and sty.show_labels and (anchor_uv_global is not None) and text_global:
                                _emit_feature_label(text_global,anchor_uv_global,sty_eff)

                    elif gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                        
                        
                        
                        
                        _cached_parts=list(_panorama_feature_parts_cached(
                            self,lyr,feat,gtype,tr,fast_preview=(render_quality=='low' or bool(self.cb_lowlat.isChecked())),
                            simplify_geometry=decision.simplify_geometry
                        )) if panoramic_overlay_mode else []
                        if _cached_parts:
                            _poly_arrays = _cached_parts
                        else:
                            _poly_arrays = []
                            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
                            for poly in polys:
                                if not poly or not poly[0]:
                                    continue
                                ring=poly[0]
                                step_idx = 1 if not self.cb_lowlat.isChecked() else max(1, int(len(ring) / 500))
                                pts_cam_src=[]
                                for idx,pxy in enumerate(ring):
                                    if (idx % step_idx)!=0: continue
                                    pts_cam_src.append(tr.transform(pxy) if tr else pxy)
                                if len(pts_cam_src)>=3:
                                    _poly_arrays.append(_transform_points_array(pts_cam_src,None))

                        for _part_idx, arr_src in enumerate(_poly_arrays):
                            try: arr_src=np.asarray(arr_src,dtype=np.float64)
                            except Exception: continue
                            if arr_src.ndim!=2 or arr_src.shape[0]<3 or arr_src.shape[1]!=2:
                                continue

                            pano_is_volume = bool(draw_2p5d and h > 0)
                            base_z_src, base_z_mean, raw_ground_z_arr = _effective_base_z_array(
                                self, arr_src, vector_z_sampler, geometry_kind='polygon',
                                force_horizontal=pano_is_volume
                            )
                            arr_ring, base_z_ring = _clean_world_polygon_ring(arr_src, base_z_src)
                            if arr_ring.shape[0] < 3:
                                continue
                            pts_cam_ring = [QgsPointXY(float(x), float(y)) for x, y in arr_ring]
                            ctx_poly = _pano_ctx_common
                            xyz_base = np.column_stack([arr_ring, base_z_ring])
                            xyz_top = (np.column_stack([arr_ring, base_z_ring + float(h)]) if pano_is_volume else None)
                            tri_indices = _panorama_tri_indices_cached(self,lyr,feat,_part_idx,arr_ring)
                            if not tri_indices:
                                continue
                            wrap_width = float(width) if bool(is360) else None
                            surface_xyz = xyz_top if xyz_top is not None else xyz_base
                            surface_faces = panorama_faces_from_world_mesh(
                                ctx_poly, surface_xyz, tri_indices, effective_maxdist,
                                wrap_width=wrap_width,
                                role=('roof' if xyz_top is not None else 'ground_surface'),
                                metadata={'projection_family':'PANORAMA','layer_id':lyr.id(),'fid':int(feat.id())},
                                render_quality=render_quality, extra_face_budget=_pano_surface_extra_budget,
                                pole_guard_px=2.5
                            )

                            wall_faces=[]
                            if xyz_top is not None and bool(getattr(sty_eff,'fill_walls',True)):
                                n=int(xyz_base.shape[0])
                                wall_xyz=np.vstack([xyz_base,xyz_top])
                                wall_tri=[]
                                for i in range(n):
                                    j=(i+1)%n
                                    wall_tri.append((i,j,n+j)); wall_tri.append((i,n+j,n+i))
                                wall_faces=panorama_faces_from_world_mesh(
                                    ctx_poly,wall_xyz,wall_tri,effective_maxdist,wrap_width=wrap_width,
                                    role='wall',metadata={'projection_family':'PANORAMA','layer_id':lyr.id(),'fid':int(feat.id())},
                                    render_quality=render_quality,extra_face_budget=_pano_surface_extra_budget,
                                    pole_guard_px=2.5
                                )

                            poly_visible=True
                            if _pano_zbuffer_enabled:
                                if bool(getattr(sty_eff,'fill_polygons',True)):
                                    _top_fill=_normalized_fill_spec_for_sty(sty_eff,transparent_objects=transparent_objects)
                                    _mix_culler = None
                                    _append_panorama_faces_for_zbuffer_419(_pano_zfaces,surface_faces,_top_fill, terrain_culler=_mix_culler)
                                    if wall_faces and bool(getattr(sty_eff,'fill_walls',True)):
                                        _append_panorama_faces_for_zbuffer_419(
                                            _pano_zfaces,wall_faces,
                                            _panorama_wall_fill_spec_419(sty_eff,_top_fill,transparent_objects=transparent_objects),
                                            terrain_culler=_mix_culler
                                        )
                            else:
                                _draw_panorama_polygon_faces(self,p,sty_eff,surface_faces,wall_faces,transparent_objects=transparent_objects)

                            
                            
                            
                            _outline_base=project_panorama_path_safe(
                                ctx_poly,xyz_base,effective_maxdist,closed=True,render_quality=render_quality,
                                wrap_width=(wrap_width or 0.0),max_points=(1200 if render_quality=='low' else 2400),
                                budget_state=_pano_frame_budget,pole_guard_px=2.5
                            )
                            _outline_top=None
                            if xyz_top is not None:
                                _outline_top=project_panorama_path_safe(
                                    ctx_poly,xyz_top,effective_maxdist,closed=True,render_quality=render_quality,
                                    wrap_width=(wrap_width or 0.0),max_points=(1200 if render_quality=='low' else 2400),
                                    budget_state=_pano_frame_budget,pole_guard_px=2.5
                                )
                            _poly_has_depth_surface = bool(_pano_zbuffer_enabled)
                            if transparent_objects or _outline_top is None:
                                if _poly_has_depth_surface:
                                    _queue_physical_edge(pen, _outline_base, (wrap_width or 0.0))
                                else:
                                    p.setPen(pen); _draw_uv_segments(self,p,_outline_base.uv)
                            if _outline_top is not None:
                                if _poly_has_depth_surface:
                                    _queue_physical_edge(pen, _outline_top, (wrap_width or 0.0))
                                else:
                                    p.setPen(pen); _draw_uv_segments(self,p,_outline_top.uv)
                                
                                edge_step=1 if transparent_objects else max(1,int(len(arr_ring)/32))
                                for i in range(0,len(arr_ring),edge_step):
                                    try:
                                        edge_xyz=np.asarray([xyz_base[i],xyz_top[i]],dtype=np.float64)
                                        _ve=project_panorama_path_safe(ctx_poly,edge_xyz,effective_maxdist,closed=False,
                                                                      render_quality=render_quality,wrap_width=(wrap_width or 0.0),
                                                                      max_points=32,budget_state=_pano_frame_budget,pole_guard_px=2.5)
                                        if _poly_has_depth_surface:
                                            _queue_physical_edge(pen, _ve, (wrap_width or 0.0))
                                        else:
                                            p.setPen(pen); _draw_uv_segments(self,p,_ve.uv)
                                        if occ_objects and not _pano_zbuffer_enabled:
                                            zt=float(base_z_ring[i])+float(h); vis,az,el=point_visibility(pts_cam_ring[i],zt)
                                            if vis or not occ_relief: self._update_horizon_by_segment(az,az,el)
                                    except Exception:
                                        pass
                            if (not layer_labels_hidden) and sty.show_labels and (anchor_uv_global is not None) and text_global:
                                _emit_feature_label(text_global,anchor_uv_global,sty_eff)


        
        
        
        
        
        
        
        if _pano_zbuffer_enabled and (_pano_zfaces or _pano_deferred_edges):
            try:
                _has_tex=any(bool((f.get('fill_spec') or {}).get('schematic_billboard_texture',False)) for f in _pano_zfaces)
                _zscale=_panorama_zbuffer_scale_for_preview_419(self,width,height,has_texture=_has_tex)
                _rgba,_depth,_zscale=_compose_panorama_faces_zbuffer_40191(width,height,_pano_zfaces,scale=_zscale,owner=self)
                _compose_panorama_strokes_zbuffer(
                    _rgba, _depth, _zscale, _pano_deferred_edges)
                if occ_relief and self._horizon is not None:
                    _rgba=_mask_panorama_zbuffer_by_horizon(
                        _rgba,_depth,_zscale,_pano_ctx_common,self._horizon,eps
                    )
                _zimg=_rgba_owned_array_to_qimage(_rgba)
                if _zimg is None or _zimg.isNull():
                    raise MemoryError("Cannot allocate panorama depth image")
                if _zimg is not None and not _zimg.isNull():
                    p.save()
                    try:
                        p.setRenderHint(QC.QPainter_RenderHint_SmoothPixmapTransform, True)
                        p.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_DestinationOver)
                        p.drawImage(QRect(0,0,int(width),int(height)),_zimg)
                    finally:
                        p.restore()
                try:
                    self._panorama_last_zbuffer_faces=int(len(_pano_zfaces))
                    self._panorama_last_zbuffer_scale=float(_zscale)
                    self._panorama_last_terrain_culled_faces=int(_pano_terrain_cull_stats.get('objects_culled',0))
                except Exception: pass
                _rgba=None; _zimg=None
            except Exception as _exc:
                try: qcv_log(f"PANORAMA z-buffer 40.19.2 : {_exc}",'PANORAMA/ZBUFFER','WARNING')
                except Exception: pass
                
                _panorama_depth_error = _exc
                raise RuntimeError("PANORAMA depth composition failed") from _exc
            finally:
                try: _pano_zfaces.clear()
                except Exception: pass

        
        
        
        if topo_draw_before_vectors and _pano_zbuffer_enabled:
            p.save()
            try:
                p.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_DestinationOver)
                _draw_topography_group()
            finally:
                p.restore()

        
        
        _pano_deferred_edges.clear()

        
        for _ltxt,_luv,_lsty in _pano_deferred_labels:
            try: self._draw_label(p,_ltxt,_luv,_lsty)
            except Exception: pass

        if _pano_defer_calib_grid:
            try:
                self._draw_calib_grid(p, cam_pt, cam_z, cam_crs, proj, width, height,
                                      yaw_eff, pitch, roll, HFOV, VFOV, is360, z_sampler)
            except Exception:
                pass

        try:
            snaps = list(getattr(self, '_budget_snapshots', []) or [])
            blocked = sum(1 for s in snaps if not s.get('accepted', True))
            active = [s for s in snaps if s.get('accepted', True)]
            if blocked and not active:
                _label_budget_text(self, f"Rendu bloqué • {blocked} couche(s) trop lourde(s)")
            elif active:
                shown = min(len(active), 3)
                main = active[:shown]
                max_count = max(int(s.get('count', 0)) for s in main) if main else 0
                min_dist = min(float(s.get('distance', 0.0)) for s in main) if main else 0.0
                labels_off = any(bool(s.get('labels_off', False)) for s in active)
                simplified = any(bool(s.get('simplify', False) or s.get('style_light', False)) for s in active)
                forced = any(str(s.get('reason', 'ok')) == 'forced_light' for s in active)
                msg = f"Aperçu : {max_count} objets • distance auto {min_dist:.0f} m"
                if labels_off:
                    msg += ' • labels coupés'
                if simplified:
                    msg += ' • rendu allégé'
                if forced:
                    msg += ' • couche(s) contraintes'
                if blocked:
                    msg += f" • {blocked} couche(s) bloquée(s)"
                _label_budget_text(self, msg)
            else:
                _label_budget_text(self, 'Budget : -')
        except Exception:
            pass

        try:
            _guard_status = format_guard_status(getattr(self, '_memory_guard_runtime', None))
            if _guard_status:
                _cur = self.lbl_render_budget.text() if hasattr(self, 'lbl_render_budget') else ''
                if _guard_status not in str(_cur):
                    _label_budget_text(self, (str(_cur) + ' • ' + _guard_status).strip(' •'))
        except Exception:
            pass

        try:
            if isinstance(_pano_schematic_budget, dict):
                _gen=int(_pano_schematic_budget.get('generated',0)); _cul=int(_pano_schematic_budget.get('culled_lod',0))
                _ex=bool(_pano_schematic_budget.get('exhausted',False))
                if _ex or _cul>0:
                    bits=[]
                    if _ex: bits.append(f"motifs plafonnés à {int(_pano_schematic_budget.get('initial',0)):,}".replace(',', ' '))
                    if _cul>0: bits.append(f"{_cul:,} sous-pixel ignorés".replace(',', ' '))
                    _cur=self.lbl_render_budget.text() if hasattr(self,'lbl_render_budget') else ''
                    _label_budget_text(self,(str(_cur)+' • '+' • '.join(bits)).strip(' •'))
        except Exception:
            pass

        try:
            _tc=int(_pano_terrain_cull_stats.get('objects_culled',0)) if panoramic_overlay_mode else 0
            _tm=int(_pano_terrain_cull_stats.get('objects_mixed',0)) if panoramic_overlay_mode else 0
            _sc=int(_pano_terrain_cull_stats.get('schematic_preculled',0)) if panoramic_overlay_mode else 0
            _ec=int(getattr(self,'_panorama_last_edge_terrain_culled',0)) if panoramic_overlay_mode else 0
            bits=[]
            if _tc>0: bits.append(f'{_tc:,} objets rejetés avant faces'.replace(',', ' '))
            if _sc>0: bits.append(f'{_sc:,} objets AVR non générés'.replace(',', ' '))
            if _ec>0: bits.append(f'{_ec:,} segments masqués par relief'.replace(',', ' '))
            if bits:
                _cur=self.lbl_render_budget.text() if hasattr(self,'lbl_render_budget') else ''
                _label_budget_text(self,(str(_cur)+' • '+' • '.join(bits)).strip(' •'))
        except Exception:
            pass
        try:
            _zrs=getattr(self,'_panorama_zbuffer_runtime_stats',None)
            if isinstance(_zrs,dict) and bool(_zrs.get('aborted_memory',False)):
                _cur=self.lbl_render_budget.text() if hasattr(self,'lbl_render_budget') else ''
                _label_budget_text(self,(str(_cur)+' • z-buffer arrêté avant limite mémoire critique').strip(' •'))
        except Exception:
            pass

        if not topo_draw_before_vectors:
            _draw_topography_group()

        
        if self.cb_show_guides.isChecked():
            self._draw_fov_frame(p, width, height)
            self._draw_axes_debug(p, width, height)

        
        try:
            p.setCompositionMode(QC.QPainter_CompositionMode_CompositionMode_SourceOver)
            self._draw_center_and_pdv_guides(
                p, width, height,
                yaw_eff, HFOV,
                proj, is360
            )
        except Exception:
            pass

        try:
            self._draw_azimuth_rule(p, width, height, yaw_eff, HFOV, proj, is360)
        except Exception:
            pass

        try:
            self._draw_monoplot_overlay(p, width, height)
        except Exception:
            pass

    except Exception as e:
        try:
            import traceback
            from qgis.core import QgsMessageLog
            _tb = traceback.format_exc()
            QgsMessageLog.logMessage(
                tr(f"QCALVIEW overlay error: {e}\n--- Python traceback complet ---\n{_tb}"),
                "QCALVIEW", 2
            )
        except Exception:
            pass

    p.end()
    if _panorama_depth_error is not None:
        
        raise RuntimeError("PANORAMA depth composition failed; render cancelled") from _panorama_depth_error
    overlay = _shift_overlay_image(self, overlay)
    self.overlay_image = overlay
    
    
    
    if getattr(self, "viewer", None) and not bool(getattr(self, '_suppress_overlay_viewer_sync', False)):
        try:
            if (self.overlay_image is not None) and (not self.overlay_image.isNull()):
                self.viewer.update_overlay(self.overlay_image)
            else:
                self.viewer.clear_overlay()
        except Exception:
            pass

    return overlay

def _overlay_params_key(self, width: int, height: int):
    
    yaw_eff = self.d_yaw.value() + self.d_yaw_offset.value()
    cam_fid = None
    cam_xy = (None, None)
    try:
        cam_feat = self._camera_current_feature()
        if cam_feat is not None and cam_feat.isValid() and cam_feat.geometry() is not None and not cam_feat.geometry().isEmpty():
            cam_fid = int(cam_feat.id())
            cam_pt, _cam_crs = self._camera_point_in_work_crs(cam_feat)
            if cam_pt is not None:
                cam_xy = (round(float(cam_pt.x()), 3), round(float(cam_pt.y()), 3))
    except Exception:
        cam_fid = None
        cam_xy = (None, None)
    return (
        int(width), int(height),
        cam_fid, cam_xy,
        round(self.d_hfov.value(), 6), round(self.d_vfov.value(), 6),
        round(yaw_eff, 6), round(self.d_pitch.value(), 6), round(self.d_roll.value(), 6),
        round(float(self.d_camheight.value()), 3),
        
        self.cmb_proj.currentText(), bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked()),
        round(max(0.0, self.d_maxdist.value()), 3),
        bool(getattr(self, "cb_auto_depth", None) and self.cb_auto_depth.isChecked()),
        int(getattr(self, "cmb_perf_budget", None).currentIndex()) if getattr(self, "cmb_perf_budget", None) else 1,
        bool(getattr(self, "cb_block_heavy_layers", None) and self.cb_block_heavy_layers.isChecked()),
        int(self.combo_relief_mode.currentIndex()) if hasattr(self, 'combo_relief_mode') else -1,
        bool(self.cb_show_dem.isChecked()),
        bool(getattr(self, 'cb_curvature', None) and self.cb_curvature.isChecked()),
        round(float(getattr(self, 'd_earth_radius_km', None).value()) if hasattr(self, 'd_earth_radius_km') else 6370.0, 3),
        bool(self.cb_draw_skyline.isChecked()),
        bool(getattr(self, 'cb_draw_ridgelines', None) and self.cb_draw_ridgelines.isChecked()),
        round(float(self.d_ridge_gap.value()), 3) if hasattr(self, 'd_ridge_gap') else 250.0,
        round(float(self.d_ridge_prom.value()), 3) if hasattr(self, 'd_ridge_prom') else 2.0,
        bool(self.cb_transparent_topo.isChecked()) if hasattr(self, 'cb_transparent_topo') else True,
        bool(self.cb_skyline_fill.isChecked()) if hasattr(self, 'cb_skyline_fill') else False,
        int(self.spin_sky_fill_alpha.value()) if hasattr(self, 'spin_sky_fill_alpha') else 0,
        int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1,
        bool(self.cb_sky_dashed.isChecked()) if hasattr(self, 'cb_sky_dashed') else False,
        round(float(self.spin_dem_step.value()), 3) if hasattr(self, 'spin_dem_step') else 50.0,
        int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1,
        int(self.spin_dem_alpha.value()) if hasattr(self, 'spin_dem_alpha') else 255,
        bool(self.cb_wire_dashed.isChecked()) if hasattr(self, 'cb_wire_dashed') else False,
        int(self.combo_wire_mode.currentIndex()) if hasattr(self, 'combo_wire_mode') else 0,
        getattr(self, '_dem_color', QColor()).rgba() if hasattr(self, '_dem_color') else None,
        getattr(self, '_dem_color', QColor()).rgba() if hasattr(self, '_dem_color') else None,
        (self.cmb_dem.currentLayer().id() if getattr(self, 'cmb_dem', None) and self.cmb_dem.currentLayer() else None),
        bool(self.cb_use_dem_z.isChecked()) if hasattr(self, 'cb_use_dem_z') else False,
        round(float(self.d_az_step.value()), 4) if hasattr(self, 'd_az_step') else 0.5,
        round(float(self.d_rad_step.value()), 3) if hasattr(self, 'd_rad_step') else 50.0,
        round(float(self.d_eps.value()), 3) if hasattr(self, 'd_eps') else 0.2,
        bool(self.cb_occ_terrain.isChecked()),
        True,  
        bool(getattr(self, 'cb_transparent_objects', None) and self.cb_transparent_objects.isChecked()),
        bool(self.cb_lowlat.isChecked()),
        bool(self.cb_draw_2p5d.isChecked()),
        bool(getattr(self, 'cb_force_horizontal_25d', None) and self.cb_force_horizontal_25d.isChecked()),
        str(self.txt_hfield.text()).strip() if hasattr(self, 'txt_hfield') else '',
        round(float(self.d_hdefault.value()), 4) if hasattr(self, 'd_hdefault') else 0.0,
        bool(self.cb_show_labels.isChecked()),
        bool(getattr(self, 'cb_show_center_axis', None) and self.cb_show_center_axis.isChecked()),
        bool(getattr(self, 'cb_show_pdv_axis', None) and self.cb_show_pdv_axis.isChecked()),
        bool(getattr(self, 'cb_calib_enable', None) and self.cb_calib_enable.isChecked()),
        bool(getattr(self, 'cb_proj_grid_enable', None) and self.cb_proj_grid_enable.isChecked()),
        round(float(getattr(self, 'd_proj_grid_step', None).value()) if hasattr(self, 'd_proj_grid_step') else 5.0, 3),
        int(getattr(self, 'cmb_calib_type', None).currentIndex()) if getattr(self, 'cmb_calib_type', None) else 0,
        round(float(getattr(self, 'd_calib_spacing', None).value()) if hasattr(self, 'd_calib_spacing') else 5.0, 3),
        round(float(getattr(self, 'd_calib_width', None).value()) if hasattr(self, 'd_calib_width') else 50.0, 3),
        round(float(getattr(self, 'd_calib_depth', None).value()) if hasattr(self, 'd_calib_depth') else 50.0, 3),
        round(float(getattr(self, 'd_calib_height', None).value()) if hasattr(self, 'd_calib_height') else 10.0, 3),
        round(float(getattr(self, 'd_calib_dist', None).value()) if hasattr(self, 'd_calib_dist') else 30.0, 3),
        round(float(getattr(self, 'd_calib_elev', None).value()) if hasattr(self, 'd_calib_elev') else 0.0, 3),
        bool(getattr(self, 'cb_calib_snap_dem', None) and self.cb_calib_snap_dem.isChecked()),
        bool(getattr(self, 'cb_calib_labels', None) and self.cb_calib_labels.isChecked()),
        bool(getattr(self, 'cb_calib_axes', None) and self.cb_calib_axes.isChecked()),
        int(getattr(self, 'spin_calib_width', None).value()) if getattr(self, 'spin_calib_width', None) else 1,
        getattr(self, '_calib_color', QColor()).rgba() if hasattr(self, '_calib_color') else None,
        round(float(getattr(self, 'current_pdv_azimuth', -9999.0) if getattr(self, 'current_pdv_azimuth', None) is not None else -9999.0), 6),
        bool(getattr(self, 'cb_az_rule_enable', None) and self.cb_az_rule_enable.isChecked()),
        round(float(getattr(self, 'd_az_rule_band_pct', None).value()) if hasattr(self, 'd_az_rule_band_pct') else 4.0, 3),
        round(float(getattr(self, 'd_az_rule_text_pct', None).value()) if hasattr(self, 'd_az_rule_text_pct') else 38.0, 3),
        str(getattr(self, '_current_render_quality', 'high')),
        
        (int(self.cmb_off_mode.currentIndex()) if hasattr(self, 'cmb_off_mode') else 0),
        round(float(self.spin_off_h.value()), 6) if hasattr(self, 'spin_off_h') else 0.0,
        round(float(self.spin_off_v.value()), 6) if hasattr(self, 'spin_off_v') else 0.0,
        
        tuple(
            (
                getattr(sty.layer, 'id', lambda: None)() if sty.layer else None,
                int(getattr(getattr(self, '_layer_cache_versions', {}), 'get', lambda *a, **k: 0)(sty.layer.id(), 0)) if getattr(sty, 'layer', None) else None,
                getattr(sty.layer, 'name', lambda: None)() if getattr(sty, 'layer', None) else None,
                getattr(sty, 'width', None),
                getattr(sty, 'color', None).rgba() if getattr(sty, 'color', None) else None,
                bool(getattr(sty, 'visible', True)),
                bool(getattr(sty, 'show_labels', False)),
                bool(getattr(sty, 'use_qgis_labels', True)),
                getattr(sty, 'label_field', None),
                getattr(sty, 'label_text', ''),
                getattr(sty, 'label_size', None),
                getattr(getattr(sty, 'label_text_color', None), 'rgba', lambda: None)() if getattr(sty, 'label_text_color', None) else None,
                bool(getattr(sty, 'label_bg', False)),
                getattr(getattr(sty, 'label_bg_color', None), 'rgba', lambda: None)() if getattr(sty, 'label_bg_color', None) else None,
                bool(getattr(sty, 'enable_25d', True)),
                getattr(sty, 'height_field_override', ''),
                getattr(sty, 'default_height_override', None),
                bool(getattr(sty, 'fill_polygons', True)),
                getattr(getattr(sty, 'fill_color', None), 'rgba', lambda: None)() if getattr(sty, 'fill_color', None) else None,
                float(getattr(sty, 'opacity', 1.0) or 1.0),
                bool(getattr(sty, 'fill_walls', True)),
                bool(getattr(sty, 'use_qgis_style', True)),
                str(getattr(sty, 'qgis_theme_name', '') or ''),
                str(getattr(sty, 'qgis_theme_style_name', '') or ''),
                bool(getattr(sty, 'schematic_enabled', False)),
                str(getattr(sty, 'schematic_symbol_id', '') or ''),
                json.dumps(getattr(sty, 'schematic_params', {}) or {}, sort_keys=True, ensure_ascii=True),
                _fill_spec_signature(getattr(sty, 'qgis_fill_style', None))
            )
            for sty in self.layer_styles
        )
    )




def _ensure_fast_render_caches(self):
    if not hasattr(self, '_geom_cache') or self._geom_cache is None:
        self._geom_cache = {}
    if not hasattr(self, '_layer_cache_versions') or self._layer_cache_versions is None:
        self._layer_cache_versions = {}


def _perf_profile_name(self):
    try:
        txt = str(self.cmb_perf_budget.currentText()).strip().lower()
    except Exception:
        txt = 'équilibré'
    if txt.startswith('s'):
        return 'safe'
    if 'max' in txt or 'détail' in txt or 'detail' in txt:
        return 'detail'
    return 'balanced'


def _label_budget_text(self, text):
    try:
        self.lbl_render_budget.setText(tr(str(text)))
    except Exception:
        pass


def _requested_maxdist_for_layer(self, sty, gtype):
    try:
        requested = float(self.d_maxdist.value())
    except Exception:
        requested = 0.0
    draw_25d = bool(getattr(self, 'cb_draw_2p5d', None) and self.cb_draw_2p5d.isChecked())
    
    
    
    extruded = bool((not style_uses_schematic(sty)) and draw_25d and getattr(sty, 'enable_25d', True))
    if requested <= 0.0:
        requested = default_interactive_distance(gtype, draw_25d=extruded)
    
    
    try:
        _cap = _active_panorama_guard(self).get('maxdist_cap')
        if _cap is not None and float(_cap) > 0.0:
            requested = min(float(requested), float(_cap))
    except Exception:
        pass
    return max(25.0, float(requested))


def _bbox_points_in_cam_crs(rect, tr):
    pts = []
    if rect is None or rect.isNull():
        return pts
    corners = [
        QgsPointXY(rect.xMinimum(), rect.yMinimum()),
        QgsPointXY(rect.xMaximum(), rect.yMinimum()),
        QgsPointXY(rect.xMaximum(), rect.yMaximum()),
        QgsPointXY(rect.xMinimum(), rect.yMaximum()),
        QgsPointXY((rect.xMinimum() + rect.xMaximum()) * 0.5, (rect.yMinimum() + rect.yMaximum()) * 0.5),
    ]
    for pt in corners:
        try:
            cpt = tr.transform(pt) if tr is not None else pt
            pts.append((float(cpt.x()), float(cpt.y())))
        except Exception:
            continue
    return pts


def _feature_candidate_stats(feat, tr, cam_pt, maxdist, yaw_deg, hfov_deg, projection, is360, width=None, min_screen_px=0.0):
    try:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return None
        rect = geom.boundingBox()
    except Exception:
        return None
    pts = _bbox_points_in_cam_crs(rect, tr)
    if not pts:
        return None

    cx, cy = float(cam_pt.x()), float(cam_pt.y())
    near_limit = min(float(maxdist or 10000.0), 12000.0 if float(maxdist or 0.0) > 12000.0 else float(maxdist or 10000.0))
    maxdist = max(1.0, float(maxdist))
    diag = 0.0
    try:
        dx = float(rect.xMaximum() - rect.xMinimum())
        dy = float(rect.yMaximum() - rect.yMinimum())
        diag = math.hypot(dx, dy)
    except Exception:
        diag = 0.0

    best_dist = None
    in_view = False
    margin = max(8.0, float(hfov_deg) * 0.12)
    for x, y in pts:
        d = math.hypot(x - cx, y - cy)
        if best_dist is None or d < best_dist:
            best_dist = d
        if d <= (maxdist + max(5.0, diag * 0.5)):
            if bool(is360):
                in_view = True
                break
            az = math.degrees(math.atan2(x - cx, y - cy))
            ddeg = abs(_wrap180_deg(float(az) - float(yaw_deg)))
            if ddeg <= (float(hfov_deg) * 0.5 + margin):
                in_view = True
                break
    if (best_dist is None) or (not in_view):
        return None

    if min_screen_px and width and best_dist > 1.0 and diag > 0.0:
        try:
            px_per_rad = float(width) / max(math.radians(max(1.0, float(hfov_deg))), 1e-6)
            apparent_px = (diag / best_dist) * px_per_rad
            if apparent_px < float(min_screen_px):
                return None
        except Exception:
            pass
    return {'distance': float(best_dist), 'diag': float(diag)}


def _make_request_rect(layer, cam_pt, cam_crs, maxdist):
    maxdist = max(1.0, float(maxdist))
    rect_cam = QgsRectangle(float(cam_pt.x()) - maxdist, float(cam_pt.y()) - maxdist, float(cam_pt.x()) + maxdist, float(cam_pt.y()) + maxdist)
    src_crs = layer.crs()
    if src_crs == cam_crs:
        return rect_cam, None
    try:
        tr_back = QgsCoordinateTransform(cam_crs, src_crs, QgsProject.instance())
        corners = [
            tr_back.transform(QgsPointXY(rect_cam.xMinimum(), rect_cam.yMinimum())),
            tr_back.transform(QgsPointXY(rect_cam.xMaximum(), rect_cam.yMinimum())),
            tr_back.transform(QgsPointXY(rect_cam.xMaximum(), rect_cam.yMaximum())),
            tr_back.transform(QgsPointXY(rect_cam.xMinimum(), rect_cam.yMaximum())),
        ]
        xs = [float(p.x()) for p in corners]
        ys = [float(p.y()) for p in corners]
        return QgsRectangle(min(xs), min(ys), max(xs), max(ys)), tr_back
    except Exception:
        return None, None


def _iter_candidate_features(self, layer, sty, cam_pt, cam_crs, maxdist, yaw_deg, hfov_deg, projection, is360,
                             render_quality='high', feedback=None, need_attrs=True, extra_attr_names=None,
                             simplify_geometry=False, hard_limit=None, width=None):
    src_crs = layer.crs()
    tr = None if src_crs == cam_crs else QgsCoordinateTransform(src_crs, cam_crs, QgsProject.instance())
    layer_rect, _ = _make_request_rect(layer, cam_pt, cam_crs, maxdist)
    req = QgsFeatureRequest()
    if layer_rect is not None:
        try:
            req.setFilterRect(layer_rect)
        except Exception:
            pass
    if feedback is not None:
        try:
            req.setFeedback(feedback)
        except Exception:
            pass
    if not need_attrs:
        try:
            req.setNoAttributes()
        except Exception:
            pass
    elif extra_attr_names:
        try:
            req.setSubsetOfAttributes(extra_attr_names, layer.fields())
        except Exception:
            pass
    if hard_limit:
        try:
            req.setLimit(int(hard_limit) * 4)
        except Exception:
            pass

    min_screen_px = 1.5 if (render_quality == 'low' or simplify_geometry) else 1.0
    yielded = 0
    for feat in layer.getFeatures(req):
        if feedback is not None:
            try:
                if feedback.isCanceled():
                    break
            except Exception:
                pass
        stats = _feature_candidate_stats(feat, tr, cam_pt, maxdist, yaw_deg, hfov_deg, projection, is360, width=width, min_screen_px=min_screen_px)
        if stats is None:
            continue
        yield feat
        yielded += 1
        if hard_limit and yielded >= int(hard_limit):
            break


def _count_geom_vertices(geom, max_vertices=5000):
    try:
        n = 0
        for _ in geom.vertices():
            n += 1
            if n >= int(max_vertices):
                break
        return int(n)
    except Exception:
        return 0


def _estimate_layer_cost(self, layer, sty, cam_pt, cam_crs, maxdist, yaw_deg, hfov_deg, projection, is360, profile,
                         render_quality='high', degrade_labels=False, degrade_style=False, simplify_geometry=False,
                         feedback=None):
    try:
        total_count = int(layer.featureCount())
    except Exception:
        total_count = -1
    gtype = QgsWkbTypes.geometryType(layer.wkbType())
    try:
        is_multi = bool(QgsWkbTypes.isMultiType(layer.wkbType()))
    except Exception:
        is_multi = False
    geom_factor = geom_factor_from_gtype(gtype, is_multi)
    style_factor = style_factor_from_style(sty, degrade_style=degrade_style)
    show_labels = bool(getattr(sty, 'show_labels', False)) and bool(getattr(self, 'cb_show_labels', None) and self.cb_show_labels.isChecked())
    if render_quality == 'low':
        show_labels = False
    labels_factor = 1.35 if (show_labels and not degrade_labels) else 1.0
    draw_25d = bool(getattr(self, 'cb_draw_2p5d', None) and self.cb_draw_2p5d.isChecked())
    is_schematic = style_uses_schematic(sty)
    extrusion_factor = extrusion_factor_for_style(sty, gtype, draw_25d=(draw_25d and not is_schematic))
    
    
    transparency_factor = 1.0 if is_schematic else (1.15 if bool(getattr(self, 'cb_transparent_objects', None) and self.cb_transparent_objects.isChecked()) else 1.0)

    hard_probe = max(120, int(profile.hard_features * 1.6))
    candidate_count = 0
    sample_vertices = 0
    sample_n = 0
    width = int(max(256, min(4096, getattr(self, '_overlay_w', 1024) or 1024)))
    for feat in _iter_candidate_features(
        self, layer, sty, cam_pt, cam_crs, maxdist, yaw_deg, hfov_deg, projection, is360,
        render_quality=render_quality, feedback=feedback, need_attrs=False, simplify_geometry=simplify_geometry,
        hard_limit=hard_probe, width=width
    ):
        candidate_count += 1
        if sample_n < 120:
            try:
                sample_vertices += _count_geom_vertices(feat.geometry(), max_vertices=6000)
                sample_n += 1
            except Exception:
                pass
        if candidate_count >= max(profile.hard_features * 2, 2500):
            break

    avg_vertices = (float(sample_vertices) / float(sample_n)) if sample_n else (4.0 if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry else 24.0)
    estimated_vertices = int(max(1.0, avg_vertices) * float(candidate_count))
    if simplify_geometry:
        estimated_vertices = int(estimated_vertices * 0.55)

    score = estimate_score(candidate_count, estimated_vertices, geom_factor, style_factor, labels_factor, extrusion_factor, transparency_factor, simplify_geometry=simplify_geometry)
    return LayerEstimate(
        layer_id=layer.id(),
        feature_count_total=total_count,
        candidate_count=int(candidate_count),
        estimated_vertices=int(estimated_vertices),
        geom_factor=float(geom_factor),
        style_factor=float(style_factor),
        labels_factor=float(labels_factor),
        extrusion_factor=float(extrusion_factor),
        score=float(score),
        needs_simplification=bool(simplify_geometry),
    )


def _estimate_fits_profile(est, profile, extruded=False):
    if est.candidate_count <= 0:
        return True
    hard_feat = int(profile.hard_features)
    if extruded:
        hard_feat = min(hard_feat, 2000)
    if est.candidate_count > hard_feat:
        return False
    if est.estimated_vertices > int(profile.hard_vertices):
        return False
    if est.score > float(profile.hard_score):
        return False
    return est.score <= float(profile.target_score)


def _decide_layer_render(self, layer, sty, cam_pt, cam_crs, yaw_deg, hfov_deg, projection, is360, render_quality='high'):
    gtype = QgsWkbTypes.geometryType(layer.wkbType())
    requested = _requested_maxdist_for_layer(self, sty, gtype)
    profile = get_budget_profile(_perf_profile_name(self))
    auto_depth = bool(getattr(self, 'cb_auto_depth', None) and self.cb_auto_depth.isChecked())
    draw_25d = bool(getattr(self, 'cb_draw_2p5d', None) and self.cb_draw_2p5d.isChecked())
    extruded = bool((not style_uses_schematic(sty)) and draw_25d and getattr(sty, 'enable_25d', True) and gtype in (QC.QgsWkbTypes_GeometryType_LineGeometry, QC.QgsWkbTypes_GeometryType_PolygonGeometry))

    attempts = [
        (False, False, False),
        (True, False, False),
        (True, False, True),
        (True, True, True),
    ]
    ratios = profile.default_search_ratios if auto_depth else (1.0,)
    best_reject = None

    for degrade_labels, degrade_style, simplify_geometry in attempts:
        best_ok = None
        last_est = None
        for ratio in ratios:
            eff = max(25.0, float(requested) * float(ratio))
            est = _estimate_layer_cost(self, layer, sty, cam_pt, cam_crs, eff, yaw_deg, hfov_deg, projection, is360, profile,
                                       render_quality=render_quality, degrade_labels=degrade_labels, degrade_style=degrade_style,
                                       simplify_geometry=simplify_geometry, feedback=getattr(self, '_feature_feedback', None))
            last_est = est
            if _estimate_fits_profile(est, profile, extruded=extruded):
                best_ok = RenderDecision(True, eff, degrade_labels, degrade_style, simplify_geometry, 'ok', est.score, est.candidate_count, est.estimated_vertices)
                break
        if best_ok is not None:
            return best_ok
        if last_est is not None:
            best_reject = RenderDecision(False, max(25.0, float(requested) * float(ratios[-1])), degrade_labels, degrade_style, simplify_geometry, 'budget_exceeded', last_est.score, last_est.candidate_count, last_est.estimated_vertices)

    if best_reject is None:
        best_reject = RenderDecision(False, requested, True, True, True, 'budget_exceeded', 0.0, 0, 0)

    allow_block = bool(getattr(self, 'cb_block_heavy_layers', None) and self.cb_block_heavy_layers.isChecked())
    if not allow_block:
        return RenderDecision(
            True,
            max(25.0, float(best_reject.effective_maxdist)),
            True,
            True,
            True,
            'forced_light',
            float(best_reject.score),
            int(best_reject.candidate_count),
            int(best_reject.estimated_vertices),
        )
    return best_reject


def _begin_feature_feedback(self):
    try:
        fb = getattr(self, '_feature_feedback', None)
        if fb is not None:
            fb.cancel()
    except Exception:
        pass
    try:
        self._feature_feedback = QgsFeedback()
    except Exception:
        self._feature_feedback = None
    return getattr(self, '_feature_feedback', None)


def _transform_points_array(points, tr):
    if not points:
        return np.empty((0, 2), dtype=np.float64)
    out = np.empty((len(points), 2), dtype=np.float64)
    if tr is None:
        for i, pt in enumerate(points):
            out[i, 0] = float(pt.x())
            out[i, 1] = float(pt.y())
    else:
        for i, pt in enumerate(points):
            p = tr.transform(pt)
            out[i, 0] = float(p.x())
            out[i, 1] = float(p.y())
    return out


def _get_layer_geometry_cache(self, layer, cam_crs, fast_preview=False, maxdist=None, yaw_deg=None, hfov_deg=None,
                              projection=None, is360=False, render_quality='high', simplify_geometry=False,
                              cam_pt=None, sty=None):
    _ensure_fast_render_caches(self)
    maxdist = float(maxdist or 0.0)
    yaw_deg = float(yaw_deg or 0.0)
    hfov_deg = float(hfov_deg or 60.0)
    layer_rev = int(self._layer_cache_versions.get(layer.id(), 0)) if hasattr(self, '_layer_cache_versions') else 0
    width_hint = int(max(256, getattr(self, '_overlay_w', 1024) or 1024))
    cam_key = None
    if cam_pt is not None:
        try:
            cam_key = (round(float(cam_pt.x()), 3), round(float(cam_pt.y()), 3))
        except Exception:
            cam_key = None
    key = (
        layer.id(), cam_crs.authid(), bool(fast_preview), str(render_quality), bool(simplify_geometry),
        int(round(maxdist)), int(round(yaw_deg * 10.0)), int(round(hfov_deg * 10.0)),
        int(round(width_hint / 64.0)) * 64, str(projection or ''), bool(is360), layer_rev,
        cam_key
    )
    cached = self._geom_cache.get(key)
    if cached is not None:
        return cached

    src_crs = layer.crs()
    tr = None if src_crs == cam_crs else QgsCoordinateTransform(src_crs, cam_crs, QgsProject.instance())
    gtype = QgsWkbTypes.geometryType(layer.wkbType())
    features = []
    hard_limit = None
    try:
        profile = get_budget_profile(_perf_profile_name(self))
        hard_limit = int(profile.hard_features * 1.6)
    except Exception:
        hard_limit = 2500

    feature_iter = _iter_candidate_features(
        self, layer, sty, cam_pt, cam_crs, maxdist, yaw_deg, hfov_deg, projection, is360,
        render_quality=render_quality, feedback=getattr(self, '_feature_feedback', None), need_attrs=True,
        simplify_geometry=simplify_geometry, hard_limit=hard_limit, width=width_hint
    ) if (cam_pt is not None and maxdist > 0.0) else layer.getFeatures()

    for feat in feature_iter:
        geom = feat.geometry()
        parts = []
        if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
            pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
            arr = _transform_points_array(pts, tr)
            if arr.size:
                parts.append(arr)
        elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for line in lines:
                if not line:
                    continue
                if simplify_geometry:
                    step_idx = max(1, int(len(line) / (60 if fast_preview else 120)))
                else:
                    step_idx = 1 if not fast_preview else max(1, int(len(line) / 120))
                pts = [pt for idx, pt in enumerate(line) if (idx % step_idx) == 0]
                arr = _transform_points_array(pts, tr)
                if arr.shape[0] >= 1:
                    parts.append(arr)
        elif gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                ring = poly[0]
                if simplify_geometry:
                    step_idx = max(1, int(len(ring) / (60 if fast_preview else 120)))
                else:
                    step_idx = 1 if not fast_preview else max(1, int(len(ring) / 120))
                pts = [pt for idx, pt in enumerate(ring) if (idx % step_idx) == 0]
                arr = _transform_points_array(pts, tr)
                if arr.shape[0] >= 1:
                    parts.append(arr)
        if parts:
            features.append({'feat': QgsFeature(feat), 'geom': geom, 'parts': parts})

    cached = {'gtype': gtype, 'tr': tr, 'features': features}
    self._geom_cache[key] = cached
    self._layer_cache_versions[layer.id()] = self._layer_cache_versions.get(layer.id(), 0)
    return cached


def _iter_uv_segments(uvs):
    prev = None
    for uv in uvs:
        if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])):
            cur = (float(uv[0]), float(uv[1]))
            if prev is not None:
                yield prev, cur
            prev = cur
        else:
            prev = None


def _feature_depth_key(item, cam_xy=None):
    parts = item.get('parts') or []
    if not parts:
        return (0.0, 0.0)
    try:
        if cam_xy is None:
            arr = parts[0]
            if arr is None or getattr(arr, 'size', 0) == 0:
                return (0.0, 0.0)
            return (float(np.nanmean(arr[:, 0])**2 + np.nanmean(arr[:, 1])**2), 0.0)
        cx, cy = float(cam_xy[0]), float(cam_xy[1])
        mins, means = [], []
        for arr in parts:
            if arr is None or getattr(arr, 'size', 0) == 0:
                continue
            d2 = (arr[:, 0] - cx)**2 + (arr[:, 1] - cy)**2
            if getattr(d2, 'size', 0) == 0:
                continue
            mins.append(float(np.nanmin(d2)))
            means.append(float(np.nanmean(d2)))
        if mins:
            return (float(min(mins)), float(sum(means) / len(means)))
    except Exception:
        pass
    return (0.0, 0.0)


def _geometry_depth_key(geom, tr, cam_pt):
    try:
        if geom is None or geom.isEmpty():
            return (0.0, 0.0)
        gtype = QgsWkbTypes.geometryType(geom.wkbType())
        pts = []
        if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
            pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
        elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for line in lines:
                if not line:
                    continue
                step = max(1, int(len(line) / 120))
                pts.extend([pt for i, pt in enumerate(line) if (i % step) == 0])
        elif gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                ring = poly[0]
                step = max(1, int(len(ring) / 120))
                pts.extend([pt for i, pt in enumerate(ring) if (i % step) == 0])
        if not pts:
            c = geom.centroid().asPoint()
            pts = [c]
        arr = _transform_points_array(pts, tr)
        if arr.shape[0] == 0:
            return (0.0, 0.0)
        cx, cy = float(cam_pt.x()), float(cam_pt.y())
        
        
        
        _min_d2 = math.inf; _sum_d2 = 0.0; _n_d2 = 0
        for _q in range(int(arr.shape[0])):
            try:
                _dx = float(arr[_q,0]) - cx; _dy = float(arr[_q,1]) - cy
                _d2 = _dx*_dx + _dy*_dy
            except Exception:
                continue
            if not math.isfinite(_d2):
                continue
            if _d2 < _min_d2:
                _min_d2 = _d2
            _sum_d2 += _d2; _n_d2 += 1
        if _n_d2 <= 0:
            return (0.0, 0.0)
        return (float(_min_d2), float(_sum_d2 / float(_n_d2)))
    except Exception:
        return (0.0, 0.0)


def _uv_subpaths(uvs):
    cur = []
    for uv in uvs:
        if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])):
            cur.append(QPointF(float(uv[0]), float(uv[1])))
        else:
            if len(cur) >= 2:
                yield cur
            cur = []
    if len(cur) >= 2:
        yield cur


def _uv_runs_array(uvs, min_len=2):
    cur = []
    for uv in uvs:
        if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])):
            cur.append((float(uv[0]), float(uv[1])))
        else:
            if len(cur) >= min_len:
                yield np.asarray(cur, dtype=np.float64)
            cur = []
    if len(cur) >= min_len:
        yield np.asarray(cur, dtype=np.float64)


def _unwrap_x_for_wrap(pts, wrap_width):
    
    return unwrap_x_continuous(pts, wrap_width)


def _iter_wrap_shifted_pts(pts, wrap_width, margin_px=2.0):
    
    yield from iter_viewport_copies(pts, wrap_width, margin_px=margin_px, closed=False)


def _polygon_paths_from_uvs(uvs, wrap_width=None):
    
    for pts in _uv_runs_array(uvs, min_len=3):
        if wrap_width and float(wrap_width) > 1.0:
            shifted_runs = iter_viewport_copies(pts, wrap_width, margin_px=2.0, closed=True)
        else:
            shifted_runs = (pts,)
        for run in shifted_runs:
            run = np.asarray(run, dtype=np.float64)
            if run.ndim != 2 or run.shape[0] < 3:
                continue
            
            if not wrap_width and run.shape[0] >= 4:
                try:
                    if np.linalg.norm(run[0] - run[-1]) <= 1e-6:
                        run = run[:-1]
                except Exception:
                    pass
            if run.shape[0] < 3:
                continue
            poly = QPolygonF([QPointF(float(x), float(y)) for x, y in run])
            
            
            if poly.first() != poly.last():
                poly.append(poly.first())
            yield poly


def _wall_quads_from_uvs(base_uv, top_uv, wrap_width=None):
    n = min(len(base_uv), len(top_uv))
    for i in range(max(0, n - 1)):
        bu0, bu1 = base_uv[i], base_uv[i+1]
        tu0, tu1 = top_uv[i], top_uv[i+1]
        vals = [bu0[0], bu0[1], bu1[0], bu1[1], tu0[0], tu0[1], tu1[0], tu1[1]]
        if not all(math.isfinite(float(v)) for v in vals):
            continue
        quad = np.asarray([
            [float(bu0[0]), float(bu0[1])],
            [float(bu1[0]), float(bu1[1])],
            [float(tu1[0]), float(tu1[1])],
            [float(tu0[0]), float(tu0[1])],
        ], dtype=np.float64)
        if wrap_width and float(wrap_width) > 1.0:
            shifted_quads = _iter_wrap_shifted_pts(quad, wrap_width)
        else:
            shifted_quads = (quad,)
        for sq in shifted_quads:
            yield QPolygonF([
                QPointF(float(sq[0, 0]), float(sq[0, 1])),
                QPointF(float(sq[1, 0]), float(sq[1, 1])),
                QPointF(float(sq[2, 0]), float(sq[2, 1])),
                QPointF(float(sq[3, 0]), float(sq[3, 1])),
                QPointF(float(sq[0, 0]), float(sq[0, 1])),
            ])


def _project_uv_depth_small(ctx, pts_xy, z_tgt, dist_max=None):
    
    try:
        n = len(pts_xy)
    except Exception:
        raise ValueError('pts_xy must be an array-like of shape (N,2)')
    if n == 0:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    try:
        zlen = len(z_tgt)
        z_is_scalar = False
    except Exception:
        zlen = 0
        z_is_scalar = True
    try:
        z_scalar = float(z_tgt) if z_is_scalar else None
    except Exception:
        z_scalar = float('nan')
        z_is_scalar = True

    cx, cy, cam_z = float(ctx['cx']), float(ctx['cy']), float(ctx['cam_z'])
    r, up, f = ctx['r'], ctx['u'], ctx['f']
    rx, ry, rz = float(r[0]), float(r[1]), float(r[2])
    ux, uy, uz = float(up[0]), float(up[1]), float(up[2])
    fxv, fyv, fzv = float(f[0]), float(f[1]), float(f[2])
    W, H = max(1, int(ctx['width'])), max(1, int(ctx['height']))
    proj = str(ctx['proj']).upper()
    maxd2 = None if dist_max is None else float(dist_max) ** 2
    hf = math.radians(max(1e-6, float(ctx['HFOV'])))
    vf = math.radians(max(1e-6, float(ctx['VFOV'])))
    pin_fx = (W * 0.5) / math.tan(hf * 0.5) if proj == 'PINHOLE' else None
    pin_fy = (H * 0.5) / math.tan(vf * 0.5) if proj == 'PINHOLE' else None
    fx_cc = W / max(1e-9, hf) if proj == 'CYLINDRICAL' else None
    if proj == 'CYLINDRICAL':
        if not (math.isfinite(vf) and 1e-6 < vf < math.pi - 1e-3 and abs(math.tan(vf * 0.5)) > 1e-9):
            vf = 2.0 * math.atan((H * hf) / max(1e-9, 2.0 * W))
        fy_cc = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
    else:
        fy_cc = None

    uv_rows, depth_rows = [], []
    for i in range(n):
        try:
            x, y = float(pts_xy[i][0]), float(pts_xy[i][1])
            if z_is_scalar:
                z = z_scalar
            elif zlen > 0:
                z = float(z_tgt[i if i < zlen else (i % zlen)])
            else:
                z = float('nan')
        except Exception:
            uv_rows.append((float('nan'), float('nan')))
            depth_rows.append(float('inf'))
            continue
        dx, dy, dz = x - cx, y - cy, z - cam_z
        xc = dx * rx + dy * ry + dz * rz
        yc = dx * fxv + dy * fyv + dz * fzv
        zc = dx * ux + dy * uy + dz * uz
        
        
        depth_rows.append(yc if proj == 'PINHOLE' else math.sqrt(dx * dx + dy * dy + dz * dz))
        if not all(math.isfinite(v) for v in (dx, dy, dz, xc, yc, zc)):
            uv_rows.append((float('nan'), float('nan')))
            continue
        if maxd2 is not None and (dx * dx + dy * dy) > maxd2:
            uv_rows.append((float('nan'), float('nan')))
            continue
        uu = vv = float('nan')
        if proj == 'PINHOLE':
            if yc > 1e-6:
                uu = W * 0.5 + pin_fx * (xc / yc)
                vv = H * 0.5 - pin_fy * (zc / yc)
        else:
            alpha = math.atan2(xc, yc)
            rho = math.hypot(xc, yc)
            beta = math.atan2(zc, rho)
            if proj == 'EQUIRECT':
                if bool(ctx.get('is360', False)):
                    uu = (alpha + math.pi) / (2.0 * math.pi) * W
                    vv = (math.pi * 0.5 - beta) / math.pi * H
                else:
                    uu = W * 0.5 * (1.0 + alpha / (hf * 0.5))
                    vv = H * 0.5 * (1.0 - beta / (vf * 0.5))
                    eps = 1e-2
                    uu = max(eps, min(W - eps if W > 1 else eps, uu))
                    vv = max(eps, min(H - eps if H > 1 else eps, vv))
            elif proj == 'CYLINDRICAL':
                in_fov = abs(beta) <= (0.5 * vf + 1e-9)
                if bool(ctx.get('is360', False)):
                    uu = (alpha + math.pi) / (2.0 * math.pi) * W
                else:
                    in_fov = in_fov and abs(alpha) <= (0.5 * hf + 1e-9)
                    uu = W * 0.5 + fx_cc * alpha
                if in_fov:
                    vv = H * 0.5 - fy_cc * math.tan(beta)
                else:
                    uu = vv = float('nan')
        if not (math.isfinite(uu) and math.isfinite(vv)):
            uu = vv = float('nan')
        uv_rows.append((uu, vv))
    return np.asarray(uv_rows, dtype=np.float64), np.asarray(depth_rows, dtype=np.float64)

def _project_uv_depth_batch(ctx, pts_xy, z_tgt, dist_max=None):
    
    try:
        _n_hint = len(pts_xy)
    except Exception:
        _n_hint = -1
    if 0 <= _n_hint <= 16:
        return _project_uv_depth_small(ctx, pts_xy, z_tgt, dist_max=dist_max)

    pts_xy = np.asarray(pts_xy, dtype=np.float64)
    if pts_xy.ndim != 2 or pts_xy.shape[1] != 2:
        raise ValueError('pts_xy must be an array of shape (N,2)')
    n = pts_xy.shape[0]
    uvs = np.full((n, 2), np.nan, dtype=np.float64)
    depths = np.full(n, np.inf, dtype=np.float64)
    if n == 0:
        return uvs, depths

    z = np.asarray(z_tgt, dtype=np.float64)
    if z.ndim == 0:
        z = np.full(n, float(z), dtype=np.float64)
    elif z.shape[0] != n:
        z = np.resize(z, n).astype(np.float64, copy=False)

    dx = pts_xy[:, 0] - ctx['cx']
    dy = pts_xy[:, 1] - ctx['cy']
    dz = z - ctx['cam_z']

    if dist_max is not None:
        mask_dist = (dx * dx + dy * dy) <= (float(dist_max) ** 2)
    else:
        mask_dist = np.ones(n, dtype=bool)

    r = ctx['r']; u = ctx['u']; f = ctx['f']
    xc = dx * r[0] + dy * r[1] + dz * r[2]
    yc = dx * f[0] + dy * f[1] + dz * f[2]
    zc = dx * u[0] + dy * u[1] + dz * u[2]
    
    
    if str(ctx.get('proj', '') or '').upper() == 'PINHOLE':
        depths[:] = yc
    else:
        depths[:] = np.sqrt(dx * dx + dy * dy + dz * dz)

    W = max(1, int(ctx['width']))
    H = max(1, int(ctx['height']))
    proj = ctx['proj']

    if proj == 'PINHOLE':
        mask = mask_dist & (yc > 1e-6)
        if np.any(mask):
            hf = math.radians(max(1e-6, float(ctx['HFOV'])))
            vf = math.radians(max(1e-6, float(ctx['VFOV'])))
            fx = (W * 0.5) / math.tan(hf * 0.5)
            fy = (H * 0.5) / math.tan(vf * 0.5)
            u_img = W * 0.5 + fx * (xc[mask] / yc[mask])
            v_img = H * 0.5 - fy * (zc[mask] / yc[mask])
            finite = np.isfinite(u_img) & np.isfinite(v_img)
            idx = np.where(mask)[0][finite]
            uvs[idx, 0] = u_img[finite]
            uvs[idx, 1] = v_img[finite]
        return uvs, depths

    alpha = np.arctan2(xc, yc)
    rho = np.hypot(xc, yc)
    beta = np.arctan2(zc, rho)
    mask = mask_dist & np.isfinite(alpha) & np.isfinite(beta)

    if proj == 'EQUIRECT':
        if ctx['is360']:
            u = (alpha + math.pi) / (2.0 * math.pi) * W
            v = (math.pi / 2.0 - beta) / math.pi * H
            uvs[mask, 0] = u[mask]
            uvs[mask, 1] = v[mask]
        else:
            hf = math.radians(max(1e-6, float(ctx['HFOV'])))
            vf = math.radians(max(1e-6, float(ctx['VFOV'])))
            u = W * 0.5 * (1.0 + alpha / (hf * 0.5))
            v = H * 0.5 * (1.0 - beta / (vf * 0.5))
            eps = 1e-2
            u = np.clip(u, eps, W - eps if W > 1 else eps)
            v = np.clip(v, eps, H - eps if H > 1 else eps)
            uvs[mask, 0] = u[mask]
            uvs[mask, 1] = v[mask]
        return uvs, depths

    if proj == 'CYLINDRICAL':
        hf = math.radians(max(1e-6, float(ctx['HFOV'])))
        vf = math.radians(max(1e-6, float(ctx['VFOV'])))
        if not (math.isfinite(vf) and 1e-6 < vf < math.pi - 1e-3 and abs(math.tan(vf * 0.5)) > 1e-9):
            vf = 2.0 * math.atan((H * hf) / max(1e-9, 2.0 * W))
        fx_cc = W / max(1e-9, hf)
        fy_cc = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        if bool(ctx.get('is360', False)):
            u = (alpha + math.pi) / (2.0 * math.pi) * W
            finite = mask & (np.abs(beta) <= (0.5 * vf + 1e-9))
        else:
            u = W * 0.5 + fx_cc * alpha
            finite = mask & (np.abs(alpha) <= (0.5 * hf + 1e-9)) & (np.abs(beta) <= (0.5 * vf + 1e-9))
        v = H * 0.5 - fy_cc * np.tan(beta)
        finite &= np.isfinite(u) & np.isfinite(v)
        uvs[finite, 0] = u[finite]
        uvs[finite, 1] = v[finite]
        return uvs, depths

    return uvs, depths


def _unwrap_x_triplet(x0, xm, x1, wrap_width):
    W = float(wrap_width or 0.0)
    if W <= 1.0:
        return float(x0), float(xm), float(x1)
    x0 = float(x0)
    x1u = min((float(x1) - W, float(x1), float(x1) + W), key=lambda v: abs(v - x0))
    center = 0.5 * (x0 + x1u)
    xmu = min((float(xm) - W, float(xm), float(xm) + W), key=lambda v: abs(v - center))
    return x0, xmu, x1u


def _point_line_distance_px(p, a, b):
    try:
        px, py = float(p[0]), float(p[1])
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
    except Exception:
        return math.inf
    abx, aby = bx - ax, by - ay
    den = abx * abx + aby * aby
    if den <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / den
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * abx, ay + t * aby
    return math.hypot(px - qx, py - qy)

def _project_panorama_scalar_with_depth(ctx, x, y, z, dist_max=None):
    
    try:
        x = float(x); y = float(y); z = float(z)
        dx = x - float(ctx['cx']); dy = y - float(ctx['cy']); dz = z - float(ctx['cam_z'])
        if dist_max is not None and (dx * dx + dy * dy) > float(dist_max) ** 2:
            return math.nan, math.nan, math.inf
        r = ctx['r']; up = ctx['u']; f = ctx['f']
        xc = dx * float(r[0]) + dy * float(r[1]) + dz * float(r[2])
        yc = dx * float(f[0]) + dy * float(f[1]) + dz * float(f[2])
        zc = dx * float(up[0]) + dy * float(up[1]) + dz * float(up[2])
        if not all(math.isfinite(v) for v in (xc, yc, zc)):
            return math.nan, math.nan, math.inf
        radial = math.sqrt(dx * dx + dy * dy + dz * dz)
        alpha = math.atan2(xc, yc)
        rho = math.hypot(xc, yc)
        beta = math.atan2(zc, rho)
        W = max(1, int(ctx['width'])); H = max(1, int(ctx['height']))
        proj = str(ctx.get('proj', '') or '').upper()
        hf = math.radians(max(1e-6, float(ctx['HFOV'])))
        vf = math.radians(max(1e-6, float(ctx['VFOV'])))
        uu = vv = math.nan
        if proj in ('EQUIRECT', 'EQUIRECTANGULAR'):
            if bool(ctx.get('is360', False)):
                uu = (alpha + math.pi) / (2.0 * math.pi) * W
                vv = (math.pi * 0.5 - beta) / math.pi * H
            else:
                uu = W * 0.5 * (1.0 + alpha / (hf * 0.5))
                vv = H * 0.5 * (1.0 - beta / (vf * 0.5))
        elif proj == 'CYLINDRICAL':
            if not (math.isfinite(vf) and 1e-6 < vf < math.pi - 1e-3 and abs(math.tan(vf * 0.5)) > 1e-9):
                vf = 2.0 * math.atan((H * hf) / max(1e-9, 2.0 * W))
            if bool(ctx.get('is360', False)):
                uu = (alpha + math.pi) / (2.0 * math.pi) * W
            else:
                uu = W * 0.5 + (W / max(1e-9, hf)) * alpha
            fy_cc = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
            vv = H * 0.5 - fy_cc * math.tan(beta)
        if not (math.isfinite(uu) and math.isfinite(vv)):
            return math.nan, math.nan, radial
        return float(uu), float(vv), float(radial)
    except Exception:
        return math.nan, math.nan, math.inf


def _densify_projected_segment(ctx, p0, z0, p1, z1, dist_max=None, wrap_width=None,
                               max_seg_px=40.0, curve_tol_px=0.75, max_depth=6,
                               max_output_points=96):
    
    try:
        x0, y0 = float(p0[0]), float(p0[1]); x1, y1 = float(p1[0]), float(p1[1])
        z0 = float(z0); z1 = float(z1)
    except Exception:
        return []
    max_depth = max(0, min(10, int(max_depth)))
    max_output_points = max(2, min(512, int(max_output_points)))
    
    stack = [(x0, y0, z0, x1, y1, z1, 0)]
    _global_end = (x1, y1, z1)
    out = []
    nodes = 0
    max_nodes = max(32, max_output_points * 6)
    W = float(wrap_width or 0.0)
    while stack:
        ax, ay, az, bx, by, bz, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or len(out) >= max_output_points - 1:
            
            
            gx, gy, gz = _global_end
            out.append((np.asarray([gx, gy], dtype=np.float64), float(gz)))
            stack.clear()
            break
        mx = 0.5 * (ax + bx); my = 0.5 * (ay + by); mz = 0.5 * (az + bz)
        ua, va, da = _project_panorama_scalar_with_depth(ctx, ax, ay, az, dist_max=dist_max)
        um, vm, dm = _project_panorama_scalar_with_depth(ctx, mx, my, mz, dist_max=dist_max)
        ub, vb, db = _project_panorama_scalar_with_depth(ctx, bx, by, bz, dist_max=dist_max)
        finite = all(math.isfinite(v) for v in (ua, va, da, um, vm, dm, ub, vb, db))
        split = False
        if finite:
            if W > 1.0:
                ua, um, ub = _unwrap_x_triplet(ua, um, ub, W)
            seg_len = math.hypot(ub - ua, vb - va)
            dev = _point_line_distance_px((um, vm), (ua, va), (ub, vb))
            split = (seg_len > float(max_seg_px)) or (dev > float(curve_tol_px))
        else:
            
            
            split = True
        if split and depth < max_depth and (len(out) + len(stack) + 2) < max_output_points:
            nd = depth + 1
            stack.append((mx, my, mz, bx, by, bz, nd))
            stack.append((ax, ay, az, mx, my, mz, nd))
        else:
            out.append((np.asarray([bx, by], dtype=np.float64), float(bz)))
    return out


def _densify_path_for_projection(ctx, arr_xy, z_vals, dist_max=None, closed=False, render_quality='high', budget_state=None):
    arr = np.asarray(arr_xy, dtype=np.float64)
    z = np.asarray(z_vals, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] != 2 or z.ndim != 1 or z.shape[0] != arr.shape[0]:
        return arr, z
    proj = str(ctx.get('proj', '') or '').upper()
    if proj not in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
        return arr, z

    
    
    q = str(render_quality or 'high').lower()
    if q == 'low':
        max_seg_px, curve_tol_px, max_depth, path_budget = 92.0, 1.55, 4, 2400
    elif q == 'normal':
        max_seg_px, curve_tol_px, max_depth, path_budget = 70.0, 1.15, 5, 4800
    else:
        max_seg_px, curve_tol_px, max_depth, path_budget = 54.0, 0.88, 5, 7200

    
    
    
    if isinstance(budget_state, dict):
        try:
            global_remaining = max(0, int(budget_state.get('remaining', 0)))
        except Exception:
            global_remaining = 0
        if global_remaining <= 16:
            return arr, z
        path_budget = min(int(path_budget), max(32, global_remaining))

    wrap_width = float(ctx.get('width', 0.0) or 0.0) if bool(ctx.get('is360', False)) else None
    arr0 = arr.copy(); z0 = z.copy()
    if closed and arr0.shape[0] >= 2 and np.allclose(arr0[0], arr0[-1], equal_nan=True):
        arr0 = arr0[:-1]; z0 = z0[:-1]
    if arr0.shape[0] < 2:
        return arr, z

    out_pts = [arr0[0].copy()]; out_z = [float(z0[0])]
    n = arr0.shape[0]; seg_count = n if closed else (n - 1)
    budget_hit = False
    for i in range(seg_count):
        j = (i + 1) % n
        remaining = int(path_budget - len(out_pts))
        if remaining <= 1:
            budget_hit = True
        else:
            seg_cap = min(96, max(2, remaining))
            densified = _densify_projected_segment(
                ctx, arr0[i], z0[i], arr0[j], z0[j], dist_max=dist_max, wrap_width=wrap_width,
                max_seg_px=max_seg_px, curve_tol_px=curve_tol_px, max_depth=max_depth,
                max_output_points=seg_cap
            )
            for pt, zv in densified:
                if out_pts and np.allclose(out_pts[-1], pt, equal_nan=True):
                    continue
                out_pts.append(np.asarray(pt, dtype=np.float64)); out_z.append(float(zv))
                if len(out_pts) >= path_budget:
                    budget_hit = True
                    break
        if budget_hit:
            
            
            if not closed:
                source_ids = list(range(i + 1, n))
            else:
                source_ids = list(range(i + 1, n))
            if source_ids:
                stride = max(1, int(math.ceil(len(source_ids) / 512.0)))
                for k in source_ids[::stride]:
                    if not np.allclose(out_pts[-1], arr0[k], equal_nan=True):
                        out_pts.append(arr0[k].copy()); out_z.append(float(z0[k]))
                k_last = source_ids[-1]
                if not np.allclose(out_pts[-1], arr0[k_last], equal_nan=True):
                    out_pts.append(arr0[k_last].copy()); out_z.append(float(z0[k_last]))
            break
    if closed and len(out_pts) >= 2 and not np.allclose(out_pts[0], out_pts[-1], equal_nan=True):
        out_pts.append(out_pts[0].copy()); out_z.append(float(out_z[0]))
    if isinstance(budget_state, dict):
        try:
            used = max(0, len(out_pts) - int(arr0.shape[0]))
            budget_state['remaining'] = max(0, int(budget_state.get('remaining', 0)) - used)
        except Exception:
            pass
    return np.asarray(out_pts, dtype=np.float64), np.asarray(out_z, dtype=np.float64)


def _uv_runs_with_depth(uvs, depths, min_len=2):
    cur_uv = []
    cur_d = []
    n = min(len(uvs), len(depths))
    for i in range(n):
        uv = uvs[i]
        d = depths[i]
        if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])) and math.isfinite(float(d)) and float(d) > 0:
            cur_uv.append((float(uv[0]), float(uv[1])))
            cur_d.append(float(d))
        else:
            if len(cur_uv) >= min_len:
                yield np.asarray(cur_uv, dtype=np.float64), np.asarray(cur_d, dtype=np.float64)
            cur_uv = []
            cur_d = []
    if len(cur_uv) >= min_len:
        yield np.asarray(cur_uv, dtype=np.float64), np.asarray(cur_d, dtype=np.float64)


def _uv_runs_with_depth_xyz_40192(uvs, depths, xyzs, min_len=2):
    
    cur_uv=[]; cur_d=[]; cur_xyz=[]
    try:
        n=min(len(uvs),len(depths),len(xyzs))
    except Exception:
        return
    for i in range(n):
        try:
            uv=uvs[i]; d=float(depths[i]); xyz=xyzs[i]
            ok=(math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])) and math.isfinite(d) and d>0.0
                and math.isfinite(float(xyz[0])) and math.isfinite(float(xyz[1])) and math.isfinite(float(xyz[2])))
        except Exception:
            ok=False
        if ok:
            cur_uv.append((float(uv[0]),float(uv[1]))); cur_d.append(d)
            cur_xyz.append((float(xyz[0]),float(xyz[1]),float(xyz[2])))
        else:
            if len(cur_uv)>=min_len:
                yield (np.asarray(cur_uv,dtype=np.float64),np.asarray(cur_d,dtype=np.float64),np.asarray(cur_xyz,dtype=np.float64))
            cur_uv=[]; cur_d=[]; cur_xyz=[]
    if len(cur_uv)>=min_len:
        yield (np.asarray(cur_uv,dtype=np.float64),np.asarray(cur_d,dtype=np.float64),np.asarray(cur_xyz,dtype=np.float64))


def _iter_wrapped_runs_with_depth_xyz_40192(uvs, depths, xyzs, wrap_width=None, min_len=2):
    for pts,dep,xyz in _uv_runs_with_depth_xyz_40192(uvs,depths,xyzs,min_len=min_len):
        if wrap_width and float(wrap_width)>1.0:
            for shifted in _iter_wrap_shifted_pts(pts,wrap_width):
                yield shifted,dep.copy(),xyz
        else:
            yield pts,dep,xyz


def _terrain_segment_any_visible_40192(horizon, xyz0, xyz1, cam_xyz, eps_deg):
    
    if not horizon or xyz0 is None or xyz1 is None:
        return True
    try:
        cx,cy,cz=(float(cam_xyz[0]),float(cam_xyz[1]),float(cam_xyz[2]))
        a=(float(xyz0[0]),float(xyz0[1]),float(xyz0[2])); b=(float(xyz1[0]),float(xyz1[1]),float(xyz1[2]))
        m=((a[0]+b[0])*0.5,(a[1]+b[1])*0.5,(a[2]+b[2])*0.5)
        for q in (a,m,b):
            if _terrain_point_visible_fast_40192(horizon,q[0],q[1],q[2],cx,cy,cz,eps_deg):
                return True
        return False
    except Exception:
        return True


def _zbuffer_scale_for_preview(self, width, height):
    
    q = str(getattr(self, '_current_render_quality', 'high')).lower()
    lowlat = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked())
    maxdim = max(int(width), int(height))
    if q == 'low':
        return 0.40 if maxdim >= 2000 else 0.50
    if q == 'normal':
        if lowlat:
            return 0.60 if maxdim >= 2000 else 0.75
        return 0.70 if maxdim >= 2000 else 0.85
    return 1.0

def _qimage_rgba_owned_array(img):
    
    try:
        if img is None or img.isNull():
            return None
        src = img.convertToFormat(QC.QImage_Format_Format_RGBA8888)
        w, h = int(src.width()), int(src.height())
        if w <= 0 or h <= 0:
            return None
        nbytes = w * h * 4
        ptr = src.constBits() if hasattr(src, 'constBits') else src.bits()
        if hasattr(ptr, 'asstring'):
            raw = ptr.asstring(nbytes)
        else:
            
            if hasattr(ptr, 'setsize'):
                ptr.setsize(nbytes)
            raw = bytes(ptr)
        if len(raw) < nbytes:
            return None
        return np.frombuffer(raw[:nbytes], dtype=np.uint8).copy().reshape((h, w, 4))
    except Exception:
        return None

def _rgba_owned_array_to_qimage(rgba):
    
    try:
        arr = np.ascontiguousarray(rgba, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] != 4:
            return QImage()
        h, w = int(arr.shape[0]), int(arr.shape[1])
        if h <= 0 or w <= 0:
            return QImage()
        raw = arr.tobytes(order='C')
        return QImage(raw, w, h, w * 4, QC.QImage_Format_Format_RGBA8888).copy()
    except Exception:
        return QImage()

def _finite_bbox_xy_scalar(pts):
    
    try:
        n = len(pts)
    except Exception:
        return None
    minx = miny = float('inf')
    maxx = maxy = float('-inf')
    found = False
    for i in range(n):
        try:
            x = float(pts[i][0]); y = float(pts[i][1])
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        found = True
        if x < minx: minx = x
        if x > maxx: maxx = x
        if y < miny: miny = y
        if y > maxy: maxy = y
    return (minx, maxx, miny, maxy) if found else None

def _overlay_offset_pixels(self, width=None, height=None):
    try:
        mode = int(getattr(self, 'cmb_off_mode', None).currentIndex()) if getattr(self, 'cmb_off_mode', None) else 0
    except Exception:
        mode = 0
    try:
        dx = float(getattr(self, 'spin_off_h', None).value()) if getattr(self, 'spin_off_h', None) else 0.0
        dy = float(getattr(self, 'spin_off_v', None).value()) if getattr(self, 'spin_off_v', None) else 0.0
    except Exception:
        dx = 0.0
        dy = 0.0

    try:
        W = int(width if width is not None else getattr(self, '_overlay_w', 0) or getattr(self, 'spin_w', None).value())
    except Exception:
        W = int(width or 0)
    try:
        H = int(height if height is not None else getattr(self, '_overlay_h', 0) or getattr(self, 'spin_h', None).value())
    except Exception:
        H = int(height or 0)

    if mode == 1:
        
        dx *= max(1, W)
        dy *= max(1, H)
    else:
        
        
        
        
        
        
        try:
            full_w = max(1.0, float(getattr(self, 'spin_w', None).value())) if getattr(self, 'spin_w', None) else float(max(1, W))
        except Exception:
            full_w = float(max(1, W))
        try:
            full_h = max(1.0, float(getattr(self, 'spin_h', None).value())) if getattr(self, 'spin_h', None) else float(max(1, H))
        except Exception:
            full_h = float(max(1, H))
        dx *= float(max(1, W)) / full_w
        dy *= float(max(1, H)) / full_h
    return float(dx), float(dy)


def _shift_overlay_image(self, img):
    if img is None or img.isNull():
        return img
    dx, dy = _overlay_offset_pixels(self, img.width(), img.height())
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return img

    out = QImage(img.width(), img.height(), QC.QImage_Format_Format_ARGB32_Premultiplied)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    try:
        proj_txt = str(self.cmb_proj.currentText()).strip().upper() if hasattr(self, 'cmb_proj') else ''
        is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else (bool(getattr(self, 'cb_360', None) and self.cb_360.isChecked()) and proj_txt in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'))
    except Exception:
        is360 = False

    ix = int(round(dx))
    iy = int(round(dy))
    if is360:
        w = max(1, img.width())
        ix = ix % w
        p.drawImage(ix, iy, img)
        p.drawImage(ix - w, iy, img)
        p.drawImage(ix + w, iy, img)
    else:
        p.drawImage(ix, iy, img)
    p.end()
    return out


def _wrap360_enabled(self):
    try:
        proj_txt = str(getattr(self, 'cmb_proj', None).currentText()).strip().upper() if getattr(self, 'cmb_proj', None) else ''
        if proj_txt not in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
            return False
        if hasattr(self, '_is360_mode'):
            return bool(self._is360_mode())
        checked = bool(getattr(self, 'cb_360', None) and self.cb_360.isChecked())
        hfov_full = bool(getattr(self, 'd_hfov', None) and float(self.d_hfov.value()) >= 359.999)
        return bool(checked or hfov_full)
    except Exception:
        return False


def _wrap_width_from_painter(self, painter):
    if not _wrap360_enabled(self):
        return None
    try:
        dev = painter.device() if painter is not None else None
        w = int(dev.width()) if dev is not None and hasattr(dev, 'width') else 0
    except Exception:
        w = 0
    return float(w) if w > 1 else None


def _color_to_rgba_arr(col):
    c = QColor(col)
    return np.array([c.red(), c.green(), c.blue(), c.alpha()], dtype=np.uint8)


def _derive_pattern_bg(fill_candidate, edge_candidate, fallback=QColor(225,225,225,255)):
    base = QColor(fill_candidate) if fill_candidate is not None else QColor(fallback)
    edge = QColor(edge_candidate) if edge_candidate is not None else QColor(base)
    if (base.alpha() <= 5) or ((base.red() + base.green() + base.blue()) <= 24):
        base = QColor(edge)
        base = base.lighter(175)
        base.setAlpha(255)
    return base


def _dominant_fill_color_from_spec(spec, fallback=QColor(180,180,180,255)):
    spec = dict(spec or {})
    kind = str(spec.get('kind', 'simple') or 'simple').lower()
    if kind == 'simple':
        c = QColor(spec.get('color', fallback))
        if c.alpha() <= 5:
            c = QColor(fallback)
        c.setAlpha(255)
        return c
    if kind == 'gradient':
        c1 = QColor(spec.get('color1', fallback))
        c2 = QColor(spec.get('color2', c1))
        c = QColor(int(round((c1.red()+c2.red())/2.0)), int(round((c1.green()+c2.green())/2.0)), int(round((c1.blue()+c2.blue())/2.0)), 255)
        return c
    if kind == 'line_pattern':
        bg = _derive_pattern_bg(spec.get('bg_color', fallback), spec.get('line_color', fallback), fallback=fallback)
        line = QColor(spec.get('line_color', bg))
        c = QColor(int(round(0.78*bg.red()+0.22*line.red())), int(round(0.78*bg.green()+0.22*line.green())), int(round(0.78*bg.blue()+0.22*line.blue())), 255)
        return c
    if kind == 'point_pattern':
        bg = _derive_pattern_bg(spec.get('bg_color', fallback), spec.get('point_color', fallback), fallback=fallback)
        pt = QColor(spec.get('point_color', bg))
        c = QColor(int(round(0.80*bg.red()+0.20*pt.red())), int(round(0.80*bg.green()+0.20*pt.green())), int(round(0.80*bg.blue()+0.20*pt.blue())), 255)
        return c
    c = QColor(fallback)
    c.setAlpha(255)
    return c


def _background_fill_color_from_spec(spec, fallback=QColor(225,225,225,255)):
    spec = dict(spec or {})
    kind = str(spec.get('kind', 'simple') or 'simple').lower()
    if kind == 'line_pattern':
        return _derive_pattern_bg(spec.get('bg_color', fallback), spec.get('line_color', fallback), fallback=fallback)
    if kind == 'point_pattern':
        return _derive_pattern_bg(spec.get('bg_color', fallback), spec.get('point_color', fallback), fallback=fallback)
    if kind == 'gradient':
        c1 = QColor(spec.get('color1', fallback))
        if c1.alpha() > 5:
            c1.setAlpha(255)
            return c1
    if kind == 'simple':
        c = QColor(spec.get('color', fallback))
        if c.alpha() > 5:
            c.setAlpha(255)
            return c
    c = QColor(fallback)
    c.setAlpha(255)
    return c



def _alpha_from_fill_spec(spec, fallback_alpha=255):
    try:
        spec = dict(spec or {})
        kind = str(spec.get('kind', 'simple') or 'simple').lower()
        if kind == 'simple' and 'color' in spec:
            return QColor(spec.get('color')).alpha()
        if kind == 'gradient':
            vals = [QColor(spec.get(k)).alpha() for k in ('color1', 'color2') if k in spec]
            if vals:
                return int(round(sum(vals) / float(len(vals))))
        if kind == 'line_pattern':
            vals = [QColor(spec.get(k)).alpha() for k in ('bg_color', 'line_color') if k in spec]
            if vals:
                return int(round(sum(vals) / float(len(vals))))
        if kind == 'point_pattern':
            vals = [QColor(spec.get(k)).alpha() for k in ('bg_color', 'point_color') if k in spec]
            if vals:
                return int(round(sum(vals) / float(len(vals))))
    except Exception:
        pass
    return max(0, min(255, int(fallback_alpha)))

def _wall_color_from_fill_spec(fill_spec, fallback=QColor(190,190,190,255), transparent_objects=False):
    fallback_alpha = QColor(fallback).alpha() if fallback is not None else 255
    base_alpha = _alpha_from_fill_spec(fill_spec, fallback_alpha=fallback_alpha)
    base = _dominant_fill_color_from_spec(fill_spec, fallback=fallback)
    lum = int(base.red()) + int(base.green()) + int(base.blue())
    if lum < 54:
        base = base.lighter(180)
    else:
        base = QColor(max(0, min(255, int(base.red() * 0.94))),
                      max(0, min(255, int(base.green() * 0.94))),
                      max(0, min(255, int(base.blue() * 0.94))),
                      255)
    if (int(base.red()) + int(base.green()) + int(base.blue())) < 42:
        base = QColor(fallback).lighter(125)
    base.setAlpha(base_alpha if (not transparent_objects) else min(110, max(55, base_alpha)))
    return base


def _texture_rgba_from_fill_spec(fill_spec):
    tex = (fill_spec or {}).get('texture_img', None)
    if tex is None:
        return None
    arr = fill_spec.get('_texture_rgba', None)
    if arr is not None:
        return arr
    try:
        arr = _qimage_rgba_owned_array(tex)
        if arr is None:
            return None
        fill_spec['_texture_rgba'] = arr
        fill_spec['_texture_size'] = (int(arr.shape[1]), int(arr.shape[0]))
        return arr
    except Exception:
        return None

def _fill_spec_signature(spec):
    if not spec:
        return None
    out = []
    for k in sorted(spec.keys()):
        if str(k).startswith('_'):
            continue
        v = spec[k]
        if isinstance(v, QColor):
            out.append((k, v.rgba()))
        elif isinstance(v, QImage):
            try:
                out.append((k, ('QImage', int(v.cacheKey()), int(v.width()), int(v.height()))))
            except Exception:
                out.append((k, ('QImage', id(v))))
        elif isinstance(v, np.ndarray):
            out.append((k, ('ndarray', tuple(v.shape), str(v.dtype))))
        else:
            out.append((k, v))
    return tuple(out)


def _normalized_fill_spec_for_sty(sty, transparent_objects=False):
    spec = None
    if bool(getattr(sty, '_force_simple_fill', False)):
        base = getattr(sty, 'qgis_fill_style', None) if bool(getattr(sty, 'use_qgis_style', True)) else None
        spec = {'kind': 'simple', 'color': _dominant_fill_color_from_spec(base, fallback=QColor(getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255)))))}
    elif bool(getattr(sty, 'use_qgis_style', True)):
        spec = getattr(sty, 'qgis_fill_style', None)
    if not spec:
        spec = {'kind': 'simple', 'color': QColor(getattr(sty, 'fill_color', getattr(sty, 'color', QColor(0,255,0,180))))}
    spec = dict(spec)
    kind = str(spec.get('kind', 'simple')).lower()
    sty_opacity = _style_opacity_factor(sty)

    def _alpha_adj(c):
        cc = _color_with_opacity(c, sty_opacity)
        if transparent_objects:
            cc.setAlpha(min(max(40, cc.alpha()), 110))
        return cc

    target_alpha = 110 if transparent_objects else 255
    if kind == 'simple':
        spec['color'] = _alpha_adj(spec.get('color', getattr(sty, 'fill_color', getattr(sty, 'color', QColor(0,255,0,180)))))
    elif kind == 'line_pattern':
        bg = _derive_pattern_bg(spec.get('bg_color', getattr(sty, 'fill_color', None)), spec.get('line_color', getattr(sty, 'color', None)))
        spec['bg_color'] = _alpha_adj(bg)
        spec['line_color'] = _alpha_adj(spec.get('line_color', getattr(sty, 'color', QColor(0,255,0,255))))
    elif kind == 'point_pattern':
        bg = _derive_pattern_bg(spec.get('bg_color', getattr(sty, 'fill_color', None)), spec.get('point_color', getattr(sty, 'color', None)))
        spec['bg_color'] = _alpha_adj(bg)
        spec['point_color'] = _alpha_adj(spec.get('point_color', getattr(sty, 'color', QColor(0,255,0,255))))
    elif kind == 'gradient':
        spec['color1'] = _alpha_adj(spec.get('color1', getattr(sty, 'fill_color', QColor(0,255,0,255))))
        spec['color2'] = _alpha_adj(spec.get('color2', getattr(sty, 'color', QColor(0,180,0,255))))
    spec['outline_color'] = QColor(spec.get('outline_color', getattr(sty, 'color', QColor(0,255,0,255))))
    spec['outline_width'] = float(spec.get('outline_width', getattr(sty, 'width', 1.0) or 1.0))
    spec['pen_style'] = spec.get('pen_style', getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine))
    spec['target_alpha'] = int(target_alpha)
    return spec


def _feature_local_style(self, base_sty, feat):
    if base_sty is None or feat is None or (not bool(getattr(base_sty, 'use_qgis_style', True))):
        return base_sty
    try:
        cache = getattr(self, '_feature_style_cache', None)
        if cache is None:
            cache = {}
            self._feature_style_cache = cache
        layer = getattr(base_sty, 'layer', None)
        layer_id = layer.id() if layer is not None else ''
        key = (layer_id, int(feat.id()))
        cached = cache.get(key)
        if cached is not None:
            return cached
        from ._utils_ops import _extract_qgis_layer_style, _temporarily_extract_style_from_named_qgis_style
        theme_style_name = str(getattr(base_sty, 'qgis_theme_style_name', '') or '')
        if theme_style_name:
            qsty = _temporarily_extract_style_from_named_qgis_style(self, layer, theme_style_name, base_sty, feat=feat)
        else:
            qsty = _extract_qgis_layer_style(self, layer, feat=feat, fallback=base_sty)
        eff = copy.copy(base_sty)
        eff.color = qsty.get('color', getattr(base_sty, 'color', QColor(0,255,0,255)))
        eff.fill_color = qsty.get('fill_color', getattr(base_sty, 'fill_color', eff.color))
        eff.width = qsty.get('width', getattr(base_sty, 'width', 1.0))
        eff.pen_style = qsty.get('pen_style', getattr(base_sty, 'pen_style', QC.Qt_PenStyle_SolidLine))
        eff.opacity = qsty.get('opacity', getattr(base_sty, 'opacity', 1.0))
        eff.fill_polygons = bool(qsty.get('fill_polygons', getattr(base_sty, 'fill_polygons', True)))
        eff.qgis_fill_style = qsty.get('fill_style', getattr(base_sty, 'qgis_fill_style', None))
        cache[key] = eff
        return eff
    except Exception:
        return base_sty


def _qt_brush_from_fill_spec(fill_spec, poly=None):
    spec = fill_spec or {}
    kind = str(spec.get('kind', 'simple') or 'simple').lower()
    tex = spec.get('texture_img', None)
    try:
        if tex is not None and (not tex.isNull()):
            brush = QBrush(QPixmap.fromImage(tex))
            try:
                if poly is not None and not poly.isEmpty():
                    rect = poly.boundingRect()
                    brush.setTransform(QTransform().translate(float(rect.left()), float(rect.top())))
            except Exception:
                pass
            return brush
    except Exception:
        pass
    if kind == 'simple':
        return QBrush(QColor(spec.get('color', QColor(0,255,0,255))))
    if kind == 'gradient':
        try:
            from qgis.PyQt.QtGui import QLinearGradient, QRadialGradient
            rect = poly.boundingRect() if poly is not None else QRect(0,0,64,64)
            if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
                rect = QRect(0,0,64,64)
            gtype = str(spec.get('gradient_type', 'linear') or 'linear').lower()
            c1 = QColor(spec.get('color1', QColor(0,255,0,255)))
            c2 = QColor(spec.get('color2', QColor(0,180,0,255)))
            if 'radial' in gtype:
                grad = QRadialGradient(rect.center(), 0.5 * max(rect.width(), rect.height()))
                grad.setColorAt(0.0, c1)
                grad.setColorAt(1.0, c2)
                return QBrush(grad)
            ang = math.radians(float(spec.get('angle_deg', 0.0) or 0.0))
            cx = rect.center().x(); cy = rect.center().y()
            dx = math.cos(ang) * rect.width() * 0.5
            dy = math.sin(ang) * rect.height() * 0.5
            grad = QLinearGradient(cx - dx, cy + dy, cx + dx, cy - dy)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            return QBrush(grad)
        except Exception:
            return None
    return None


def _sample_rgba_from_fill_spec(fill_spec, xs, ys, bbox, texture_uv=None):
    
    kind = str((fill_spec or {}).get('kind', 'simple')).lower()
    h, w = xs.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    def _apply_target_alpha(arr):
        ta = int((fill_spec or {}).get('target_alpha', 255) or 255)
        if ta < 255:
            arr[:, :, 3] = np.clip(np.round(arr[:, :, 3].astype(np.float32) * (float(ta) / 255.0)), 0, 255).astype(np.uint8)
        return arr

    def _fill_const(col):
        arr = _color_to_rgba_arr(col)
        rgba[:, :, 0] = arr[0]
        rgba[:, :, 1] = arr[1]
        rgba[:, :, 2] = arr[2]
        rgba[:, :, 3] = arr[3]
        return _apply_target_alpha(rgba)

    tex_rgba = _texture_rgba_from_fill_spec(fill_spec or {})
    if tex_rgba is not None and getattr(tex_rgba, 'size', 0):
        th, tw = tex_rgba.shape[:2]
        bx0, bx1, by0, by1 = bbox
        mode = str((fill_spec or {}).get('texture_mode', 'tile') or 'tile').lower()
        mapped_uv = None
        if texture_uv is not None:
            try:
                uu, vv = texture_uv
                uu = np.asarray(uu, dtype=np.float64)
                vv = np.asarray(vv, dtype=np.float64)
                if uu.shape == xs.shape and vv.shape == ys.shape:
                    mapped_uv = (uu, vv)
            except Exception:
                mapped_uv = None
        if mapped_uv is not None:
            uu, vv = mapped_uv
            tx = np.clip(np.round(uu * max(0, tw - 1)), 0, max(0, tw - 1)).astype(np.int64)
            ty = np.clip(np.round(vv * max(0, th - 1)), 0, max(0, th - 1)).astype(np.int64)
        elif mode == 'stretch':
            sx = max(1.0, float(bx1 - bx0))
            sy = max(1.0, float(by1 - by0))
            tx = np.clip(np.round(((xs - bx0) / sx) * max(0, tw - 1)), 0, max(0, tw - 1)).astype(np.int64)
            ty = np.clip(np.round(((ys - by0) / sy) * max(0, th - 1)), 0, max(0, th - 1)).astype(np.int64)
        else:
            tx = np.mod(np.floor(xs - float(bx0)).astype(np.int64), tw)
            ty = np.mod(np.floor(ys - float(by0)).astype(np.int64), th)
        sampled = tex_rgba[ty, tx].copy()
        
        
        
        preserve_alpha = bool((fill_spec or {}).get('preserve_texture_alpha', False))
        if (not preserve_alpha) and sampled[:, :, 3].min() < 250:
            bg = _color_to_rgba_arr(_background_fill_color_from_spec(fill_spec or {}, fallback=QColor(225,225,225,255))).astype(np.float32)
            alpha = sampled[:, :, 3:4].astype(np.float32) / 255.0
            sampled[:, :, :3] = np.clip(np.round(sampled[:, :, :3].astype(np.float32) * alpha + bg[None, None, :3] * (1.0 - alpha)), 0, 255).astype(np.uint8)
            sampled[:, :, 3] = 255
        rgba[:] = sampled
        return _apply_target_alpha(rgba)

    if kind == 'simple':
        return _fill_const(fill_spec.get('color', QColor(0,255,0,255)))

    if kind == 'line_pattern':
        bg = _color_to_rgba_arr(fill_spec.get('bg_color', QColor(0,0,0,0)))
        fg = _color_to_rgba_arr(fill_spec.get('line_color', QColor(0,255,0,255)))
        spacing = max(2.0, float(fill_spec.get('spacing_px', 8.0) or 8.0))
        lw = max(1.0, float(fill_spec.get('line_width_px', 2.0) or 2.0))
        ang = math.radians(float(fill_spec.get('angle_deg', 45.0) or 45.0))
        u = xs * math.cos(ang) + ys * math.sin(ang)
        dist = np.abs(((u + 0.5 * spacing) % spacing) - 0.5 * spacing)
        on = dist <= (0.5 * lw)
        rgba[:] = bg
        rgba[on] = fg
        return _apply_target_alpha(rgba)

    if kind == 'point_pattern':
        bg = _color_to_rgba_arr(fill_spec.get('bg_color', QColor(0,0,0,0)))
        fg = _color_to_rgba_arr(fill_spec.get('point_color', QColor(0,255,0,255)))
        sx = max(3.0, float(fill_spec.get('spacing_x_px', 10.0) or 10.0))
        sy = max(3.0, float(fill_spec.get('spacing_y_px', sx) or sx))
        ps = max(1.0, float(fill_spec.get('point_size_px', 3.0) or 3.0))
        shape = str(fill_spec.get('shape', 'circle')).lower()
        bx0, bx1, by0, by1 = bbox
        gx = ((xs - bx0) % sx) - 0.5 * sx
        gy = ((ys - by0) % sy) - 0.5 * sy
        if 'square' in shape:
            on = (np.abs(gx) <= 0.5 * ps) & (np.abs(gy) <= 0.5 * ps)
        elif 'diamond' in shape:
            on = (np.abs(gx) + np.abs(gy)) <= ps
        else:
            on = (gx * gx + gy * gy) <= (0.5 * ps) ** 2
        rgba[:] = bg
        rgba[on] = fg
        return _apply_target_alpha(rgba)

    if kind == 'gradient':
        c1 = _color_to_rgba_arr(fill_spec.get('color1', QColor(0,255,0,255))).astype(np.float64)
        c2 = _color_to_rgba_arr(fill_spec.get('color2', QColor(0,180,0,255))).astype(np.float64)
        bx0, bx1, by0, by1 = bbox
        cx = 0.5 * (bx0 + bx1)
        cy = 0.5 * (by0 + by1)
        rx = max(1.0, 0.5 * (bx1 - bx0))
        ry = max(1.0, 0.5 * (by1 - by0))
        gtype = str(fill_spec.get('gradient_type', 'linear') or 'linear').lower()
        if 'radial' in gtype:
            dx = (xs - cx) / rx
            dy = (ys - cy) / ry
            t = np.clip(np.sqrt(dx * dx + dy * dy), 0.0, 1.0)[..., None]
        else:
            ang = math.radians(float(fill_spec.get('angle_deg', 0.0) or 0.0))
            denom = abs(rx * math.cos(ang)) + abs(ry * math.sin(ang))
            denom = max(1.0, float(denom))
            t = 0.5 + (((xs - cx) * math.cos(ang) + (ys - cy) * math.sin(ang)) / (2.0 * denom))
            t = np.clip(t, 0.0, 1.0)[..., None]
        arr = np.round(c1[None, None, :] * (1.0 - t) + c2[None, None, :] * t).astype(np.uint8)
        rgba[:] = arr
        return _apply_target_alpha(rgba)

    return _fill_const(fill_spec.get('color', QColor(0,255,0,255)))


def _signed_area_2d(pts):
    
    try:
        n = len(pts)
    except Exception:
        return 0.0
    if n < 3:
        return 0.0
    area2 = 0.0
    try:
        for i in range(n):
            a = pts[i]; b = pts[(i + 1) % n]
            ax, ay = float(a[0]), float(a[1])
            bx, by = float(b[0]), float(b[1])
            if not all(math.isfinite(v) for v in (ax, ay, bx, by)):
                return 0.0
            area2 += ax * by - bx * ay
    except Exception:
        return 0.0
    return 0.5 * area2

def _point_in_triangle_2d(p, a, b, c, eps=1e-9):
    
    try:
        px, py = float(p[0]), float(p[1])
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        cx, cy = float(c[0]), float(c[1])
    except Exception:
        return False
    den = ((cx - ax) * (by - ay) - (bx - ax) * (cy - ay))
    if (not math.isfinite(den)) or abs(den) < eps:
        return False
    u = ((px - ax) * (by - ay) - (bx - ax) * (py - ay)) / den
    v = ((cx - ax) * (py - ay) - (px - ax) * (cy - ay)) / den
    w = 1.0 - u - v
    return (u >= -eps) and (v >= -eps) and (w >= -eps)

def _clean_polygon_ring_pts_dep(pts, dep, eps=1e-6):
    
    keep_pts = []
    keep_dep = []
    try:
        n = min(len(pts), len(dep))
    except Exception:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    for i in range(n):
        try:
            x, y = float(pts[i][0]), float(pts[i][1]); d = float(dep[i])
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(d)):
            continue
        if keep_pts and math.hypot(x - keep_pts[-1][0], y - keep_pts[-1][1]) <= eps:
            continue
        keep_pts.append((x, y)); keep_dep.append(d)
    if len(keep_pts) >= 2 and math.hypot(keep_pts[0][0] - keep_pts[-1][0], keep_pts[0][1] - keep_pts[-1][1]) <= eps:
        keep_pts.pop(); keep_dep.pop()
    if not keep_pts:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    return np.asarray(keep_pts, dtype=np.float64), np.asarray(keep_dep, dtype=np.float64)

def _earclip_triangulation_indices(pts, eps=1e-9):
    
    try:
        seq = [(float(p[0]), float(p[1])) for p in pts]
    except Exception:
        return []
    n = len(seq)
    if n < 3: return []
    if n == 3: return [(0, 1, 2)]
    ccw = _signed_area_2d(seq) >= 0.0
    idxs = list(range(n)); tris = []; guard = 0; guard_max = max(32, n*n)
    def _cross(a,b,c):
        return ((b[0]-a[0])*(c[1]-a[1])) - ((b[1]-a[1])*(c[0]-a[0]))
    while len(idxs) > 3 and guard < guard_max:
        ear_found = False; m = len(idxs)
        for j in range(m):
            i0=idxs[(j-1)%m]; i1=idxs[j]; i2=idxs[(j+1)%m]
            a,b,c=seq[i0],seq[i1],seq[i2]; cr=_cross(a,b,c)
            if not math.isfinite(cr): continue
            if (ccw and cr <= eps) or ((not ccw) and cr >= -eps): continue
            if any(_point_in_triangle_2d(seq[ik],a,b,c,eps=eps) for ik in idxs if ik not in (i0,i1,i2)):
                continue
            tris.append((i0,i1,i2)); del idxs[j]; ear_found=True; break
        if not ear_found:
            base=idxs[0]
            tris.extend((base,idxs[j],idxs[j+1]) for j in range(1,len(idxs)-1))
            return tris
        guard += 1
    if len(idxs)==3: tris.append((idxs[0],idxs[1],idxs[2]))
    return tris

def _triangulate_polygon_run(pts, dep, fill_spec, pattern_bbox=None):
    
    pts, dep = _clean_polygon_ring_pts_dep(pts, dep)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2 or dep.ndim != 1:
        return []
    tris=[]
    for i0,i1,i2 in _earclip_triangulation_indices(pts):
        try:
            tri=((float(pts[i0,0]),float(pts[i0,1])),
                 (float(pts[i1,0]),float(pts[i1,1])),
                 (float(pts[i2,0]),float(pts[i2,1])))
            dtri=(float(dep[i0]),float(dep[i1]),float(dep[i2]))
        except Exception:
            continue
        vals=(tri[0][0],tri[0][1],tri[1][0],tri[1][1],tri[2][0],tri[2][1],dtri[0],dtri[1],dtri[2])
        if all(math.isfinite(v) for v in vals):
            tris.append({'uv':tri,'depths':dtri,'fill_spec':dict(fill_spec),'pattern_bbox':pattern_bbox})
    return tris

def _clip_triangle_to_viewport(pts, dep, sw, sh):
    
    try:
        poly = [(float(pts[k][0]), float(pts[k][1]), float(dep[k])) for k in range(3)]
    except Exception:
        return []
    if not all(all(math.isfinite(v) for v in p) for p in poly):
        return []
    xmin, ymin, xmax, ymax = -0.5, -0.5, float(sw) - 0.5, float(sh) - 0.5
    def clip(pin, axis, bound, greater):
        if not pin:
            return []
        out, prev = [], pin[-1]
        pv = prev[axis]; pi = (pv >= bound) if greater else (pv <= bound)
        for cur in pin:
            cv = cur[axis]; ci = (cv >= bound) if greater else (cv <= bound)
            if ci != pi:
                den = cv - pv
                if math.isfinite(den) and abs(den) > 1e-15:
                    t = max(0.0, min(1.0, (bound - pv) / den))
                    q = (prev[0] + (cur[0] - prev[0]) * t,
                         prev[1] + (cur[1] - prev[1]) * t,
                         prev[2] + (cur[2] - prev[2]) * t)
                    if all(math.isfinite(v) for v in q):
                        out.append(q)
            if ci:
                out.append(cur)
            prev, pv, pi = cur, cv, ci
        return out
    poly = clip(poly, 0, xmin, True)
    poly = clip(poly, 0, xmax, False)
    poly = clip(poly, 1, ymin, True)
    poly = clip(poly, 1, ymax, False)
    if len(poly) < 3:
        return []
    out, b = [], poly[0]
    for k in range(1, len(poly) - 1):
        out.append((((b[0], b[1]), (poly[k][0], poly[k][1]), (poly[k+1][0], poly[k+1][1])),
                    (b[2], poly[k][2], poly[k+1][2])))
    return out

def _merge_panorama_stroke_tile(rgba, depth, cols, xx, yy, p0, p1, d0, d1):
    
    vx = float(p1[0]) - float(p0[0]); vy = float(p1[1]) - float(p0[1])
    length2 = vx*vx + vy*vy
    t = np.clip(((xx-p0[0])*vx + (yy-p0[1])*vy) / max(length2, 1e-20), 0.0, 1.0)
    z = (float(d0) + t*(float(d1)-float(d0))).astype(np.float32)
    visible = (cols[:, :, 3] > 0) & np.isfinite(z) & (z > 0.0) & (z <= depth)
    depth[visible] = z[visible]
    rgba[visible] = cols[visible]


def _compose_panorama_strokes_zbuffer(rgba, depth, scale, edges):
    
    sh, sw = depth.shape
    sc = max(1e-9, float(scale))
    tile = 160
    for pen, uvs, depths, _xyz, wrap in edges:
        if pen.color().alpha() <= 0:
            continue
        radius = max(1.0, float(pen.widthF())*sc)*0.5 + 2.0
        for pts, dep in _iter_wrapped_runs_with_depth(
                uvs, depths, wrap_width=(float(wrap) if wrap else None), min_len=2):
            for i in range(min(len(pts), len(dep))-1):
                p0 = (float(pts[i][0])*sc, float(pts[i][1])*sc)
                p1 = (float(pts[i+1][0])*sc, float(pts[i+1][1])*sc)
                d0, d1 = float(dep[i]), float(dep[i+1])
                if not all(math.isfinite(v) for v in (*p0, *p1, d0, d1)) or min(d0,d1) <= 0:
                    continue
                
                
                clipped = _clip_zsegment_to_viewport(
                    p0[0]+radius, p0[1]+radius, d0,
                    p1[0]+radius, p1[1]+radius, d1,
                    sw+2*radius, sh+2*radius)
                if clipped is None:
                    continue
                ax,ay,_,bx,by,_ = clipped
                ax-=radius; ay-=radius; bx-=radius; by-=radius
                xmin=max(0,int(math.floor(min(ax,bx)-radius)))
                xmax=min(sw-1,int(math.ceil(max(ax,bx)+radius)))
                ymin=max(0,int(math.floor(min(ay,by)-radius)))
                ymax=min(sh-1,int(math.ceil(max(ay,by)+radius)))
                for y0 in range(ymin,ymax+1,tile):
                    y1=min(ymax+1,y0+tile)
                    for x0 in range(xmin,xmax+1,tile):
                        x1=min(xmax+1,x0+tile)
                        img=QImage(x1-x0,y1-y0,QC.QImage_Format_Format_ARGB32_Premultiplied)
                        if img.isNull():
                            raise MemoryError("Cannot allocate panorama stroke tile")
                        img.fill(QColor(0,0,0,0))
                        painter=QPainter(img)
                        try:
                            painter.setRenderHint(QC.QPainter_RenderHint_Antialiasing,True)
                            painter.translate(-float(x0),-float(y0))
                            painter.scale(sc,sc)
                            painter.setPen(pen)
                            painter.drawLine(QPointF(p0[0]/sc,p0[1]/sc),QPointF(p1[0]/sc,p1[1]/sc))
                        finally:
                            painter.end()
                        cols=_qimage_rgba_owned_array(img)
                        if cols is None:
                            raise RuntimeError("Cannot read panorama stroke coverage")
                        xx=np.arange(x0,x1,dtype=np.float64)[None,:]+0.5
                        yy=np.arange(y0,y1,dtype=np.float64)[:,None]+0.5
                        _merge_panorama_stroke_tile(
                            rgba[y0:y1,x0:x1],depth[y0:y1,x0:x1],cols,xx,yy,p0,p1,d0,d1)


def _compose_panorama_faces_zbuffer_40191(width, height, faces, scale=1.0, owner=None):
    
    sw = max(1, int(round(float(width) * float(scale))))
    sh = max(1, int(round(float(height) * float(scale))))
    depth = np.full((sh, sw), np.inf, dtype=np.float32)
    rgba = np.zeros((sh, sw, 4), dtype=np.uint8)
    sc = float(scale)
    tile_px = 160

    
    
    A = np.empty((tile_px, tile_px), dtype=np.float32)
    B = np.empty((tile_px, tile_px), dtype=np.float32)
    C = np.empty((tile_px, tile_px), dtype=np.float32)
    Z = np.empty((tile_px, tile_px), dtype=np.float32)
    T = np.empty((tile_px, tile_px), dtype=np.float32)
    
    
    U = np.empty((tile_px, tile_px), dtype=np.float32)
    V = np.empty((tile_px, tile_px), dtype=np.float32)
    N = np.empty((tile_px, tile_px), dtype=np.float32)
    M = np.empty((tile_px, tile_px), dtype=np.bool_)
    M2 = np.empty((tile_px, tile_px), dtype=np.bool_)
    M3 = np.empty((tile_px, tile_px), dtype=np.bool_)
    xbase = np.arange(tile_px, dtype=np.float32) + np.float32(0.5)
    ybase = np.arange(tile_px, dtype=np.float32) + np.float32(0.5)

    
    
    try:
        work_faces = sorted(
            list(faces or ()),
            key=lambda f: min(float(v) for v in (f.get('depths', ()) or (math.inf,)))
        )
    except Exception:
        work_faces = list(faces or ())

    processed = 0
    skipped_depth_tiles = 0
    aborted_memory = False
    _MIB_local = 1024 * 1024

    for face_index, face in enumerate(work_faces):
        
        
        
        if owner is not None and face_index and (face_index % 512) == 0:
            try:
                snap = memory_snapshot()
                if snap.total_bytes > 0 and snap.available_bytes > 0:
                    reserve = max(512 * _MIB_local, int(0.055 * snap.total_bytes))
                    if int(snap.available_bytes) < int(reserve):
                        aborted_memory = True
                        break
            except Exception:
                pass

        try:
            pts0 = face.get('uv', ())
            dep0 = face.get('depths', ())
            pts_s = tuple((float(pts0[k][0]) * sc, float(pts0[k][1]) * sc) for k in range(3))
            dep = tuple(float(dep0[k]) for k in range(3))
            tuv0 = face.get('texture_uv', None)
            texture_uv_vertices = (tuple((float(tuv0[k][0]), float(tuv0[k][1])) for k in range(3))
                                   if tuv0 is not None else None)
        except Exception:
            continue
        vals = tuple(v for pp in pts_s for v in pp) + dep
        if texture_uv_vertices is not None:
            vals += tuple(v for pp in texture_uv_vertices for v in pp)
        if not all(math.isfinite(v) for v in vals):
            continue

        fill_spec = face.get('fill_spec') or {'kind':'simple','color':QColor(255,0,255,255)}
        pb0 = face.get('pattern_bbox', None)
        if pb0 is not None:
            try: pb_scaled = tuple(float(v) * sc for v in pb0)
            except Exception: pb_scaled = None
        else:
            pb_scaled = None

        tex_denom = None
        tex_coeff = None
        if texture_uv_vertices is not None:
            try:
                (tx0s, ty0s), (tx1s, ty1s), (tx2s, ty2s) = pts_s
                td = ((ty1s-ty2s)*(tx0s-tx2s)+(tx2s-tx1s)*(ty0s-ty2s))
                if math.isfinite(td) and abs(td) >= 1e-12:
                    tex_denom = float(td)
                    tinv = np.float32(1.0 / tex_denom)
                    tax = np.float32(ty1s - ty2s) * tinv
                    tay = np.float32(tx2s - tx1s) * tinv
                    tac = np.float32((-(ty1s-ty2s)*tx2s-(tx2s-tx1s)*ty2s) / tex_denom)
                    tbx = np.float32(ty2s - ty0s) * tinv
                    tby = np.float32(tx0s - tx2s) * tinv
                    tbc = np.float32((-(ty2s-ty0s)*tx2s-(tx0s-tx2s)*ty2s) / tex_denom)
                    tex_coeff = (tax, tay, tac, tbx, tby, tbc)
                else:
                    texture_uv_vertices = None
            except Exception:
                texture_uv_vertices = None
                tex_denom = None
                tex_coeff = None

        
        kind = str(fill_spec.get('kind', 'simple') or 'simple').lower()
        fast_simple = (kind == 'simple' and fill_spec.get('texture_img', None) is None)
        simple_rgba = None
        if fast_simple:
            try:
                simple_rgba = _color_to_rgba_arr(fill_spec.get('color', QColor(0,255,0,255))).copy()
                ta = int(fill_spec.get('target_alpha', 255))
                if ta < 255:
                    simple_rgba[3] = np.uint8(max(0, min(255, int(round(float(simple_rgba[3]) * (float(ta)/255.0))))))
            except Exception:
                simple_rgba = np.array((0,255,0,255), dtype=np.uint8)

        ath = max(0,min(255,int(fill_spec.get('depth_alpha_threshold',1) or 1)))
        intrinsic = bool(fill_spec.get('depth_uses_intrinsic_alpha',False))
        target = max(0,min(255,int(fill_spec.get('target_alpha',255) or 0)))

        for tri_s, dep_s in _clip_triangle_to_viewport(pts_s, dep, sw, sh):
            xs3=(tri_s[0][0],tri_s[1][0],tri_s[2][0]); ys3=(tri_s[0][1],tri_s[1][1],tri_s[2][1])
            minx=max(0,int(math.floor(min(xs3)))); maxx=min(sw-1,int(math.ceil(max(xs3))))
            miny=max(0,int(math.floor(min(ys3)))); maxy=min(sh-1,int(math.ceil(max(ys3))))
            if maxx < minx or maxy < miny: continue
            (x0,y0),(x1,y1),(x2,y2)=tri_s
            denom=((y1-y2)*(x0-x2)+(x2-x1)*(y0-y2))
            if (not math.isfinite(denom)) or abs(denom)<1e-9: continue
            invd=np.float32(1.0/denom)
            a_x=np.float32(y1-y2)*invd; a_y=np.float32(x2-x1)*invd
            a_c=np.float32((-(y1-y2)*x2-(x2-x1)*y2)/denom)
            b_x=np.float32(y2-y0)*invd; b_y=np.float32(x0-x2)*invd
            b_c=np.float32((-(y2-y0)*x2-(x0-x2)*y2)/denom)
            pb=pb_scaled if pb_scaled is not None else (minx,maxx,miny,maxy)
            min_face_depth=np.float32(min(float(dep_s[0]),float(dep_s[1]),float(dep_s[2])))

            for ty0 in range(miny,maxy+1,tile_px):
                ty1=min(maxy,ty0+tile_px-1); ny=ty1-ty0+1
                ys=(ybase[:ny] + np.float32(ty0))
                for tx0 in range(minx,maxx+1,tile_px):
                    tx1=min(maxx,tx0+tile_px-1); nx=tx1-tx0+1
                    sl_depth=depth[ty0:ty1+1,tx0:tx1+1]
                    
                    
                    try:
                        if np.all(sl_depth < min_face_depth):
                            skipped_depth_tiles += 1
                            continue
                    except Exception:
                        pass

                    xs=(xbase[:nx] + np.float32(tx0))
                    XX=np.broadcast_to(xs[None,:],(ny,nx))
                    YY=np.broadcast_to(ys[:,None],(ny,nx))
                    av=A[:ny,:nx]; bv=B[:ny,:nx]; cv=C[:ny,:nx]; zv=Z[:ny,:nx]; tv=T[:ny,:nx]
                    uv_u=U[:ny,:nx]; uv_v=V[:ny,:nx]; nv=N[:ny,:nx]
                    mv=M[:ny,:nx]; m2=M2[:ny,:nx]; m3=M3[:ny,:nx]

                    
                    
                    
                    np.multiply(XX,a_x,out=av); np.multiply(YY,a_y,out=tv); np.add(av,tv,out=av); av += a_c
                    np.multiply(XX,b_x,out=bv); np.multiply(YY,b_y,out=tv); np.add(bv,tv,out=bv); bv += b_c
                    np.add(av,bv,out=cv); np.subtract(np.float32(1.0),cv,out=cv)

                    epsb=np.float32(1e-6)
                    np.greater_equal(av,-epsb,out=mv)
                    np.greater_equal(bv,-epsb,out=m2); np.logical_and(mv,m2,out=mv)
                    np.greater_equal(cv,-epsb,out=m2); np.logical_and(mv,m2,out=mv)
                    
                    
                    
                    if not np.any(mv): continue

                    np.multiply(av,np.float32(dep_s[0]-dep_s[2]),out=zv)
                    zv += np.float32(dep_s[2])
                    np.multiply(bv,np.float32(dep_s[1]-dep_s[2]),out=tv)
                    zv += tv

                    mapped_texture_uv=None
                    if texture_uv_vertices is not None and tex_denom is not None and tex_coeff is not None:
                        
                        
                        
                        
                        
                        
                        tax,tay,tac,tbx,tby,tbc = tex_coeff
                        np.multiply(XX,tax,out=av); np.multiply(YY,tay,out=tv); np.add(av,tv,out=av); av += tac
                        np.multiply(XX,tbx,out=bv); np.multiply(YY,tby,out=tv); np.add(bv,tv,out=bv); bv += tbc
                        np.add(av,bv,out=cv); np.subtract(np.float32(1.0),cv,out=cv)
                        (u0,v0),(u1,v1),(u2,v2)=texture_uv_vertices
                        if dep[0]>1e-9 and dep[1]>1e-9 and dep[2]>1e-9:
                            iz0=np.float32(1.0/dep[0]); iz1=np.float32(1.0/dep[1]); iz2=np.float32(1.0/dep[2])
                            
                            np.multiply(av,iz0,out=tv)
                            np.multiply(bv,iz1,out=nv); np.add(tv,nv,out=tv)
                            np.multiply(cv,iz2,out=nv); np.add(tv,nv,out=tv)
                            np.isfinite(tv,out=m2)
                            np.absolute(tv,out=nv)
                            np.greater(nv,np.float32(1.0e-15),out=m3); np.logical_and(m2,m3,out=m2)
                            
                            np.multiply(av,np.float32(u0)*iz0,out=uv_u)
                            np.multiply(bv,np.float32(u1)*iz1,out=nv); np.add(uv_u,nv,out=uv_u)
                            np.multiply(cv,np.float32(u2)*iz2,out=nv); np.add(uv_u,nv,out=uv_u)
                            np.divide(uv_u,tv,out=uv_u,where=m2)
                            
                            np.multiply(av,np.float32(v0)*iz0,out=uv_v)
                            np.multiply(bv,np.float32(v1)*iz1,out=nv); np.add(uv_v,nv,out=uv_v)
                            np.multiply(cv,np.float32(v2)*iz2,out=nv); np.add(uv_v,nv,out=uv_v)
                            np.divide(uv_v,tv,out=uv_v,where=m2)
                            
                            
                            np.logical_not(m2,out=m3)
                            np.copyto(uv_u,np.float32(0.0),where=m3)
                            np.copyto(uv_v,np.float32(0.0),where=m3)
                        else:
                            np.multiply(av,np.float32(u0),out=uv_u)
                            np.multiply(bv,np.float32(u1),out=nv); np.add(uv_u,nv,out=uv_u)
                            np.multiply(cv,np.float32(u2),out=nv); np.add(uv_u,nv,out=uv_u)
                            np.multiply(av,np.float32(v0),out=uv_v)
                            np.multiply(bv,np.float32(v1),out=nv); np.add(uv_v,nv,out=uv_v)
                            np.multiply(cv,np.float32(v2),out=nv); np.add(uv_v,nv,out=uv_v)
                        mapped_texture_uv=(uv_u,uv_v)

                    np.isfinite(zv,out=m2); np.logical_and(mv,m2,out=mv)
                    np.greater(zv,np.float32(0.0),out=m2); np.logical_and(mv,m2,out=mv)
                    np.less_equal(zv,sl_depth,out=m2); np.logical_and(mv,m2,out=mv)
                    if not np.any(mv): continue

                    sl_rgba=rgba[ty0:ty1+1,tx0:tx1+1,:]
                    if fast_simple and simple_rgba is not None and not intrinsic:
                        if int(simple_rgba[3]) < ath:
                            continue
                        sl_depth[mv]=zv[mv]
                        for ch in range(4):
                            cc=sl_rgba[:,:,ch]; cc[mv]=simple_rgba[ch]
                    else:
                        cols=_sample_rgba_from_fill_spec(fill_spec,XX,YY,pb,texture_uv=mapped_texture_uv)
                        aa=cols[:,:,3]
                        if intrinsic:
                            if target<=0:
                                np.logical_and(mv,False,out=mv)
                            elif target<255:
                                ia=np.clip(np.round(aa.astype(np.float32)*(255.0/float(target))),0,255)
                                np.greater_equal(ia,ath,out=m2); np.logical_and(mv,m2,out=mv)
                            else:
                                np.greater_equal(aa,ath,out=m2); np.logical_and(mv,m2,out=mv)
                        else:
                            np.greater_equal(aa,ath,out=m2); np.logical_and(mv,m2,out=mv)
                        if not np.any(mv): continue
                        sl_depth[mv]=zv[mv]
                        for ch in range(4):
                            cc=sl_rgba[:,:,ch]; cc[mv]=cols[:,:,ch][mv]
        processed += 1

    if owner is not None:
        try:
            owner._panorama_zbuffer_runtime_stats={
                'faces_total':int(len(work_faces)),'faces_processed':int(processed),
                'depth_tiles_skipped':int(skipped_depth_tiles),'aborted_memory':bool(aborted_memory),
                'width':int(sw),'height':int(sh),'scale':float(scale),
            }
        except Exception: pass
    if aborted_memory and bool(getattr(owner, '_qcv_export_in_progress', False)):
        raise MemoryError("PANORAMA export cancelled before memory limit; incomplete depth scene")
    return rgba, depth, float(scale)


def _compose_faces_zbuffer(width, height, faces, scale=1.0):
    
    sw = max(1, int(round(float(width) * float(scale))))
    sh = max(1, int(round(float(height) * float(scale))))
    depth = np.full((sh, sw), np.inf, dtype=np.float32)
    rgba = np.zeros((sh, sw, 4), dtype=np.uint8)
    tile_px = 192
    sc = float(scale)
    for face in faces:
        try:
            pts0 = face.get('uv', ())
            dep0 = face.get('depths', ())
            pts_s = tuple((float(pts0[k][0]) * sc, float(pts0[k][1]) * sc) for k in range(3))
            dep = tuple(float(dep0[k]) for k in range(3))
            tuv0 = face.get('texture_uv', None)
            if tuv0 is not None:
                texture_uv_vertices = tuple((float(tuv0[k][0]), float(tuv0[k][1])) for k in range(3))
            else:
                texture_uv_vertices = None
        except Exception:
            continue
        vals = tuple(v for p in pts_s for v in p) + dep
        if texture_uv_vertices is not None:
            vals += tuple(v for p in texture_uv_vertices for v in p)
        if not all(math.isfinite(v) for v in vals):
            continue
        fill_spec = face.get('fill_spec') or {'kind':'simple','color':QColor(255,0,255,255)}
        pb0 = face.get('pattern_bbox', None)
        if pb0 is not None:
            try:
                pb_scaled = tuple(float(v) * sc for v in pb0)
            except Exception:
                pb_scaled = None
        else:
            pb_scaled = None

        
        
        
        
        
        
        
        tex_denom = None
        if texture_uv_vertices is not None:
            try:
                (tx0s, ty0s), (tx1s, ty1s), (tx2s, ty2s) = pts_s
                td = ((ty1s-ty2s)*(tx0s-tx2s)+(tx2s-tx1s)*(ty0s-ty2s))
                if math.isfinite(td) and abs(td) >= 1e-12:
                    tex_denom = float(td)
                else:
                    texture_uv_vertices = None
            except Exception:
                texture_uv_vertices = None
                tex_denom = None

        for tri_s, dep_s in _clip_triangle_to_viewport(pts_s, dep, sw, sh):
            xs3 = (tri_s[0][0], tri_s[1][0], tri_s[2][0])
            ys3 = (tri_s[0][1], tri_s[1][1], tri_s[2][1])
            minx = max(0, int(math.floor(min(xs3)))); maxx = min(sw - 1, int(math.ceil(max(xs3))))
            miny = max(0, int(math.floor(min(ys3)))); maxy = min(sh - 1, int(math.ceil(max(ys3))))
            if maxx < minx or maxy < miny:
                continue
            (x0,y0),(x1,y1),(x2,y2) = tri_s
            denom = ((y1-y2)*(x0-x2)+(x2-x1)*(y0-y2))
            if not math.isfinite(denom) or abs(denom) < 1e-9:
                continue
            pb = pb_scaled if pb_scaled is not None else (minx,maxx,miny,maxy)
            ath = max(0,min(255,int(fill_spec.get('depth_alpha_threshold',1) or 1)))
            intrinsic = bool(fill_spec.get('depth_uses_intrinsic_alpha',False))
            target = max(0,min(255,int(fill_spec.get('target_alpha',255) or 0)))
            for ty0 in range(miny,maxy+1,tile_px):
                ty1 = min(maxy,ty0+tile_px-1); ys=np.arange(ty0,ty1+1,dtype=np.float64)+0.5
                for tx0 in range(minx,maxx+1,tile_px):
                    tx1 = min(maxx,tx0+tile_px-1); xs=np.arange(tx0,tx1+1,dtype=np.float64)+0.5
                    XX,YY=np.meshgrid(xs,ys)
                    a=((y1-y2)*(XX-x2)+(x2-x1)*(YY-y2))/denom
                    b=((y2-y0)*(XX-x2)+(x0-x2)*(YY-y2))/denom; c=1.0-a-b
                    inside=((a>=-1e-6)&(b>=-1e-6)&(c>=-1e-6))|((a<=1e-6)&(b<=1e-6)&(c<=1e-6))
                    if not np.any(inside):
                        continue
                    z=(a*dep_s[0]+b*dep_s[1]+c*dep_s[2]).astype(np.float32)
                    sl_depth=depth[ty0:ty1+1,tx0:tx1+1]

                    mapped_texture_uv = None
                    if texture_uv_vertices is not None and tex_denom is not None:
                        
                        
                        
                        
                        ta=((ty1s-ty2s)*(XX-tx2s)+(tx2s-tx1s)*(YY-ty2s))/tex_denom
                        tb=((ty2s-ty0s)*(XX-tx2s)+(tx0s-tx2s)*(YY-ty2s))/tex_denom
                        tc=1.0-ta-tb
                        (u0,v0),(u1,v1),(u2,v2)=texture_uv_vertices
                        if dep[0] > 1e-9 and dep[1] > 1e-9 and dep[2] > 1e-9:
                            iz0=1.0/dep[0]; iz1=1.0/dep[1]; iz2=1.0/dep[2]
                            iq=ta*iz0+tb*iz1+tc*iz2
                            good_q=np.isfinite(iq)&(np.abs(iq)>1e-15)
                            uu=np.zeros(iq.shape,dtype=np.float64)
                            vv=np.zeros(iq.shape,dtype=np.float64)
                            num_u=ta*(u0*iz0)+tb*(u1*iz1)+tc*(u2*iz2)
                            num_v=ta*(v0*iz0)+tb*(v1*iz1)+tc*(v2*iz2)
                            np.divide(num_u,iq,out=uu,where=good_q)
                            np.divide(num_v,iq,out=vv,where=good_q)
                        else:
                            uu=ta*u0+tb*u1+tc*u2
                            vv=ta*v0+tb*v1+tc*v2
                        mapped_texture_uv=(uu,vv)

                    cols=_sample_rgba_from_fill_spec(fill_spec,XX,YY,pb,texture_uv=mapped_texture_uv); aa=cols[:,:,3]
                    if intrinsic:
                        if target<=0:
                            alpha_ok=np.zeros(aa.shape,dtype=bool)
                        elif target<255:
                            ia=np.clip(np.round(aa.astype(np.float32)*(255.0/float(target))),0,255); alpha_ok=ia>=ath
                        else:
                            alpha_ok=aa>=ath
                    else:
                        alpha_ok=aa>=ath
                    better=inside&alpha_ok&np.isfinite(z)&(z>0)&(z<=sl_depth)
                    if not np.any(better):
                        continue
                    sl_rgba=rgba[ty0:ty1+1,tx0:tx1+1,:]; sl_depth[better]=z[better]
                    for ch in range(4):
                        cc=sl_rgba[:,:,ch]; cc[better]=cols[:,:,ch][better]
    return rgba, depth, float(scale)

def _iter_wrapped_runs_with_depth(uvs, depths, wrap_width=None, min_len=3):
    for pts, dep in _uv_runs_with_depth(uvs, depths, min_len=min_len):
        if wrap_width and float(wrap_width) > 1.0:
            for shifted in _iter_wrap_shifted_pts(pts, wrap_width):
                yield shifted, dep.copy()
        else:
            yield pts, dep


def _iter_wrapped_quad_faces(bu0, bu1, tu1, tu0, db0, db1, dt1, dt0, wrap_width=None):
    quad = np.asarray([
        [float(bu0[0]), float(bu0[1])],
        [float(bu1[0]), float(bu1[1])],
        [float(tu1[0]), float(tu1[1])],
        [float(tu0[0]), float(tu0[1])],
    ], dtype=np.float64)
    deps = np.asarray([float(db0), float(db1), float(dt1), float(dt0)], dtype=np.float64)
    if wrap_width and float(wrap_width) > 1.0:
        shifted_iter = _iter_wrap_shifted_pts(quad, wrap_width)
    else:
        shifted_iter = (quad,)
    for sq in shifted_iter:
        yield sq, deps.copy()


def _append_polygon_faces_for_zbuffer(faces, sty, uv_base, depth_base, uv_top=None, depth_top=None, transparent_objects=False, wrap_width=None):
    if not bool(getattr(sty, 'fill_polygons', True)):
        return
    top_fill = _normalized_fill_spec_for_sty(sty, transparent_objects=transparent_objects)
    wall_col = _wall_color_from_fill_spec(top_fill, fallback=getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255))), transparent_objects=transparent_objects)
    wall_fill = {'kind': 'simple', 'color': wall_col, 'outline_color': QColor(getattr(sty, 'color', wall_col)), 'outline_width': float(getattr(sty, 'width', 1.0) or 1.0), 'pen_style': getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine)}

    if uv_top is None or depth_top is None:
        for pts, dep in _iter_wrapped_runs_with_depth(uv_base, depth_base, wrap_width=wrap_width, min_len=3):
            pattern_bbox = _finite_bbox_xy_scalar(pts)
            if pattern_bbox is None:
                continue
            faces.extend(_triangulate_polygon_run(pts, dep, top_fill, pattern_bbox=pattern_bbox))
        return

    for pts, dep in _iter_wrapped_runs_with_depth(uv_top, depth_top, wrap_width=wrap_width, min_len=3):
        pattern_bbox = _finite_bbox_xy_scalar(pts)
        if pattern_bbox is None:
            continue
        faces.extend(_triangulate_polygon_run(pts, dep, top_fill, pattern_bbox=pattern_bbox))

    if bool(getattr(sty, 'fill_walls', True)):
        n = min(len(uv_base), len(uv_top), len(depth_base), len(depth_top))
        for i in range(max(0, n - 1)):
            bu0, bu1 = uv_base[i], uv_base[i+1]
            tu0, tu1 = uv_top[i], uv_top[i+1]
            db0, db1 = depth_base[i], depth_base[i+1]
            dt0, dt1 = depth_top[i], depth_top[i+1]
            vals = [bu0[0], bu0[1], bu1[0], bu1[1], tu0[0], tu0[1], tu1[0], tu1[1], db0, db1, dt0, dt1]
            if not all(math.isfinite(float(v)) for v in vals):
                continue
            for quad, deps in _iter_wrapped_quad_faces(bu0, bu1, tu1, tu0, db0, db1, dt1, dt0, wrap_width=wrap_width):
                tri1 = {'uv': np.asarray([quad[0], quad[1], quad[2]], dtype=np.float64),
                        'depths': np.asarray([deps[0], deps[1], deps[2]], dtype=np.float64),
                        'fill_spec': dict(wall_fill)}
                tri2 = {'uv': np.asarray([quad[0], quad[2], quad[3]], dtype=np.float64),
                        'depths': np.asarray([deps[0], deps[2], deps[3]], dtype=np.float64),
                        'fill_spec': dict(wall_fill)}
                faces.extend([tri1, tri2])


def _append_line_walls_for_zbuffer(faces, sty, uv_base, depth_base, uv_top, depth_top, transparent_objects=False, wrap_width=None):
    if uv_top is None or depth_top is None:
        return
    top_fill = _normalized_fill_spec_for_sty(sty, transparent_objects=transparent_objects)
    wall_col = _wall_color_from_fill_spec(top_fill, fallback=getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255))), transparent_objects=transparent_objects)
    wall_fill = {'kind': 'simple', 'color': wall_col, 'outline_color': QColor(getattr(sty, 'color', wall_col)), 'outline_width': float(getattr(sty, 'width', 1.0) or 1.0), 'pen_style': getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine)}
    n = min(len(uv_base), len(uv_top), len(depth_base), len(depth_top))
    for i in range(max(0, n - 1)):
        bu0, bu1 = uv_base[i], uv_base[i+1]
        tu0, tu1 = uv_top[i], uv_top[i+1]
        db0, db1 = depth_base[i], depth_base[i+1]
        dt0, dt1 = depth_top[i], depth_top[i+1]
        vals = [bu0[0], bu0[1], bu1[0], bu1[1], tu0[0], tu0[1], tu1[0], tu1[1], db0, db1, dt0, dt1]
        if not all(math.isfinite(float(v)) for v in vals):
            continue
        for quad, deps in _iter_wrapped_quad_faces(bu0, bu1, tu1, tu0, db0, db1, dt1, dt0, wrap_width=wrap_width):
            faces.extend([
                {'uv': np.asarray([quad[0], quad[1], quad[2]], dtype=np.float64),
                 'depths': np.asarray([deps[0], deps[1], deps[2]], dtype=np.float64),
                 'fill_spec': dict(wall_fill)},
                {'uv': np.asarray([quad[0], quad[2], quad[3]], dtype=np.float64),
                 'depths': np.asarray([deps[0], deps[2], deps[3]], dtype=np.float64),
                 'fill_spec': dict(wall_fill)},
            ])


def _xy_points_scalar(arr_xy):
    
    try:
        n = len(arr_xy)
    except Exception:
        return []
    out = []
    for i in range(n):
        try:
            x, y = float(arr_xy[i][0]), float(arr_xy[i][1])
        except Exception:
            return []
        if not (math.isfinite(x) and math.isfinite(y)):
            return []
        out.append((x, y))
    return out


def _xy_points_close(a, b):
    try:
        return (math.isclose(float(a[0]), float(b[0]), rel_tol=1e-12, abs_tol=1e-9)
                and math.isclose(float(a[1]), float(b[1]), rel_tol=1e-12, abs_tol=1e-9))
    except Exception:
        return False

def _significant_ring_vertex_indices(arr_xy, closed=True, angle_tol_deg=2.0, min_seg_len=0.05):
    
    pts = _xy_points_scalar(arr_xy)
    if not pts:
        return []
    if closed and len(pts) >= 2 and _xy_points_close(pts[0], pts[-1]):
        pts = pts[:-1]
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = []
    ang_tol = float(angle_tol_deg or 0.0)
    min_len = max(1e-9, float(min_seg_len or 0.0))
    for i in range(n):
        p_prev = pts[(i - 1) % n]
        p = pts[i]
        p_next = pts[(i + 1) % n]
        v1x, v1y = p[0] - p_prev[0], p[1] - p_prev[1]
        v2x, v2y = p_next[0] - p[0], p_next[1] - p[1]
        l1 = math.hypot(v1x, v1y)
        l2 = math.hypot(v2x, v2y)
        if l1 < min_len and l2 < min_len:
            continue
        if l1 < min_len or l2 < min_len:
            keep.append(i)
            continue
        c = (v1x * v2x + v1y * v2y) / max(1e-12, l1 * l2)
        c = max(-1.0, min(1.0, c))
        turn = 180.0 - math.degrees(math.acos(c))
        if turn >= ang_tol:
            keep.append(i)
    return keep if keep else list(range(n))

def _wall_visibility_flags_xy(arr_xy, cam_pt_xy, closed=True, min_seg_len=0.05):
    
    pts = _xy_points_scalar(arr_xy)
    if len(pts) < 2:
        return []
    if closed and len(pts) >= 2 and _xy_points_close(pts[0], pts[-1]):
        pts = pts[:-1]
    n = len(pts)
    if n < 2:
        return []
    try:
        camx, camy = float(cam_pt_xy[0]), float(cam_pt_xy[1])
    except Exception:
        return [True] * n
    ccw = _signed_area_2d(pts) >= 0.0
    flags = []
    min_len = max(1e-9, float(min_seg_len or 0.0))
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        if math.hypot(dx, dy) < min_len:
            flags.append(False)
            continue
        nx, ny = (dy, -dx) if ccw else (-dy, dx)
        mx, my = 0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1])
        flags.append((nx * (camx - mx) + ny * (camy - my)) > 0.0)
    return flags

def _opaque_vertical_corner_indices(arr_xy, cam_pt_xy, angle_tol_deg=2.0, min_seg_len=0.05):
    
    ring = _xy_points_scalar(arr_xy)
    if not ring:
        return []
    while len(ring) >= 2 and _xy_points_close(ring[0], ring[-1]):
        ring = ring[:-1]
    n = len(ring)
    if n <= 2:
        return list(range(n))
    significant = set(_significant_ring_vertex_indices(ring, closed=False, angle_tol_deg=angle_tol_deg, min_seg_len=min_seg_len))
    wall_vis = _wall_visibility_flags_xy(ring, cam_pt_xy, closed=False, min_seg_len=min_seg_len)
    if len(wall_vis) != n:
        wall_vis = [True] * n
    keep = []
    for i in range(n):
        if i not in significant:
            continue
        if bool(wall_vis[(i - 1) % n]) or bool(wall_vis[i % n]):
            keep.append(i)
    return keep if keep else (sorted(significant) if significant else list(range(n)))

def _select_uv_depth_by_indices(uvs, depths, indices, closed=True):
    idxs = []
    try:
        n = min(len(uvs), len(depths))
    except Exception:
        n = 0
    if n <= 0:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    seen = set()
    for idx in indices or []:
        j = int(idx) % n
        if j in seen:
            continue
        seen.add(j)
        idxs.append(j)
    if not idxs:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    pts = []
    dep = []
    for j in idxs:
        pts.append([float(uvs[j, 0]), float(uvs[j, 1])])
        dep.append(float(depths[j]))
    if closed and idxs:
        pts.append([float(uvs[idxs[0], 0]), float(uvs[idxs[0], 1])])
        dep.append(float(depths[idxs[0]]))
    return np.asarray(pts, dtype=np.float64), np.asarray(dep, dtype=np.float64)



def _prepare_pinhole_screen_metrics(ctx):
    
    cached=ctx.get('_qcal_pinhole_metrics') if isinstance(ctx,dict) else None
    if cached is not None: return cached
    try:
        W=max(1.0,float(ctx.get('width',1))); H=max(1.0,float(ctx.get('height',1)))
        hf=math.radians(max(1e-6,min(179.999,float(ctx.get('HFOV',60.0))))); vf=math.radians(max(1e-6,min(179.999,float(ctx.get('VFOV',40.0)))))
        th=math.tan(hf*0.5); tv=math.tan(vf*0.5)
        if not (math.isfinite(th) and math.isfinite(tv)) or abs(th)<=1e-12 or abs(tv)<=1e-12: return None
        fx=(W*0.5)/th; fy=(H*0.5)/tv
        rv=tuple(float(v) for v in ctx['r']); uv=tuple(float(v) for v in ctx['u']); fv=tuple(float(v) for v in ctx['f'])
        cached=(W,H,fx,fy,rv,uv,fv); ctx['_qcal_pinhole_metrics']=cached; return cached
    except Exception: return None


def _pinhole_screen_depth_metrics(ctx, x, y, depth_yc):
    
    try:
        d=float(depth_yc)
        if not math.isfinite(d) or d<=1e-9: return None
        m=_prepare_pinhole_screen_metrics(ctx)
        if m is None: return None
        W,H,fx,fy,rvec,uvec,fvec=m
        qx=(float(x)-W*0.5)/fx; qz=-(float(y)-H*0.5)/fy
        dx=qx*rvec[0]+fvec[0]+qz*uvec[0]; dy=qx*rvec[1]+fvec[1]+qz*uvec[1]; dz=qx*rvec[2]+fvec[2]+qz*uvec[2]
        rh=math.hypot(dx,dy)
        if rh<=1e-12 or not math.isfinite(rh): return None
        return math.degrees(math.atan2(dx,dy))%360.0, math.degrees(math.atan2(dz,rh)), d*rh
    except Exception: return None

def _horizon_distance_index_python(d_values, dist_m):
    
    try:
        n = int(getattr(d_values, 'size', 0) or len(d_values))
        if n <= 0:
            return -1
        target = float(dist_m)
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if float(d_values[mid]) < target:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1
    except Exception:
        return -1


def _prepare_horizon_fast_distance_lut_40192(horizon):
    
    if not isinstance(horizon, dict):
        return None
    cached = horizon.get('_qcv_dist_lut_40192')
    if cached is not None:
        return cached
    try:
        dv = horizon.get('d_values')
        nd = int(getattr(dv, 'size', 0) or len(dv))
        if dv is None or nd <= 0:
            return None
        maxd = max(1.0, float(dv[nd-1]))
        
        
        step = max(2.0, min(20.0, maxd / 2048.0))
        count = max(2, int(math.ceil(maxd / step)) + 2)
        lut = [-1] * count
        j = -1
        for k in range(count):
            target = float(k) * step
            while (j + 1) < nd and float(dv[j + 1]) < target:
                j += 1
            lut[k] = int(j)
        cached = (float(step), tuple(lut), int(nd), float(maxd))
        horizon['_qcv_dist_lut_40192'] = cached
        return cached
    except Exception:
        return None


def _horizon_distance_index_fast_40192(horizon, dist_m, *, exact=True):
    
    try:
        dv = horizon.get('d_values') if isinstance(horizon, dict) else None
        nd = int(getattr(dv, 'size', 0) or len(dv))
        if dv is None or nd <= 0:
            return -1
        d = float(dist_m)
        if not math.isfinite(d) or d <= 0.0:
            return -1
        lut = _prepare_horizon_fast_distance_lut_40192(horizon)
        if lut is None:
            return _horizon_distance_index_python(dv, d)
        step, vals, _nd, _maxd = lut
        k = int(d // step)
        if k < 0:
            return -1
        if k >= len(vals):
            k = len(vals) - 1
        j = int(vals[k])
        if not exact:
            return min(j, nd - 1)
        
        while (j + 1) < nd and float(dv[j + 1]) < d:
            j += 1
        while j >= 0 and float(dv[j]) >= d:
            j -= 1
        return min(j, nd - 1)
    except Exception:
        return -1


def _horizon_azimuth_index_fast_40192(horizon, az_deg):
    try:
        el_bins = horizon.get('el_bins')
        n_bins = int(horizon.get('n_bins') or getattr(el_bins, 'size', 0) or len(el_bins))
        if el_bins is None or n_bins <= 0:
            return None
        az_min = float(horizon.get('az_min', 0.0)); az_max = float(horizon.get('az_max', az_min))
        az_val = _unwrap_az_for_range(float(az_deg), az_min, az_max)
        if az_max <= az_min or n_bins == 1:
            return 0
        frac = (az_val - az_min) / (az_max - az_min)
        if frac <= 0.0:
            return 0
        if frac >= 1.0:
            return n_bins - 1
        return int(round(frac * (n_bins - 1)))
    except Exception:
        return None


def _terrain_point_visible_fast_40192(horizon, x, y, z, cam_x, cam_y, cam_z, eps_deg):
    
    if not horizon:
        return True
    try:
        dx = float(x) - float(cam_x); dy = float(y) - float(cam_y)
        rr = math.hypot(dx, dy)
        if not math.isfinite(rr) or rr < 1.0:
            return True
        az = math.degrees(math.atan2(dx, dy))
        el = math.degrees(math.atan2(float(z) - float(cam_z), rr))
        ai = _horizon_azimuth_index_fast_40192(horizon, az)
        if ai is None:
            return True
        env = None
        ec = horizon.get('el_cummax'); eb = horizon.get('el_bins')
        j = _horizon_distance_index_fast_40192(horizon, rr, exact=True)
        if j >= 0 and ec is not None:
            try:
                env = float(ec[ai, j])
            except Exception:
                env = None
        elif j < 0:
            return True
        if env is None and eb is not None:
            env = float(eb[ai])
        if env is None or not math.isfinite(env):
            return True
        return float(el) >= (float(env) - float(eps_deg))
    except Exception:
        return True


def _terrain_object_classify_xyz_40192(horizon, xyz, cam_x, cam_y, cam_z, eps_deg, *, max_samples=24, max_span_m=850.0):
    
    if not horizon or xyz is None:
        return 2
    try:
        n = int(len(xyz))
        if n <= 0:
            return 2
        minx = miny = math.inf; maxx = maxy = -math.inf
        valid_rows = []
        for i in range(n):
            try:
                x = float(xyz[i][0]); y = float(xyz[i][1]); z = float(xyz[i][2])
            except Exception:
                continue
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                continue
            valid_rows.append((x,y,z))
            minx=min(minx,x); maxx=max(maxx,x); miny=min(miny,y); maxy=max(maxy,y)
        n = len(valid_rows)
        if n <= 0:
            return 2
        span = math.hypot(maxx-minx, maxy-miny)
        
        if math.isfinite(span) and span > float(max_span_m):
            return 1
        want = min(max(4, int(max_samples)), n)
        idxs = []
        if want >= n:
            idxs = list(range(n))
        elif want == 1:
            idxs = [0]
        else:
            for k in range(want):
                ii = int(round(k * (n - 1) / float(want - 1)))
                if not idxs or ii != idxs[-1]:
                    idxs.append(ii)
        visible = 0; hidden = 0; sx=sy=sz=0.0; sc=0
        for ii in idxs:
            x,y,z = valid_rows[ii]; sx+=x; sy+=y; sz+=z; sc+=1
            if _terrain_point_visible_fast_40192(horizon,x,y,z,cam_x,cam_y,cam_z,eps_deg): visible += 1
            else: hidden += 1
        
        if sc > 1:
            cx=sx/sc; cy=sy/sc; cz=sz/sc
            if _terrain_point_visible_fast_40192(horizon,cx,cy,cz,cam_x,cam_y,cam_z,eps_deg): visible += 1
            else: hidden += 1
        if visible <= 0 and hidden > 0:
            return 0
        if hidden <= 0 and visible > 0:
            return 2
        return 1
    except Exception:
        return 2


def _terrain_probe_xyz_from_xy_parts_40192(parts, z_sampler, top_height_m, *, max_samples=24):
    
    if not parts or z_sampler is None:
        return None
    try:
        candidates=[]
        total=sum(max(0,int(len(a))) for a in parts if a is not None)
        if total <= 0:
            return None
        budget=max(4,int(max_samples)); remaining=budget
        for pi,a in enumerate(parts):
            n=int(len(a))
            if n<=0 or remaining<=0: continue
            take=max(1,min(n, max(2, int(round(budget*n/max(1,total))))))
            take=min(take,remaining); remaining-=take
            if take>=n: ids=range(n)
            else: ids=[int(round(k*(n-1)/float(max(1,take-1)))) for k in range(take)]
            for ii in ids:
                x=float(a[ii][0]); y=float(a[ii][1])
                if not (math.isfinite(x) and math.isfinite(y)): continue
                try: gz=float(z_sampler(QgsPointXY(x,y)))
                except Exception: continue
                if math.isfinite(gz): candidates.append((x,y,gz+max(0.0,float(top_height_m))))
        return candidates or None
    except Exception:
        return None


def _pinhole_fragment_visible_by_horizon(ctx, horizon, eps_deg, x, y, depth_yc):
    
    if not horizon:
        return True
    metrics = _pinhole_screen_depth_metrics(ctx, x, y, depth_yc)
    if metrics is None:
        return True
    az_deg, el_deg, dist_m = metrics
    try:
        el_bins = horizon.get('el_bins')
        n_bins = int(horizon.get('n_bins') or getattr(el_bins, 'size', 0) or len(el_bins))
        if el_bins is None or n_bins <= 0:
            return True
        az_min = float(horizon.get('az_min', 0.0))
        az_max = float(horizon.get('az_max', az_min))
        center = 0.5 * (az_min + az_max)
        
        az_val = center + (((float(az_deg) - center + 180.0) % 360.0) - 180.0)
        if az_max <= az_min or n_bins == 1:
            idx = 0
        else:
            frac = (az_val - az_min) / (az_max - az_min)
            idx = int(round(max(0.0, min(1.0, frac)) * (n_bins - 1)))
        env = None
        d_values = horizon.get('d_values')
        el_cummax = horizon.get('el_cummax')
        if d_values is not None and el_cummax is not None:
            j = _horizon_distance_index_python(d_values, dist_m)
            if j >= 0:
                try:
                    env = float(el_cummax[idx, j])
                except Exception:
                    env = None
            else:
                return True
        if env is None:
            env = float(el_bins[idx])
        if not math.isfinite(env):
            return True
        return float(el_deg) >= (env - float(eps_deg))
    except Exception:
        return True


def _mask_pinhole_zbuffer_by_horizon(rgba, depth_buf, depth_scale, ctx, horizon, eps_deg):
    
    if rgba is None or depth_buf is None or not horizon or str(ctx.get('proj','')).upper()!='PINHOLE':
        return rgba
    try:
        m=_prepare_pinhole_screen_metrics(ctx)
        if m is None:
            return rgba
        W,H,fx,fy,rt,ut,ft=m
        scale=max(1e-9,float(depth_scale)); depth=np.asarray(depth_buf)
        if depth.ndim!=2 or getattr(rgba, 'ndim', 0) != 3:
            return rgba
        rx,ry,rz=(float(rt[0]),float(rt[1]),float(rt[2]))
        ux,uy,uz=(float(ut[0]),float(ut[1]),float(ut[2]))
        fwx,fwy,fwz=(float(ft[0]),float(ft[1]),float(ft[2]))
        el_bins=horizon.get('el_bins'); n_bins=int(horizon.get('n_bins') or getattr(el_bins,'size',0) or len(el_bins))
        if el_bins is None or n_bins<=0:
            return rgba
        eb=np.asarray(el_bins,dtype=np.float64); az_min=float(horizon.get('az_min',0.0)); az_max=float(horizon.get('az_max',az_min)); center=0.5*(az_min+az_max)
        dv0=horizon.get('d_values'); ec0=horizon.get('el_cummax'); dv=np.asarray(dv0,dtype=np.float64) if dv0 is not None else None; ec=np.asarray(ec0) if ec0 is not None else None; nd=int(dv.size) if dv is not None else 0
        hh,ww=depth.shape; tile=256
        for y0 in range(0,hh,tile):
            y1=min(hh,y0+tile)
            for x0 in range(0,ww,tile):
                x1=min(ww,x0+tile); ds=depth[y0:y1,x0:x1]; finite=np.isfinite(ds)&(ds>1e-9)
                if not np.any(finite): continue
                yy,xx=np.nonzero(finite); dep=ds[yy,xx].astype(np.float64,copy=False)
                x=((xx.astype(np.float64)+x0)+0.5)/scale; y=((yy.astype(np.float64)+y0)+0.5)/scale
                qx=(x-W*0.5)/fx; qz=-(y-H*0.5)/fy
                dx=qx*rx+fwx+qz*ux; dy=qx*ry+fwy+qz*uy; dz=qx*rz+fwz+qz*uz
                rh=np.hypot(dx,dy); valid=np.isfinite(rh)&(rh>1e-12); dist=dep*rh; az=(np.degrees(np.arctan2(dx,dy))+360.0)%360.0; el=np.degrees(np.arctan2(dz,rh)); av=center+(((az-center+180.0)%360.0)-180.0)
                if az_max<=az_min or n_bins==1: ai=np.zeros_like(xx,dtype=np.int32)
                else: ai=np.rint(np.clip((av-az_min)/(az_max-az_min),0.0,1.0)*(n_bins-1)).astype(np.int32)
                env=eb[ai]
                if dv is not None and ec is not None and nd>0 and ec.ndim>=2:
                    lo=np.full(dist.shape,-1,dtype=np.int32); hi=np.full(dist.shape,nd,dtype=np.int32); iterations=max(1,int(math.ceil(math.log(max(2,nd+1),2)))+1)
                    for _ in range(iterations):
                        active=(hi-lo)>1
                        if not np.any(active): break
                        mid=((lo+hi)//2).astype(np.int32); mc=np.clip(mid,0,nd-1); go=active&(dv[mc]<dist); lo=np.where(go,mid,lo); hi=np.where(active&(~go),mid,hi)
                    hp=lo>=0
                    if np.any(hp):
                        e2=env.copy(); e2[hp]=ec[ai[hp],lo[hp]]; env=e2
                    env=np.where(hp,env,-np.inf)
                hidden=valid&np.isfinite(env)&(el<(env-float(eps_deg)))
                if not np.any(hidden): continue
                hy=yy[hidden]; hx=xx[hidden]; rgba[y0+hy,x0+hx,:]=0; ds[hy,hx]=np.inf
        return rgba
    except Exception:
        return rgba

def _panorama_fragment_visible_by_horizon(ctx, horizon, eps_deg, x, y, radial_depth):
    
    if not horizon or ctx is None:
        return True
    try:
        proj=str(ctx.get('proj','') or '').upper()
        if proj not in ('EQUIRECT','EQUIRECTANGULAR','CYLINDRICAL'):
            return True
        W=max(1,int(ctx.get('width',1))); H=max(1,int(ctx.get('height',1)))
        xx=float(x); yy=float(y); dep=float(radial_depth)
        if not all(math.isfinite(v) for v in (xx,yy,dep)) or dep<=0.0:
            return True
        hf=math.radians(max(1e-6,float(ctx.get('HFOV',360.0))))
        vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
        full360=bool(ctx.get('is360',False))
        if proj in ('EQUIRECT','EQUIRECTANGULAR'):
            if full360:
                alpha=(xx/float(W))*(2.0*math.pi)-math.pi
                beta=math.pi*0.5-(yy/float(H))*math.pi
            else:
                alpha=(xx-float(W)*0.5)*(hf/float(W))
                beta=(float(H)*0.5-yy)*(vf/float(H))
        else:
            if full360:
                alpha=(xx/float(W))*(2.0*math.pi)-math.pi
            else:
                alpha=(xx-float(W)*0.5)*(hf/float(W))
            vf=_validated_vfov_for_cylindrical(float(ctx.get('VFOV',90.0)),float(ctx.get('HFOV',360.0)),W,H)
            fy=(H*0.5)/max(1e-9,math.tan(vf*0.5))
            beta=math.atan((float(H)*0.5-yy)/float(fy))
        cb=math.cos(beta)
        xc=cb*math.sin(alpha); yc=cb*math.cos(alpha); zc=math.sin(beta)
        rt=ctx.get('r'); ut=ctx.get('u'); ft=ctx.get('f')
        if rt is None or ut is None or ft is None:
            return True
        dx=xc*float(rt[0])+yc*float(ft[0])+zc*float(ut[0])
        dy=xc*float(rt[1])+yc*float(ft[1])+zc*float(ut[1])
        dz=xc*float(rt[2])+yc*float(ft[2])+zc*float(ut[2])
        rh=math.hypot(dx,dy)
        if not math.isfinite(rh) or rh<=1e-12:
            return True
        dist=dep*rh
        az=(math.degrees(math.atan2(dx,dy))+360.0)%360.0
        el=math.degrees(math.atan2(dz,rh))
        ai=_horizon_azimuth_index_fast_40192(horizon,az)
        if ai is None:
            return True
        env=None
        ec=horizon.get('el_cummax'); eb=horizon.get('el_bins')
        j=_horizon_distance_index_fast_40192(horizon,dist,exact=True)
        if j>=0 and ec is not None:
            try: env=float(ec[ai,j])
            except Exception: env=None
        elif j<0:
            return True
        if env is None and eb is not None:
            try: env=float(eb[ai])
            except Exception: env=None
        if env is None or not math.isfinite(env):
            return True
        return float(el)>=(float(env)-float(eps_deg))
    except Exception:
        return True


def _mask_panorama_zbuffer_by_horizon(rgba, depth_buf, depth_scale, ctx, horizon, eps_deg):
    
    if rgba is None or depth_buf is None or not horizon or ctx is None:
        return rgba
    try:
        proj=str(ctx.get('proj','') or '').upper()
        if proj not in ('EQUIRECT','EQUIRECTANGULAR','CYLINDRICAL'):
            return rgba
        depth=np.asarray(depth_buf)
        if depth.ndim!=2 or getattr(rgba,'ndim',0)!=3:
            return rgba
        W=max(1,int(ctx.get('width',rgba.shape[1]))); H=max(1,int(ctx.get('height',rgba.shape[0])))
        scale=max(1e-9,float(depth_scale))
        hf=math.radians(max(1e-6,float(ctx.get('HFOV',360.0))))
        vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
        full360=bool(ctx.get('is360',False))
        if proj=='CYLINDRICAL':
            vf=_validated_vfov_for_cylindrical(float(ctx.get('VFOV',90.0)),float(ctx.get('HFOV',360.0)),W,H)
            fy=(H*0.5)/max(1e-9,math.tan(vf*0.5))
        else:
            fy=None
        rt=ctx.get('r'); ut=ctx.get('u'); ft=ctx.get('f')
        if rt is None or ut is None or ft is None:
            return rgba
        rx,ry,rz=(float(rt[0]),float(rt[1]),float(rt[2]))
        ux,uy,uz=(float(ut[0]),float(ut[1]),float(ut[2]))
        fxw,fyw,fzw=(float(ft[0]),float(ft[1]),float(ft[2]))
        el_bins=horizon.get('el_bins')
        n_bins=int(horizon.get('n_bins') or getattr(el_bins,'size',0) or len(el_bins))
        if el_bins is None or n_bins<=0:
            return rgba
        eb=np.asarray(el_bins,dtype=np.float64)
        az_min=float(horizon.get('az_min',0.0)); az_max=float(horizon.get('az_max',az_min)); center=0.5*(az_min+az_max)
        dv0=horizon.get('d_values'); ec0=horizon.get('el_cummax')
        dv=np.asarray(dv0,dtype=np.float64) if dv0 is not None else None
        ec=np.asarray(ec0) if ec0 is not None else None
        nd=int(dv.size) if dv is not None else 0
        hh,ww=depth.shape; tile=256
        for y0 in range(0,hh,tile):
            y1=min(hh,y0+tile)
            for x0 in range(0,ww,tile):
                x1=min(ww,x0+tile); ds=depth[y0:y1,x0:x1]
                finite=np.isfinite(ds)&(ds>1e-9)
                if not np.any(finite):
                    continue
                yy,xx=np.nonzero(finite); dep=ds[yy,xx].astype(np.float64,copy=False)
                x=((xx.astype(np.float64)+x0)+0.5)/scale
                y=((yy.astype(np.float64)+y0)+0.5)/scale
                if proj in ('EQUIRECT','EQUIRECTANGULAR'):
                    if full360:
                        alpha=(x/float(W))*(2.0*math.pi)-math.pi
                        beta=math.pi*0.5-(y/float(H))*math.pi
                    else:
                        alpha=(x-float(W)*0.5)*(hf/float(W))
                        beta=(float(H)*0.5-y)*(vf/float(H))
                else:
                    if full360:
                        alpha=(x/float(W))*(2.0*math.pi)-math.pi
                    else:
                        alpha=(x-float(W)*0.5)*(hf/float(W))
                    beta=np.arctan((float(H)*0.5-y)/float(fy))
                cb=np.cos(beta)
                xc=cb*np.sin(alpha); yc=cb*np.cos(alpha); zc=np.sin(beta)
                dx=xc*rx+yc*fxw+zc*ux
                dy=xc*ry+yc*fyw+zc*uy
                dz=xc*rz+yc*fzw+zc*uz
                rh=np.hypot(dx,dy)
                valid=np.isfinite(rh)&(rh>1e-12)
                dist=dep*rh
                az=(np.degrees(np.arctan2(dx,dy))+360.0)%360.0
                el=np.degrees(np.arctan2(dz,rh))
                av=center+(((az-center+180.0)%360.0)-180.0)
                if az_max<=az_min or n_bins==1:
                    ai=np.zeros_like(xx,dtype=np.int32)
                else:
                    ai=np.rint(np.clip((av-az_min)/(az_max-az_min),0.0,1.0)*(n_bins-1)).astype(np.int32)
                env=eb[ai]
                if dv is not None and ec is not None and nd>0 and ec.ndim>=2:
                    lo=np.full(dist.shape,-1,dtype=np.int32); hi=np.full(dist.shape,nd,dtype=np.int32)
                    iterations=max(1,int(math.ceil(math.log(max(2,nd+1),2)))+1)
                    for _ in range(iterations):
                        active=(hi-lo)>1
                        if not np.any(active):
                            break
                        mid=((lo+hi)//2).astype(np.int32); mc=np.clip(mid,0,nd-1)
                        go=active&(dv[mc]<dist)
                        lo=np.where(go,mid,lo); hi=np.where(active&(~go),mid,hi)
                    hp=lo>=0
                    if np.any(hp):
                        e2=env.copy(); e2[hp]=ec[ai[hp],lo[hp]]; env=e2
                    env=np.where(hp,env,-np.inf)
                hidden=valid&np.isfinite(env)&(el<(env-float(eps_deg)))
                if not np.any(hidden):
                    continue
                hy=yy[hidden]; hx=xx[hidden]
                rgba[y0+hy,x0+hx,:]=0
                ds[hy,hx]=np.inf
        return rgba
    except Exception as exc:
        raise RuntimeError("PANORAMA terrain fragment mask failed") from exc


def _clip_zsegment_to_viewport(x0,y0,d0,x1,y1,d1,xmax,ymax):
    
    dx=x1-x0; dy=y1-y0; t0=0.0; t1=1.0
    for p,q in ((-dx,x0),(dx,xmax-x0),(-dy,y0),(dy,ymax-y0)):
        if abs(p)<=1e-15:
            if q<0.0: return None
            continue
        rr=q/p
        if p<0.0:
            if rr>t1: return None
            if rr>t0: t0=rr
        else:
            if rr<t0: return None
            if rr<t1: t1=rr
    if t1<t0: return None
    vals=(x0+dx*t0,y0+dy*t0,d0+(d1-d0)*t0,x0+dx*t1,y0+dy*t1,d0+(d1-d0)*t1)
    return vals if all(math.isfinite(v) for v in vals) else None



def _draw_panorama_ztested_segment_4019(self, painter, uv0, uv1, d0, d1, depth_buf, scale, terrain_test=None):
    
    try:
        x0,y0=float(uv0[0]),float(uv0[1]); x1,y1=float(uv1[0]),float(uv1[1])
        d0=float(d0); d1=float(d1)
    except Exception:
        return
    if not all(math.isfinite(v) for v in (x0,y0,x1,y1,d0,d1)):
        return
    try:
        h,w=depth_buf.shape
    except Exception:
        return
    sc=max(float(scale),1e-9)
    clipped=_clip_zsegment_to_viewport(x0,y0,d0,x1,y1,d1,max(0.0,(w-1)/sc),max(0.0,(h-1)/sc))
    if clipped is None:
        return
    x0,y0,d0,x1,y1,d1=clipped
    
    
    steps=max(2,int(math.ceil(max(abs(x1-x0),abs(y1-y0))*sc/1.0)))
    prev_pt=None; prev_vis=False
    for i in range(steps+1):
        t=float(i)/float(steps)
        x=x0+(x1-x0)*t; y=y0+(y1-y0)*t; d=d0+(d1-d0)*t
        sx=int(round(x*sc)); sy=int(round(y*sc)); vis=False
        if 0<=sx<w and 0<=sy<h:
            
            
            
            eps=max(0.75,min(4.0,0.75+abs(d)*0.00035))
            try:
                zc=float(depth_buf[sy,sx])
            except Exception:
                zc=math.inf
            if math.isfinite(zc) and zc>0.0:
                vis=(d<=zc+eps)
            else:
                
                
                
                
                xa=max(0,sx-1); xb=min(w,sx+2); ya=max(0,sy-1); yb=min(h,sy+2)
                zmin=math.inf
                try:
                    win=depth_buf[ya:yb,xa:xb]
                    for yy in range(win.shape[0]):
                        for xx in range(win.shape[1]):
                            zv=float(win[yy,xx])
                            if math.isfinite(zv) and zv>0.0 and zv<zmin:
                                zmin=zv
                except Exception:
                    zmin=math.inf
                vis=(not math.isfinite(zmin)) or (d<=zmin+eps)
        if vis and terrain_test is not None:
            try: vis=bool(terrain_test(x,y,d))
            except Exception: vis=True
        cur=(x,y)
        if vis and prev_vis and prev_pt is not None:
            self._safe_line(painter,prev_pt,cur)
        prev_pt=cur; prev_vis=vis


def _draw_panorama_uv_segments_ztested_4019(self, painter, uvs, depths, depth_buf, scale, wrap_width=0.0,
                                               world_xyz=None, terrain_horizon=None, terrain_cam=None, terrain_eps=0.0,
                                               terrain_ctx=None):
    
    try:
        W=float(wrap_width or 0.0)
    except Exception:
        W=0.0
    terrain_test=None
    if terrain_horizon is not None and terrain_ctx is not None:
        terrain_test=lambda x,y,d: _panorama_fragment_visible_by_horizon(
            terrain_ctx,terrain_horizon,float(terrain_eps),x,y,d
        )
    for pts,dep in _iter_wrapped_runs_with_depth(uvs,depths,wrap_width=(W if W>1.0 else None),min_len=2):
        n=min(len(pts),len(dep))
        for i in range(max(0,n-1)):
            _draw_panorama_ztested_segment_4019(
                self,painter,pts[i],pts[i+1],dep[i],dep[i+1],depth_buf,scale,
                terrain_test=terrain_test
            )


def _draw_ztested_segment(self, painter, uv0, uv1, d0, d1, depth_buf, scale, eps_depth=1.0, terrain_test=None):
    try:
        x0,y0=float(uv0[0]),float(uv0[1]); x1,y1=float(uv1[0]),float(uv1[1]); d0=float(d0); d1=float(d1)
    except Exception: return
    if not all(math.isfinite(v) for v in (x0,y0,x1,y1,d0,d1)): return
    h,w=depth_buf.shape; sc=max(float(scale),1e-9); clipped=_clip_zsegment_to_viewport(x0,y0,d0,x1,y1,d1,max(0.0,(w-1)/sc),max(0.0,(h-1)/sc))
    if clipped is None: return
    x0,y0,d0,x1,y1,d1=clipped; steps=max(2,int(math.ceil(max(abs(x1-x0),abs(y1-y0))*sc/6.0))); prev_pt=None; prev_vis=False
    for i in range(steps+1):
        t=float(i)/float(steps); x=x0+(x1-x0)*t; y=y0+(y1-y0)*t; d=d0+(d1-d0)*t; sx=int(round(x*sc)); sy=int(round(y*sc))
        if 0<=sx<w and 0<=sy<h:
            z=float(depth_buf[sy,sx]); vis=(not math.isfinite(z)) or (d<=z+float(eps_depth))
        else: vis=False
        if vis and terrain_test is not None:
            try: vis=bool(terrain_test(x,y,d))
            except Exception: vis=True
        cur=(x,y)
        if vis and prev_vis and prev_pt is not None: self._safe_line(painter,prev_pt,cur)
        prev_pt=cur; prev_vis=vis

def _draw_uv_segments_ztested(self, painter, uvs, depths, depth_buf, scale, terrain_test=None):
    n = min(len(uvs), len(depths))
    prev_uv = None
    prev_d = None
    for i in range(n):
        uv = uvs[i]
        d = depths[i]
        if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])) and math.isfinite(float(d)) and float(d) > 0:
            cur_uv = (float(uv[0]), float(uv[1]))
            cur_d = float(d)
            if prev_uv is not None and prev_d is not None:
                _draw_ztested_segment(self, painter, prev_uv, cur_uv, prev_d, cur_d, depth_buf, scale, terrain_test=terrain_test)
            prev_uv = cur_uv
            prev_d = cur_d
        else:
            prev_uv = None
            prev_d = None


def _sty_fill_color(sty, transparent_objects=False, alpha_override=None):
    fc = getattr(sty, 'fill_color', None) or getattr(sty, 'color', QColor(0,255,0,180))
    try:
        qspec = getattr(sty, 'qgis_fill_style', None)
        if bool(getattr(sty, 'use_qgis_style', True)) and qspec:
            fc = _dominant_fill_color_from_spec(qspec, fallback=fc)
    except Exception:
        pass
    out = QColor(fc)
    if alpha_override is not None:
        out.setAlpha(int(max(0, min(255, alpha_override))))
    elif transparent_objects:
        out.setAlpha(min(max(40, out.alpha()), 110))
    else:
        out.setAlpha(255)
    return out


def _paint_fill_spec_on_polygon(painter, poly, fill_spec):
    try:
        rect = poly.boundingRect().toAlignedRect()
    except Exception:
        rect = QRect()
    if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return
    try:
        dev = painter.device()
        dev_rect = QRect(0, 0, int(dev.width()), int(dev.height())) if dev is not None else QRect()
    except Exception:
        dev_rect = QRect()
    if not dev_rect.isNull():
        rect = rect.intersected(dev_rect)
    if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return
    area = int(rect.width()) * int(rect.height())
    if area > 2_000_000:
        fallback = _dominant_fill_color_from_spec(fill_spec, fallback=QColor(180, 180, 180, 255))
        try:
            if isinstance(fill_spec, dict):
                target_alpha = int(fill_spec.get('target_alpha', fallback.alpha()))
                if 'color' in fill_spec: target_alpha = int(QColor(fill_spec.get('color')).alpha())
                elif 'color1' in fill_spec: target_alpha = int(QColor(fill_spec.get('color1')).alpha())
                elif 'bg_color' in fill_spec: target_alpha = int(QColor(fill_spec.get('bg_color')).alpha())
                fallback.setAlpha(max(0, min(255, target_alpha)))
        except Exception:
            pass
        path = QPainterPath(); path.addPolygon(poly)
        painter.save(); painter.setClipPath(path); painter.fillPath(path, QBrush(fallback)); painter.restore()
        return
    xs = (np.arange(rect.width(), dtype=np.float64)[None, :] + float(rect.left()) + 0.5)
    ys = (np.arange(rect.height(), dtype=np.float64)[:, None] + float(rect.top()) + 0.5)
    XX = np.broadcast_to(xs, (rect.height(), rect.width()))
    YY = np.broadcast_to(ys, (rect.height(), rect.width()))
    cols = _sample_rgba_from_fill_spec(fill_spec, XX, YY, (rect.left(), rect.left() + rect.width() - 1, rect.top(), rect.top() + rect.height() - 1))
    img = _rgba_owned_array_to_qimage(cols)
    if img.isNull():
        return
    path = QPainterPath(); path.addPolygon(poly)
    painter.save(); painter.setClipPath(path); painter.drawImage(rect.topLeft(), img); painter.restore()

def _draw_filled_projected_polygon(self, painter, sty, uv_base, uv_top=None, transparent_objects=False):
    if not bool(getattr(sty, 'fill_polygons', True)):
        return
    top_fill = _normalized_fill_spec_for_sty(sty, transparent_objects=transparent_objects)
    edge_col = QColor(getattr(sty, 'color', getattr(sty, 'fill_color', QColor(0,255,0,255))))
    pen = _make_pen_for_style(edge_col, getattr(sty, 'width', 0.0), 1.0, getattr(sty, 'pen_style', QC.Qt_PenStyle_SolidLine), _style_opacity_factor(sty))
    wrap_width = _wrap_width_from_painter(self, painter)
    painter.save()
    
    if uv_top is not None and bool(getattr(sty, 'fill_walls', True)):
        wall_col = _wall_color_from_fill_spec(top_fill, fallback=getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255))), transparent_objects=transparent_objects)
        painter.setPen(QC.Qt_PenStyle_NoPen)
        painter.setBrush(QBrush(wall_col))
        for quad in _wall_quads_from_uvs(uv_base, uv_top, wrap_width=wrap_width):
            painter.drawPolygon(quad)
    painter.setPen(pen)
    painter.setBrush(QC.Qt_BrushStyle_NoBrush)
    src_uv = uv_top if uv_top is not None else uv_base
    for poly in _polygon_paths_from_uvs(src_uv, wrap_width=wrap_width):
        _paint_fill_spec_on_polygon(painter, poly, top_fill)
        painter.drawPolygon(poly)
    painter.restore()




def _pano_face_uv3_scalar(face):
    
    try:
        uv = face.uv if hasattr(face, 'uv') else face.get('uv')
        if uv is None or len(uv) != 3:
            return None
        out = []
        for i in range(3):
            x = float(uv[i][0]); y = float(uv[i][1])
            if not (math.isfinite(x) and math.isfinite(y)):
                return None
            out.append((x, y))
        return tuple(out)
    except Exception:
        return None


def _pano_face_mean_depth_scalar(face):
    try:
        dep = face.radial_depth if hasattr(face, 'radial_depth') else face.get('radial_depth', face.get('depths'))
        if dep is None or len(dep) != 3:
            return math.nan
        d0 = float(dep[0]); d1 = float(dep[1]); d2 = float(dep[2])
        if not (math.isfinite(d0) and math.isfinite(d1) and math.isfinite(d2)):
            return math.nan
        if d0 <= 0.0 or d1 <= 0.0 or d2 <= 0.0:
            return math.nan
        return (d0 + d1 + d2) / 3.0
    except Exception:
        return math.nan


def _pano_qpolygon_from_uv3(uv3):
    try:
        return QPolygonF([
            QPointF(uv3[0][0], uv3[0][1]),
            QPointF(uv3[1][0], uv3[1][1]),
            QPointF(uv3[2][0], uv3[2][1]),
            QPointF(uv3[0][0], uv3[0][1]),
        ])
    except Exception:
        return None


def _paint_fill_spec_on_panorama_faces(painter, faces, fill_spec):
    
    polys = []
    path = QPainterPath()
    for face in faces or ():
        uv3 = _pano_face_uv3_scalar(face)
        if uv3 is None:
            continue
        poly = _pano_qpolygon_from_uv3(uv3)
        if poly is None:
            continue
        polys.append(poly); path.addPolygon(poly)
    if not polys:
        return
    kind = str((fill_spec or {}).get('kind', 'simple') or 'simple').lower()
    if kind == 'simple':
        col = QColor((fill_spec or {}).get('color', QColor(180,180,180,255)))
        try:
            ta = int((fill_spec or {}).get('target_alpha', col.alpha()))
            col.setAlpha(max(0, min(255, ta)))
        except Exception:
            pass
        painter.fillPath(path, QBrush(col))
        return
    try:
        dev = painter.device()
        dw = int(dev.width()) if dev is not None else 0
        dh = int(dev.height()) if dev is not None else 0
        dev_rect = QRect(0, 0, dw, dh) if dw > 0 and dh > 0 else QRect()
        common_bbox = (0.0, float(max(0, dw - 1)), 0.0, float(max(0, dh - 1)))
    except Exception:
        dev_rect = QRect(); common_bbox = (0.0, 1.0, 0.0, 1.0)
    for poly in polys:
        try:
            rect = poly.boundingRect().toAlignedRect()
            if not dev_rect.isNull():
                rect = rect.intersected(dev_rect)
        except Exception:
            continue
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            continue
        area = int(rect.width()) * int(rect.height())
        if area > 1_000_000:
            fallback = _dominant_fill_color_from_spec(fill_spec, fallback=QColor(180,180,180,255))
            tri_path = QPainterPath(); tri_path.addPolygon(poly)
            painter.fillPath(tri_path, QBrush(fallback))
            continue
        xs = np.arange(rect.width(), dtype=np.float64)[None, :] + float(rect.left()) + 0.5
        ys = np.arange(rect.height(), dtype=np.float64)[:, None] + float(rect.top()) + 0.5
        XX = np.broadcast_to(xs, (rect.height(), rect.width()))
        YY = np.broadcast_to(ys, (rect.height(), rect.width()))
        cols = _sample_rgba_from_fill_spec(fill_spec, XX, YY, common_bbox)
        img = _rgba_owned_array_to_qimage(cols)
        if img.isNull():
            continue
        tri_path = QPainterPath(); tri_path.addPolygon(poly)
        painter.save(); painter.setClipPath(tri_path); painter.drawImage(rect.topLeft(), img); painter.restore()


def _fill_panorama_wall_faces(painter, faces, color):
    
    brush = QBrush(QColor(color))
    painter.setBrush(brush)
    for face in faces or ():
        uv3 = _pano_face_uv3_scalar(face)
        if uv3 is None:
            continue
        poly = _pano_qpolygon_from_uv3(uv3)
        if poly is not None:
            painter.drawPolygon(poly)

def _draw_panorama_polygon_faces(self, painter, sty, surface_faces, wall_faces=None, transparent_objects=False):
    
    if not bool(getattr(sty, 'fill_polygons', True)):
        return
    top_fill = _normalized_fill_spec_for_sty(sty, transparent_objects=transparent_objects)
    wall_enabled = bool(wall_faces and bool(getattr(sty, 'fill_walls', True)))
    wall_col = None
    if wall_enabled:
        wall_col = _wall_color_from_fill_spec(
            top_fill,
            fallback=getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255))),
            transparent_objects=transparent_objects
        )

    queue = []
    for face in surface_faces or ():
        d = _pano_face_mean_depth_scalar(face)
        if math.isfinite(d):
            queue.append((d, 1, face))  
    if wall_enabled:
        for face in wall_faces or ():
            d = _pano_face_mean_depth_scalar(face)
            if math.isfinite(d):
                queue.append((d, 0, face))  
    if not queue:
        return
    
    
    queue.sort(key=lambda item: item[0], reverse=True)

    kind = str((top_fill or {}).get('kind', 'simple') or 'simple').lower()
    top_col = None
    if kind == 'simple':
        top_col = QColor((top_fill or {}).get('color', QColor(180,180,180,255)))
        try:
            ta = int((top_fill or {}).get('target_alpha', top_col.alpha()))
            top_col.setAlpha(max(0, min(255, ta)))
        except Exception:
            pass

    painter.save()
    painter.setPen(QC.Qt_PenStyle_NoPen)
    for _depth, role_code, face in queue:
        if role_code == 0:
            uv3 = _pano_face_uv3_scalar(face)
            if uv3 is None:
                continue
            poly = _pano_qpolygon_from_uv3(uv3)
            if poly is None:
                continue
            painter.setBrush(QBrush(QColor(wall_col)))
            painter.drawPolygon(poly)
        elif kind == 'simple':
            uv3 = _pano_face_uv3_scalar(face)
            if uv3 is None:
                continue
            poly = _pano_qpolygon_from_uv3(uv3)
            if poly is None:
                continue
            painter.setBrush(QBrush(QColor(top_col)))
            painter.drawPolygon(poly)
        else:
            
            
            _paint_fill_spec_on_panorama_faces(painter, (face,), top_fill)
    painter.restore()



def _panorama_feature_parts_cached(self, layer, feat, gtype, tr, fast_preview=False, simplify_geometry=False):
    
    try:
        cache=getattr(self,'_panorama_feature_parts_cache',None)
        if not isinstance(cache,dict): cache={}; self._panorama_feature_parts_cache=cache
        lid=layer.id() if layer is not None else ''
        rev=int(getattr(self,'_layer_cache_versions',{}).get(lid,0)); fid=int(feat.id())
        crskey=''
        try: crskey=layer.crs().authid()
        except Exception: pass
        dstkey=''
        try: dstkey=tr.destinationCrs().authid() if tr is not None else crskey
        except Exception: dstkey=crskey
        key=(lid,rev,fid,int(gtype),crskey,dstkey,bool(fast_preview),bool(simplify_geometry))
        hit=cache.get(key)
        if hit is not None: return hit
        geom=feat.geometry(); parts=[]
        if gtype==QC.QgsWkbTypes_GeometryType_LineGeometry:
            lines=geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for line in lines:
                if not line: continue
                if simplify_geometry: step=max(1,int(len(line)/(60 if fast_preview else 120)))
                else: step=1 if not fast_preview else max(1,int(len(line)/120))
                pts=[pt for i,pt in enumerate(line) if (i%step)==0]
                arr=_transform_points_array(pts,tr)
                if arr.shape[0]>=2: parts.append(arr)
        elif gtype==QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            polys=geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                if not poly or not poly[0]: continue
                ring=poly[0]
                if simplify_geometry: step=max(1,int(len(ring)/(60 if fast_preview else 120)))
                else: step=1 if not fast_preview else max(1,int(len(ring)/120))
                pts=[pt for i,pt in enumerate(ring) if (i%step)==0]
                arr=_transform_points_array(pts,tr)
                if arr.shape[0]>=3: parts.append(arr)
        cache[key]=tuple(parts)
        if len(cache)>12000:
            for k in list(cache.keys())[:3000]: cache.pop(k,None)
        return cache[key]
    except Exception:
        return ()

def _panorama_tri_indices_cached(self, layer, feat, part_index, arr_ring):
    
    try:
        cache=getattr(self,'_panorama_mesh_cache',None)
        if not isinstance(cache,dict):
            cache={}; self._panorama_mesh_cache=cache
        lid=layer.id() if layer is not None else ''
        rev=int(getattr(self,'_layer_cache_versions',{}).get(lid,0))
        fid=int(feat.id()) if feat is not None else -1
        a=np.asarray(arr_ring,dtype=np.float64)
        n=int(a.shape[0])
        if n<3: return []
        
        
        key=(lid,rev,fid,int(part_index),n)
        hit=cache.get(key)
        if hit is not None: return hit
        tri=tuple(tuple(int(v) for v in t) for t in (_earclip_triangulation_indices(a) or ()))
        cache[key]=tri
        if len(cache)>16000:
            for k in list(cache.keys())[:4000]: cache.pop(k,None)
        return tri
    except Exception:
        return _earclip_triangulation_indices(arr_ring) or []

def _clean_world_polygon_ring(arr_xy, z_vals, eps=1e-7):
    
    arr = np.asarray(arr_xy, dtype=np.float64)
    z = np.asarray(z_vals, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2 or z.ndim != 1 or len(arr) != len(z):
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    pts = []; zz = []
    for i in range(len(arr)):
        x, y, zv = float(arr[i,0]), float(arr[i,1]), float(z[i])
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(zv)):
            continue
        if pts and math.hypot(x - pts[-1][0], y - pts[-1][1]) <= eps:
            continue
        pts.append((x,y)); zz.append(zv)
    if len(pts) >= 2 and math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) <= eps:
        pts.pop(); zz.pop()
    if len(pts) < 3:
        return np.empty((0, 2), dtype=np.float64), np.empty((0,), dtype=np.float64)
    return np.asarray(pts, dtype=np.float64), np.asarray(zz, dtype=np.float64)


def _closed_uv_ring_for_outline(uv):
    arr = np.asarray(uv, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] != 2:
        return arr
    try:
        return np.vstack([arr, arr[0:1]])
    except Exception:
        return arr


def _draw_line_walls(self, painter, sty, uv_base, uv_top, transparent_objects=False):
    if uv_top is None:
        return
    top_fill = _normalized_fill_spec_for_sty(sty, transparent_objects=transparent_objects)
    wall_col = _wall_color_from_fill_spec(top_fill, fallback=getattr(sty, 'fill_color', getattr(sty, 'color', QColor(180,180,180,255))), transparent_objects=transparent_objects)
    wrap_width = _wrap_width_from_painter(self, painter)
    painter.save()
    painter.setPen(QC.Qt_PenStyle_NoPen)
    painter.setBrush(QBrush(wall_col))
    for quad in _wall_quads_from_uvs(uv_base, uv_top, wrap_width=wrap_width):
        painter.drawPolygon(quad)
    painter.restore()


def _draw_uv_segments(self, painter, uvs):
    
    wrap_width = _wrap_width_from_painter(self, painter)
    if not wrap_width:
        for a, b in _iter_uv_segments(uvs):
            self._safe_line(painter, a, b)
        return
    for pts in _uv_runs_array(uvs, min_len=2):
        for run in _iter_wrap_shifted_pts(pts, wrap_width):
            for i in range(run.shape[0] - 1):
                self._safe_line(
                    painter,
                    (float(run[i, 0]), float(run[i, 1])),
                    (float(run[i + 1, 0]), float(run[i + 1, 1]))
                )


def _sample_z_array(z_sampler, arr_xy):
    if z_sampler is None:
        return np.zeros(arr_xy.shape[0], dtype=np.float64)
    batch = getattr(z_sampler, 'batch', None)
    if callable(batch):
        try:
            return np.asarray(batch(arr_xy), dtype=np.float64)
        except Exception:
            pass
    out = np.zeros(arr_xy.shape[0], dtype=np.float64)
    for i in range(arr_xy.shape[0]):
        out[i] = float(z_sampler(QgsPointXY(float(arr_xy[i, 0]), float(arr_xy[i, 1]))))
    return out


def _effective_base_z_array(self, arr_xy, z_sampler, geometry_kind='polygon', force_horizontal=False):
    
    arr_xy = np.asarray(arr_xy, dtype=np.float64)
    raw = _sample_z_array(z_sampler, arr_xy)
    if raw.ndim == 0:
        raw = np.full(arr_xy.shape[0], float(raw), dtype=np.float64)
    if raw.shape[0] != arr_xy.shape[0]:
        raw = np.resize(raw, arr_xy.shape[0]).astype(np.float64, copy=False)
    finite = np.isfinite(raw)
    _sum_z = 0.0; _count_z = 0
    for _q in range(int(raw.shape[0])):
        try:
            _zq = float(raw[_q])
        except Exception:
            continue
        if math.isfinite(_zq):
            _sum_z += _zq; _count_z += 1
    mean_z = (_sum_z / float(_count_z)) if _count_z > 0 else 0.0
    safe_raw = raw.astype(np.float64, copy=True)
    safe_raw[~finite] = mean_z
    kind = str(geometry_kind or '').lower()
    if kind == 'polygon' and bool(force_horizontal):
        return np.full(arr_xy.shape[0], mean_z, dtype=np.float64), mean_z, raw
    
    
    return safe_raw, mean_z, raw



_SCHEMATIC_SVG_TEXTURE_CACHE = {}


def _schematic_svg_texture(primitive, target_w_px=256, target_h_px=None):
    
    target_w_px = max(16, min(1536, int(round(target_w_px))))
    if target_h_px is None:
        target_h_px = target_w_px
    target_h_px = max(16, min(1536, int(round(target_h_px))))
    path = str(getattr(primitive, 'svg_path', '') or '')
    fallback = str((getattr(primitive, 'metadata', {}) or {}).get('fallback', 'generic') or 'generic').lower()
    try:
        mtime = os.path.getmtime(path) if path and os.path.isfile(path) else None
    except Exception:
        mtime = None
    key = (path, mtime, fallback, target_w_px, target_h_px, 'svg-safe-pad-preload-3.5pct-v2')
    cached = _SCHEMATIC_SVG_TEXTURE_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached

    
    img = QImage(target_w_px, target_h_px, QC.QImage_Format_Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    ok = False
    if path and os.path.isfile(path):
        try:
            if path.lower().endswith('.png'):
                src = QImage(path)
                if not src.isNull():
                    scaled = src.scaled(
                        target_w_px, target_h_px,
                        QC.Qt_AspectRatioMode_KeepAspectRatio,
                        QC.Qt_TransformationMode_SmoothTransformation,
                    )
                    qp = QPainter(img)
                    try:
                        qp.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
                        qp.setRenderHint(QC.QPainter_RenderHint_SmoothPixmapTransform, True)
                        x = 0.5 * (target_w_px - scaled.width())
                        y = float(target_h_px - scaled.height())
                        qp.drawImage(QPointF(float(x), float(y)), scaled)
                        ok = True
                    finally:
                        qp.end()
            elif QSvgRenderer is not None:
                renderer = _safe_svg_renderer(path)
                if renderer is not None and renderer.isValid():
                    qp = QPainter(img)
                    try:
                        qp.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
                        qp.setRenderHint(QC.QPainter_RenderHint_SmoothPixmapTransform, True)
                        
                        
                        
                        guard = max(1.0, min(3.0, 0.015 * float(min(target_w_px, target_h_px))))
                        bounds = QRectF(
                            guard, guard,
                            max(1.0, float(target_w_px) - 2.0 * guard),
                            max(1.0, float(target_h_px) - 2.0 * guard),
                        )
                        renderer.render(qp, bounds)
                        ok = True
                    finally:
                        qp.end()
        except Exception:
            ok = False

    if not ok:
        
        
        
        qp = QPainter(img)
        try:
            qp.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
            col = QColor(78, 115, 82, 255)
            line = QColor(48, 70, 50, 255)
            qp.setPen(QPen(line, max(1.0, min(target_w_px, target_h_px) / 100.0)))
            qp.setBrush(QBrush(col))
            w = float(target_w_px); h = float(target_h_px)
            r = QRectF(1.0, 1.0, max(1.0, w - 2.0), max(1.0, h - 2.0))
            if fallback in ('tree', 'tree_deciduous'):
                qp.drawEllipse(QRectF(w*0.10, h*0.02, w*0.80, h*0.72))
                qp.drawRect(QRectF(w*0.46, h*0.58, w*0.10, h*0.40))
            elif fallback in ('conifer', 'tree_conifer'):
                poly = QPolygonF([
                    QPointF(w*0.50, h*0.02),
                    QPointF(w*0.92, h*0.82),
                    QPointF(w*0.08, h*0.82),
                ])
                qp.drawPolygon(poly)
                qp.drawRect(QRectF(w*0.46, h*0.76, w*0.08, h*0.22))
            elif fallback in ('animal', 'vehicle'):
                qp.drawRoundedRect(QRectF(w*0.05, h*0.35, w*0.72, h*0.42), 8.0, 8.0)
                qp.drawEllipse(QRectF(w*0.72, h*0.28, w*0.22, h*0.26))
            else:
                qp.drawRoundedRect(r, 8.0, 8.0)
        finally:
            qp.end()

    _SCHEMATIC_SVG_TEXTURE_CACHE[key] = img
    
    if len(_SCHEMATIC_SVG_TEXTURE_CACHE) > 128:
        for k in list(_SCHEMATIC_SVG_TEXTURE_CACHE.keys())[:40]:
            _SCHEMATIC_SVG_TEXTURE_CACHE.pop(k, None)
    return img


def _panorama_face_fill_dict_419(face, fill_spec, pattern_bbox=None):
    try:
        uv=tuple((float(face.uv[i][0]),float(face.uv[i][1])) for i in range(3))
        dep=tuple(float(face.radial_depth[i]) for i in range(3))
        if not all(math.isfinite(v) for pt in uv for v in pt): return None
        if not all(math.isfinite(v) and v>0.0 for v in dep): return None
        out={'uv':uv,'depths':dep,'fill_spec':fill_spec}
        if pattern_bbox is not None: out['pattern_bbox']=tuple(float(v) for v in pattern_bbox)
        elif fill_spec and str(fill_spec.get('kind','simple')).lower()!='simple':
            out['pattern_bbox']=(min(p[0] for p in uv),max(p[0] for p in uv),min(p[1] for p in uv),max(p[1] for p in uv))
        if getattr(face,'texture_uv',None) is not None:
            try: out['texture_uv']=tuple((float(face.texture_uv[i][0]),float(face.texture_uv[i][1])) for i in range(3))
            except Exception: pass
        return out
    except Exception:
        return None


def _append_panorama_faces_for_zbuffer_419(dst, faces, fill_spec, terrain_culler=None):
    
    added=0
    for face in faces or ():
        if terrain_culler is not None:
            try:
                if not bool(terrain_culler(face)):
                    continue
            except Exception:
                pass
        item=_panorama_face_fill_dict_419(face,fill_spec)
        if item is not None:
            dst.append(item); added+=1
    return added


def _panorama_wall_fill_spec_419(sty, top_fill, transparent_objects=False):
    wall_col=_wall_color_from_fill_spec(
        top_fill,
        fallback=getattr(sty,'fill_color',getattr(sty,'color',QColor(180,180,180,255))),
        transparent_objects=transparent_objects
    )
    c=QColor(wall_col)
    return {'kind':'simple','color':c,'target_alpha':c.alpha(),'depth_alpha_threshold':1}



def _draw_panorama_zfaces_fallback_419(painter, faces):
    
    queue=[]
    for f in faces or ():
        try:
            uv=f.get('uv',()); dep=f.get('depths',()); d=sum(float(v) for v in dep)/3.0
            if len(uv)!=3 or not math.isfinite(d): continue
            queue.append((d,f))
        except Exception: continue
    queue.sort(key=lambda it:it[0],reverse=True)
    painter.save(); painter.setPen(QC.Qt_PenStyle_NoPen)
    try:
        for _d,f in queue:
            try:
                pts=f['uv']; spec=f.get('fill_spec') or {}; col=QColor(spec.get('color',QColor(180,180,180,180)))
                if spec.get('texture_img') is not None:
                    col=_dominant_fill_color_from_spec(spec,fallback=QColor(90,130,90,180))
                poly=QPolygonF([QPointF(float(pts[i][0]),float(pts[i][1])) for i in range(3)])
                painter.setBrush(QBrush(col)); painter.drawPolygon(poly)
            except Exception: continue
    finally:
        painter.restore()

def _panorama_zbuffer_scale_for_preview_419(self,width,height,has_texture=False):
    
    pixels=max(1,int(width))*max(1,int(height))
    q=str(getattr(self,'_current_render_quality','high') or 'high').lower()
    preview=bool(getattr(self,'_memory_guard_in_preview',False))
    if preview:
        target=3_000_000 if q=='low' else 7_000_000 if q=='normal' else 24_000_000
        if has_texture and q=='high': target=26_000_000
    else:
        
        
        
        target=56_000_000 if has_texture else 72_000_000
    try:
        st=getattr(self,'_memory_guard_runtime',None)
        if preview and isinstance(st,dict) and bool(st.get('safe_mode',False)):
            target=min(target,4_000_000 if str(st.get('level'))=='dangerous' else 8_000_000)
    except Exception:
        pass
    if pixels<=target: return 1.0
    return max(0.20,min(1.0,math.sqrt(float(target)/float(pixels))))


def _schematic_texture_size_419(pr, ctx, camera_xy, quality='high'):
    try:
        cx,cy=float(camera_xy[0]),float(camera_xy[1])
        dx=float(pr.x)-cx; dy=float(pr.y)-cy; dz=float(pr.z)-float(ctx['cam_z'])
        d=max(0.5,math.sqrt(dx*dx+dy*dy+dz*dz))
        hf=math.radians(max(1e-6,float(ctx.get('HFOV',360.0)))); vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
        ppr=max(float(ctx['width'])/max(1e-9,hf),float(ctx['height'])/max(1e-9,vf))
        hp=max(8.0,float(pr.height)/d*ppr); wp=max(8.0,float(pr.width)/d*ppr)
        mul=1.0 if str(quality).lower()=='low' else 1.2 if str(quality).lower()=='normal' else 1.45
        def bucket(v):
            need=max(24.0,min(768.0,float(v)*mul))
            for b in (32,48,64,96,128,192,256,384,512,768):
                if need<=b: return b
            return 768
        return bucket(wp),bucket(hp)
    except Exception:
        return 128,192


def _append_schematic_primitives_for_panorama_zbuffer_419(faces, primitives, style, definition,
                                                            ctx, maxdist, camera_xy, width,
                                                            render_quality='high', transparent_objects=False,
                                                            extra_budget=None, visibility_test=None,
                                                            painter=None, terrain_face_culler=None,
                                                            deferred_edges=None):
    
    if not primitives: return False
    handled=False; W=float(width) if bool(ctx.get('is360',False)) else 0.0
    appearance=(definition or {}).get('appearance',{}) or {}; lod=(definition or {}).get('lod',{}) or {}
    simple_min=float(lod.get('simple_min_px',2.0) or 2.0)
    for pr in primitives:
        if isinstance(pr,Billboard3D):
            try: xyz=billboard_world_quad(pr,camera_xy)
            except Exception: continue
            if visibility_test is not None:
                try:
                    if not any(bool(visibility_test(float(r[0]),float(r[1]),float(r[2]))) for r in xyz): handled=True; continue
                except Exception: pass
            
            try:
                dx=float(pr.x)-float(camera_xy[0]); dy=float(pr.y)-float(camera_xy[1]); dz=float(pr.z)-float(ctx['cam_z'])
                dd=max(0.5,math.sqrt(dx*dx+dy*dy+dz*dz)); vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
                ppr=float(ctx['height'])/max(1e-9,vf); est=max(float(pr.height),float(pr.width))/dd*ppr
                if est<simple_min: handled=True; continue
            except Exception: pass
            texuv=np.asarray(((0.0,1.0),(1.0,1.0),(1.0,0.0),(0.0,0.0)),dtype=np.float64)
            pf=panorama_faces_from_world_mesh(
                ctx,np.asarray(xyz,dtype=np.float64),((0,1,2),(0,2,3)),maxdist,texture_uv=texuv,
                wrap_width=(W if W>1.0 else None),role=pr.role,
                metadata={'projection_family':'PANORAMA','schematic':True},render_quality=render_quality,
                extra_face_budget=extra_budget,pole_guard_px=2.5
            )
            if not pf: handled=True; continue
            tw,th=_schematic_texture_size_419(pr,ctx,camera_xy,quality=render_quality)
            teximg=_schematic_svg_texture(pr,tw,th)
            fill,_line=schematic_role_colors(style,definition,pr.role)
            try: nominal=int(appearance.get('fill_alpha',255)) if 'fill_alpha' in appearance else int(QColor(fill).alpha())
            except Exception: nominal=255
            target=max(0,min(255,int(round(float(nominal)*_style_opacity_factor(style)))))
            if transparent_objects: target=min(target,110)
            spec={'kind':'simple','texture_img':teximg,'texture_mode':'stretch','preserve_texture_alpha':True,
                  'target_alpha':target,'depth_alpha_threshold':10,'depth_uses_intrinsic_alpha':True,
                  'schematic_billboard_texture':True}
            _append_panorama_faces_for_zbuffer_419(faces,pf,spec,terrain_culler=terrain_face_culler); handled=True; continue

        if isinstance(pr,Polygon3D):
            try: xyz=np.asarray(pr.xyz,dtype=np.float64)
            except Exception: continue
            if xyz.ndim!=2 or xyz.shape[0]<3: continue
            if visibility_test is not None:
                try:
                    if not any(bool(visibility_test(float(r[0]),float(r[1]),float(r[2]))) for r in xyz): handled=True; continue
                except Exception: pass
            tri=_earclip_triangulation_indices(xyz[:,:2]) or [(0,i,i+1) for i in range(1,xyz.shape[0]-1)]
            pf=panorama_faces_from_world_mesh(
                ctx,xyz,tri,maxdist,wrap_width=(W if W>1.0 else None),role=pr.role,
                metadata={'projection_family':'PANORAMA','schematic':True},render_quality=render_quality,
                extra_face_budget=extra_budget,pole_guard_px=2.5
            )
            fill,line=schematic_role_colors(style,definition,pr.role); fill=QColor(fill); line=QColor(line)
            if transparent_objects: fill.setAlpha(min(fill.alpha(),110)); line.setAlpha(min(line.alpha(),110))
            do_fill=bool(pr.fill and getattr(style,'fill_polygons',True))
            if pr.role=='wall' and not bool(getattr(style,'fill_walls',True)): do_fill=False
            if do_fill: _append_panorama_faces_for_zbuffer_419(faces,pf,{'kind':'simple','color':fill,'target_alpha':fill.alpha(),'depth_alpha_threshold':1},terrain_culler=terrain_face_culler)
            if bool(getattr(pr,'outline',False)):
                path=project_panorama_path_safe(ctx,xyz,maxdist,closed=True,render_quality=render_quality,wrap_width=W,max_points=900,pole_guard_px=2.5)
                pen=QPen(line); pen.setWidthF(max(0.6,float(getattr(style,'width',1.0) or 1.0)))
                if deferred_edges is not None and path is not None:
                    try:
                        if len(path.uv)>=2 and len(path.radial_depth)>=2:
                            deferred_edges.append((QPen(pen),path.uv,path.radial_depth,getattr(path,'world_xyz',None),float(W or 0.0)))
                    except Exception:
                        pass
                elif painter is not None:
                    painter.setPen(pen)
                    for run in _uv_runs_array(path.uv,min_len=2):
                        for rr in (_iter_wrap_shifted_pts(run,W) if W>1.0 else (run,)):
                            for ii in range(len(rr)-1):
                                try: painter.drawLine(QPointF(float(rr[ii,0]),float(rr[ii,1])),QPointF(float(rr[ii+1,0]),float(rr[ii+1,1])))
                                except Exception: pass
            handled=True; continue

        if isinstance(pr,Polyline3D):
            try: xyz=np.asarray(pr.xyz,dtype=np.float64)
            except Exception: continue
            if xyz.ndim!=2 or xyz.shape[0]<2: continue
            path=project_panorama_path_safe(ctx,xyz,maxdist,closed=False,render_quality=render_quality,wrap_width=W,max_points=1200,pole_guard_px=2.5)
            _fill,line=schematic_role_colors(style,definition,pr.role); pen=QPen(line); pen.setWidthF(max(0.7,float(getattr(style,'width',1.0) or 1.0)))
            if deferred_edges is not None and path is not None:
                try:
                    if len(path.uv)>=2 and len(path.radial_depth)>=2:
                        deferred_edges.append((QPen(pen),path.uv,path.radial_depth,getattr(path,'world_xyz',None),float(W or 0.0)))
                except Exception:
                    pass
            elif painter is not None:
                painter.setPen(pen)
                for run in _uv_runs_array(path.uv,min_len=2):
                    for rr in (_iter_wrap_shifted_pts(run,W) if W>1.0 else (run,)):
                        for ii in range(len(rr)-1):
                            try: painter.drawLine(QPointF(float(rr[ii,0]),float(rr[ii,1])),QPointF(float(rr[ii+1,0]),float(rr[ii+1,1])))
                            except Exception: pass
            handled=True
    return handled

def _append_schematic_primitives_for_zbuffer(faces, deferred_edges, primitives, style, definition,
                                              ctx, maxdist, camera_xy, width, fast_preview=False,
                                              transparent_objects=False):
    
    if not primitives:
        return False
    handled = False
    wrap_width = None  
    appearance = (definition or {}).get('appearance', {}) or {}
    lod = (definition or {}).get('lod', {}) or {}
    simple_min = float(lod.get('simple_min_px', 2.0) or 2.0)
    billboard_specs = {}

    def _lod_visible(uv):
        try:
            xs = []; ys = []
            for i in range(len(uv)):
                x, y = float(uv[i][0]), float(uv[i][1])
                if math.isfinite(x) and math.isfinite(y):
                    xs.append(x); ys.append(y)
            if not xs:
                return False
            return max(max(xs) - min(xs), max(ys) - min(ys)) >= simple_min
        except Exception:
            return False

    for pr in primitives:
        if isinstance(pr, Billboard3D):
            xyz = billboard_world_quad(pr, camera_xy)
            uv, dep = _project_uv_depth_batch(ctx, xyz[:, :2], xyz[:, 2], dist_max=maxdist)
            if not _lod_visible(uv):
                continue
            try:
                uv_vals = [(float(uv[i][0]), float(uv[i][1])) for i in range(len(uv))]
                dep_vals = [float(dep[i]) for i in range(len(dep))]
            except Exception:
                continue
            if not uv_vals or not all(math.isfinite(v) for p in uv_vals for v in p) or not all(math.isfinite(v) for v in dep_vals):
                continue
            left, right = min(p[0] for p in uv_vals), max(p[0] for p in uv_vals)
            top, bottom = min(p[1] for p in uv_vals), max(p[1] for p in uv_vals)
            if right <= left or bottom <= top:
                continue
            
            
            
            bw = max(1.0, float(right - left))
            bh = max(1.0, float(bottom - top))
            tex_quality = 1.35 if fast_preview else 1.75

            def _texture_bucket(px):
                
                
                
                need = max(24.0, min(1536.0, float(px)))
                for bucket in (32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536):
                    if need <= bucket:
                        return bucket
                return 1536

            tex_w = _texture_bucket(bw * tex_quality)
            tex_h = _texture_bucket(bh * tex_quality)
            tex = _schematic_svg_texture(pr, tex_w, tex_h)
            fill, _line = schematic_role_colors(style, definition, pr.role)
            
            
            
            
            
            
            
            try:
                if 'fill_alpha' in appearance:
                    nominal_alpha = int(appearance.get('fill_alpha', 255))
                else:
                    nominal_alpha = int(QColor(getattr(style, 'fill_color', QColor(255, 255, 255, 255))).alpha())
            except Exception:
                nominal_alpha = 255
            target_alpha = max(0, min(255, int(round(float(nominal_alpha) * _style_opacity_factor(style)))))
            if transparent_objects:
                target_alpha = min(target_alpha, 110)
            spec_key = (str(getattr(pr, 'svg_path', '') or ''), str((getattr(pr, 'metadata', {}) or {}).get('fallback', '')), int(tex_w), int(tex_h), int(target_alpha))
            spec = billboard_specs.get(spec_key)
            if spec is None:
                spec = {
                    'kind': 'simple',
                    'texture_img': tex,
                    'texture_mode': 'stretch',
                    'preserve_texture_alpha': True,
                    'target_alpha': int(target_alpha),
                    
                    'depth_alpha_threshold': 10,
                    'depth_uses_intrinsic_alpha': True,
                    
                    
                    'schematic_billboard_texture': True,
                }
                billboard_specs[spec_key] = spec
            bbox = (left, right, top, bottom)
            
            
            quad_texture_uv = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
            
            
            
            
            for tri_idx in ((0,1,2),(0,2,3)):
                tri = np.asarray([uv[i] for i in tri_idx], dtype=np.float64)
                dtri = np.asarray([dep[i] for i in tri_idx], dtype=np.float64)
                ttri = tuple(quad_texture_uv[i] for i in tri_idx)
                faces.append({
                    'uv': tri,
                    'depths': dtri,
                    'fill_spec': spec,
                    'pattern_bbox': bbox,
                    'texture_uv': ttri,
                })
            handled = True
            continue

        if isinstance(pr, Polygon3D):
            xyz = np.asarray(pr.xyz, dtype=np.float64)
            if xyz.ndim != 2 or xyz.shape[0] < 3:
                continue
            uv, dep = _project_uv_depth_batch(ctx, xyz[:, :2], xyz[:, 2], dist_max=maxdist)
            if not _lod_visible(uv):
                continue
            fill, line = schematic_role_colors(style, definition, pr.role)
            fill = QColor(fill); line = QColor(line)
            if transparent_objects:
                fill.setAlpha(min(fill.alpha(), 110))
                line.setAlpha(min(line.alpha(), 110))
            do_fill = bool(pr.fill and getattr(style, 'fill_polygons', True))
            if pr.role == 'wall' and not bool(getattr(style, 'fill_walls', True)):
                do_fill = False
            if do_fill:
                
                
                spec = {'kind': 'simple', 'color': fill, 'target_alpha': 255, 'depth_alpha_threshold': 1}
                for pts, dps in _iter_wrapped_runs_with_depth(uv, dep, wrap_width=wrap_width, min_len=3):
                    bbox = _finite_bbox_xy_scalar(pts)
                    if bbox is None:
                        continue
                    faces.extend(_triangulate_polygon_run(pts, dps, spec, pattern_bbox=bbox))
            if pr.outline:
                closed_uv = np.vstack([uv, uv[0]]) if not _xy_points_close(uv[0], uv[-1]) else uv
                closed_dep = np.concatenate([dep, dep[:1]]) if closed_uv.shape[0] == uv.shape[0] + 1 else dep
                pen = QPen(line); pen.setWidthF(max(0.6, float(getattr(style, 'width', 1.0) or 1.0)))
                deferred_edges.append((pen, closed_uv, closed_dep))
            handled = True
            continue

        if isinstance(pr, Polyline3D):
            xyz = np.asarray(pr.xyz, dtype=np.float64)
            if xyz.ndim != 2 or xyz.shape[0] < 2:
                continue
            uv, dep = _project_uv_depth_batch(ctx, xyz[:, :2], xyz[:, 2], dist_max=maxdist)
            if not _lod_visible(uv):
                continue
            _fill, line = schematic_role_colors(style, definition, pr.role)
            line = QColor(line)
            if transparent_objects:
                line.setAlpha(min(line.alpha(), 110))
            pen = QPen(line); pen.setWidthF(max(0.7, float(getattr(style, 'width', 1.0) or 1.0)))
            deferred_edges.append((pen, uv, dep))
            handled = True
    return handled


def _render_vector_layers_fast(self, painter, cam_pt, cam_z, cam_crs, proj, width, height,
                               yaw_eff, pitch, roll, HFOV, VFOV, is360, maxdist,
                               z_sampler, labels_hidden, height_field, h_default, transparent_objects=False,
                               terrain_horizon=None, terrain_eps=0.0):
    render_quality = getattr(self, '_current_render_quality', 'high')
    fast_preview = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked()) or (render_quality == 'low')
    ctx = build_camera_context(cam_pt, cam_z, proj, width, height, yaw_eff, pitch, roll, HFOV, VFOV, is360)
    terrain_test = None
    if terrain_horizon is not None and str(proj).upper() == 'PINHOLE':
        _prepare_pinhole_screen_metrics(ctx)
        terrain_test = lambda x, y, d: _pinhole_fragment_visible_by_horizon(ctx, terrain_horizon, terrain_eps, x, y, d)
    global_draw_2p5d = bool(self.cb_draw_2p5d.isChecked())
    panoramic_overlay_mode = str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
    force_horizontal_25d = bool(getattr(self, 'cb_force_horizontal_25d', None) and self.cb_force_horizontal_25d.isChecked())
    
    
    
    use_object_zbuffer = (str(proj).upper() == 'PINHOLE') and (not panoramic_overlay_mode)

    face_primitives = []
    deferred_edges = []
    deferred_points = []
    deferred_labels = []

    if not hasattr(self, '_budget_snapshots'):
        self._budget_snapshots = []

    for sty in self.layer_styles:
        if not bool(getattr(sty, 'visible', True)):
            continue
        lyr = sty.layer
        if not lyr:
            continue

        decision = _decide_layer_render(self, lyr, sty, cam_pt, cam_crs, yaw_eff, HFOV, proj, is360, render_quality=render_quality)
        effective_maxdist = float(decision.effective_maxdist)
        layer_labels_hidden = bool(labels_hidden or decision.degrade_labels)
        sty._force_simple_fill = bool(decision.degrade_style)
        sty._budget_warning = bool(decision.accepted and (decision.degrade_labels or decision.degrade_style or decision.simplify_geometry or effective_maxdist < (_requested_maxdist_for_layer(self, sty, QgsWkbTypes.geometryType(lyr.wkbType())) - 1.0)))
        self._budget_snapshots.append({
            'name': lyr.name(), 'accepted': bool(decision.accepted), 'distance': effective_maxdist,
            'count': int(decision.candidate_count), 'score': float(decision.score),
            'labels_off': bool(decision.degrade_labels), 'style_light': bool(decision.degrade_style),
            'simplify': bool(decision.simplify_geometry),
            'reason': str(getattr(decision, 'reason', 'ok'))
        })
        if not decision.accepted:
            continue

        cached = _get_layer_geometry_cache(
            self, lyr, cam_crs, fast_preview, maxdist=effective_maxdist, yaw_deg=yaw_eff, hfov_deg=HFOV,
            projection=proj, is360=is360, render_quality=render_quality, simplify_geometry=decision.simplify_geometry,
            cam_pt=cam_pt, sty=sty
        )
        gtype = cached['gtype']

        font_base = QFont('Arial', max(6, int(sty.label_size * max(0.5, width/4000.0))))

        _items = list(cached['features'])
        try:
            _items.sort(key=lambda it: _feature_depth_key(it, (cam_pt.x(), cam_pt.y())), reverse=True)
        except Exception:
            pass

        for item in _items:
            feat = item['feat']
            geom = item['geom']
            parts = item['parts']

            sty_eff = _feature_local_style(self, sty, feat)
            pen = _make_pen_for_style(sty_eff.color, getattr(sty_eff, 'width', 0.0), (width/4000.0 if width < 4000 else width/6000.0), getattr(sty_eff, 'pen_style', QC.Qt_PenStyle_SolidLine), _style_opacity_factor(sty_eff))
            font = QFont(font_base)
            font.setPointSize(max(6, int(getattr(sty_eff, 'label_size', sty.label_size) * max(0.5, width/4000.0))))
            draw_2p5d = global_draw_2p5d and bool(getattr(sty_eff, 'enable_25d', True))
            sty_height_field = getattr(sty_eff, 'height_field_override', '') or height_field
            sty_h_default = getattr(sty_eff, 'default_height_override', None)
            if sty_h_default is None:
                sty_h_default = h_default
            h = None
            if sty_height_field and (sty_height_field in feat.fields().names()):
                try:
                    h = float(feat[sty_height_field])
                except Exception:
                    h = None
            if h is None:
                h = sty_h_default

            anchor_uv_global = None
            text_global = None
            if sty_eff.show_labels and not layer_labels_hidden:
                try:
                    anchor_uv_global, text_global = self._label_anchor_uv(
                        sty_eff, feat, geom, proj, width, height, cam_pt, cam_z,
                        cached['tr'], yaw_eff, pitch, roll, HFOV, VFOV, is360, effective_maxdist, z_sampler, h
                    )
                except Exception:
                    anchor_uv_global, text_global = None, None

            if style_uses_schematic(sty_eff):
                
                
                
                
                
                _plugin_dir = os.path.dirname(os.path.dirname(__file__))
                handled = False
                definition = None
                try:
                    camera_xy = (float(cam_pt.x()), float(cam_pt.y()))
                    
                    
                    
                    
                    
                    _ground_contract = []
                    for _arr_src in parts:
                        _arr_contract = np.asarray(_arr_src, dtype=np.float64)
                        if _arr_contract.ndim != 2 or _arr_contract.shape[0] <= 0:
                            continue
                        
                        
                        
                        _force_horizontal_obj = bool(
                            global_draw_2p5d and bool(getattr(sty_eff, 'enable_25d', True))
                            and force_horizontal_25d
                            and gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry
                        )
                        _kind = 'polygon' if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry else ('line' if gtype == QC.QgsWkbTypes_GeometryType_LineGeometry else 'point')
                        _base_contract, _mean_contract, _raw_contract = _effective_base_z_array(
                            self, _arr_contract, z_sampler, geometry_kind=_kind,
                            force_horizontal=(True if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry else False)
                        )
                        _ground_contract.append((_arr_contract, _base_contract, _mean_contract))
                    definition, primitives = build_schematic_feature_primitives(
                        feat, parts, gtype, sty_eff, z_sampler, h, _plugin_dir, camera_xy,
                        ground_profiles=_ground_contract
                    )
                    if definition is not None and primitives:
                        handled = _append_schematic_primitives_for_zbuffer(
                            face_primitives, deferred_edges, primitives, sty_eff, definition,
                            ctx, effective_maxdist, camera_xy, width, fast_preview=fast_preview,
                            transparent_objects=transparent_objects
                        )
                except Exception as exc:
                    try:
                        qcv_log(f"{lyr.name()} | FID {feat.id()} | AVR {getattr(sty_eff,'schematic_symbol_id','?')} : {exc}", 'SCHEMATIC/RENDER', 'WARNING')
                    except Exception:
                        pass
                    handled = False
                if definition is not None:
                    if handled and sty_eff.show_labels and (not layer_labels_hidden) and text_global and anchor_uv_global is not None:
                        deferred_labels.append((font, text_global, anchor_uv_global, sty_eff))
                    continue
                try:
                    qcv_log(f"{lyr.name()} | FID {feat.id()} : état AVR invalide neutralisé ({getattr(sty_eff,'schematic_symbol_id','')})", 'SCHEMATIC/STATE', 'WARNING')
                except Exception:
                    pass

            if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
                for arr in parts:
                    ground_z = _sample_z_array(z_sampler, arr)
                    uv_base, depth_base = _project_uv_depth_batch(ctx, arr, ground_z, dist_max=effective_maxdist)
                    uv_top = None; depth_top = None
                    if draw_2p5d and h > 0:
                        uv_top, depth_top = _project_uv_depth_batch(ctx, arr, ground_z + float(h), dist_max=effective_maxdist)
                    deferred_points.append((pen, uv_base, depth_base, uv_top, depth_top))
                    label_anchor = anchor_uv_global
                    if label_anchor is None:
                        finite = np.isfinite(uv_base[:, 0]) & np.isfinite(uv_base[:, 1])
                        if uv_top is not None:
                            finite_top = np.isfinite(uv_top[:, 0]) & np.isfinite(uv_top[:, 1])
                            finite = finite_top | finite
                        if np.any(finite):
                            idx = int(np.argmax(finite))
                            label_anchor = tuple((uv_top[idx] if (uv_top is not None and np.isfinite(uv_top[idx, 0])) else uv_base[idx]).tolist())
                    if sty_eff.show_labels and (not layer_labels_hidden) and text_global and label_anchor:
                        deferred_labels.append((font, text_global, label_anchor, sty_eff))
            else:
                for arr in parts:
                    arr_draw = np.asarray(arr, dtype=np.float64)
                    ground_z = _sample_z_array(z_sampler, arr_draw)
                    if panoramic_overlay_mode and arr_draw.shape[0] >= 2:
                        try:
                            arr_draw, ground_z = _densify_path_for_projection(
                                ctx, arr_draw, ground_z, dist_max=effective_maxdist,
                                closed=(gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry), render_quality=render_quality
                            )
                        except Exception:
                            arr_draw = np.asarray(arr, dtype=np.float64)
                            ground_z = _sample_z_array(z_sampler, arr_draw)
                    force_horizontal_obj = bool(draw_2p5d and force_horizontal_25d and gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry)
                    base_z_arr, _base_z_mean, _raw_ground_z = _effective_base_z_array(
                        self, arr_draw, z_sampler,
                        geometry_kind=('polygon' if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry else 'line'),
                        force_horizontal=(True if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry else False)
                    )
                    uv_base, depth_base = _project_uv_depth_batch(ctx, arr_draw, base_z_arr, dist_max=effective_maxdist)
                    uv_top = None; depth_top = None
                    if draw_2p5d and h > 0:
                        uv_top, depth_top = _project_uv_depth_batch(ctx, arr_draw, base_z_arr + float(h), dist_max=effective_maxdist)

                    if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry and use_object_zbuffer:
                        _append_polygon_faces_for_zbuffer(face_primitives, sty_eff, uv_base, depth_base, uv_top=uv_top, depth_top=depth_top, transparent_objects=transparent_objects, wrap_width=(float(width) if bool(is360 and str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')) else None))
                        if transparent_objects:
                            deferred_edges.append((pen, uv_base, depth_base))
                            if uv_top is not None:
                                deferred_edges.append((pen, uv_top, depth_top))
                                step = 1
                                for i in range(0, arr_draw.shape[0], step):
                                    if np.isfinite(uv_base[i, 0]) and np.isfinite(uv_base[i, 1]) and np.isfinite(uv_top[i, 0]) and np.isfinite(uv_top[i, 1]):
                                        vu = np.asarray([[uv_base[i, 0], uv_base[i, 1]], [uv_top[i, 0], uv_top[i, 1]]], dtype=np.float64)
                                        vd = np.asarray([depth_base[i], depth_top[i]], dtype=np.float64)
                                        deferred_edges.append((pen, vu, vd))
                        else:
                            if uv_top is None:
                                deferred_edges.append((pen, uv_base, depth_base))
                            else:
                                top_corner_idx = _significant_ring_vertex_indices(arr_draw, closed=True, angle_tol_deg=2.0, min_seg_len=0.05)
                                top_uv_sel, top_d_sel = _select_uv_depth_by_indices(uv_top, depth_top, top_corner_idx, closed=True)
                                if top_uv_sel.shape[0] >= 2:
                                    deferred_edges.append((pen, top_uv_sel, top_d_sel))
                                try:
                                    cam_xy = (float(cam_pt.x()), float(cam_pt.y()))
                                except Exception:
                                    cam_xy = None
                                vertical_idx = _opaque_vertical_corner_indices(arr_draw, cam_xy, angle_tol_deg=2.0, min_seg_len=0.05)
                                for i in (vertical_idx or []):
                                    if i < 0 or i >= arr_draw.shape[0]:
                                        continue
                                    if np.isfinite(uv_base[i, 0]) and np.isfinite(uv_base[i, 1]) and np.isfinite(uv_top[i, 0]) and np.isfinite(uv_top[i, 1]):
                                        vu = np.asarray([[uv_base[i, 0], uv_base[i, 1]], [uv_top[i, 0], uv_top[i, 1]]], dtype=np.float64)
                                        vd = np.asarray([depth_base[i], depth_top[i]], dtype=np.float64)
                                        deferred_edges.append((pen, vu, vd))
                    elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry and use_object_zbuffer and uv_top is not None:
                        _append_line_walls_for_zbuffer(face_primitives, sty_eff, uv_base, depth_base, uv_top, depth_top, transparent_objects=transparent_objects, wrap_width=(float(width) if bool(is360 and str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')) else None))
                        if transparent_objects:
                            deferred_edges.append((pen, uv_base, depth_base))
                        deferred_edges.append((pen, uv_top, depth_top))
                    else:
                        if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                            _draw_filled_projected_polygon(self, painter, sty_eff, uv_base, uv_top=uv_top, transparent_objects=transparent_objects)
                        elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry and uv_top is not None:
                            _draw_line_walls(self, painter, sty_eff, uv_base, uv_top, transparent_objects=transparent_objects)
                        painter.setPen(pen)
                        if gtype != QC.QgsWkbTypes_GeometryType_PolygonGeometry or uv_top is None or transparent_objects:
                            _draw_uv_segments(self, painter, uv_base)
                        if uv_top is not None:
                            if transparent_objects or gtype != QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                                _draw_uv_segments(self, painter, uv_top)
                                step = 1 if transparent_objects else max(1, int(arr_draw.shape[0] / (24 if fast_preview else 40)))
                                for i in range(0, arr_draw.shape[0], step):
                                    if np.isfinite(uv_base[i, 0]) and np.isfinite(uv_base[i, 1]) and np.isfinite(uv_top[i, 0]) and np.isfinite(uv_top[i, 1]):
                                        self._safe_line(painter, (float(uv_base[i, 0]), float(uv_base[i, 1])), (float(uv_top[i, 0]), float(uv_top[i, 1])))
                            else:
                                try:
                                    top_corner_idx = _significant_ring_vertex_indices(arr_draw, closed=True, angle_tol_deg=2.0, min_seg_len=0.05)
                                except Exception:
                                    top_corner_idx = list(range(max(0, arr_draw.shape[0] - 1)))
                                if top_corner_idx:
                                    top_pts = []
                                    seen = set()
                                    n_top = int(uv_top.shape[0])
                                    if n_top <= 0:
                                        top_corner_idx = []
                                    for idx in top_corner_idx:
                                        j = int(idx) % n_top
                                        if j in seen:
                                            continue
                                        seen.add(j)
                                        if np.isfinite(uv_top[j, 0]) and np.isfinite(uv_top[j, 1]):
                                            top_pts.append((float(uv_top[j, 0]), float(uv_top[j, 1])))
                                    if len(top_pts) >= 2:
                                        top_pts.append(top_pts[0])
                                        for a, b in zip(top_pts[:-1], top_pts[1:]):
                                            self._safe_line(painter, a, b)
                                try:
                                    cam_xy = (float(cam_pt.x()), float(cam_pt.y()))
                                except Exception:
                                    cam_xy = None
                                for i in (_opaque_vertical_corner_indices(arr_draw, cam_xy, angle_tol_deg=2.0, min_seg_len=0.05) or []):
                                    if i < 0 or i >= arr_draw.shape[0]:
                                        continue
                                    if np.isfinite(uv_base[i, 0]) and np.isfinite(uv_base[i, 1]) and np.isfinite(uv_top[i, 0]) and np.isfinite(uv_top[i, 1]):
                                        self._safe_line(painter, (float(uv_base[i, 0]), float(uv_base[i, 1])), (float(uv_top[i, 0]), float(uv_top[i, 1])))
                    label_anchor = anchor_uv_global
                    if label_anchor is None:
                        src = uv_top if (uv_top is not None and np.isfinite(uv_top[:, 0]).any()) else uv_base
                        try:
                            finite = np.isfinite(src[:, 0]) & np.isfinite(src[:, 1])
                            if np.any(finite):
                                pts_vis = src[finite]
                                label_anchor = tuple(np.nanmean(pts_vis, axis=0).tolist())
                        except Exception:
                            label_anchor = None
                    if sty_eff.show_labels and (not layer_labels_hidden) and text_global and label_anchor is not None:
                        deferred_labels.append((font, text_global, label_anchor, sty_eff))

    depth_buf = None
    depth_scale = 1.0
    if use_object_zbuffer and face_primitives:
        
        
        
        
        depth_scale = _zbuffer_scale_for_preview(self, width, height)
        
        
        
        
        
        
        has_schematic_texture = any(
            bool((face.get('fill_spec') or {}).get('schematic_billboard_texture', False))
            for face in face_primitives
        )
        if has_schematic_texture:
            maxdim = max(int(width), int(height))
            if maxdim <= 2200:
                depth_scale = 1.0
            else:
                depth_scale = max(float(depth_scale), 0.75)
        face_rgba, depth_buf, depth_scale = _compose_faces_zbuffer(width, height, face_primitives, scale=depth_scale)
        if terrain_horizon is not None:
            face_rgba = _mask_pinhole_zbuffer_by_horizon(face_rgba, depth_buf, depth_scale, ctx, terrain_horizon, terrain_eps)
        face_img = _rgba_owned_array_to_qimage(face_rgba)
        painter.drawImage(QRect(0, 0, int(width), int(height)), face_img)

    for pen, uv_base, depth_base, uv_top, depth_top in deferred_points:
        painter.setPen(pen)
        for i in range(uv_base.shape[0]):
            if not (np.isfinite(uv_base[i, 0]) and np.isfinite(uv_base[i, 1])):
                continue
            base_uv = (float(uv_base[i, 0]), float(uv_base[i, 1]))
            if terrain_test is not None and np.isfinite(depth_base[i]) and depth_base[i] > 0:
                try:
                    if not terrain_test(base_uv[0], base_uv[1], float(depth_base[i])):
                        continue
                except Exception:
                    pass
            if depth_buf is not None and np.isfinite(depth_base[i]) and depth_base[i] > 0:
                sx = int(round(base_uv[0] * depth_scale)); sy = int(round(base_uv[1] * depth_scale))
                if 0 <= sx < depth_buf.shape[1] and 0 <= sy < depth_buf.shape[0]:
                    z = float(depth_buf[sy, sx])
                    if np.isfinite(z) and depth_base[i] > z + 5.0:
                        continue
            if uv_top is not None and np.isfinite(uv_top[i, 0]) and np.isfinite(uv_top[i, 1]):
                top_uv = (float(uv_top[i, 0]), float(uv_top[i, 1]))
                if depth_buf is not None and np.isfinite(depth_top[i]) and depth_top[i] > 0:
                    _draw_ztested_segment(self, painter, base_uv, top_uv, depth_base[i], depth_top[i], depth_buf, depth_scale, terrain_test=terrain_test)
                else:
                    self._safe_line(painter, base_uv, top_uv)
            else:
                try:
                    painter.drawEllipse(int(base_uv[0]) - 2, int(base_uv[1]) - 2, 4, 4)
                except Exception:
                    pass

    for pen, uvs, depths in deferred_edges:
        painter.setPen(pen)
        if depth_buf is not None:
            _draw_uv_segments_ztested(self, painter, uvs, depths, depth_buf, depth_scale, terrain_test=terrain_test)
        else:
            _draw_uv_segments(self, painter, uvs)

    for font, text, anchor_uv, sty in deferred_labels:
        painter.setFont(font)
        self._draw_label(painter, text, anchor_uv, sty)

def export_overlay(self):
    
    try:
        from ._export_ops import _default_export_dir
        _d = _default_export_dir(self)
    except Exception:
        _d = ""
    path, _ = QFileDialog.getSaveFileName(self, tr("Exporter overlay seul PNG transparent"), os.path.join(_d, "qcalview_overlay.png") if _d else "qcalview_overlay.png", tr("PNG (*.png)"))
    if not path: return
    overlay = self._render_overlay(width=self.spin_w.value(), height=self.spin_h.value())
    if not overlay.save(path, "PNG"):
        return
    try:
        from ._export_ops import _write_metadata_with_exiftool
        from ._camera_layer_ops import _camera_layer, _camera_current_feature, _camera_set_status
        ok_meta, err_meta = _write_metadata_with_exiftool(self, path, _camera_layer(self), _camera_current_feature(self))
        if not ok_meta:
            _camera_set_status(self, err_meta or 'Échec écriture métadonnées.', '#c44')
    except Exception:
        pass



def _pick_dem_color(self):
    base = QColor(getattr(self, "_dem_color", QColor(255,255,0,160)))
    dlg = QColorDialog(base, self)
    try:
        dlg.setOption(QC.QColorDialog_ColorDialogOption_ShowAlphaChannel, True)
    except Exception:
        pass
    dlg.setWindowTitle(tr("Couleur du relief"))
    if dialog_exec(dlg):
        c = dlg.selectedColor()
        if c.isValid():
            self._dem_color = QColor(c)
            
            
            self._sky_color = QColor(c)
            try:
                if hasattr(self, 'spin_dem_alpha'):
                    old = self.spin_dem_alpha.blockSignals(True)
                    self.spin_dem_alpha.setValue(int(c.alpha()))
                    self.spin_dem_alpha.blockSignals(old)
            except Exception:
                pass
            try:
                self.render_preview()
            except Exception:
                pass








def _build_horizon_cache(self, *args, **kwargs):
    
    try:
        proj  = self.cmb_proj.currentText()
    except Exception:
        proj = "PINHOLE"
    try:
        width = int(self.spin_w.value()); height = int(self.spin_h.value())
    except Exception:
        width, height = 1920, 1080
    try:
        yaw   = float(self.d_yaw.value()) + float(self.d_yaw_offset.value())
    except Exception:
        yaw = float(getattr(self, "d_yaw", 0.0) or 0.0)
    try:
        pitch = float(self.d_pitch.value()); roll = float(self.d_roll.value())
    except Exception:
        pitch, roll = 0.0, 0.0
    try:
        HFOV  = float(self.d_hfov.value()); VFOV = float(self.d_vfov.value())
    except Exception:
        HFOV, VFOV = 60.0, 40.0
    try:
        is360 = bool(self._is360_mode()) if hasattr(self, '_is360_mode') else bool(self.cb_360.isChecked())
    except Exception:
        is360 = False

    curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked()) if hasattr(self, 'cb_curvature') else True
    k_ref = 0.0
    az_step = float(getattr(self, 'd_az_step', None).value()) if hasattr(self, 'd_az_step') else 0.2
    rad_step = float(getattr(self, 'd_rad_step', None).value()) if hasattr(self, 'd_rad_step') else 50.0

    z_sampler = None
    cam_pt = None
    cam_z = None
    cam_crs = None
    maxdist = None

    n = len(args)

    def is_point_xy(obj):
        try:
            return hasattr(obj, "x") and hasattr(obj, "y")
        except Exception:
            return False

    if n >= 13 and is_point_xy(args[0]):
        cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist = args[:13]
    elif n >= 12 and is_point_xy(args[0]):
        cam_pt, cam_z, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist = args[:12]
    elif n >= 14 and (not is_point_xy(args[0])) and is_point_xy(args[1]):
        z_sampler, cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist = args[:14]
        if n >= 15: az_step = args[14]
        if n >= 16: rad_step = args[15]
    elif n >= 7 and (not is_point_xy(args[0])) and is_point_xy(args[1]):
        z_sampler, cam_pt, cam_z, cam_crs, maxdist = args[:5]
        if n >= 6: az_step = args[5]
        if n >= 7: rad_step = args[6]
    else:
        z_sampler = kwargs.get("z_sampler")
        cam_pt = kwargs.get("cam_pt")
        cam_z = kwargs.get("cam_z")
        cam_crs = kwargs.get("cam_crs")
        proj = kwargs.get("proj", proj)
        width = int(kwargs.get("width", width)); height = int(kwargs.get("height", height))
        yaw = float(kwargs.get("yaw", yaw)); pitch = float(kwargs.get("pitch", pitch)); roll = float(kwargs.get("roll", roll))
        HFOV = float(kwargs.get("HFOV", HFOV)); VFOV = float(kwargs.get("VFOV", VFOV))
        is360 = bool(kwargs.get("is360", is360))
        maxdist = float(kwargs.get("maxdist", 20000.0))
        az_step = float(kwargs.get("az_step", az_step))
        rad_step = float(kwargs.get("rad_step", rad_step))

    if maxdist is None:
        maxdist = 20000.0

    if cam_pt is None:
        try:
            cam_layer = self.cmb_camera.currentLayer()
            cam_feat = None
            try:
                cam_feat = self._camera_current_feature()
            except Exception:
                cam_feat = None
            if cam_feat is None or (not cam_feat.isValid()) or cam_feat.geometry() is None or cam_feat.geometry().isEmpty():
                cam_feat = next(cam_layer.getFeatures())
            cam_pt = cam_feat.geometry().asPoint()
            if cam_z is None:
                cam_z = float(getattr(self, "d_camheight", None).value()) if hasattr(self, "d_camheight") else 1.7
        except Exception:
            raise RuntimeError("Camera point non défini pour _build_horizon_cache")

    if z_sampler is None:
        dem_layer = self.cmb_dem.currentLayer() if hasattr(self, 'cmb_dem') else None
        if dem_layer and dem_layer.isValid():
            try:
                z_sampler, _crs = self._make_z_sampler(dem_layer, self.cmb_camera.currentLayer().crs())
            except Exception:
                z_sampler = None
    if z_sampler is None:
        self._horizon = None
        self._horizon_params = None
        return

    cx, cy = float(cam_pt.x()), float(cam_pt.y())
    maxdist = float(maxdist or 20000.0)
    az_step = max(0.05, float(az_step))
    rad_step = max(1.0, float(rad_step))
    half = (180.0 if is360 else float(HFOV) * 0.5)
    az0, az1 = float(yaw) - half, float(yaw) + half
    n_bins = max(36, int(math.ceil((az1 - az0) / az_step)))
    az_bins = np.linspace(az0, az1, n_bins, endpoint=True).astype(np.float32)
    el_bins = np.full_like(az_bins, -1e9, dtype=np.float32)
    uv_bins = np.full((n_bins, 2), np.nan, dtype=np.float32)
    poly = []

    R_earth = float(getattr(self, 'd_earth_radius_km', None).value() * 1000.0) if hasattr(self, 'd_earth_radius_km') else 6370000.0
    R_eff = (R_earth / (1.0 - float(k_ref))) if curvature_enabled else float('inf')

    d_values = topo_build_radial_distances(rad_step, maxdist)
    if d_values.size == 0:
        d_values = np.asarray([maxdist], dtype=np.float64)
    d_values = np.asarray(d_values, dtype=np.float64)
    n_dist = int(d_values.size)

    
    
    
    
    
    
    try:
        _dem = self.cmb_dem.currentLayer() if hasattr(self, 'cmb_dem') else None
        _dem_token = (
            str(_dem.id()) if _dem is not None else '',
            str(_dem.source()) if _dem is not None else '',
            str(_dem.crs().authid()) if _dem is not None and _dem.crs().isValid() else '',
        )
    except Exception:
        _dem_token = ('', '', '')
    try:
        _cam_crs_token = str(cam_crs.authid()) if cam_crs is not None and cam_crs.isValid() else ''
    except Exception:
        _cam_crs_token = ''
    _terrain_sample_key = (
        _dem_token, _cam_crs_token,
        round(cx, 3), round(cy, 3),
        round(float(az0), 5), round(float(az1), 5), int(n_bins),
        round(float(maxdist), 3), round(float(az_step), 5), round(float(rad_step), 5), int(n_dist),
    )

    _sample_cache = getattr(self, '_horizon_dem_sample_cache', None)
    z_raw = None
    if isinstance(_sample_cache, dict) and _sample_cache.get('key') == _terrain_sample_key:
        try:
            _cached = np.asarray(_sample_cache.get('z_raw'), dtype=np.float32)
            if _cached.shape == (n_bins, n_dist):
                z_raw = _cached
        except Exception:
            z_raw = None

    if z_raw is None:
        z_raw = np.zeros((n_bins, n_dist), dtype=np.float32)
        _batch_uncached = getattr(z_sampler, 'batch_uncached', None)
        _xy_uncached = getattr(z_sampler, 'xy_uncached', None)
        _chunk = 16
        _drow = d_values.reshape(1, -1)
        for _i0 in range(0, n_bins, _chunk):
            _i1 = min(n_bins, _i0 + _chunk)
            _az = np.radians(np.asarray(az_bins[_i0:_i1], dtype=np.float64)).reshape(-1, 1)
            _xs = cx + np.sin(_az) * _drow
            _ys = cy + np.cos(_az) * _drow
            _pts = np.empty(((_i1 - _i0) * n_dist, 2), dtype=np.float64)
            _pts[:, 0] = _xs.reshape(-1)
            _pts[:, 1] = _ys.reshape(-1)
            try:
                if callable(_batch_uncached):
                    _zs = np.asarray(_batch_uncached(_pts), dtype=np.float64).reshape(_i1 - _i0, n_dist)
                elif callable(_xy_uncached):
                    _zs = np.empty((_i1 - _i0, n_dist), dtype=np.float64)
                    for _rr in range(_i1 - _i0):
                        for _cc in range(n_dist):
                            _zs[_rr, _cc] = float(_xy_uncached(_xs[_rr, _cc], _ys[_rr, _cc]))
                else:
                    _zs = np.empty((_i1 - _i0, n_dist), dtype=np.float64)
                    _pt = QgsPointXY(0.0, 0.0)
                    for _rr in range(_i1 - _i0):
                        for _cc in range(n_dist):
                            _pt.setX(float(_xs[_rr, _cc])); _pt.setY(float(_ys[_rr, _cc]))
                            _zs[_rr, _cc] = float(z_sampler(_pt))
                _zs[~np.isfinite(_zs)] = 0.0
                z_raw[_i0:_i1, :] = _zs.astype(np.float32, copy=False)
            except Exception:
                
                
                z_raw[_i0:_i1, :] = 0.0
        self._horizon_dem_sample_cache = {'key': _terrain_sample_key, 'z_raw': z_raw}

    
    
    if math.isfinite(R_eff):
        _drop = (d_values * d_values) / (2.0 * float(R_eff))
        z_corr = z_raw.astype(np.float64) - _drop.reshape(1, -1)
    else:
        z_corr = z_raw.astype(np.float64)
    _elev = np.degrees(np.arctan2(z_corr - float(cam_z), d_values.reshape(1, -1)))
    _elev[~np.isfinite(_elev)] = -1e9
    el_profile = _elev.astype(np.float32, copy=False)
    el_cummax = np.maximum.accumulate(el_profile, axis=1).astype(np.float32, copy=False)
    
    
    _best_j = (n_dist - 1 - np.argmax(el_profile[:, ::-1], axis=1)).astype(np.int64, copy=False)
    _rows = np.arange(n_bins, dtype=np.int64)
    el_bins[:] = el_profile[_rows, _best_j]

    
    
    _best_d = d_values[_best_j]
    _azr = np.radians(np.asarray(az_bins, dtype=np.float64))
    _best_xy = np.column_stack((
        cx + _best_d * np.sin(_azr),
        cy + _best_d * np.cos(_azr),
    ))
    _best_z = z_corr[_rows, _best_j]
    try:
        _ctx_h = build_camera_context((cx, cy), float(cam_z), str(proj), int(width), int(height),
                                      float(yaw), float(pitch), float(roll),
                                      float(HFOV), float(VFOV), bool(is360))
        _uv_all = project_points_batch(_ctx_h, _best_xy, _best_z, dist_max=float(maxdist))
    except Exception:
        _uv_all = np.full((n_bins, 2), np.nan, dtype=np.float64)
    if _uv_all.shape[0] == n_bins:
        _good_uv = np.isfinite(_uv_all[:, 0]) & np.isfinite(_uv_all[:, 1])
        uv_bins[_good_uv, 0] = _uv_all[_good_uv, 0].astype(np.float32, copy=False)
        uv_bins[_good_uv, 1] = _uv_all[_good_uv, 1].astype(np.float32, copy=False)
        poly = [(float(u), float(v)) for u, v in _uv_all[_good_uv]]

    earth_radius_m = float(getattr(self, 'd_earth_radius_km', None).value() * 1000.0) if hasattr(self, 'd_earth_radius_km') else 6370000.0
    view_key = (
        round(cx, 3), round(cy, 3), round(float(cam_z), 3),
        str(proj), int(width), int(height),
        round(float(yaw), 6), round(float(pitch), 6), round(float(roll), 6),
        round(float(HFOV), 6), round(float(VFOV), 6), bool(is360),
        round(maxdist, 3), round(az_step, 4), round(rad_step, 4),
        bool(curvature_enabled), round(float(earth_radius_m), 3),
    )
    occ_key = (
        round(cx, 3), round(cy, 3), round(float(cam_z), 3),
        round(maxdist, 3), round(az_step, 4), round(rad_step, 4),
        bool(curvature_enabled), round(float(earth_radius_m), 3),
    )

    self._horizon = {
        "poly": poly,
        "az_bins": az_bins,
        "el_bins": el_bins,
        "uv_bins": uv_bins,
        "az_min": float(az0),
        "az_max": float(az1),
        "n_bins": int(n_bins),
        "view_key": view_key,
        "occ_key": occ_key,
        "d_values": d_values.astype(np.float32),
        "el_profile": el_profile,
        "el_cummax": el_cummax,
    }
    
    
    if str(proj).upper() in ('EQUIRECT','EQUIRECTANGULAR','CYLINDRICAL'):
        try:
            _prepare_horizon_fast_distance_lut_40192(self._horizon)
        except Exception:
            pass
    
    self._horizon_params = occ_key


def _is_visible_by_horizon(self, az_deg, el_deg, eps_deg, dist_m=None):
    
    H = getattr(self, "_horizon", None)
    if not H or ("el_bins" not in H):
        return True
    try:
        el_bins = H.get("el_bins")
        n_bins = int(H.get("n_bins") or getattr(el_bins, "size", 0) or len(el_bins))
        if el_bins is None or n_bins <= 0:
            return True
        az_min = float(H.get("az_min", 0.0))
        az_max = float(H.get("az_max", az_min))
        az_val = _unwrap_az_for_range(float(az_deg), az_min, az_max)
        if az_max <= az_min or n_bins == 1:
            idx = 0
        else:
            frac = (az_val - az_min) / (az_max - az_min)
            if frac <= 0.0:
                idx = 0
            elif frac >= 1.0:
                idx = n_bins - 1
            else:
                idx = int(round(frac * (n_bins - 1)))
        el_env = None
        if dist_m is not None:
            d_values = H.get("d_values")
            el_cummax = H.get("el_cummax")
            if d_values is not None and el_cummax is not None:
                nd = int(getattr(d_values, "size", 0) or len(d_values))
                if nd > 0:
                    j = int(np.searchsorted(d_values, float(dist_m), side='left')) - 1
                    if j >= 0:
                        j = min(j, nd - 1)
                        try:
                            el_env = float(el_cummax[idx, j])
                        except Exception:
                            try:
                                el_env = float(el_cummax[j])
                            except Exception:
                                el_env = None
                    else:
                        return True
        if el_env is None:
            el_env = float(el_bins[idx])
        if not _math.isfinite(el_env):
            return True
        return float(el_deg) >= (el_env - float(eps_deg))
    except Exception:
        return True


def _update_horizon_by_segment(self, az1_deg, az2_deg, el_deg):
    
    H = getattr(self, "_horizon", None)
    if not H or ("el_bins" not in H):
        return
    try:
        el_bins = H.get("el_bins")
        n_bins = int(H.get("n_bins") or getattr(el_bins, "size", 0) or len(el_bins))
        if el_bins is None or n_bins <= 0:
            return
        az_min = float(H.get("az_min", 0.0))
        az_max = float(H.get("az_max", az_min))
        if az_max <= az_min or n_bins == 1:
            idx0 = idx1 = 0
        else:
            a0 = _unwrap_az_for_range(float(az1_deg), az_min, az_max)
            a1 = _unwrap_az_for_range(float(az2_deg), az_min, az_max)
            if a1 < a0:
                a0, a1 = a1, a0
            frac0 = max(0.0, min(1.0, (a0 - az_min) / (az_max - az_min)))
            frac1 = max(0.0, min(1.0, (a1 - az_min) / (az_max - az_min)))
            idx0 = int(round(frac0 * (n_bins - 1)))
            idx1 = int(round(frac1 * (n_bins - 1)))
        v = float(el_deg)
        for idx in range(max(0, idx0), min(n_bins - 1, idx1) + 1):
            try:
                if v > float(el_bins[idx]):
                    el_bins[idx] = v
            except Exception:
                pass
    except Exception:
        return
def _terrain_visibility_test(self, cam_pt, cam_z, eps_deg):
    
    try:
        cx = float(cam_pt.x()); cy = float(cam_pt.y()); cz = float(cam_z)
        eps = float(eps_deg)
    except Exception:
        cx = cy = cz = 0.0; eps = 0.2

    def _tester(x, y, z):
        try:
            xx = float(x); yy = float(y); zz = float(z)
            if not (math.isfinite(xx) and math.isfinite(yy) and math.isfinite(zz)):
                return False
            dx = xx - cx; dy = yy - cy
            r = math.hypot(dx, dy)
            if (not math.isfinite(r)) or r <= 1e-6:
                return True
            az = math.degrees(math.atan2(dx, dy))
            if az < 0.0:
                az += 360.0
            el = math.degrees(math.atan2(zz - cz, max(r, 1e-9)))
            if not (math.isfinite(az) and math.isfinite(el)):
                return False
            return bool(self._is_visible_by_horizon(az, el, eps, r))
        except Exception:
            
            return True
    return _tester


def _draw_dem_opaque(self, painter, cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist):
    H = getattr(self, "_horizon", None)
    if not H:
        return
    uv_bins = np.asarray(H.get("uv_bins"), dtype=np.float64) if H.get("uv_bins") is not None else None
    az_bins = np.asarray(H.get("az_bins"), dtype=np.float64) if H.get("az_bins") is not None else None
    el_bins = np.asarray(H.get("el_bins"), dtype=np.float64) if H.get("el_bins") is not None else None
    if uv_bins is None or az_bins is None or el_bins is None or uv_bins.ndim != 2 or uv_bins.shape[0] != az_bins.shape[0] or el_bins.shape[0] != az_bins.shape[0]:
        return

    fill_color = QColor(getattr(self, "_dem_color", QColor(255, 255, 0, 220)))
    alpha = max(0, min(255, int(fill_color.alpha())))
    fill_color.setAlpha(alpha)
    pen_color = QColor(fill_color)
    pen_color.setAlpha(alpha)
    pen = QPen(pen_color)
    pen.setWidth(int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1)

    try:
        width = int(width); height = int(height)
    except Exception:
        return
    if width <= 1 or height <= 1:
        return

    
    
    down = max(1, int(math.ceil(max(width, height) / 1600.0)))
    Wm = max(1, int(math.ceil(width / float(down))))
    Hm = max(1, int(math.ceil(height / float(down))))
    xs = np.minimum((np.arange(Wm, dtype=np.float64) + 0.5) * float(down), float(width) - 0.5)
    ys = np.minimum((np.arange(Hm, dtype=np.float64) + 0.5) * float(down), float(height) - 0.5)
    U, V = np.meshgrid(xs, ys)

    proj_name = str(proj or 'PINHOLE').upper()
    hf = math.radians(max(1e-6, float(HFOV)))
    vf = math.radians(max(1e-6, float(VFOV)))

    if proj_name == 'PINHOLE':
        fx = (float(width) * 0.5) / max(1e-9, math.tan(hf * 0.5))
        fy = (float(height) * 0.5) / max(1e-9, math.tan(vf * 0.5))
        xc = (U - float(width) * 0.5) / fx
        yc = np.ones_like(xc)
        zc = (float(height) * 0.5 - V) / fy
    elif proj_name == 'CYLINDRICAL':
        vf_cyl = _validated_vfov_for_cylindrical(VFOV, HFOV, width, height)
        fy = (float(height) * 0.5) / max(1e-9, math.tan(vf_cyl * 0.5))
        if bool(is360):
            alpha_img = (U / float(width)) * (2.0 * math.pi) - math.pi
        else:
            fx = float(width) / max(1e-9, hf)
            alpha_img = (U - float(width) * 0.5) / fx
        beta_img = np.arctan((float(height) * 0.5 - V) / fy)
        xc = np.sin(alpha_img)
        yc = np.cos(alpha_img)
        zc = np.tan(beta_img)
    else:  
        if bool(is360):
            alpha_img = (U / float(width)) * (2.0 * math.pi) - math.pi
            beta_img = (0.5 * math.pi) - (V / float(height)) * math.pi
        else:
            alpha_img = ((U / float(width)) - 0.5) * hf
            beta_img = (0.5 - (V / float(height))) * vf
        cb = np.cos(beta_img)
        xc = np.sin(alpha_img) * cb
        yc = np.cos(alpha_img) * cb
        zc = np.sin(beta_img)

    r, uvec, f = _basis_from_yaw_pitch_roll(float(yaw), float(pitch), float(roll))
    wx = xc * float(r[0]) + yc * float(f[0]) + zc * float(uvec[0])
    wy = xc * float(r[1]) + yc * float(f[1]) + zc * float(uvec[1])
    wz = xc * float(r[2]) + yc * float(f[2]) + zc * float(uvec[2])
    rho = np.hypot(wx, wy)
    el = np.degrees(np.arctan2(wz, np.maximum(rho, 1e-9)))
    az = np.degrees(np.arctan2(wx, wy))

    az_min = float(H.get('az_min', float(az_bins[0])))
    az_max = float(H.get('az_max', float(az_bins[-1])))
    center = 0.5 * (az_min + az_max)
    az_un = center + (((az - center) + 180.0) % 360.0) - 180.0
    az_un = np.clip(az_un, float(az_bins[0]), float(az_bins[-1]))
    el_env = np.interp(az_un, az_bins.astype(np.float64), el_bins.astype(np.float64))
    mask = np.isfinite(el) & np.isfinite(el_env) & (el <= (el_env + 0.05))

    if np.any(mask):
        rgba = np.zeros((Hm, Wm, 4), dtype=np.uint8)
        rgba[..., 0] = fill_color.red()
        rgba[..., 1] = fill_color.green()
        rgba[..., 2] = fill_color.blue()
        rgba[..., 3] = np.where(mask, fill_color.alpha(), 0).astype(np.uint8)
        qimg = QImage(rgba.data, Wm, Hm, 4 * Wm, QC.QImage_Format_Format_RGBA8888).copy()
        painter.save()
        painter.setPen(QC.Qt_PenStyle_NoPen)
        painter.drawImage(QRect(0, 0, int(width), int(height)), qimg)
        painter.restore()

    def _split_valid_chunks(arr, wrap=None):
        valid = np.isfinite(arr[:, 0]) & np.isfinite(arr[:, 1])
        chunks = []
        start = None
        for i, ok in enumerate(valid):
            if ok and start is None:
                start = i
            elif (not ok) and start is not None:
                if i - start >= 2:
                    chunks.append((start, i))
                start = None
        if start is not None and len(valid) - start >= 2:
            chunks.append((start, len(valid)))
        out = []
        jump_thr = (0.45 * float(wrap)) if wrap else None
        for s, e in chunks:
            last = s
            for i in range(s + 1, e):
                dx = abs(float(arr[i, 0]) - float(arr[i - 1, 0]))
                if jump_thr is not None and dx > jump_thr:
                    if i - last >= 2:
                        out.append((last, i))
                    last = i
            if e - last >= 2:
                out.append((last, e))
        return out

    painter.setPen(pen)
    wrap_width = float(width) if bool(is360 and str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')) else None
    for s, e in _split_valid_chunks(uv_bins, wrap=wrap_width):
        run = uv_bins[s:e]
        for (a, b) in zip(run[:-1], run[1:]):
            self._safe_line(painter, (float(a[0]), float(a[1])), (float(b[0]), float(b[1])))




def _draw_dem_wireframe(self, painter, cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist, z_sampler, eps=0.2):
    base_spacing = float(self.spin_dem_step.value()) if hasattr(self, 'spin_dem_step') else 50.0
    if str(proj or '').strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
        try:
            _guard_step = _active_panorama_guard(self).get('dem_step_min')
            if _guard_step is not None:
                base_spacing = max(base_spacing, float(_guard_step))
        except Exception:
            pass
    
    if float(maxdist or 0.0) > 10000.0:
        far_factor = min(3.0, 1.0 + ((float(maxdist) - 10000.0) / 15000.0))
        base_spacing *= far_factor
    D_adapt = getattr(self, 'd_adapt_D', None)
    D_adapt_val = float(D_adapt.value()) if D_adapt is not None else (100.0 * base_spacing)
    if float(maxdist or 0.0) > 10000.0:
        D_adapt_val *= min(4.0, 1.0 + ((float(maxdist) - 10000.0) / 10000.0))
    curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked()) if hasattr(self, 'cb_curvature') else True
    k_ref = 0.0

    
    
    
    
    
    vis_test = None
    wire_mode = int(self.combo_wire_mode.currentData()) if hasattr(self, 'combo_wire_mode') and self.combo_wire_mode.currentData() is not None else (int(self.combo_wire_mode.currentIndex()) if hasattr(self, 'combo_wire_mode') else 2)
    horizon_data = (getattr(self, '_horizon', None) if wire_mode != 0 else None)
    if wire_mode == 2 and not horizon_data:
        return
    segs = topo_draw_dem_wireframe(
        cam_xy=(cam_pt.x(), cam_pt.y()), cam_z=float(cam_z),
        projector=project_point,
        proj_name=proj, width=int(width), height=int(height),
        yaw=float(yaw), pitch=float(pitch), roll=float(roll),
        HFOV=float(HFOV), VFOV=float(VFOV), is360=bool(is360),
        maxdist=float(maxdist or 20000.0),
        z_sampler=z_sampler,
        base_spacing=base_spacing, D_adapt=D_adapt_val,
        visibility_test=vis_test,
        wire_mode=wire_mode,
        horizon_data=horizon_data, visibility_eps_deg=float(eps),
        curvature_enabled=curvature_enabled, R_earth=float(getattr(self, 'd_earth_radius_km', None).value() * 1000.0) if hasattr(self, 'd_earth_radius_km') else 6370000.0, k_refraction=k_ref
    )

    
    color = QColor(getattr(self, "_dem_color", QColor(255,255,0,160)))
    pen = QPen(color)
    pen.setWidth(int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1)
    if hasattr(self, 'cb_wire_dashed') and self.cb_wire_dashed.isChecked():
        pen.setStyle(QC.Qt_PenStyle_DashLine)
    painter.setPen(pen)

    for (a, b) in segs:
        self._safe_line(painter, a, b)

def _draw_skyline(self, painter, cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist):
    _az_step_eff = float(self.d_az_step.value())
    _rad_step_eff = float(self.d_rad_step.value())
    if str(proj or '').strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
        try:
            _guard_rad = _active_panorama_guard(self).get('rad_step_min')
            if _guard_rad is not None:
                _rad_step_eff = max(_rad_step_eff, float(_guard_rad))
        except Exception:
            pass
    params = (
        round(float(cam_pt.x()), 3), round(float(cam_pt.y()), 3), round(float(cam_z), 3),
        str(proj), int(width), int(height),
        round(float(yaw), 6), round(float(pitch), 6), round(float(roll), 6),
        round(float(HFOV), 6), round(float(VFOV), 6), bool(is360),
        round(float(maxdist or 20000.0), 3),
        round(float(_az_step_eff), 4), round(float(_rad_step_eff), 4),
    )
    existing = getattr(self, "_horizon", None) or {}
    if existing.get("view_key") != params:
        _build_horizon_cache(
            self, cam_pt=cam_pt, cam_z=cam_z, cam_crs=cam_crs, proj=proj,
            width=width, height=height, yaw=yaw, pitch=pitch, roll=roll,
            HFOV=HFOV, VFOV=VFOV, is360=is360, maxdist=(maxdist or 20000.0),
            az_step=_az_step_eff, rad_step=_rad_step_eff
        )

    H = getattr(self, "_horizon", None)
    if not H or not H.get("poly"):
        return

    color = getattr(self, "_dem_color", QColor(255,255,0,220))
    pen = QPen(color)
    pen.setWidth(int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1)
    if hasattr(self, 'cb_sky_dashed') and self.cb_sky_dashed.isChecked():
        pen.setStyle(QC.Qt_PenStyle_DashLine)
    painter.setPen(pen)

    poly = np.asarray([(float(a[0]), float(a[1])) for a in H.get("poly", []) if a is not None], dtype=np.float64)
    if poly.shape[0] < 2:
        return
    wrap_width = float(width) if bool(is360 and str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')) else None
    runs = _iter_wrap_shifted_pts(poly, wrap_width) if wrap_width else (poly,)
    for run in runs:
        if run is None or len(run) < 2:
            continue
        for (a, b) in zip(run[:-1], run[1:]):
            self._safe_line(painter, (float(a[0]), float(a[1])), (float(b[0]), float(b[1])))

def _draw_dem_ridgelines(self, painter, cam_pt, cam_z, cam_crs, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360, maxdist, z_sampler, eps=0.2):
    
    H = getattr(self, "_horizon", None)
    if not H:
        return
    az_bins = H.get("az_bins")
    d_values = H.get("d_values")
    el_profile = H.get("el_profile")
    if az_bins is None or d_values is None or el_profile is None:
        return
    try:
        n_az = int(len(az_bins))
        n_d = int(len(d_values))
    except Exception:
        return
    if n_az <= 2 or n_d <= 1:
        return

    ridge_gap = float(self.d_ridge_gap.value()) if hasattr(self, 'd_ridge_gap') else 250.0
    ridge_prom = float(self.d_ridge_prom.value()) if hasattr(self, 'd_ridge_prom') else 2.0
    base_spacing = float(self.spin_dem_step.value()) if hasattr(self, 'spin_dem_step') else 150.0
    if str(proj or '').strip().upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL'):
        try:
            _guard_step = _active_panorama_guard(self).get('dem_step_min')
            if _guard_step is not None:
                base_spacing = max(base_spacing, float(_guard_step))
        except Exception:
            pass
    if float(maxdist or 0.0) > 10000.0:
        base_spacing *= min(2.5, 1.0 + ((float(maxdist) - 10000.0) / 20000.0))

    color = QColor(getattr(self, '_dem_color', QColor(255, 255, 0, 220)))
    pen = QPen(color)
    pen.setWidth(max(1, int(self.spin_dem_width.value()) if hasattr(self, 'spin_dem_width') else 1))
    if hasattr(self, 'cb_sky_dashed') and self.cb_sky_dashed.isChecked():
        pen.setStyle(QC.Qt_PenStyle_DashLine)
    painter.setPen(pen)

    curvature_enabled = bool(getattr(self, 'cb_curvature', None).isChecked()) if hasattr(self, 'cb_curvature') else True
    k_ref = 0.0
    R_earth = float(getattr(self, 'd_earth_radius_km', None).value() * 1000.0) if hasattr(self, 'd_earth_radius_km') else 6370000.0
    R_eff = (R_earth / (1.0 - float(k_ref))) if curvature_enabled else float('inf')
    cx, cy = float(cam_pt.x()), float(cam_pt.y())
    wrap_width = float(width) if bool(is360 and str(proj).upper() in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')) else None

    def _smooth_run(run):
        arr = np.asarray(run, dtype=np.float64).copy()
        if arr.shape[0] < 4:
            return arr
        ys = arr[:, 1].copy()
        xs = arr[:, 0].copy()
        for i in range(1, arr.shape[0] - 1):
            ys[i] = (ys[i - 1] + 2.0 * ys[i] + ys[i + 1]) / 4.0
            xs[i] = (xs[i - 1] + 2.0 * xs[i] + xs[i + 1]) / 4.0
        arr[:, 0] = xs
        arr[:, 1] = ys
        return arr

    def _mean_visible_elevation(j):
        vals = []
        for i in range(n_az):
            try:
                e = float(el_profile[i, j])
            except Exception:
                continue
            if np.isfinite(e):
                vals.append(e)
        if not vals:
            return None
        return float(np.mean(vals))

    kept_js = []
    last_kept_d = -1e18
    last_kept_mean_el = None
    min_visible_pts = max(12, int(0.12 * n_az))

    for j in range(n_d):
        d = float(d_values[j])
        if d < max(5.0, 0.5 * base_spacing):
            continue
        mean_el = _mean_visible_elevation(j)
        if mean_el is None:
            continue
        if kept_js:
            if (d - last_kept_d) < ridge_gap:
                continue
            if last_kept_mean_el is not None and abs(mean_el - last_kept_mean_el) < ridge_prom:
                continue

        curve = np.full((n_az, 2), np.nan, dtype=np.float64)
        visible_count = 0
        for i in range(n_az):
            az = float(az_bins[i])
            th = math.radians(az)
            x = cx + d * math.sin(th)
            y = cy + d * math.cos(th)
            z = float(z_sampler(QgsPointXY(x, y)))
            if math.isfinite(R_eff):
                z -= (d * d) / (2.0 * R_eff)
            r = math.hypot(x - cx, y - cy)
            if r <= 1e-6:
                continue
            el = math.degrees(math.atan2(z - float(cam_z), r))
            try:
                vis = bool(self._is_visible_by_horizon(az, el, float(eps), r))
            except Exception:
                vis = True
            if not vis:
                continue
            uv = project_point(
                QgsPointXY(cx, cy), float(cam_z),
                QgsPointXY(x, y), None,
                str(proj), int(width), int(height),
                float(yaw), float(pitch), float(roll),
                float(HFOV), float(VFOV), bool(is360),
                dist_max=maxdist, z_tgt=float(z), z_sampler=None
            )
            if uv is None:
                continue
            if math.isfinite(float(uv[0])) and math.isfinite(float(uv[1])):
                curve[i, 0] = float(uv[0])
                curve[i, 1] = float(uv[1])
                visible_count += 1

        if visible_count < min_visible_pts:
            continue
        kept_js.append(j)
        last_kept_d = d
        last_kept_mean_el = mean_el

        runs = _uv_runs_array(curve, min_len=max(6, int(0.06 * n_az)))
        for run in runs:
            if run is None or len(run) < 2:
                continue
            run = _smooth_run(run)
            shifted_runs = _iter_wrap_shifted_pts(run, wrap_width) if wrap_width else (run,)
            for shifted in shifted_runs:
                if shifted is None or len(shifted) < 2:
                    continue
                for a, b in zip(shifted[:-1], shifted[1:]):
                    self._safe_line(painter, (float(a[0]), float(a[1])), (float(b[0]), float(b[1])))


def set_pdv_azimuth(self, az_deg: float):
    
    try:
        self.current_pdv_azimuth = float(az_deg) % 360.0
    except Exception:
        self.current_pdv_azimuth = None
    
    try:
        self.render_preview()
    except Exception:
        pass
