



from __future__ import annotations

import math
import random
from typing import Iterable, List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def deterministic_noise(seed: int, count: int, amplitude: float = 1.0) -> List[float]:
    
    rnd = random.Random(int(seed) & 0xFFFFFFFF)  
    amp = abs(float(amplitude))
    return [rnd.uniform(-amp, amp) for _ in range(max(0, int(count)))]


def polyline_length(points: Sequence[Point2]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])) for a, b in zip(points[:-1], points[1:]))


def resample_polyline(points: Sequence[Point2], step: float) -> List[Point2]:
    
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return pts
    step = max(0.05, float(step))
    out: List[Point2] = [pts[0]]
    carry = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        ax, ay = a; bx, by = b
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        if seg <= 1e-12:
            continue
        travelled = step - carry if carry > 1e-12 else step
        while travelled < seg - 1e-12:
            t = travelled / seg
            out.append((ax + dx * t, ay + dy * t))
            travelled += step
        
        if out:
            lx, ly = out[-1]
            carry = math.hypot(bx - lx, by - ly)
            if carry >= step:
                carry = math.fmod(carry, step)
        else:
            carry = 0.0
    if not out or math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > 1e-9:
        out.append(pts[-1])
    return out


def segment_intersection(a: Point2, b: Point2, c: Point2, d: Point2, eps: float = 1e-10) -> Optional[Tuple[Point2, float, float]]:
    
    ax, ay = map(float, a); bx, by = map(float, b)
    cx, cy = map(float, c); dx, dy = map(float, d)
    r = (bx - ax, by - ay)
    s = (dx - cx, dy - cy)
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) <= eps:
        return None
    qpx, qpy = cx - ax, cy - ay
    t = (qpx * s[1] - qpy * s[0]) / den
    u = (qpx * r[1] - qpy * r[0]) / den
    if -eps <= t <= 1.0 + eps and -eps <= u <= 1.0 + eps:
        return ((ax + t * r[0], ay + t * r[1]), t, u)
    return None


def required_occlusion_height_at_point(
    camera_xy: Point2,
    camera_z: float,
    target_xy: Point2,
    target_z: float,
    barrier_xy: Point2,
    barrier_ground_z: float,
    margin_m: float = 0.0,
) -> Optional[float]:
    
    cx, cy = map(float, camera_xy); tx, ty = map(float, target_xy)
    bx, by = map(float, barrier_xy)
    vx, vy = tx - cx, ty - cy
    den = vx * vx + vy * vy
    if den <= 1e-12:
        return None
    lam = ((bx - cx) * vx + (by - cy) * vy) / den
    if lam < -1e-9 or lam > 1.0 + 1e-9:
        return None
    los_z = float(camera_z) + lam * (float(target_z) - float(camera_z))
    return max(0.0, los_z - float(barrier_ground_z) + float(margin_m))


def required_occlusion_height_on_polyline(
    camera_xy: Point2,
    camera_z: float,
    target_xy: Point2,
    target_z: float,
    barrier_points: Sequence[Point2],
    ground_z_at,
    margin_m: float = 0.0,
) -> Optional[Tuple[float, Point2, int]]:
    
    pts = [(float(x), float(y)) for x, y in barrier_points]
    if len(pts) < 2:
        return None
    best = None
    for idx, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
        hit = segment_intersection(camera_xy, target_xy, a, b)
        if hit is None:
            continue
        xy, t_cam, _ = hit
        if t_cam <= 1e-9 or t_cam >= 1.0 - 1e-9:
            continue
        gz = float(ground_z_at(float(xy[0]), float(xy[1])))
        h = required_occlusion_height_at_point(camera_xy, camera_z, target_xy, target_z, xy, gz, margin_m)
        if h is None:
            continue
        candidate = (float(t_cam), float(h), xy, idx)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        return None
    return best[1], best[2], best[3]


def occlusion_hits_on_polylines(
    camera_xy: Point2,
    camera_z: float,
    target_xy: Point2,
    target_z: float,
    barrier_parts: Sequence[Sequence[Point2]],
    ground_z_at,
    margin_m: float = 0.0,
):
    
    cx, cy = map(float, camera_xy)
    tx, ty = map(float, target_xy)
    ray_len = math.hypot(tx - cx, ty - cy)
    if ray_len <= 1e-12:
        return []
    hits = []
    for part_idx, part in enumerate(barrier_parts or []):
        pts = [(float(x), float(y)) for x, y in (part or [])]
        if len(pts) < 2:
            continue
        for seg_idx, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
            hit = segment_intersection(camera_xy, target_xy, a, b)
            if hit is None:
                continue
            xy, t_cam, t_barrier = hit
            
            if t_cam <= 1e-9 or t_cam >= 1.0 - 1e-9:
                continue
            try:
                gz = float(ground_z_at(float(xy[0]), float(xy[1])))
            except Exception:
                continue
            if not math.isfinite(gz):
                continue
            los_z = float(camera_z) + float(t_cam) * (float(target_z) - float(camera_z))
            h_req = max(0.0, los_z - gz + float(margin_m))
            hits.append({
                "t_cam": float(t_cam),
                "t_barrier": float(t_barrier),
                "xy": (float(xy[0]), float(xy[1])),
                "part_index": int(part_idx),
                "segment_index": int(seg_idx),
                "ground_z": float(gz),
                "los_z": float(los_z),
                "height_m": float(h_req),
                "distance_camera_m": float(ray_len * t_cam),
                "distance_target_m": float(ray_len * (1.0 - t_cam)),
            })
    hits.sort(key=lambda rec: (rec["t_cam"], rec["part_index"], rec["segment_index"]))
    return hits


def required_occlusion_hit_on_polylines(
    camera_xy: Point2,
    camera_z: float,
    target_xy: Point2,
    target_z: float,
    barrier_parts: Sequence[Sequence[Point2]],
    ground_z_at,
    margin_m: float = 0.0,
):
    
    hits = occlusion_hits_on_polylines(
        camera_xy, camera_z, target_xy, target_z,
        barrier_parts, ground_z_at, margin_m=margin_m,
    )
    return hits[0] if hits else None
