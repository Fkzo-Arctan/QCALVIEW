



from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence
import math
import numpy as np

from ..projector import project_points_batch, _validated_vfov_for_cylindrical


PANORAMIC_PROJECTIONS = ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL")


def _finite_uv_rows_scalar(arr, rows=None):
    
    try:
        a = arr
        n = int(len(a) if rows is None else min(int(rows), len(a)))
        if n <= 0:
            return False
        for i in range(n):
            if not (math.isfinite(float(a[i][0])) and math.isfinite(float(a[i][1]))):
                return False
        return True
    except Exception:
        return False


def _finite_positive_depth_scalar(arr, rows=None):
    try:
        a = arr
        n = int(len(a) if rows is None else min(int(rows), len(a)))
        if n <= 0:
            return False
        for i in range(n):
            v = float(a[i])
            if not (math.isfinite(v) and v > 0.0):
                return False
        return True
    except Exception:
        return False


def _xy_distance2_scalar(a, b):
    try:
        dx = float(a[0]) - float(b[0])
        dy = float(a[1]) - float(b[1])
        return dx * dx + dy * dy
    except Exception:
        return math.inf


@dataclass
class PanoramicPrimitive2D:
    
    kind: str
    uv: np.ndarray
    radial_depth: np.ndarray
    world_xyz: np.ndarray
    texture_uv: Optional[np.ndarray] = None
    closed: bool = False
    role: str = "generic"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PanoramicFace:
    
    uv: np.ndarray
    radial_depth: np.ndarray
    world_xyz: np.ndarray
    texture_uv: Optional[np.ndarray] = None
    role: str = "surface"
    metadata: Dict[str, Any] = field(default_factory=dict)


def is_panorama_context(ctx) -> bool:
    return str((ctx or {}).get("proj", "") or "").upper() in PANORAMIC_PROJECTIONS


def project_panorama_point_scalar(ctx, x: float, y: float, z: float, dist_max=None, *, clip_to_fov=True):
    
    try:
        x=float(x); y=float(y); z=float(z)
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None
        dx=x-float(ctx['cx']); dy=y-float(ctx['cy']); dz=z-float(ctx['cam_z'])
        if dist_max is not None:
            dm=float(dist_max)
            if dm > 0.0 and (dx*dx+dy*dy) > dm*dm:
                return None
        r=ctx['r']; up=ctx['u']; f=ctx['f']
        xc=dx*float(r[0])+dy*float(r[1])+dz*float(r[2])
        yc=dx*float(f[0])+dy*float(f[1])+dz*float(f[2])
        zc=dx*float(up[0])+dy*float(up[1])+dz*float(up[2])
        alpha=math.atan2(xc,yc)
        beta=math.atan2(zc, math.hypot(xc,yc))
        if not (math.isfinite(alpha) and math.isfinite(beta)):
            return None
        W=max(1,int(ctx['width'])); H=max(1,int(ctx['height']))
        proj=str(ctx.get('proj','') or '').upper()
        hf=math.radians(max(1e-6,float(ctx['HFOV'])))
        vf=math.radians(max(1e-6,float(ctx['VFOV'])))
        full360=bool(ctx.get('is360',False))
        if proj in ('EQUIRECT','EQUIRECTANGULAR'):
            if full360:
                uu=(alpha+math.pi)/(2.0*math.pi)*W
                vv=(math.pi*0.5-beta)/math.pi*H
            else:
                uu=W*0.5*(1.0+alpha/max(1e-12,hf*0.5))
                vv=H*0.5*(1.0-beta/max(1e-12,vf*0.5))
                if clip_to_fov:
                    eps=1e-2
                    uu=min(max(uu,eps), W-eps if W>1 else eps)
                    vv=min(max(vv,eps), H-eps if H>1 else eps)
        elif proj == 'CYLINDRICAL':
            vf=_validated_vfov_for_cylindrical(ctx['VFOV'],ctx['HFOV'],W,H)
            if clip_to_fov:
                if (not full360) and abs(alpha) > 0.5*hf+1e-9:
                    return None
                if abs(beta) > 0.5*vf+1e-9:
                    return None
            
            
            
            
            if abs(beta) >= (math.pi*0.5-1e-9):
                return None
            uu=(alpha+math.pi)/(2.0*math.pi)*W if full360 else W*0.5+(W/max(1e-9,hf))*alpha
            fy=(H*0.5)/max(1e-9,math.tan(vf*0.5))
            vv=H*0.5-fy*math.tan(beta)
        else:
            return None
        if not (math.isfinite(uu) and math.isfinite(vv)):
            return None
        depth=math.sqrt(dx*dx+dy*dy+dz*dz)
        if not (math.isfinite(depth) and depth>0.0):
            return None
        return (float(uu),float(vv),float(depth))
    except Exception:
        return None


def panorama_quad_rects_scalar(points, wrap_width: float, viewport_width: float, margin_px: float = 2.0):
    
    try:
        pts=[(float(p[0]),float(p[1])) for p in points]
        if len(pts)<4 or any(not (math.isfinite(x) and math.isfinite(y)) for x,y in pts):
            return ()
        W=float(wrap_width or 0.0); VW=max(1.0,float(viewport_width or 0.0)); m=max(0.0,float(margin_px))
        if W>1.0:
            unwrapped=[pts[0]]; prev=pts[0][0]
            for x,y in pts[1:]:
                k0=int(round((prev-x)/W))
                c0=x+(k0-1)*W; c1=x+k0*W; c2=x+(k0+1)*W
                bx=min((c0,c1,c2), key=lambda q: abs(q-prev))
                unwrapped.append((float(bx),y)); prev=float(bx)
        else:
            unwrapped=pts
        xs=[q[0] for q in unwrapped]; ys=[q[1] for q in unwrapped]
        minx=min(xs); maxx=max(xs); top=min(ys); bottom=max(ys)
        if W<=1.0:
            if maxx < -m or minx > VW+m:
                return ()
            return ((minx,maxx,top,bottom),)
        
        kmin=int(math.ceil((-m-maxx)/W)); kmax=int(math.floor((VW+m-minx)/W))
        if kmax<kmin:
            return ()
        
        
        kmin=max(kmin,-3); kmax=min(kmax,3)
        out=[]
        for k in range(kmin,kmax+1):
            shift=float(k)*W
            left=minx+shift; right=maxx+shift
            if right < -m or left > VW+m:
                continue
            out.append((float(left),float(right),float(top),float(bottom)))
        return tuple(out)
    except Exception:
        return ()


def radial_depths(ctx, xyz) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] < 3:
        return np.empty((0,), dtype=np.float64)
    dx = xyz[:, 0] - float(ctx["cx"])
    dy = xyz[:, 1] - float(ctx["cy"])
    dz = xyz[:, 2] - float(ctx["cam_z"])
    return np.sqrt(dx * dx + dy * dy + dz * dz)



def project_panorama_points_unclipped(ctx, xyz, dist_max=None):
    
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] < 3:
        return np.empty((0, 2), dtype=np.float64)
    n = xyz.shape[0]
    out = np.full((n, 2), np.nan, dtype=np.float64)
    if n == 0:
        return out
    dx = xyz[:, 0] - float(ctx['cx'])
    dy = xyz[:, 1] - float(ctx['cy'])
    dz = xyz[:, 2] - float(ctx['cam_z'])
    r = ctx['r']; up = ctx['u']; f = ctx['f']
    xc = dx * r[0] + dy * r[1] + dz * r[2]
    yc = dx * f[0] + dy * f[1] + dz * f[2]
    zc = dx * up[0] + dy * up[1] + dz * up[2]
    alpha = np.arctan2(xc, yc)
    beta = np.arctan2(zc, np.hypot(xc, yc))
    mask = np.isfinite(alpha) & np.isfinite(beta)
    if dist_max is not None:
        mask &= (dx * dx + dy * dy) <= float(dist_max) ** 2
    W = max(1, int(ctx['width'])); H = max(1, int(ctx['height']))
    hf = math.radians(max(1e-6, float(ctx['HFOV'])))
    vf = math.radians(max(1e-6, float(ctx['VFOV'])))
    proj = str(ctx.get('proj', '') or '').upper()
    if proj in ('EQUIRECT', 'EQUIRECTANGULAR'):
        if bool(ctx.get('is360', False)):
            uu = (alpha + math.pi) / (2.0 * math.pi) * W
            vv = (math.pi * 0.5 - beta) / math.pi * H
        else:
            uu = W * 0.5 * (1.0 + alpha / (hf * 0.5))
            vv = H * 0.5 * (1.0 - beta / (vf * 0.5))
    elif proj == 'CYLINDRICAL':
        vf = _validated_vfov_for_cylindrical(ctx['VFOV'], ctx['HFOV'], W, H)
        if bool(ctx.get('is360', False)):
            uu = (alpha + math.pi) / (2.0 * math.pi) * W
        else:
            uu = W * 0.5 + (W / max(1e-9, hf)) * alpha
        fy = (H * 0.5) / max(1e-9, math.tan(vf * 0.5))
        vv = H * 0.5 - fy * np.tan(beta)
    else:
        return out
    finite = mask & np.isfinite(uu) & np.isfinite(vv)
    out[finite, 0] = uu[finite]
    out[finite, 1] = vv[finite]
    return out


def project_panorama_primitive(ctx, xyz, dist_max=None, *, kind="polyline", texture_uv=None,
                               closed=False, role="generic", metadata=None, clip_to_fov=True):
    
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[0] == 0 or xyz.shape[1] < 3:
        return PanoramicPrimitive2D(
            kind=str(kind), uv=np.empty((0, 2), dtype=np.float64),
            radial_depth=np.empty((0,), dtype=np.float64),
            world_xyz=np.empty((0, 3), dtype=np.float64),
            texture_uv=None, closed=bool(closed), role=str(role), metadata=dict(metadata or {})
        )
    xyz3 = np.asarray(xyz[:, :3], dtype=np.float64)
    
    
    
    if xyz3.shape[0] <= 8:
        uv = np.full((xyz3.shape[0], 2), np.nan, dtype=np.float64)
        depth = np.full((xyz3.shape[0],), np.inf, dtype=np.float64)
        for i in range(xyz3.shape[0]):
            q = project_panorama_point_scalar(
                ctx, float(xyz3[i,0]), float(xyz3[i,1]), float(xyz3[i,2]),
                dist_max=dist_max, clip_to_fov=bool(clip_to_fov)
            )
            if q is None:
                continue
            uv[i,0], uv[i,1], depth[i] = float(q[0]), float(q[1]), float(q[2])
    else:
        uv = (project_points_batch(ctx, xyz3[:, :2], xyz3[:, 2], dist_max=dist_max)
              if bool(clip_to_fov) else project_panorama_points_unclipped(ctx, xyz3, dist_max=dist_max))
        depth = radial_depths(ctx, xyz3)
    tex = None
    if texture_uv is not None:
        tex = np.asarray(texture_uv, dtype=np.float64)
        if tex.ndim != 2 or tex.shape[0] != xyz3.shape[0] or tex.shape[1] != 2:
            tex = None
    return PanoramicPrimitive2D(
        kind=str(kind), uv=np.asarray(uv, dtype=np.float64),
        radial_depth=np.asarray(depth, dtype=np.float64), world_xyz=xyz3,
        texture_uv=tex, closed=bool(closed), role=str(role), metadata=dict(metadata or {})
    )


def unwrap_x_continuous(pts, wrap_width):
    
    if isinstance(pts, np.ndarray):
        arr = pts
    else:
        arr = np.asarray(pts, dtype=np.float64)
    pts = arr
    W = float(wrap_width or 0.0)
    if pts.ndim != 2 or pts.shape[0] <= 1 or pts.shape[1] != 2 or W <= 1.0:
        return pts.copy()
    out = pts.copy()
    prev_x = float(out[0, 0])
    for i in range(1, out.shape[0]):
        x = float(pts[i, 0])
        k0 = int(round((prev_x - x) / W))
        candidates = (x + (k0 - 1) * W, x + k0 * W, x + (k0 + 1) * W)
        best = min(candidates, key=lambda v: abs(v - prev_x))
        out[i, 0] = float(best)
        prev_x = float(best)
    return out


def _same_wrapped_point(a, b, wrap_width, tol=1e-5):
    try:
        W = float(wrap_width or 0.0)
        dy = abs(float(a[1]) - float(b[1]))
        if dy > tol:
            return False
        dx = abs(float(a[0]) - float(b[0]))
        if dx <= tol:
            return True
        if W > 1.0:
            dx_mod = abs(((float(a[0]) - float(b[0]) + 0.5 * W) % W) - 0.5 * W)
            return dx_mod <= tol
    except Exception:
        return False
    return False


def unwrap_closed_ring(pts, wrap_width):
    
    if not isinstance(pts, np.ndarray):
        pts = np.asarray(pts, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        return pts.copy()
    finite = np.isfinite(pts[:, 0]) & np.isfinite(pts[:, 1])
    pts = pts[finite]
    if pts.shape[0] < 3:
        return pts
    W = float(wrap_width or 0.0)
    if pts.shape[0] >= 4 and _same_wrapped_point(pts[0], pts[-1], W):
        pts = pts[:-1]
    return unwrap_x_continuous(pts, W)


def _small_finite_x_stats(base):
    
    
    
    try:
        n = int(len(base))
    except Exception:
        return None
    if n <= 0:
        return None
    lo = math.inf; hi = -math.inf; total = 0.0; count = 0
    for i in range(n):
        try:
            row = base[i]
            x = float(row[0])
        except Exception:
            continue
        if not math.isfinite(x):
            continue
        if x < lo: lo = x
        if x > hi: hi = x
        total += x; count += 1
    if count <= 0:
        return None
    return lo, hi, total / float(count)


def _viewport_shift_indices(base, wrap_width, margin_px=2.0):
    
    W = float(wrap_width or 0.0)
    if W <= 1.0:
        return (0,)
    
    
    stats = _small_finite_x_stats(base)
    if stats is None:
        return ()
    minx, maxx, center = stats
    start = int(math.floor((-(float(margin_px)) - maxx) / W))
    end = int(math.ceil(((W + float(margin_px)) - minx) / W))
    if end - start > 6:
        k0 = int(round(((0.5 * W) - center) / W))
        shifts = (k0 - 1, k0, k0 + 1)
    else:
        shifts = range(start, end + 1)
    return tuple(dict.fromkeys(int(k) for k in shifts))


def iter_viewport_copies(pts, wrap_width, margin_px=2.0, *, closed=False):
    
    if not isinstance(pts, np.ndarray):
        pts = np.asarray(pts, dtype=np.float64)
    W = float(wrap_width or 0.0)
    if pts.ndim != 2 or pts.shape[0] == 0 or pts.shape[1] != 2 or W <= 1.0:
        yield pts
        return
    base = unwrap_closed_ring(pts, W) if closed else unwrap_x_continuous(pts, W)
    if base.shape[0] == 0:
        return
    for k in _viewport_shift_indices(base, W, margin_px=margin_px):
        shifted = base.copy(); shifted[:, 0] += float(k) * W
        stats = _small_finite_x_stats(shifted)
        if stats is None:
            continue
        minx, maxx, _center = stats
        if maxx < -margin_px or minx > W + margin_px:
            continue
        yield shifted


def periodic_triangle_copies(uv3, wrap_width, margin_px=2.0):
    
    uv = uv3 if isinstance(uv3, np.ndarray) else np.asarray(uv3, dtype=np.float64)
    W = float(wrap_width or 0.0)
    if uv.shape != (3, 2) or not _finite_uv_rows_scalar(uv, 3):
        return
    if W <= 1.0:
        yield uv.copy()
        return
    base = unwrap_x_continuous(uv, W)
    for k in _viewport_shift_indices(base, W, margin_px=margin_px):
        shifted = base.copy(); shifted[:, 0] += float(k) * W
        stats = _small_finite_x_stats(shifted)
        if stats is None:
            continue
        minx, maxx, _center = stats
        if maxx < -margin_px or minx > W + margin_px:
            continue
        yield shifted







def panorama_angles_batch(ctx, xyz, dist_max=None):
    
    a = np.asarray(xyz, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] < 3:
        return (np.empty((0,), dtype=np.float64),)*3
    dx = a[:,0] - float(ctx['cx']); dy = a[:,1] - float(ctx['cy']); dz = a[:,2] - float(ctx['cam_z'])
    r=ctx['r']; up=ctx['u']; f=ctx['f']
    xc=dx*r[0]+dy*r[1]+dz*r[2]
    yc=dx*f[0]+dy*f[1]+dz*f[2]
    zc=dx*up[0]+dy*up[1]+dz*up[2]
    alpha=np.arctan2(xc,yc)
    beta=np.arctan2(zc,np.hypot(xc,yc))
    dep=np.sqrt(dx*dx+dy*dy+dz*dz)
    bad=~(np.isfinite(alpha)&np.isfinite(beta)&np.isfinite(dep)&(dep>0.0))
    if dist_max is not None:
        try: bad |= ((dx*dx+dy*dy) > float(dist_max)**2)
        except Exception: pass
    if np.any(bad):
        alpha=alpha.copy(); beta=beta.copy(); dep=dep.copy()
        alpha[bad]=np.nan; beta[bad]=np.nan; dep[bad]=np.inf
    return alpha,beta,dep


def _panorama_beta_limits(ctx, pole_guard_px=2.0):
    
    H=max(1,int(ctx.get('height',1) or 1)); W=max(1,int(ctx.get('width',1) or 1))
    proj=str(ctx.get('proj','') or '').upper()
    if proj in ('EQUIRECT','EQUIRECTANGULAR'):
        guard=max(math.radians(0.02), math.pi*max(1.0,float(pole_guard_px))/float(H))
        hard=math.pi*0.5-guard
        if bool(ctx.get('is360',False)):
            return -hard,hard
        vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
        lim=min(hard,0.5*vf)
        return -lim,lim
    if proj=='CYLINDRICAL':
        vf=_validated_vfov_for_cylindrical(ctx.get('VFOV',60.0),ctx.get('HFOV',360.0),W,H)
        lim=max(1e-6,0.5*float(vf)-1e-8)
        return -lim,lim
    return -math.pi*0.5+1e-6, math.pi*0.5-1e-6


def _camera_angle_point(ctx, xyz, dist_max=None):
    try:
        x,y,z=float(xyz[0]),float(xyz[1]),float(xyz[2])
        dx=x-float(ctx['cx']); dy=y-float(ctx['cy']); dz=z-float(ctx['cam_z'])
        if dist_max is not None and float(dist_max)>0 and dx*dx+dy*dy>float(dist_max)**2:
            return None
        r=ctx['r']; up=ctx['u']; f=ctx['f']
        xc=dx*float(r[0])+dy*float(r[1])+dz*float(r[2])
        yc=dx*float(f[0])+dy*float(f[1])+dz*float(f[2])
        zc=dx*float(up[0])+dy*float(up[1])+dz*float(up[2])
        alpha=math.atan2(xc,yc); beta=math.atan2(zc,math.hypot(xc,yc))
        dep=math.sqrt(dx*dx+dy*dy+dz*dz)
        if not all(math.isfinite(v) for v in (alpha,beta,dep)) or dep<=0.0: return None
        return (alpha,beta,dep)
    except Exception:
        return None


def _unwrap_alpha_near(alpha, ref):
    a=float(alpha); r=float(ref)
    twopi=2.0*math.pi
    return a+round((r-a)/twopi)*twopi


def _map_panorama_angles(ctx, alpha, beta):
    
    try:
        a=float(alpha); b=float(beta)
        if not (math.isfinite(a) and math.isfinite(b)): return None
        W=max(1,int(ctx['width'])); H=max(1,int(ctx['height']))
        proj=str(ctx.get('proj','') or '').upper(); full=bool(ctx.get('is360',False))
        hf=math.radians(max(1e-6,float(ctx.get('HFOV',360.0))))
        if proj in ('EQUIRECT','EQUIRECTANGULAR'):
            vf=math.radians(max(1e-6,float(ctx.get('VFOV',180.0))))
            u=(a+math.pi)/(2.0*math.pi)*W if full else W*0.5*(1.0+a/max(1e-12,hf*0.5))
            v=(math.pi*0.5-b)/math.pi*H if full else H*0.5*(1.0-b/max(1e-12,vf*0.5))
        elif proj=='CYLINDRICAL':
            vf=_validated_vfov_for_cylindrical(ctx.get('VFOV',60.0),ctx.get('HFOV',360.0),W,H)
            if abs(b)>=math.pi*0.5-1e-10: return None
            u=(a+math.pi)/(2.0*math.pi)*W if full else W*0.5+(W/max(1e-12,hf))*a
            fy=(H*0.5)/max(1e-12,math.tan(vf*0.5)); v=H*0.5-fy*math.tan(b)
        else: return None
        if not (math.isfinite(u) and math.isfinite(v)): return None
        return float(u),float(v)
    except Exception:
        return None


def _make_angle_vertex(ctx, xyz, tex=None, dist_max=None, pre=None):
    q=pre if pre is not None else _camera_angle_point(ctx,xyz,dist_max=dist_max)
    if q is None: return None
    return {'xyz':(float(xyz[0]),float(xyz[1]),float(xyz[2])),
            'alpha':float(q[0]),'beta':float(q[1]),'depth':float(q[2]),
            'tex':None if tex is None else (float(tex[0]),float(tex[1]))}


def _intersect_beta_edge(ctx, va, vb, beta_limit, dist_max=None):
    
    ba=float(va['beta'])-float(beta_limit); bb=float(vb['beta'])-float(beta_limit)
    if abs(ba)<1e-12: return dict(va)
    if abs(bb)<1e-12: return dict(vb)
    if ba*bb>0.0: return None
    xa=np.asarray(va['xyz'],dtype=np.float64); xb=np.asarray(vb['xyz'],dtype=np.float64)
    lo=0.0; hi=1.0; vlo=ba
    best=None
    for _ in range(24):
        t=0.5*(lo+hi); x=xa+(xb-xa)*t
        q=_camera_angle_point(ctx,x,dist_max=dist_max)
        if q is None: break
        val=float(q[1])-float(beta_limit); best=(t,x,q)
        if abs(val)<1e-10: break
        if vlo*val<=0.0: hi=t
        else: lo=t; vlo=val
    if best is None: return None
    t,x,q=best
    tex=None
    if va.get('tex') is not None and vb.get('tex') is not None:
        ta=va['tex']; tb=vb['tex']; tex=(ta[0]+(tb[0]-ta[0])*t,ta[1]+(tb[1]-ta[1])*t)
    out=_make_angle_vertex(ctx,x,tex=tex,dist_max=dist_max,pre=q)
    if out is not None: out['beta']=float(beta_limit)
    return out


def _clip_vertices_beta(ctx, verts, lower, upper, dist_max=None):
    
    poly=[v for v in verts if v is not None]
    if len(poly)<3: return []
    for lim,keep_ge in ((float(lower),True),(float(upper),False)):
        if not poly: break
        out=[]; prev=poly[-1]
        pin=(float(prev['beta'])>=lim) if keep_ge else (float(prev['beta'])<=lim)
        for cur in poly:
            cin=(float(cur['beta'])>=lim) if keep_ge else (float(cur['beta'])<=lim)
            if cin:
                if not pin:
                    inter=_intersect_beta_edge(ctx,prev,cur,lim,dist_max=dist_max)
                    if inter is not None: out.append(inter)
                out.append(cur)
            elif pin:
                inter=_intersect_beta_edge(ctx,prev,cur,lim,dist_max=dist_max)
                if inter is not None: out.append(inter)
            prev=cur; pin=cin
        poly=out
    return poly


def _mapped_triangle(ctx, verts, wrap_width=0.0):
    
    if len(verts)!=3: return []
    a0=float(verts[0]['alpha']); al=[a0]
    al.append(_unwrap_alpha_near(float(verts[1]['alpha']),al[-1]))
    
    
    al.append(_unwrap_alpha_near(float(verts[2]['alpha']),0.5*(al[0]+al[1])))
    uv=[]; dep=[]; xyz=[]; tex=[]; have_tex=True
    for v,a in zip(verts,al):
        m=_map_panorama_angles(ctx,a,float(v['beta']))
        if m is None: return []
        uv.append(m); dep.append(float(v['depth'])); xyz.append(v['xyz'])
        if v.get('tex') is None: have_tex=False
        tex.append(v.get('tex'))
    uv=np.asarray(uv,dtype=np.float64); dep=np.asarray(dep,dtype=np.float64); xyz=np.asarray(xyz,dtype=np.float64)
    texarr=np.asarray(tex,dtype=np.float64) if have_tex else None
    W=float(wrap_width or 0.0)
    copies=periodic_triangle_copies(uv,W) if W>1.0 else (uv,)
    return [(cp,dep,xyz,texarr) for cp in copies]


def _triangle_quick_needs_split(ctx, verts, quality='high', wrap_width=0.0):
    
    mapped=_mapped_triangle(ctx,verts,wrap_width=0.0)
    if not mapped: return True
    uv=mapped[0][0]
    lengths=(math.hypot(float(uv[1,0]-uv[0,0]),float(uv[1,1]-uv[0,1])),
             math.hypot(float(uv[2,0]-uv[1,0]),float(uv[2,1]-uv[1,1])),
             math.hypot(float(uv[0,0]-uv[2,0]),float(uv[0,1]-uv[2,1])))
    q=str(quality or 'high').lower(); trigger=700.0 if q=='low' else 520.0 if q=='normal' else 380.0
    if max(lengths)>trigger: return True
    
    if str(ctx.get('proj','')).upper() in ('EQUIRECT','EQUIRECTANGULAR'):
        if max(abs(float(v['beta'])) for v in verts)>math.radians(78.0): return True
    return False


def _split_triangle_longest_world(ctx, verts, dist_max=None):
    pairs=((0,1,2),(1,2,0),(2,0,1)); best=None; bestd=-1.0
    for i,j,k in pairs:
        a=verts[i]['xyz']; b=verts[j]['xyz']
        d=sum((float(a[c])-float(b[c]))**2 for c in range(3))
        if d>bestd: bestd=d; best=(i,j,k)
    i,j,k=best; va,vb,vk=verts[i],verts[j],verts[k]
    xyz=tuple((float(va['xyz'][c])+float(vb['xyz'][c]))*0.5 for c in range(3))
    tex=None
    if va.get('tex') is not None and vb.get('tex') is not None:
        tex=tuple((float(va['tex'][c])+float(vb['tex'][c]))*0.5 for c in range(2))
    vm=_make_angle_vertex(ctx,xyz,tex=tex,dist_max=dist_max)
    if vm is None: return None
    return ([va,vm,vk],[vm,vb,vk])


def panorama_faces_from_world_mesh(ctx, world_xyz, triangle_indices, dist_max=None, *, texture_uv=None,
                                   wrap_width=None, role='surface', metadata=None, render_quality='high',
                                   extra_face_budget=None, pole_guard_px=2.0):
    
    xyz=np.asarray(world_xyz,dtype=np.float64)
    if xyz.ndim!=2 or xyz.shape[0]<3 or xyz.shape[1]<3: return []
    tex=None
    if texture_uv is not None:
        try:
            tex=np.asarray(texture_uv,dtype=np.float64)
            if tex.ndim!=2 or tex.shape[0]!=xyz.shape[0] or tex.shape[1]!=2: tex=None
        except Exception: tex=None
    alpha,beta,dep=panorama_angles_batch(ctx,xyz,dist_max=dist_max)
    lower,upper=_panorama_beta_limits(ctx,pole_guard_px=pole_guard_px)
    W=float(wrap_width or 0.0); md=dict(metadata or {}); out=[]
    q=str(render_quality or 'high').lower(); max_depth=1 if q=='low' else 2
    max_extra_per_source=1 if q=='low' else 3 if q=='normal' else 5
    for tri in triangle_indices or ():
        try:
            ids=tuple(int(i) for i in tri)
            if len(ids)!=3 or any(i<0 or i>=xyz.shape[0] for i in ids): continue
            verts=[]
            for ii in ids:
                pre=(float(alpha[ii]),float(beta[ii]),float(dep[ii]))
                if not all(math.isfinite(v) for v in pre) or pre[2]<=0.0: verts=[]; break
                verts.append(_make_angle_vertex(ctx,xyz[ii],tex=(None if tex is None else tex[ii]),dist_max=dist_max,pre=pre))
            if len(verts)!=3: continue
        except Exception:
            continue
        clipped=_clip_vertices_beta(ctx,verts,lower,upper,dist_max=dist_max)
        if len(clipped)<3: continue
        
        seeds=[[clipped[0],clipped[i],clipped[i+1]] for i in range(1,len(clipped)-1)]
        for seed in seeds:
            stack=[(seed,0,0)]
            while stack:
                cur,depth,extras=stack.pop()
                can_split=_triangle_quick_needs_split(ctx,cur,quality=q,wrap_width=W) and depth<max_depth and extras<max_extra_per_source
                if can_split:
                    budget_ok=True
                    if isinstance(extra_face_budget,dict):
                        try: budget_ok=int(extra_face_budget.get('remaining',0))>0
                        except Exception: budget_ok=False
                    if budget_ok:
                        two=_split_triangle_longest_world(ctx,cur,dist_max=dist_max)
                        if two is not None:
                            if isinstance(extra_face_budget,dict):
                                try: extra_face_budget['remaining']=max(0,int(extra_face_budget.get('remaining',0))-1)
                                except Exception: pass
                            for child in reversed(two):
                                cclip=_clip_vertices_beta(ctx,child,lower,upper,dist_max=dist_max)
                                if len(cclip)==3: stack.append((cclip,depth+1,extras+1))
                                elif len(cclip)>3:
                                    for kk in range(1,len(cclip)-1): stack.append(([cclip[0],cclip[kk],cclip[kk+1]],depth+1,extras+1))
                            continue
                for uv3,d3,xyz3,t3 in _mapped_triangle(ctx,cur,wrap_width=W):
                    out.append(PanoramicFace(uv=uv3,radial_depth=d3,world_xyz=xyz3,texture_uv=t3,
                                             role=str(role),metadata=dict(md)))
    return out


def _clip_world_segment_beta(ctx, a, b, lower, upper, dist_max=None):
    va=_make_angle_vertex(ctx,a,dist_max=dist_max); vb=_make_angle_vertex(ctx,b,dist_max=dist_max)
    if va is None or vb is None: return None
    verts=[va,vb]
    
    for lim,keep_ge in ((lower,True),(upper,False)):
        a0,b0=verts[0],verts[-1]
        ia=(a0['beta']>=lim) if keep_ge else (a0['beta']<=lim)
        ib=(b0['beta']>=lim) if keep_ge else (b0['beta']<=lim)
        if ia and ib: continue
        if (not ia) and (not ib): return None
        inter=_intersect_beta_edge(ctx,a0,b0,lim,dist_max=dist_max)
        if inter is None: return None
        verts=[a0,inter] if ia else [inter,b0]
    return verts


def _segment_midpoint_error(ctx, a, b, wrap_width=0.0, dist_max=None):
    qa=_camera_angle_point(ctx,a,dist_max=dist_max); qb=_camera_angle_point(ctx,b,dist_max=dist_max)
    mid=tuple((float(a[c])+float(b[c]))*0.5 for c in range(3)); qm=_camera_angle_point(ctx,mid,dist_max=dist_max)
    if qa is None or qb is None or qm is None: return math.inf,math.inf
    aa=float(qa[0]); ab=_unwrap_alpha_near(float(qb[0]),aa); am=_unwrap_alpha_near(float(qm[0]),0.5*(aa+ab))
    pa=_map_panorama_angles(ctx,aa,float(qa[1])); pb=_map_panorama_angles(ctx,ab,float(qb[1])); pm=_map_panorama_angles(ctx,am,float(qm[1]))
    if pa is None or pb is None or pm is None: return math.inf,math.inf
    length=math.hypot(pb[0]-pa[0],pb[1]-pa[1]); err=math.hypot(pm[0]-0.5*(pa[0]+pb[0]),pm[1]-0.5*(pa[1]+pb[1]))
    return length,err


def project_panorama_path_safe(ctx, world_xyz, dist_max=None, *, closed=False, render_quality='high',
                               wrap_width=None, max_points=4096, budget_state=None, pole_guard_px=2.0):
    
    a=np.asarray(world_xyz,dtype=np.float64)
    if a.ndim!=2 or a.shape[0]<2 or a.shape[1]<3:
        return PanoramicPrimitive2D('polyline',np.empty((0,2)),np.empty((0,)),np.empty((0,3)),closed=bool(closed))
    pts=[tuple(float(v) for v in row[:3]) for row in a if all(math.isfinite(float(v)) for v in row[:3])]
    if len(pts)<2:
        return PanoramicPrimitive2D('polyline',np.empty((0,2)),np.empty((0,)),np.empty((0,3)),closed=bool(closed))
    if closed and len(pts)>2 and sum((pts[0][c]-pts[-1][c])**2 for c in range(3))<1e-16: pts=pts[:-1]
    lower,upper=_panorama_beta_limits(ctx,pole_guard_px=pole_guard_px)
    q=str(render_quality or 'high').lower(); tol=2.5 if q=='low' else 1.6 if q=='normal' else 1.0
    len_trigger=900.0 if q=='low' else 700.0 if q=='normal' else 520.0; max_depth=3 if q=='low' else 4
    max_points=max(32,min(12000,int(max_points))); W=float(wrap_width or 0.0)
    uv_out=[]; dep_out=[]; xyz_out=[]; run_open=False; used_extra=0
    n=len(pts); seg_count=n if closed else n-1
    for si in range(seg_count):
        p0=pts[si]; p1=pts[(si+1)%n]
        stack=[(p0,p1,0)] ; leaves=[]
        while stack and (len(uv_out)+len(leaves))<max_points:
            aa,bb,depth=stack.pop(); L,E=_segment_midpoint_error(ctx,aa,bb,wrap_width=W,dist_max=dist_max)
            split=(not math.isfinite(E)) or E>tol or (math.isfinite(L) and L>len_trigger)
            budget_ok=True
            if isinstance(budget_state,dict):
                try: budget_ok=int(budget_state.get('remaining',0))>0
                except Exception: budget_ok=False
            if split and depth<max_depth and budget_ok:
                mid=tuple((aa[c]+bb[c])*0.5 for c in range(3))
                stack.append((mid,bb,depth+1)); stack.append((aa,mid,depth+1)); used_extra+=1
                if isinstance(budget_state,dict):
                    try: budget_state['remaining']=max(0,int(budget_state.get('remaining',0))-1)
                    except Exception: pass
            else: leaves.append((aa,bb))
        for aa,bb in leaves:
            seg=_clip_world_segment_beta(ctx,aa,bb,lower,upper,dist_max=dist_max)
            if seg is None:
                if run_open:
                    uv_out.append((math.nan,math.nan)); dep_out.append(math.inf); xyz_out.append((math.nan,math.nan,math.nan)); run_open=False
                continue
            va,vb=seg
            
            ma=_map_panorama_angles(ctx,float(va['alpha']),float(va['beta'])); mb=_map_panorama_angles(ctx,float(vb['alpha']),float(vb['beta']))
            if ma is None or mb is None: continue
            if not run_open:
                uv_out.append(ma); dep_out.append(float(va['depth'])); xyz_out.append(va['xyz']); run_open=True
            else:
                
                px,py,pz=xyz_out[-1]
                _d2=((float(px)-float(va['xyz'][0]))**2 + (float(py)-float(va['xyz'][1]))**2 + (float(pz)-float(va['xyz'][2]))**2) if all(math.isfinite(v) for v in (px,py,pz)) else math.inf
                if _d2>1e-10:
                    uv_out.append((math.nan,math.nan)); dep_out.append(math.inf); xyz_out.append((math.nan,math.nan,math.nan))
                    uv_out.append(ma); dep_out.append(float(va['depth'])); xyz_out.append(va['xyz'])
            uv_out.append(mb); dep_out.append(float(vb['depth'])); xyz_out.append(vb['xyz'])
    if not uv_out:
        return PanoramicPrimitive2D('polyline',np.empty((0,2)),np.empty((0,)),np.empty((0,3)),closed=bool(closed))
    return PanoramicPrimitive2D('polyline',np.asarray(uv_out,dtype=np.float64),np.asarray(dep_out,dtype=np.float64),
                                np.asarray(xyz_out,dtype=np.float64),closed=bool(closed),role='safe_outline')

def surface_faces_from_primitive(primitive: PanoramicPrimitive2D, triangle_indices: Sequence[Sequence[int]],
                                 wrap_width=None, *, role="surface", metadata=None):
    
    faces = []
    if primitive is None:
        return faces
    uv = primitive.uv if isinstance(primitive.uv, np.ndarray) else np.asarray(primitive.uv, dtype=np.float64)
    dep = primitive.radial_depth if isinstance(primitive.radial_depth, np.ndarray) else np.asarray(primitive.radial_depth, dtype=np.float64)
    xyz = primitive.world_xyz if isinstance(primitive.world_xyz, np.ndarray) else np.asarray(primitive.world_xyz, dtype=np.float64)
    tex = None if primitive.texture_uv is None else (primitive.texture_uv if isinstance(primitive.texture_uv, np.ndarray) else np.asarray(primitive.texture_uv, dtype=np.float64))
    n = min(len(uv), len(dep), len(xyz))
    if n < 3:
        return faces
    W = float(wrap_width or 0.0)
    md = dict(metadata or {})
    for tri in triangle_indices:
        try:
            ids = tuple(int(i) for i in tri)
            if len(ids) != 3 or any(i < 0 or i >= n for i in ids):
                continue
            uv3 = np.asarray([uv[i] for i in ids], dtype=np.float64)
            d3 = np.asarray([dep[i] for i in ids], dtype=np.float64)
            xyz3 = np.asarray([xyz[i] for i in ids], dtype=np.float64)
            tex3 = None if tex is None else np.asarray([tex[i] for i in ids], dtype=np.float64)
        except Exception:
            continue
        if not (_finite_uv_rows_scalar(uv3, 3) and _finite_positive_depth_scalar(d3, 3)):
            continue
        copies = periodic_triangle_copies(uv3, W) if W > 1.0 else (uv3,)
        for uv_copy in copies:
            
            
            
            faces.append(PanoramicFace(
                uv=uv_copy, radial_depth=d3, world_xyz=xyz3,
                texture_uv=tex3, role=str(role), metadata=dict(md),
            ))
    return faces


def wall_faces_from_primitives(base: PanoramicPrimitive2D, top: PanoramicPrimitive2D,
                               wrap_width=None, *, role="wall", metadata=None):
    
    faces = []
    if base is None or top is None:
        return faces
    buv = base.uv if isinstance(base.uv, np.ndarray) else np.asarray(base.uv, dtype=np.float64)
    tuv = top.uv if isinstance(top.uv, np.ndarray) else np.asarray(top.uv, dtype=np.float64)
    bd = base.radial_depth if isinstance(base.radial_depth, np.ndarray) else np.asarray(base.radial_depth, dtype=np.float64)
    td = top.radial_depth if isinstance(top.radial_depth, np.ndarray) else np.asarray(top.radial_depth, dtype=np.float64)
    bxyz = base.world_xyz if isinstance(base.world_xyz, np.ndarray) else np.asarray(base.world_xyz, dtype=np.float64)
    txyz = top.world_xyz if isinstance(top.world_xyz, np.ndarray) else np.asarray(top.world_xyz, dtype=np.float64)
    n = min(len(buv), len(tuv), len(bd), len(td), len(bxyz), len(txyz))
    if n < 2:
        return faces
    
    closed = bool(base.closed or top.closed)
    if closed and n >= 3:
        try:
            if _xy_distance2_scalar(bxyz[0], bxyz[n - 1]) <= 1e-16:
                n -= 1
        except Exception:
            pass
    seg_count = n if closed else n - 1
    W = float(wrap_width or 0.0)
    md = dict(metadata or {})
    for i in range(max(0, seg_count)):
        j = (i + 1) % n
        try:
            uv4 = np.asarray([buv[i], buv[j], tuv[j], tuv[i]], dtype=np.float64)
            d4 = np.asarray([bd[i], bd[j], td[j], td[i]], dtype=np.float64)
            xyz4 = np.asarray([bxyz[i], bxyz[j], txyz[j], txyz[i]], dtype=np.float64)
        except Exception:
            continue
        if not (_finite_uv_rows_scalar(uv4, 4) and _finite_positive_depth_scalar(d4, 4)):
            continue
        if W > 1.0:
            uv4_base = unwrap_x_continuous(uv4, W)
            shifts = _viewport_shift_indices(uv4_base, W, margin_px=2.0)
        else:
            uv4_base = uv4
            shifts = (0,)
        for k in shifts:
            uvc = uv4_base.copy()
            if W > 1.0:
                uvc[:, 0] += float(k) * W
                _stats = _small_finite_x_stats(uvc)
                if _stats is None or _stats[1] < -2.0 or _stats[0] > W + 2.0:
                    continue
            for ids in ((0, 1, 2), (0, 2, 3)):
                faces.append(PanoramicFace(
                    uv=np.asarray([uvc[q] for q in ids], dtype=np.float64),
                    radial_depth=np.asarray([d4[q] for q in ids], dtype=np.float64),
                    world_xyz=np.asarray([xyz4[q] for q in ids], dtype=np.float64),
                    texture_uv=None, role=str(role), metadata=dict(md),
                ))
    return faces
