



from ._exceptions import qcv_suppress_exception as _qcv_suppress
from typing import List, Tuple, Optional, Dict
import math
import numpy as np
from qgis.core import QgsPointXY
from ..projector import build_camera_context, project_points_batch




Segment2D = Tuple[Tuple[float, float], Tuple[float, float]]   
Polyline2D = List[Tuple[float, float]]                        




def effective_radius(R_earth: float = 6370000.0, k: float = 1.0/6.0, enabled: bool = True) -> float:
    
    if not enabled:
        return float('inf')  
    k = max(-0.5, min(0.49, float(k)))  
    return R_earth / (1.0 - k)

def curvature_drop(d: np.ndarray, R_eff: float) -> np.ndarray:
    
    if not np.isfinite(R_eff):
        return np.zeros_like(d)
    return (d * d) / (2.0 * R_eff)




def build_adaptive_grid(
    maxdist: float,
    base_spacing: float,
    D_adapt: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    
    maxdist = float(maxdist)
    s0 = float(base_spacing)
    D = float(D_adapt if D_adapt > 0 else (100.0 * s0))

    n = int(math.ceil(maxdist / s0))
    xs = np.linspace(-n * s0, n * s0, 2 * n + 1)
    ys = np.linspace(-n * s0, n * s0, 2 * n + 1)
    X, Y = np.meshgrid(xs, ys)  

    
    L1 = np.abs(X) + np.abs(Y)
    rings = np.floor(L1 / D).astype(np.int32)  
    
    stride = np.left_shift(1, np.clip(rings, 0, 20))  

    
    M, N = X.shape
    row_idx = np.arange(M).reshape(-1, 1).repeat(N, axis=1)
    col_idx = np.arange(N).reshape(1, -1).repeat(M, axis=0)

    
    
    mask = ((row_idx % stride) == 0) & ((col_idx % stride) == 0)

    
    R = np.hypot(X, Y)
    mask &= (R <= maxdist + 1e-6)

    return X, Y, mask






def build_radial_distances(d_step_min: float, maxdist: float) -> np.ndarray:
    
    d_step_min = max(1.0, float(d_step_min))
    maxdist = max(d_step_min, float(maxdist))
    vals = []
    d = d_step_min
    step = d_step_min
    while d <= maxdist + 1e-9:
        vals.append(d)
        
        if d < 5000.0:
            step = max(d_step_min, step * 1.04)
        elif d < 10000.0:
            step = max(d_step_min * 1.5, step * 1.06)
        elif d < 20000.0:
            step = max(d_step_min * 2.5, step * 1.08)
        else:
            step = max(d_step_min * 4.0, step * 1.10)
        d += step
    return np.asarray(vals, dtype=np.float64)

def sample_dem_Z(
    X: np.ndarray, Y: np.ndarray, mask: np.ndarray,
    sampler_callable,
    cam_xy: tuple,
    R_eff: float
):
    
    return sample_dem_Z_optimized(X, Y, mask, sampler_callable, cam_xy, R_eff)


def sample_dem_Z_optimized(
    X: np.ndarray, Y: np.ndarray, mask: np.ndarray,
    sampler_callable,
    cam_xy: tuple,
    R_eff: float
):
    
    cx, cy = cam_xy
    xs = X[mask].ravel()
    ys = Y[mask].ravel()
    d = np.hypot(xs, ys)
    
    
    pts = np.column_stack((xs + cx, ys + cy))
    
    
    if hasattr(sampler_callable, 'batch'):
        z = sampler_callable.batch(pts)
    else:
        
        z = np.empty(pts.shape[0], dtype=np.float64)
        for i in range(pts.shape[0]):
            z[i] = float(sampler_callable(QgsPointXY(pts[i, 0], pts[i, 1])))
    
    
    h = curvature_drop(d, R_eff)
    z_corr = z - h
    
    Z = np.full_like(X, np.nan, dtype=np.float64)
    Z[mask] = z_corr
    return Z




def project_points(
    cam_pt_xy: Tuple[float,float], cam_z: float,
    pts_xy: np.ndarray, z_tgt: np.ndarray,
    projector,                       
    proj_name: str, width: int, height: int,
    yaw: float, pitch: float, roll: float,
    HFOV: float, VFOV: float, is360: bool,
    dist_max: Optional[float]
) -> np.ndarray:
    
    uvs = np.full((pts_xy.shape[0], 2), np.nan, dtype=np.float64)
    cx, cy = cam_pt_xy
    for i in range(pts_xy.shape[0]):
        x, y = pts_xy[i, 0], pts_xy[i, 1]
        uv = projector(
            QgsPointXY(cx, cy), cam_z,
            QgsPointXY(x, y), None,  
            proj_name, width, height, yaw, pitch, roll, HFOV, VFOV, is360,
            dist_max=dist_max, z_tgt=float(z_tgt[i]), z_sampler=None
        )
        if uv is not None and np.isfinite(uv[0]) and np.isfinite(uv[1]):
            uvs[i, 0] = uv[0]
            uvs[i, 1] = uv[1]
    return uvs





def _draw_dem_wireframe_sparse_adaptive(
    cam_xy: Tuple[float,float], cam_z: float,
    proj_name: str, width: int, height: int,
    yaw: float, pitch: float, roll: float, HFOV: float, VFOV: float, is360: bool,
    maxdist: float, z_sampler, base_spacing: float, D_adapt: Optional[float],
    wire_mode: int = 1, horizon_data=None, visibility_eps_deg: float = 0.2,
    curvature_enabled: bool = True, R_earth: float = 6370000.0,
    k_refraction: float = 1.0/6.0
) -> List[Segment2D]:
    
    maxdist = max(0.0, float(maxdist))
    s0 = max(0.1, float(base_spacing))
    D = float(D_adapt if (D_adapt is not None and D_adapt > 0) else (100.0 * s0))
    n = int(math.ceil(maxdist / s0))
    size = 2 * n + 1
    if size < 2:
        return []

    js_all = np.arange(size, dtype=np.int32)
    x_all = (js_all.astype(np.float64) - float(n)) * s0
    r2_lim = maxdist * maxdist + 1e-6
    ii_parts = []; jj_parts = []; x_parts = []; y_parts = []

    
    for i in range(size):
        y = (float(i) - float(n)) * s0
        rings = np.floor((np.abs(x_all) + abs(y)) / D).astype(np.int32, copy=False)
        stride = np.left_shift(np.int32(1), np.clip(rings, 0, 20))
        keep = ((int(i) % stride) == 0) & ((js_all % stride) == 0)
        keep &= ((x_all * x_all + y * y) <= r2_lim)
        jj = js_all[keep]
        if jj.size == 0:
            continue
        ii_parts.append(np.full(jj.shape, int(i), dtype=np.int32))
        jj_parts.append(jj.astype(np.int32, copy=False))
        x_parts.append(x_all[keep].astype(np.float64, copy=False))
        y_parts.append(np.full(jj.shape, y, dtype=np.float64))

    if not ii_parts:
        return []
    ii = np.concatenate(ii_parts)
    jj = np.concatenate(jj_parts)
    Xn = np.concatenate(x_parts)
    Yn = np.concatenate(y_parts)
    count = int(ii.size)
    if count < 2:
        return []

    cx, cy = float(cam_xy[0]), float(cam_xy[1])
    pts_world = np.column_stack((cx + Xn, cy + Yn))
    batch = getattr(z_sampler, 'batch', None)
    if callable(batch):
        try:
            Zn = np.asarray(batch(pts_world), dtype=np.float64).reshape(-1)
        except Exception:
            Zn = np.full(count, np.nan, dtype=np.float64)
    else:
        Zn = np.full(count, np.nan, dtype=np.float64)
        for q in range(count):
            try:
                Zn[q] = float(z_sampler(QgsPointXY(float(pts_world[q,0]), float(pts_world[q,1]))))
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_topography_ops.py:233")
    if Zn.size != count:
        try:
            Zn = np.resize(Zn, count).astype(np.float64, copy=False)
        except Exception:
            return []

    R_eff = effective_radius(R_earth, k_refraction, enabled=curvature_enabled)
    dist = np.hypot(Xn, Yn)
    Zn = Zn - curvature_drop(dist, R_eff)
    valid = np.isfinite(Zn)

    U = np.full(count, np.nan, dtype=np.float64)
    V = np.full(count, np.nan, dtype=np.float64)
    if bool(valid.any()):
        ids = np.flatnonzero(valid)
        ctx = build_camera_context(
            (cx, cy), float(cam_z), proj_name, int(width), int(height),
            float(yaw), float(pitch), float(roll), float(HFOV), float(VFOV), bool(is360)
        )
        uv = project_points_batch(ctx, pts_world[ids], Zn[ids], dist_max=float(maxdist))
        if uv.shape[0] == ids.size:
            U[ids] = uv[:,0]; V[ids] = uv[:,1]

    horizon_visible = None
    if horizon_data is not None and int(wire_mode) != 0:
        try:
            az_bins = np.asarray(horizon_data.get('az_bins'), dtype=np.float64)
            d_values = np.asarray(horizon_data.get('d_values'), dtype=np.float64)
            el_cummax = np.asarray(horizon_data.get('el_cummax'), dtype=np.float64)
            n_bins = int(az_bins.size); n_dist = int(d_values.size)
            if n_bins > 0 and n_dist > 0 and el_cummax.ndim == 2 and el_cummax.shape[0] >= n_bins and el_cummax.shape[1] >= n_dist:
                az = np.degrees(np.arctan2(Xn, Yn))
                elev = np.degrees(np.arctan2(Zn - float(cam_z), np.maximum(dist, 1e-9)))
                az_min = float(horizon_data.get('az_min', az_bins[0])); az_max = float(horizon_data.get('az_max', az_bins[-1]))
                center = 0.5 * (az_min + az_max)
                az_un = center + (((az - center) + 180.0) % 360.0) - 180.0
                if az_max > az_min and n_bins > 1:
                    frac = np.clip((az_un - az_min) / (az_max - az_min), 0.0, 1.0)
                    iaz = np.rint(frac * float(n_bins - 1)).astype(np.int64)
                else:
                    iaz = np.zeros(count, dtype=np.int64)
                jdist = np.searchsorted(d_values, dist, side='left') - 1
                has_front = jdist >= 0
                jclip = np.clip(jdist, 0, n_dist - 1)
                env = el_cummax[iaz, jclip]
                horizon_visible = valid & ((~has_front) | (~np.isfinite(env)) | (elev >= (env - float(visibility_eps_deg))))
                horizon_visible &= np.isfinite(elev)
        except Exception:
            horizon_visible = None

    
    
    
    if int(wire_mode) == 2 and horizon_visible is None:
        return []

    def node_ok(q):
        if not valid[q]:
            return False
        if horizon_visible is None or int(wire_mode) == 0:
            return True
        return bool(horizon_visible[q])

    segs = []
    def append_pair(a, b):
        ua,va,ub,vb = float(U[a]),float(V[a]),float(U[b]),float(V[b])
        if not (math.isfinite(ua) and math.isfinite(va) and math.isfinite(ub) and math.isfinite(vb)):
            return
        if int(wire_mode) != 0 and horizon_visible is not None:
            vaa = node_ok(a); vbb = node_ok(b)
            keep = (vaa and vbb) if int(wire_mode) == 2 else (vaa or vbb)
            if not keep:
                return
        segs.append(((ua,va),(ub,vb)))

    
    start = 0
    while start < count:
        row = int(ii[start]); end = start + 1
        while end < count and int(ii[end]) == row:
            end += 1
        for q in range(start, end - 1):
            append_pair(q, q + 1)
        start = end

    
    order = np.lexsort((ii, jj))
    start = 0
    olen = int(order.size)
    while start < olen:
        a0 = int(order[start]); col = int(jj[a0]); end = start + 1
        while end < olen and int(jj[int(order[end])]) == col:
            end += 1
        for q in range(start, end - 1):
            append_pair(int(order[q]), int(order[q + 1]))
        start = end
    return segs


def draw_dem_wireframe(
    cam_xy: Tuple[float,float], cam_z: float,
    projector,
    proj_name: str, width: int, height: int,
    yaw: float, pitch: float, roll: float, HFOV: float, VFOV: float, is360: bool,
    maxdist: float,
    z_sampler,
    base_spacing: float,
    D_adapt: Optional[float],
    visibility_test=None,
    wire_mode: int = 1,
    horizon_data=None,
    visibility_eps_deg: float = 0.2,
    curvature_enabled: bool = True,
    R_earth: float = 6370000.0,
    k_refraction: float = 1.0/6.0
) -> List[Segment2D]:
    
    R_eff = effective_radius(R_earth, k_refraction, enabled=curvature_enabled)
    
    
    
    
    _n_est = int(math.ceil(float(maxdist) / max(0.1, float(base_spacing))))
    _dense_cells_est = int(2 * _n_est + 1) ** 2
    _proj_upper = str(proj_name or '').strip().upper()
    _is_panorama = _proj_upper in ('EQUIRECT', 'EQUIRECTANGULAR', 'CYLINDRICAL')
    
    
    if _is_panorama and _dense_cells_est > 1200000:
        return _draw_dem_wireframe_sparse_adaptive(
            cam_xy=cam_xy, cam_z=cam_z, proj_name=proj_name, width=width, height=height,
            yaw=yaw, pitch=pitch, roll=roll, HFOV=HFOV, VFOV=VFOV, is360=is360,
            maxdist=maxdist, z_sampler=z_sampler, base_spacing=base_spacing, D_adapt=D_adapt,
            wire_mode=wire_mode, horizon_data=horizon_data, visibility_eps_deg=visibility_eps_deg,
            curvature_enabled=curvature_enabled, R_earth=R_earth, k_refraction=k_refraction
        )
    X, Y, mask = build_adaptive_grid(maxdist, base_spacing, D_adapt or (100.0 * base_spacing))
    Z = sample_dem_Z(X, Y, mask, z_sampler, cam_xy, R_eff)

    segs: List[Segment2D] = []
    M, N = X.shape

    
    
    
    valid_nodes = mask & np.isfinite(Z)
    U = np.full(X.shape, np.nan, dtype=np.float64)
    V = np.full(X.shape, np.nan, dtype=np.float64)
    if np.any(valid_nodes):
        ii, jj = np.where(valid_nodes)
        pts_xy = np.column_stack((
            float(cam_xy[0]) + X[ii, jj],
            float(cam_xy[1]) + Y[ii, jj],
        )).astype(np.float64, copy=False)
        z_vals = np.asarray(Z[ii, jj], dtype=np.float64)
        ctx = build_camera_context(
            (float(cam_xy[0]), float(cam_xy[1])), float(cam_z),
            proj_name, int(width), int(height),
            float(yaw), float(pitch), float(roll),
            float(HFOV), float(VFOV), bool(is360)
        )
        uv = project_points_batch(ctx, pts_xy, z_vals, dist_max=float(maxdist))
        if uv.shape[0] == ii.shape[0]:
            U[ii, jj] = uv[:, 0]
            V[ii, jj] = uv[:, 1]

    
    
    
    
    horizon_visible = None
    if horizon_data is not None and int(wire_mode) != 0:
        try:
            az_bins = np.asarray(horizon_data.get('az_bins'), dtype=np.float64)
            d_values = np.asarray(horizon_data.get('d_values'), dtype=np.float64)
            el_cummax = np.asarray(horizon_data.get('el_cummax'), dtype=np.float64)
            n_bins = int(az_bins.size)
            n_dist = int(d_values.size)
            if n_bins > 0 and n_dist > 0 and el_cummax.ndim == 2 and el_cummax.shape[0] >= n_bins and el_cummax.shape[1] >= n_dist:
                dist = np.hypot(X, Y)
                az = np.degrees(np.arctan2(X, Y))
                elev = np.degrees(np.arctan2(Z - float(cam_z), np.maximum(dist, 1e-9)))
                az_min = float(horizon_data.get('az_min', az_bins[0]))
                az_max = float(horizon_data.get('az_max', az_bins[-1]))
                center = 0.5 * (az_min + az_max)
                az_un = center + (((az - center) + 180.0) % 360.0) - 180.0
                if az_max > az_min and n_bins > 1:
                    frac = np.clip((az_un - az_min) / (az_max - az_min), 0.0, 1.0)
                    iaz = np.rint(frac * float(n_bins - 1)).astype(np.int64)
                else:
                    iaz = np.zeros(X.shape, dtype=np.int64)
                jdist = np.searchsorted(d_values, dist, side='left') - 1
                has_front = jdist >= 0
                jclip = np.clip(jdist, 0, n_dist - 1)
                env = el_cummax[iaz, jclip]
                horizon_visible = valid_nodes & ((~has_front) | (~np.isfinite(env)) | (elev >= (env - float(visibility_eps_deg))))
                horizon_visible &= np.isfinite(elev)
        except Exception:
            horizon_visible = None

    
    
    if int(wire_mode) == 2 and horizon_visible is None:
        return []

    def _node_vis(i: int, j: int) -> bool:
        if horizon_visible is not None:
            return bool(horizon_visible[i, j])
        if visibility_test is None:
            return True
        if not valid_nodes[i, j]:
            return False
        try:
            x = float(cam_xy[0] + X[i, j])
            y = float(cam_xy[1] + Y[i, j])
            z = float(Z[i, j])
            return bool(visibility_test(x, y, z))
        except Exception:
            return True

    def _append_run(indices, fixed_index: int, horizontal: bool):
        if len(indices) < 2:
            return
        prev_vis = None
        for k in range(len(indices) - 1):
            a_idx = int(indices[k]); b_idx = int(indices[k + 1])
            if horizontal:
                ia, ja = fixed_index, a_idx
                ib, jb = fixed_index, b_idx
            else:
                ia, ja = a_idx, fixed_index
                ib, jb = b_idx, fixed_index

            ua, va = U[ia, ja], V[ia, ja]
            ub, vb = U[ib, jb], V[ib, jb]
            if not (np.isfinite(ua) and np.isfinite(va) and np.isfinite(ub) and np.isfinite(vb)):
                continue

            keep = True
            if (visibility_test is not None or horizon_visible is not None) and wire_mode != 0:
                vis_a = _node_vis(ia, ja)
                vis_b = _node_vis(ib, jb)
                keep = bool(vis_a or vis_b)
                if (not keep) and valid_nodes[ia, ja] and valid_nodes[ib, jb]:
                    mx = 0.5 * (float(cam_xy[0] + X[ia, ja]) + float(cam_xy[0] + X[ib, jb]))
                    my = 0.5 * (float(cam_xy[1] + Y[ia, ja]) + float(cam_xy[1] + Y[ib, jb]))
                    mz = 0.5 * (float(Z[ia, ja]) + float(Z[ib, jb]))
                    try:
                        keep = bool(visibility_test(mx, my, mz))
                    except Exception:
                        keep = False
                if wire_mode == 2:
                    keep = bool(vis_a and vis_b)

            if keep:
                segs.append(((float(ua), float(va)), (float(ub), float(vb))))

    
    for i in range(M):
        _append_run(np.where(mask[i])[0], i, True)
    for j in range(N):
        _append_run(np.where(mask[:, j])[0], j, False)

    return segs




def draw_dem_horizon(
    cam_xy: Tuple[float,float], cam_z: float,
    projector,
    proj_name: str, width: int, height: int,
    yaw: float, pitch: float, roll: float, HFOV: float, VFOV: float, is360: bool,
    maxdist: float,
    z_sampler,
    az_step_deg: float,
    d_step_min: float,
    curvature_enabled: bool = True,
    R_earth: float = 6370000.0,
    k_refraction: float = 1.0/6.0
) -> Polyline2D:
    
    R_eff = effective_radius(R_earth, k_refraction, enabled=curvature_enabled)
    cx, cy = cam_xy
    poly: Polyline2D = []

    
    
    half = HFOV * 0.5 if not is360 else 180.0
    az0 = yaw - half
    az1 = yaw + half
    n_az = max(4, int(math.ceil((az1 - az0) / max(0.05, az_step_deg))))
    az_list = np.linspace(az0, az1, n_az, endpoint=True)

    for az in az_list:
        th = math.radians(az)
        
        d_values = build_radial_distances(d_step_min, maxdist)
        alpha_max = -1e9
        best_xy = None
        for d in d_values:
            x = cx + d * math.sin(th)
            y = cy + d * math.cos(th)
            z = float(z_sampler(QgsPointXY(x, y)))
            z -= float(curvature_drop(np.asarray([d]), R_eff)[0])  
            elev = math.degrees(math.atan2(z - cam_z, d))
            if elev >= alpha_max:
                alpha_max = elev
                best_xy = (x, y, z)

        if best_xy is None:
            continue

        uv = projector(
            QgsPointXY(cx, cy), cam_z,
            QgsPointXY(best_xy[0], best_xy[1]), None,
            proj_name, width, height, yaw, pitch, roll, HFOV, VFOV, is360,
            dist_max=maxdist, z_tgt=best_xy[2], z_sampler=None
        )
        if uv is not None and np.isfinite(uv[0]) and np.isfinite(uv[1]):
            poly.append((float(uv[0]), float(uv[1])))

    return poly




def draw_dem_ridgelines(
    cam_xy: Tuple[float,float], cam_z: float,
    projector,
    proj_name: str, width: int, height: int,
    yaw: float, pitch: float, roll: float, HFOV: float, VFOV: float, is360: bool,
    maxdist: float,
    z_sampler,
    base_spacing: float,
    near_dist_for_ridges: float = 10000.0,  
    angle_deg: float = 2.0,
    visibility_test=None,
    curvature_enabled: bool = True,
    R_earth: float = 6370000.0,
    k_refraction: float = 1.0/6.0
) -> List[Segment2D]:
    
    R_eff = effective_radius(R_earth, k_refraction, enabled=curvature_enabled)
    s0 = float(base_spacing)
    n = int(math.ceil(near_dist_for_ridges / s0))
    xs = np.linspace(-n * s0, n * s0, 2 * n + 1)
    ys = np.linspace(-n * s0, n * s0, 2 * n + 1)
    X, Y = np.meshgrid(xs, ys)
    R = np.hypot(X, Y)
    mask = (R <= near_dist_for_ridges + 1e-6)

    
    Z = np.full_like(X, np.nan, dtype=np.float64)
    cx, cy = cam_xy
    xs_f = X[mask].ravel(); ys_f = Y[mask].ravel()
    d = np.hypot(xs_f, ys_f)
    z = np.empty_like(xs_f, dtype=np.float64)
    for i in range(xs_f.size):
        z[i] = float(z_sampler(QgsPointXY(cx + xs_f[i], cy + ys_f[i])))
    z -= curvature_drop(d, R_eff)
    Z[mask] = z

    
    
    
    nX = np.full_like(Z, np.nan); nY = np.full_like(Z, np.nan)
    nX[:, 1:-1] = (Z[:, 2:] - Z[:, :-2]) / (2.0 * s0)
    nY[1:-1, :] = (Z[2:, :] - Z[:-2, :]) / (2.0 * s0)

    
    
    segs: List[Segment2D] = []
    thres = float(angle_deg)

    M, N = Z.shape
    for i in range(1, M - 1):
        for j in range(1, N - 1):
            if not np.isfinite(Z[i, j]): 
                continue
            
            x = cx + X[i, j]; y = cy + Y[i, j]; zc = Z[i, j]
            vx, vy, vz = (cx - x), (cy - y), (cam_z - zc)
            vnorm = math.sqrt(vx*vx + vy*vy + vz*vz)
            if vnorm < 1e-6: 
                continue
            vx /= vnorm; vy /= vnorm; vz /= vnorm

            
            if not (np.isfinite(nX[i, j]) and np.isfinite(nY[i, j])):
                continue
            nx, ny, nz = (-nX[i, j], -nY[i, j], 1.0)
            nnorm = math.sqrt(nx*nx + ny*ny + nz*nz)
            if nnorm < 1e-6: 
                continue
            nx /= nnorm; ny /= nnorm; nz /= nnorm

            
            
            dot = max(-1.0, min(1.0, nx*vx + ny*vy + nz*vz))
            theta = math.degrees(math.acos(dot))
            beta = 90.0 - theta

            
            if beta < thres:
                
                
                for di, dj in [(0, 1), (1, 0)]:
                    ii, jj = i + di, j + dj
                    if ii >= M or jj >= N: 
                        continue
                    if not np.isfinite(Z[ii, jj]): 
                        continue
                    
                    uv1 = projector(
                        QgsPointXY(cx, cy), cam_z,
                        QgsPointXY(x, y), None,
                        proj_name, width, height, yaw, pitch, roll, HFOV, VFOV, is360,
                        dist_max=maxdist, z_tgt=zc, z_sampler=None
                    )
                    x2 = cx + X[ii, jj]; y2 = cy + Y[ii, jj]; z2 = Z[ii, jj]
                    uv2 = projector(
                        QgsPointXY(cx, cy), cam_z,
                        QgsPointXY(x2, y2), None,
                        proj_name, width, height, yaw, pitch, roll, HFOV, VFOV, is360,
                        dist_max=maxdist, z_tgt=z2, z_sampler=None
                    )
                    if (uv1 is not None and uv2 is not None and
                        np.isfinite(uv1[0]) and np.isfinite(uv1[1]) and
                        np.isfinite(uv2[0]) and np.isfinite(uv2[1])):
                        keep = True
                        if visibility_test is not None:
                            try:
                                vis1 = bool(visibility_test(x, y, zc))
                                vis2 = bool(visibility_test(x2, y2, z2))
                                vm = bool(visibility_test(0.5 * (x + x2), 0.5 * (y + y2), 0.5 * (zc + z2)))
                                keep = bool(vis1 or vis2 or vm)
                            except Exception:
                                keep = True
                        if keep:
                            segs.append(((float(uv1[0]), float(uv1[1])), (float(uv2[0]), float(uv2[1]))))

    return segs
