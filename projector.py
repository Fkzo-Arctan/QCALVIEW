




import math
import numpy as np



def hfov_from_focal_sensor(focal_mm, sensor_width_mm):

    f = float(focal_mm); sw = float(sensor_width_mm)
    if f <= 0 or sw <= 0:
        return 60.0
    return math.degrees(2.0 * math.atan(sw / (2.0 * f)))

def vfov_from_hfov_ratio(hfov_deg, width_px, height_px):

    hf = math.radians(max(1e-6, float(hfov_deg)))
    w = max(1, int(width_px)); h = max(1, int(height_px))
    return math.degrees(2.0 * math.atan(math.tan(hf/2.0) * (h / float(w))))



def _dot(a,b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _cross(a,b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def _norm(a): return math.sqrt(_dot(a,a))
def _normalize(a):
    n = _norm(a)
    if n == 0: return [0.0,0.0,0.0]
    return [a[0]/n, a[1]/n, a[2]/n]

def _basis_from_yaw_pitch_roll(yaw_deg, pitch_deg, roll_deg):
    
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)

    cy = math.cos(yaw); sy = math.sin(yaw)
    cp = math.cos(pitch); sp = math.sin(pitch)

    f = [sy*cp, cy*cp, sp]  
    world_up = [0.0, 0.0, 1.0]

    r0 = _normalize(_cross(f, world_up))
    if _norm(r0) < 1e-8:
        world_up = [0.0, 1.0, 0.0]
        r0 = _normalize(_cross(f, world_up))
    u0 = _normalize(_cross(r0, f))

    cr = math.cos(roll); sr = math.sin(roll)
    r = [r0[0]*cr + u0[0]*sr, r0[1]*cr + u0[1]*sr, r0[2]*cr + u0[2]*sr]
    u = [-r0[0]*sr + u0[0]*cr, -r0[1]*sr + u0[1]*cr, -r0[2]*sr + u0[2]*cr]
    r = _normalize(r); u = _normalize(u); f = _normalize(f)
    return r, u, f



def _validated_vfov_for_cylindrical(vfov_deg, hfov_deg, width, height):
    vf = math.radians(max(1e-6, float(vfov_deg)))
    hf = math.radians(max(1e-6, float(hfov_deg)))
    W = max(1, int(width)); H = max(1, int(height))
    if not (math.isfinite(vf) and 1e-6 < vf < math.pi - 1e-3 and abs(math.tan(vf * 0.5)) > 1e-9):
        vf = 2.0 * math.atan((H * hf) / max(1e-9, 2.0 * W))
    return vf


def _project_cylindrical_uv(alpha, beta, width, height, HFOV, VFOV, is360=False,
                            soft_clip_px=0.0, hard_clip=True):
    W = max(1, int(width)); H = max(1, int(height))
    hf = math.radians(max(1e-6, float(HFOV)))
    vf = _validated_vfov_for_cylindrical(VFOV, HFOV, W, H)
    fx = W / max(1e-9, hf)
    fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
    m_alpha = hf * (float(soft_clip_px) / max(1.0, W))
    m_beta = vf * (float(soft_clip_px) / max(1.0, H))

    if is360:
        u = (alpha + math.pi) / (2.0 * math.pi) * W
    else:
        if hard_clip and abs(alpha) > (0.5 * hf + m_alpha):
            return None
        u = W * 0.5 + fx * alpha

    if hard_clip and abs(beta) > (0.5 * vf + m_beta):
        return None

    v = H * 0.5 - fy * math.tan(beta)
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    return (u, v)


def _project_cylindrical_batch(alpha, beta, width, height, HFOV, VFOV, is360=False):
    W = max(1, int(width)); H = max(1, int(height))
    hf = math.radians(max(1e-6, float(HFOV)))
    vf = _validated_vfov_for_cylindrical(VFOV, HFOV, W, H)
    fx = W / max(1e-9, hf)
    fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))

    if is360:
        u = (alpha + math.pi) / (2.0 * math.pi) * W
        mask = np.isfinite(alpha) & np.isfinite(beta) & (np.abs(beta) <= (0.5 * vf + 1e-9))
    else:
        u = W * 0.5 + fx * alpha
        mask = np.isfinite(alpha) & np.isfinite(beta) & (np.abs(alpha) <= (0.5 * hf + 1e-9)) & (np.abs(beta) <= (0.5 * vf + 1e-9))

    v = H * 0.5 - fy * np.tan(beta)
    mask &= np.isfinite(u) & np.isfinite(v)
    return u, v, mask


def project_point(cam_pt, cam_z, pt, tr,
                  proj, width, height,
                  yaw, pitch, roll,
                  HFOV, VFOV, is360,
                  dist_max=None, z_tgt=None, z_sampler=None,
                  soft_clip_px=0, hard_clip=True):

    
    try:
        p = tr.transform(pt) if tr is not None else pt
        px = float(p.x()); py = float(p.y())
    except Exception:
        try:
            px, py = float(pt[0]), float(pt[1])
        except Exception:
            return None

    
    if z_tgt is not None:
        pz = float(z_tgt)
    elif z_sampler is not None:
        try:
            from qgis.core import QgsPointXY
            pz = float(z_sampler(QgsPointXY(px, py)))
        except Exception:
            try:
                dummy = type('P', (), {'x': lambda self=px: px, 'y': lambda self=py: py})()
                pz = float(z_sampler(dummy))
            except Exception:
                pz = 0.0
    else:
        pz = 0.0

    
    cx = float(cam_pt.x()) if hasattr(cam_pt, 'x') else float(cam_pt[0])
    cy = float(cam_pt.y()) if hasattr(cam_pt, 'y') else float(cam_pt[1])
    dx = px - cx
    dy = py - cy
    dz = pz - float(cam_z)

    if dist_max is not None and (dx*dx + dy*dy) > float(dist_max)**2:
        return None

    r, u, f = _basis_from_yaw_pitch_roll(yaw, pitch, roll)

    
    xc = _dot([dx,dy,dz], r)   
    yc = _dot([dx,dy,dz], f)   
    zc = _dot([dx,dy,dz], u)   

    W = int(width); H = int(height)
    if W <= 1 or H <= 1:
        return None

    
    if str(proj).upper() == "PINHOLE":
        if yc <= 1e-6:
            return None
        hf = math.radians(max(1e-6, float(HFOV)))
        vf = math.radians(max(1e-6, float(VFOV)))
        fx = (W * 0.5) / math.tan(hf * 0.5)
        fy = (H * 0.5) / math.tan(vf * 0.5)
        u_img = W*0.5 + fx * (xc / yc)
        v_img = H*0.5 - fy * (zc / yc)
        return (u_img, v_img) if (math.isfinite(u_img) and math.isfinite(v_img)) else None

    
    alpha = math.atan2(xc, yc)              
    rho   = math.hypot(xc, yc)
    beta  = math.atan2(zc, rho)             

    
    m_alpha = math.radians(HFOV) * (float(soft_clip_px) / max(1.0, W))
    m_beta  = math.radians(VFOV) * (float(soft_clip_px) / max(1.0, H))

    
    if str(proj).upper() == "EQUIRECT":
        if is360:
            
            u = (alpha + math.pi) / (2.0*math.pi) * W
            v = (math.pi/2.0 - beta) / math.pi * H
            return (u, v) if (math.isfinite(u) and math.isfinite(v)) else None
        else:
            
            hf = math.radians(max(1e-6, float(HFOV)))
            vf = math.radians(max(1e-6, float(VFOV)))
            
            u = W*0.5 * (1.0 + alpha / (hf*0.5))
            v = H*0.5 * (1.0 - beta  / (vf*0.5))
            if not (math.isfinite(u) and math.isfinite(v)):
                return None
            
            eps = 1e-2
            if W > 1:
                u = min(max(u, eps), W - eps)
            if H > 1:
                v = min(max(v, eps), H - eps)
            return (u, v)
    
    if proj.upper() == "CYLINDRICAL":
        return _project_cylindrical_uv(alpha, beta, W, H, HFOV, VFOV, is360=bool(is360),
                                       soft_clip_px=soft_clip_px, hard_clip=hard_clip)

    return None




def build_camera_context(cam_pt, cam_z, proj, width, height, yaw, pitch, roll, HFOV, VFOV, is360):

    cx = float(cam_pt.x()) if hasattr(cam_pt, 'x') else float(cam_pt[0])
    cy = float(cam_pt.y()) if hasattr(cam_pt, 'y') else float(cam_pt[1])
    r, u, f = _basis_from_yaw_pitch_roll(yaw, pitch, roll)
    return {
        'cx': cx, 'cy': cy, 'cam_z': float(cam_z),
        'proj': str(proj).upper(), 'width': int(width), 'height': int(height),
        'yaw': float(yaw), 'pitch': float(pitch), 'roll': float(roll),
        'HFOV': float(HFOV), 'VFOV': float(VFOV), 'is360': bool(is360),
        'r': np.asarray(r, dtype=np.float64),
        'u': np.asarray(u, dtype=np.float64),
        'f': np.asarray(f, dtype=np.float64),
    }


def project_points_batch(ctx, pts_xy, z_tgt, dist_max=None):

    pts_xy = np.asarray(pts_xy, dtype=np.float64)
    if pts_xy.ndim != 2 or pts_xy.shape[1] != 2:
        raise ValueError('pts_xy must be an array of shape (N,2)')
    n = pts_xy.shape[0]
    uvs = np.full((n, 2), np.nan, dtype=np.float64)
    if n == 0:
        return uvs

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
        return uvs

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
        return uvs

    if proj == 'CYLINDRICAL':
        u, v, finite = _project_cylindrical_batch(alpha, beta, W, H, ctx['HFOV'], ctx['VFOV'], is360=bool(ctx.get('is360', False)))
        finite = mask & finite
        uvs[finite, 0] = u[finite]
        uvs[finite, 1] = v[finite]
        return uvs

    return uvs
