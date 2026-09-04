# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parametric schematic (AVR 0/1) symbols for QCALVIEW.

The module deliberately stays 2.5D: source vector geometries are converted to a
small set of world-space primitives, projected analytically with QCALVIEW's
camera, then painted with Qt. No 3D scene, mesh engine, lighting or material
system is involved.
"""
from __future__ import annotations
from ._compat import QC, dialog_exec

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from qgis.PyQt.QtCore import Qt, QRectF, QPointF, QByteArray
from qgis.PyQt.QtGui import QColor, QPen, QBrush, QPolygonF, QPainterPath, QImage
from qgis.core import QgsPointXY, QgsWkbTypes

try:
    from qgis.PyQt.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - depends on Qt packaging
    QSvgRenderer = None

from ..projector import build_camera_context, project_points_batch
from ._schematic_math import deterministic_noise, resample_polyline
from ._panorama_primitives import (
    PanoramicPrimitive2D, is_panorama_context, project_panorama_primitive,
    iter_viewport_copies, project_panorama_point_scalar, panorama_quad_rects_scalar,
)


# -----------------------------------------------------------------------------
# SVG rendering safety
# -----------------------------------------------------------------------------

# Small transparent safety margin around the SVG logical viewport.  40.17.3
# expands the root viewBox *before* QSvgRenderer parses the document, then keeps
# setViewBox() as a fallback.  This is more robust than 40.17.2, where only the
# already-loaded renderer viewBox was altered.
_SVG_SAFE_PADDING_RATIO = 0.035


def _apply_svg_safe_viewbox(renderer, padding_ratio=_SVG_SAFE_PADDING_RATIO):
    """Expand a QSvgRenderer logical viewBox by a small safety margin.

    Works with the QRectF API exposed by both Qt5/PyQt5 (QGIS 3.44) and
    Qt6/PyQt6 (QGIS 4.x).  Failure is intentionally non-fatal: an unusual SVG
    or Qt binding simply falls back to the renderer's original viewBox.
    """
    if renderer is None:
        return False
    try:
        ratio = max(0.0, min(0.10, float(padding_ratio)))
    except Exception:
        ratio = _SVG_SAFE_PADDING_RATIO
    if ratio <= 0.0:
        return False
    try:
        box = renderer.viewBoxF()
        w = float(box.width())
        h = float(box.height())
        if (not math.isfinite(w)) or (not math.isfinite(h)) or w <= 0.0 or h <= 0.0:
            return False
        pad_x = w * ratio
        pad_y = h * ratio
        safe_box = QRectF(
            float(box.x()) - pad_x,
            float(box.y()) - pad_y,
            w + 2.0 * pad_x,
            h + 2.0 * pad_y,
        )
        renderer.setViewBox(safe_box)
        return True
    except Exception:
        return False


# Root SVG viewBox matcher. The replacement is deliberately byte-based so the
# rest of the SVG (namespaces, metadata, entity text, formatting) is untouched.
_SVG_ROOT_VIEWBOX_RE = re.compile(rb'\bviewBox\s*=\s*(["\'])([^"\']+)\1', re.IGNORECASE)


def _svg_bytes_with_safe_viewbox(path, padding_ratio=_SVG_SAFE_PADDING_RATIO):
    """Return SVG bytes whose root viewBox is expanded before Qt parses it."""
    try:
        ratio = max(0.0, min(0.10, float(padding_ratio)))
    except Exception:
        ratio = _SVG_SAFE_PADDING_RATIO
    if ratio <= 0.0 or not path or not os.path.isfile(path):
        return None
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except Exception:
        return None
    try:
        m = _SVG_ROOT_VIEWBOX_RE.search(raw)
        if m is None:
            return None
        vals = m.group(2).replace(b',', b' ').split()
        if len(vals) != 4:
            return None
        x, y, w, h = (float(v.decode('ascii')) for v in vals)
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0.0 or h <= 0.0:
            return None
        px = w * ratio
        py = h * ratio
        vb = ('%.12g %.12g %.12g %.12g' % (
            x - px, y - py, w + 2.0 * px, h + 2.0 * py
        )).encode('ascii')
        quote = m.group(1)
        repl = b'viewBox=' + quote + vb + quote
        return raw[:m.start()] + repl + raw[m.end():]
    except Exception:
        return None


def _safe_svg_renderer(path, padding_ratio=_SVG_SAFE_PADDING_RATIO):
    """Load an SVG renderer with padding applied before parsing when possible."""
    if QSvgRenderer is None or not path:
        return None
    try:
        payload = _svg_bytes_with_safe_viewbox(path, padding_ratio=padding_ratio)
        if payload:
            renderer = QSvgRenderer()
            if renderer.load(QByteArray(payload)) and renderer.isValid():
                return renderer
    except Exception:
        pass
    try:
        renderer = QSvgRenderer(path)
        if renderer.isValid():
            _apply_svg_safe_viewbox(renderer, padding_ratio=padding_ratio)
            return renderer
    except Exception:
        pass
    return None


# Panorama-only asset caches.  The legacy/PINHOLE helper remains untouched;
# these caches prevent dense panoramic stands from constructing a new native
# QSvgRenderer/QImage for every billboard instance.
_PANORAMA_SVG_RENDERER_CACHE = {}
_PANORAMA_IMAGE_CACHE = {}

def _panorama_cached_svg_renderer(path):
    if not path or QSvgRenderer is None:
        return None
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    except Exception:
        mtime = None
    key = (str(path), mtime, 'safe-pad-3.5pct')
    cached = _PANORAMA_SVG_RENDERER_CACHE.get(key)
    try:
        if cached is not None and cached.isValid():
            return cached
    except Exception:
        _PANORAMA_SVG_RENDERER_CACHE.pop(key, None)
    renderer = _safe_svg_renderer(path)
    if renderer is not None:
        try:
            if renderer.isValid():
                _PANORAMA_SVG_RENDERER_CACHE[key] = renderer
                if len(_PANORAMA_SVG_RENDERER_CACHE) > 32:
                    for old_key in list(_PANORAMA_SVG_RENDERER_CACHE.keys())[:8]:
                        if old_key != key:
                            _PANORAMA_SVG_RENDERER_CACHE.pop(old_key, None)
                return renderer
        except Exception:
            pass
    return None

def _panorama_cached_image(path):
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    except Exception:
        mtime = None
    key = (str(path), mtime)
    cached = _PANORAMA_IMAGE_CACHE.get(key)
    if cached is not None:
        try:
            if not cached.isNull():
                return cached
        except Exception:
            _PANORAMA_IMAGE_CACHE.pop(key, None)
    img = QImage(path)
    if not img.isNull():
        _PANORAMA_IMAGE_CACHE[key] = img
        if len(_PANORAMA_IMAGE_CACHE) > 32:
            for old_key in list(_PANORAMA_IMAGE_CACHE.keys())[:8]:
                if old_key != key:
                    _PANORAMA_IMAGE_CACHE.pop(old_key, None)
        return img
    return None

def _small_xy_bounds(arr):
    """Bounds for tiny projected primitives without NumPy reductions."""
    pts = np.asarray(arr, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] == 0 or pts.shape[1] < 2:
        return None
    minx = math.inf; maxx = -math.inf; miny = math.inf; maxy = -math.inf
    count = 0
    for i in range(int(pts.shape[0])):
        try:
            x = float(pts[i, 0]); y = float(pts[i, 1])
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        minx = min(minx, x); maxx = max(maxx, x)
        miny = min(miny, y); maxy = max(maxy, y); count += 1
    if count <= 0:
        return None
    return minx, maxx, miny, maxy

# -----------------------------------------------------------------------------
# Primitive model
# -----------------------------------------------------------------------------

@dataclass
class Primitive3D:
    role: str = "generic"
    fill: bool = True
    outline: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Polygon3D(Primitive3D):
    xyz: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=np.float64))


@dataclass
class Polyline3D(Primitive3D):
    xyz: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=np.float64))
    fill: bool = False


@dataclass
class Billboard3D(Primitive3D):
    anchor_xyz: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    width_m: float = 1.0
    height_m: float = 1.0
    svg_path: str = ""


class ProjectedSymbol:
    """Abstract world-primitive generator.

    Subclasses only know geometry + real dimensions. They do not know how Qt
    paints or how PINHOLE/CYLINDRICAL/EQUIRECT are projected.
    """
    generator_id = "base"

    def build(self, ctx: "SymbolBuildContext") -> List[Primitive3D]:
        raise NotImplementedError


@dataclass
class SymbolBuildContext:
    definition: Dict[str, Any]
    feature: Any
    parts: List[np.ndarray]
    gtype: int
    style: Any
    z_sampler: Any
    camera_xy: Tuple[float, float]
    layer_height_m: float
    plugin_dir: str
    # Profil Z contractuel fourni par le renderer classique. Lorsqu'il est
    # présent, les AVR l'utilisent tel quel : aucun nouvel échantillonnage MNT.
    ground_profiles_override: Any = None
    # 40.18.7 preview-only runtime hints (billboard budget/LOD). They are not
    # style parameters and are never persisted in the project.
    runtime_overrides: Any = None
    _ground_profiles: Any = field(default=None, init=False, repr=False)

    def _sample_raw_ground(self, x: float, y: float) -> float:
        try:
            if self.z_sampler is not None:
                z = float(self.z_sampler(QgsPointXY(float(x), float(y))))
                if math.isfinite(z):
                    return z
        except Exception:
            pass
        return 0.0

    def _ensure_ground_profiles(self):
        """Construit un référentiel Z immuable en types Python natifs.

        40.17 : ``ground_z`` est appelé très fréquemment par les générateurs AVR.
        Les profils sont donc convertis une seule fois en tuples ``(x, y)`` et
        ``z`` Python ; aucune vue ndarray/QGIS n'est conservée dans ce chemin.
        """
        if self._ground_profiles is not None:
            return self._ground_profiles

        profiles = []
        source = self.ground_profiles_override if self.ground_profiles_override is not None else None
        if source is not None:
            for item in list(source or []):
                try:
                    xy0, z0, mean0 = item
                    n = min(len(xy0), len(z0))
                    if n <= 0:
                        continue
                    xy = []
                    zs = []
                    for i in range(n):
                        x = float(xy0[i][0]); y = float(xy0[i][1]); z = float(z0[i])
                        if not (math.isfinite(x) and math.isfinite(y)):
                            continue
                        xy.append((x, y))
                        zs.append(z if math.isfinite(z) else None)
                    if not xy:
                        continue
                    finite_z = [z for z in zs if z is not None]
                    try:
                        mean_z = float(mean0)
                    except Exception:
                        mean_z = 0.0
                    if not math.isfinite(mean_z):
                        mean_z = (sum(finite_z) / len(finite_z)) if finite_z else 0.0
                    zs = tuple(mean_z if z is None else float(z) for z in zs)
                    profiles.append((tuple(xy), zs, float(mean_z)))
                except Exception:
                    continue
            if profiles:
                self._ground_profiles = tuple(profiles)
                return self._ground_profiles

        for arr in list(self.parts or []):
            try:
                n = len(arr)
            except Exception:
                continue
            if n <= 0:
                continue
            xy = []
            zs = []
            for i in range(n):
                try:
                    x = float(arr[i][0]); y = float(arr[i][1])
                except Exception:
                    continue
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                z = self._sample_raw_ground(x, y)
                xy.append((x, y)); zs.append(float(z) if math.isfinite(float(z)) else 0.0)
            if not xy:
                continue
            mean_z = (sum(zs) / len(zs)) if zs else 0.0
            profiles.append((tuple(xy), tuple(zs), float(mean_z)))
        self._ground_profiles = tuple(profiles)
        return self._ground_profiles

    @staticmethod
    def _segment_interp_z(x: float, y: float, xy, z):
        best_d2 = math.inf
        best_z = None
        n = min(len(xy), len(z))
        if n <= 0:
            return None
        if n == 1:
            return float(z[0])
        for i in range(n - 1):
            ax, ay = xy[i]; bx, by = xy[i + 1]
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            t = 0.0 if den <= 1e-18 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / den))
            qx, qy = ax + t * dx, ay + t * dy
            d2 = (x - qx) * (x - qx) + (y - qy) * (y - qy)
            if d2 < best_d2:
                best_d2 = d2
                best_z = float(z[i] + t * (z[i + 1] - z[i]))
        return best_z

    def ground_z(self, x: float, y: float) -> float:
        """Altitude de base contractuelle de la géométrie source."""
        try:
            x = float(x); y = float(y)
            if not (math.isfinite(x) and math.isfinite(y)):
                return 0.0
            profiles = self._ensure_ground_profiles()
            if not profiles:
                return self._sample_raw_ground(x, y)

            if self.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                best = None; best_d2 = math.inf
                for xy, _z, mean_z in profiles:
                    n = len(xy)
                    if n <= 0:
                        continue
                    local_d2 = math.inf
                    if n == 1:
                        ax, ay = xy[0]
                        local_d2 = (x - ax) ** 2 + (y - ay) ** 2
                    else:
                        for i in range(n - 1):
                            ax, ay = xy[i]; bx, by = xy[i + 1]
                            dx, dy = bx - ax, by - ay
                            den = dx * dx + dy * dy
                            t = 0.0 if den <= 1e-18 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / den))
                            qx, qy = ax + t * dx, ay + t * dy
                            d2 = (x - qx) ** 2 + (y - qy) ** 2
                            if d2 < local_d2:
                                local_d2 = d2
                    if local_d2 < best_d2:
                        best_d2 = local_d2; best = mean_z
                return float(best if best is not None else profiles[0][2])

            if self.gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
                best_z = None; best_d2 = math.inf
                for xy, z, _mean_z in profiles:
                    n = min(len(xy), len(z))
                    if n <= 0:
                        continue
                    if n == 1:
                        ax, ay = xy[0]
                        d2 = (x - ax) ** 2 + (y - ay) ** 2
                        if d2 < best_d2:
                            best_d2, best_z = d2, float(z[0])
                        continue
                    for i in range(n - 1):
                        ax, ay = xy[i]; bx, by = xy[i + 1]
                        dx, dy = bx - ax, by - ay
                        den = dx * dx + dy * dy
                        t = 0.0 if den <= 1e-18 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / den))
                        qx, qy = ax + t * dx, ay + t * dy
                        d2 = (x - qx) ** 2 + (y - qy) ** 2
                        if d2 < best_d2:
                            best_d2 = d2
                            best_z = float(z[i] + t * (z[i + 1] - z[i]))
                return float(best_z if best_z is not None else profiles[0][2])

            best_z = None; best_d2 = math.inf
            for xy, z, _mean_z in profiles:
                for i in range(min(len(xy), len(z))):
                    ax, ay = xy[i]
                    d2 = (x - ax) ** 2 + (y - ay) ** 2
                    if d2 < best_d2:
                        best_d2 = d2; best_z = float(z[i])
            return float(best_z if best_z is not None else self._sample_raw_ground(x, y))
        except Exception:
            return self._sample_raw_ground(x, y)

    def field_value(self, name: str, default: Any = None) -> Any:
        name = str(name or "").strip()
        if not name:
            return default
        try:
            if name in self.feature.fields().names():
                value = self.feature[name]
                if value is not None:
                    return value
        except Exception:
            pass
        return default

    def runtime(self, name: str, default: Any = None) -> Any:
        try:
            ro = self.runtime_overrides if isinstance(self.runtime_overrides, dict) else {}
            return ro.get(name, default)
        except Exception:
            return default

    def param(self, name: str, default: Any = None) -> Any:
        overrides = dict(getattr(self.style, "schematic_params", {}) or {})
        if name in overrides:
            return overrides[name]
        raw = (self.definition.get("parameters", {}) or {}).get(name, default)
        if isinstance(raw, dict):
            if raw.get("source") == "layer_height":
                # Use the per-layer height only when it is explicitly configured.
                # Otherwise the symbol library default wins instead of blindly
                # inheriting QCALVIEW's generic 3 m extrusion default (critical
                # for trees, PV tables and wind turbines).
                explicit_height = bool(str(getattr(self.style, "height_field_override", "") or "").strip()) or (getattr(self.style, "default_height_override", None) is not None)
                if explicit_height:
                    try:
                        return float(self.layer_height_m)
                    except Exception:
                        pass
                return raw.get("default", default)
            fld = str(raw.get("field", "") or "").strip()
            if fld:
                val = self.field_value(fld, None)
                if val not in (None, ""):
                    return val
            return raw.get("default", default)
        return raw


# -----------------------------------------------------------------------------
# Symbol library
# -----------------------------------------------------------------------------

class SymbolLibrary:
    def __init__(self, plugin_dir: str):
        self.plugin_dir = os.path.abspath(plugin_dir)
        self.root = os.path.join(self.plugin_dir, "resources", "symbols")
        self._defs: Dict[str, Dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        defs: Dict[str, Dict[str, Any]] = {}
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                if not name.lower().endswith(".json"):
                    continue
                path = os.path.join(self.root, name)
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    sid = str(data.get("id", "") or "").strip()
                    if not sid:
                        continue
                    data["_path"] = path
                    defs[sid] = data
                except Exception:
                    continue
        self._defs = defs

    def definitions(self) -> List[Dict[str, Any]]:
        return [self._defs[k] for k in sorted(
            self._defs,
            key=lambda x: (
                str(self._defs[x].get("category", "") or "").lower(),
                str(self._defs[x].get("name", x)).lower(),
            ),
        )]

    def get(self, symbol_id: str) -> Optional[Dict[str, Any]]:
        return self._defs.get(str(symbol_id or ""))

    def asset_path(self, definition: Dict[str, Any], rel: str) -> str:
        rel = str(rel or "").strip()
        if not rel:
            return ""
        if os.path.isabs(rel):
            return rel
        return os.path.normpath(os.path.join(self.root, rel))


_LIBRARY_CACHE: Dict[str, SymbolLibrary] = {}


def get_symbol_library(plugin_dir: str) -> SymbolLibrary:
    key = os.path.abspath(plugin_dir)
    lib = _LIBRARY_CACHE.get(key)
    if lib is None:
        lib = SymbolLibrary(key)
        _LIBRARY_CACHE[key] = lib
    return lib


def style_uses_schematic(style: Any) -> bool:
    return bool(getattr(style, "schematic_enabled", False) and str(getattr(style, "schematic_symbol_id", "") or "").strip())


def _selected_assets(ctx: SymbolBuildContext, default_rel: str = "") -> List[str]:
    out=[]
    for raw in list(getattr(ctx.style, 'schematic_asset_paths', []) or [])[:3]:
        path=os.path.normpath(str(raw or '').strip())
        if path and os.path.isfile(path) and path.lower().endswith(('.svg','.png')):
            out.append(path)
    if out: return out
    if default_rel:
        path=get_symbol_library(ctx.plugin_dir).asset_path(ctx.definition, default_rel)
        if path: out.append(path)
    return out

def _asset_for_index(assets: Sequence[str], idx: int) -> str:
    return str(assets[int(idx) % len(assets)]) if assets else ''


# -----------------------------------------------------------------------------
# Geometry generators
# -----------------------------------------------------------------------------

class ExtrusionSymbol(ProjectedSymbol):
    generator_id = "extrusion"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        h = max(0.0, float(ctx.layer_height_m or 0.0))
        out: List[Primitive3D] = []
        for arr in ctx.parts:
            if arr is None or arr.shape[0] < 2:
                continue
            base = _xyz_on_ground(arr, ctx)
            top = base.copy(); top[:, 2] += h
            if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry and arr.shape[0] >= 3:
                out.append(Polygon3D(xyz=top, role="roof"))
                for i in range(arr.shape[0] - 1):
                    face = np.vstack([base[i], base[i + 1], top[i + 1], top[i]])
                    out.append(Polygon3D(xyz=face, role="wall"))
            elif ctx.gtype == QC.QgsWkbTypes_GeometryType_LineGeometry and h > 0:
                for i in range(arr.shape[0] - 1):
                    face = np.vstack([base[i], base[i + 1], top[i + 1], top[i]])
                    out.append(Polygon3D(xyz=face, role="wall"))
        return out


def _resample_line_with_vertex_ground(ctx: SymbolBuildContext, arr: np.ndarray, step: float):
    """Rééchantillonne XY en interpolant le profil Z des sommets source.

    Le rendu linéaire/2,5D classique pose les sommets source sur le MNT puis les
    relie. Les haies/clôtures AVR rééchantillonnaient auparavant le MNT à chaque
    point généré, ce qui déplaçait visuellement le pied de la même géométrie.
    """
    try:
        xy=[(float(x),float(y)) for x,y in np.asarray(arr)[:,:2]]
    except Exception:
        return []
    if len(xy)<2: return []
    zsrc=[float(ctx.ground_z(x,y)) for x,y in xy]
    step=max(0.05,float(step)); out=[(xy[0][0],xy[0][1],zsrc[0])]; carry=0.0
    for si,(a,b) in enumerate(zip(xy[:-1],xy[1:])):
        ax,ay=a; bx,by=b; za,zb=zsrc[si],zsrc[si+1]; dx,dy=bx-ax,by-ay; seg=math.hypot(dx,dy)
        if seg<=1e-12: continue
        travelled=step-carry if carry>1e-12 else step
        while travelled<seg-1e-12:
            u=travelled/seg; out.append((ax+dx*u,ay+dy*u,za+(zb-za)*u)); travelled+=step
        lx,ly,_=out[-1]; carry=math.hypot(bx-lx,by-ly)
        if carry>=step: carry=math.fmod(carry,step)
    if math.hypot(out[-1][0]-xy[-1][0],out[-1][1]-xy[-1][1])>1e-9:
        out.append((xy[-1][0],xy[-1][1],zsrc[-1]))
    return out


class VegetationRibbonSymbol(ProjectedSymbol):
    generator_id = "vegetation_ribbon"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        h0 = max(0.05, _to_float(ctx.param("height_m", ctx.layer_height_m), max(0.05, ctx.layer_height_m)))
        sample_step = max(0.25, _to_float(ctx.param("sample_step_m", 2.0), 2.0))
        irr = max(0.0, min(0.45, _to_float(ctx.param("irregularity", 0.12), 0.12)))
        crown_min = max(0.2, min(1.0, _to_float(ctx.param("crown_min_ratio", 0.72), 0.72)))
        seed = _feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if arr is None or arr.shape[0] < 2:
                continue
            pts3 = _resample_line_with_vertex_ground(ctx, arr, sample_step)
            if len(pts3) < 2:
                continue
            noise = deterministic_noise(seed + pidx * 977, len(pts3), amplitude=irr)
            base = np.zeros((len(pts3), 3), dtype=np.float64)
            top = np.zeros((len(pts3), 3), dtype=np.float64)
            for i, ((x, y, z), n) in enumerate(zip(pts3, noise)):
                ratio = max(crown_min, 1.0 + float(n))
                base[i] = (x, y, z)
                top[i] = (x, y, z + h0 * ratio)
            for i in range(len(pts3) - 1):
                out.append(Polygon3D(xyz=np.vstack([base[i], base[i + 1], top[i + 1], top[i]]), role="vegetation"))
        return out

class PVSurfaceSymbol(ProjectedSymbol):
    generator_id = "pv_surface"

    @staticmethod
    def _append_vertical_post(out: List[Primitive3D], ctx: SymbolBuildContext, x: float, y: float,
                              z_top: float, width_m: float, role: str = "pv_support", seen: set = None) -> None:
        """Append a square vertical support using metric world dimensions.

        Four side faces are generated so the support itself participates in the
        PINHOLE z-buffer.  This replaces the former pixel-width Polyline3D feet.
        """
        if not (math.isfinite(float(x)) and math.isfinite(float(y)) and math.isfinite(float(z_top))):
            return
        width_m = max(0.005, float(width_m or 0.005))
        key = (round(float(x), 5), round(float(y), 5), round(width_m, 4), str(role))
        if seen is not None and key in seen:
            return
        if seen is not None:
            seen.add(key)
        z0 = float(ctx.ground_z(float(x), float(y)))
        z1 = float(z_top)
        if not math.isfinite(z0) or z1 <= z0 + 1e-4:
            return
        h = 0.5 * width_m
        xy = [
            (x-h, y-h), (x+h, y-h), (x+h, y+h), (x-h, y+h)
        ]
        for a, b in zip(xy, xy[1:] + xy[:1]):
            out.append(Polygon3D(xyz=np.asarray([
                [a[0], a[1], z0], [b[0], b[1], z0],
                [b[0], b[1], z1], [a[0], a[1], z1]
            ], dtype=np.float64), role=role, outline=True, fill=True))
        out.append(Polygon3D(xyz=np.asarray([[xx, yy, z1] for xx, yy in xy], dtype=np.float64),
                             role=role, outline=True, fill=True))

    @classmethod
    def _append_corner_supports(cls, out: List[Primitive3D], ctx: SymbolBuildContext, corners: np.ndarray,
                                top_z: np.ndarray, width_m: float, seen: set) -> None:
        if corners is None or top_z is None:
            return
        for (x, y), z_top in zip(np.asarray(corners, dtype=np.float64), np.asarray(top_z, dtype=np.float64)):
            cls._append_vertical_post(out, ctx, float(x), float(y), float(z_top), width_m,
                                      role="pv_support", seen=seen)

    @staticmethod
    def _line_normal_for_azimuth(a, b, azimuth_deg: float):
        dx, dy = float(b[0]-a[0]), float(b[1]-a[1])
        seg = math.hypot(dx, dy)
        if seg <= 1e-9:
            return None
        nx, ny = -dy/seg, dx/seg
        az = math.radians(float(azimuth_deg))
        tx, ty = math.sin(az), math.cos(az)
        if (nx * tx + ny * ty) < 0.0:
            nx, ny = -nx, -ny
        return np.asarray([nx, ny], dtype=np.float64)

    @staticmethod
    def _resample_axis(arr: np.ndarray, step_m: float):
        pts = resample_polyline([(float(x), float(y)) for x, y in np.asarray(arr)[:, :2]], max(0.5, float(step_m)))
        # Always keep the last source endpoint so a tracker row is supported at both ends.
        if arr is not None and len(arr) and pts:
            last=(float(arr[-1][0]), float(arr[-1][1]))
            if math.hypot(pts[-1][0]-last[0], pts[-1][1]-last[1]) > 0.05:
                pts.append(last)
        return pts

    def _build_line_fixed_or_vertical(self, ctx, out, arr, tilt_deg, azimuth_deg, width, low,
                                      show_supports, support_width, support_seen):
        tilt_deg = max(0.0, min(90.0, float(tilt_deg)))
        t = math.radians(tilt_deg)
        horiz_half = 0.5 * width * math.cos(t)
        dz = width * math.sin(t)
        for a, b in zip(arr[:-1], arr[1:]):
            n = self._line_normal_for_azimuth(a, b, azimuth_deg)
            if n is None:
                continue
            low_xy = np.asarray([
                [float(a[0])-n[0]*horiz_half, float(a[1])-n[1]*horiz_half],
                [float(b[0])-n[0]*horiz_half, float(b[1])-n[1]*horiz_half],
            ], dtype=np.float64)
            high_xy = np.asarray([
                [float(b[0])+n[0]*horiz_half, float(b[1])+n[1]*horiz_half],
                [float(a[0])+n[0]*horiz_half, float(a[1])+n[1]*horiz_half],
            ], dtype=np.float64)
            corners = np.vstack([low_xy, high_xy])
            z_ground = np.asarray([ctx.ground_z(x,y) for x,y in corners], dtype=np.float64)
            z = z_ground + low
            z[2:] += dz
            out.append(Polygon3D(xyz=np.column_stack([corners, z]), role="pv", outline=True, fill=True))
            if show_supports:
                self._append_corner_supports(out, ctx, corners, z, support_width, support_seen)

    def _build_polygon_fixed_or_vertical(self, ctx, out, arr, tilt_deg, azimuth_deg, low,
                                         show_supports, support_width, support_seen):
        xy = np.asarray(arr[:, :2], dtype=np.float64)
        if xy.shape[0] < 3:
            return
        tilt_deg = max(0.0, min(90.0, float(tilt_deg)))
        t = math.radians(tilt_deg)
        az = math.radians(float(azimuth_deg))
        s = np.asarray([math.sin(az), math.cos(az)], dtype=np.float64)
        q = np.asarray([-s[1], s[0]], dtype=np.float64)
        u = xy @ s
        v = xy @ q
        finite_u = [float(v) for v in u if math.isfinite(float(v))]
        if not finite_u:
            return
        u0 = min(finite_u)
        # Keep the real panel length constant: its plan projection shrinks with tilt.
        u_new = u0 + (u-u0) * math.cos(t)
        xy_new = np.outer(u_new, s) + np.outer(v, q)
        z_ground = np.asarray([ctx.ground_z(x,y) for x,y in xy_new], dtype=np.float64)
        z = z_ground + low + (u-u0) * math.sin(t)
        out.append(Polygon3D(xyz=np.column_stack([xy_new, z]), role="pv", outline=True, fill=True))
        if show_supports:
            self._append_corner_supports(out, ctx, xy_new, z, support_width, support_seen)

    def _build_tracker_line(self, ctx, out, arr, angle_deg, azimuth_deg, width, axis_height,
                            show_supports, support_width, post_spacing, show_torque_tube):
        angle_deg = max(-60.0, min(60.0, float(angle_deg)))
        t = math.radians(angle_deg)
        post_seen=set()
        # Surfaces are generated per source segment around the longitudinal torque axis.
        for a, b in zip(arr[:-1], arr[1:]):
            n = self._line_normal_for_azimuth(a, b, azimuth_deg)
            if n is None:
                continue
            hhalf=0.5*width*math.cos(t)
            dzhalf=0.5*width*math.sin(t)
            corners=np.asarray([
                [float(a[0])-n[0]*hhalf, float(a[1])-n[1]*hhalf],
                [float(b[0])-n[0]*hhalf, float(b[1])-n[1]*hhalf],
                [float(b[0])+n[0]*hhalf, float(b[1])+n[1]*hhalf],
                [float(a[0])+n[0]*hhalf, float(a[1])+n[1]*hhalf],
            ], dtype=np.float64)
            axis_ground_a=ctx.ground_z(float(a[0]),float(a[1])); axis_ground_b=ctx.ground_z(float(b[0]),float(b[1]))
            z=np.asarray([axis_ground_a+axis_height-dzhalf, axis_ground_b+axis_height-dzhalf,
                          axis_ground_b+axis_height+dzhalf, axis_ground_a+axis_height+dzhalf], dtype=np.float64)
            out.append(Polygon3D(xyz=np.column_stack([corners,z]), role="pv_tracker", outline=True, fill=True))
        axis_pts=self._resample_axis(arr, post_spacing)
        if show_supports:
            for x,y in axis_pts:
                ztop=ctx.ground_z(x,y)+axis_height
                self._append_vertical_post(out, ctx, x, y, ztop, support_width, role="pv_tracker_support", seen=post_seen)
        if show_torque_tube and arr.shape[0] >= 2:
            tube=np.asarray([[float(x),float(y),ctx.ground_z(float(x),float(y))+axis_height] for x,y in arr[:,:2]], dtype=np.float64)
            out.append(Polyline3D(xyz=tube, role="pv_torque_tube"))

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        mode = str(ctx.param("pv_mode", "fixed") or "fixed").strip().lower()
        low = max(0.0, _to_float(ctx.param("low_height_m", ctx.layer_height_m), ctx.layer_height_m))
        tilt_deg = _to_float(ctx.param("tilt_deg", 20.0), 20.0)
        azimuth_deg = _to_float(ctx.param("azimuth_deg", 180.0), 180.0)
        width = max(0.1, _to_float(ctx.param("table_width_m", 4.5), 4.5))
        show_supports = _to_bool(ctx.param("show_supports", True), True)
        support_width = max(0.01, min(0.50, _to_float(ctx.param("support_width_m", 0.08), 0.08)))
        support_seen=set()

        if mode == "vertical":
            tilt_deg = 90.0

        if mode == "tracker":
            # Trackers are intentionally line-based: a row is a longitudinal torque axis.
            if ctx.gtype != QC.QgsWkbTypes_GeometryType_LineGeometry:
                return out
            axis_height=max(0.2, _to_float(ctx.param("tracker_axis_height_m", 2.0), 2.0))
            post_spacing=max(1.0, _to_float(ctx.param("tracker_post_spacing_m", 8.0), 8.0))
            tracker_support_width=max(0.02, min(0.50, _to_float(ctx.param("tracker_support_width_m", 0.14), 0.14)))
            show_torque=_to_bool(ctx.param("show_torque_tube", True), True)
            for arr in ctx.parts:
                if arr is None or arr.shape[0] < 2:
                    continue
                self._build_tracker_line(ctx, out, np.asarray(arr,dtype=np.float64), tilt_deg, azimuth_deg, width,
                                         axis_height, show_supports, tracker_support_width, post_spacing, show_torque)
            return out

        if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            for arr in ctx.parts:
                if arr is None or arr.shape[0] < 3:
                    continue
                self._build_polygon_fixed_or_vertical(ctx, out, np.asarray(arr,dtype=np.float64), tilt_deg, azimuth_deg,
                                                      low, show_supports, support_width, support_seen)
        elif ctx.gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            for arr in ctx.parts:
                if arr is None or arr.shape[0] < 2:
                    continue
                self._build_line_fixed_or_vertical(ctx, out, np.asarray(arr,dtype=np.float64), tilt_deg, azimuth_deg, width,
                                                   low, show_supports, support_width, support_seen)
        return out


class WindTurbineSymbol(ProjectedSymbol):
    generator_id = "wind_turbine"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        hub_h = max(1.0, _to_float(ctx.param("hub_height_m", ctx.layer_height_m), ctx.layer_height_m))
        rotor_d = max(1.0, _to_float(ctx.param("rotor_diameter_m", 160.0), 160.0))
        base_w = max(0.1, _to_float(ctx.param("tower_base_width_m", 6.0), 6.0))
        top_w = max(0.1, _to_float(ctx.param("tower_top_width_m", 3.0), 3.0))
        nac_len = max(0.1, _to_float(ctx.param("nacelle_length_m", 12.0), 12.0))
        blade_root = max(0.05, _to_float(ctx.param("blade_root_width_m", 5.0), 5.0))
        blade_tip = max(0.02, _to_float(ctx.param("blade_tip_width_m", 1.2), 1.2))
        rotor_angle = math.radians(_to_float(ctx.param("rotor_angle_deg", 0.0), 0.0))
        radius = rotor_d * 0.5
        cx, cy = ctx.camera_xy
        for pidx, arr in enumerate(ctx.parts):
            if arr is None or arr.shape[0] < 1:
                continue
            if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                ring=[(float(px),float(py)) for px,py in arr[:, :2]]
                spacing=max(1.0, _to_float(ctx.param("spacing_m", max(rotor_d * 2.0, 250.0)), max(rotor_d * 2.0, 250.0)))
                jitter=max(0.0, min(0.45, _to_float(ctx.param("position_jitter", 0.12), 0.12)))
                limit=max(1, int(round(_to_float(ctx.param("max_instances", 100), 100))))
                targets=_points_in_polygon_grid(ring, spacing, _feature_seed(ctx.feature)+pidx*3253, jitter, limit)
            else:
                targets=[(float(px),float(py)) for px,py in arr[:, :2]]
            for x, y in targets:
                x = float(x); y = float(y); gz = ctx.ground_z(x, y)
                # Rotor orientation. Default is face-camera; optional common azimuth
                # gives the nacelle/rotor axis in QGIS azimuth convention.
                vx, vy = x - cx, y - cy
                d = math.hypot(vx, vy)
                mode = str(ctx.param("orientation_mode", "face_camera") or "face_camera").strip().lower()
                if mode in ("oriented", "azimuth", "orientation"):
                    naz = math.radians(_to_float(ctx.param("azimuth_deg", 0.0), 0.0))
                    nx, ny = math.sin(naz), math.cos(naz)
                    ex, ey = -ny, nx
                elif d <= 1e-9:
                    nx, ny = 0.0, 1.0; ex, ey = 1.0, 0.0
                else:
                    # axis points toward camera; rotor plane is perpendicular to it
                    nx, ny = -vx / d, -vy / d
                    ex, ey = -ny, nx
                hub = np.array([x, y, gz + hub_h], dtype=np.float64)
                # Tower filled trapezoid.
                tower = np.asarray([
                    [x - ex * base_w * 0.5, y - ey * base_w * 0.5, gz],
                    [x + ex * base_w * 0.5, y + ey * base_w * 0.5, gz],
                    [x + ex * top_w * 0.5, y + ey * top_w * 0.5, gz + hub_h],
                    [x - ex * top_w * 0.5, y - ey * top_w * 0.5, gz + hub_h],
                ], dtype=np.float64)
                out.append(Polygon3D(xyz=tower, role="wind_tower", metadata={"paint_priority": 0}))
                # Nacelle is aligned with the wind axis. A small transverse width
                # guarantees an opaque cap over the tower at the hub.
                nh = max(top_w * 1.4, rotor_d * 0.018)
                nw = max(top_w * 1.35, rotor_d * 0.014)
                c = hub + np.array([nx * nac_len * 0.15, ny * nac_len * 0.15, 0.0])
                # visible side face; face-camera mode naturally collapses its length
                nac = np.asarray([
                    c + np.array([-nx*nac_len*0.5-ex*nw*0.5, -ny*nac_len*0.5-ey*nw*0.5, -nh*0.5]),
                    c + np.array([ nx*nac_len*0.5-ex*nw*0.5,  ny*nac_len*0.5-ey*nw*0.5, -nh*0.5]),
                    c + np.array([ nx*nac_len*0.5+ex*nw*0.5,  ny*nac_len*0.5+ey*nw*0.5,  nh*0.5]),
                    c + np.array([-nx*nac_len*0.5+ex*nw*0.5, -ny*nac_len*0.5+ey*nw*0.5,  nh*0.5]),
                ], dtype=np.float64)
                out.append(Polygon3D(xyz=nac, role="wind_nacelle", metadata={"paint_priority": 20}))
                # Three tapered blade polygons in the vertical plane facing camera.
                for k in range(3):
                    a = rotor_angle + k * (2.0 * math.pi / 3.0)
                    # In-plane coordinates: horizontal along e=(ex,ey), vertical in Z.
                    root_r = max(1.5, radius * 0.035)
                    def wp(rad: float, tang: float) -> np.ndarray:
                        rr = np.array([ex * math.cos(a), ey * math.cos(a), math.sin(a)], dtype=np.float64)
                        tt = np.array([-ex * math.sin(a), -ey * math.sin(a), math.cos(a)], dtype=np.float64)
                        return hub + rr * rad + tt * tang
                    blade = np.vstack([
                        wp(root_r, -blade_root * 0.5),
                        wp(radius, -blade_tip * 0.5),
                        wp(radius, blade_tip * 0.5),
                        wp(root_r, blade_root * 0.5),
                    ])
                    out.append(Polygon3D(xyz=blade, role="wind_blade", metadata={"paint_priority": 30}))
        return out


class BillboardSVGSymbol(ProjectedSymbol):
    generator_id = "billboard_svg"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        h = max(0.05, _to_float(ctx.param("height_m", ctx.layer_height_m), ctx.layer_height_m))
        w = max(0.05, _to_float(ctx.param("width_m", max(0.5, h * 0.6)), max(0.5, h * 0.6)))
        rel_svg = str(ctx.param("svg", "") or "")
        assets = _selected_assets(ctx, rel_svg)
        item_idx = 0
        seed=_feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if _billboard_budget_exhausted(ctx):
                break
            if arr is None or arr.shape[0] < 1:
                continue
            if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                ring=[(float(px),float(py)) for px,py in arr[:, :2]]
                spacing=max(0.25, _to_float(ctx.param("spacing_m", max(w * 2.0, 2.0)), max(w * 2.0, 2.0)))
                jitter=max(0.0, min(0.45, _to_float(ctx.param("position_jitter", 0.18), 0.18)))
                limit=max(1, int(round(_to_float(ctx.param("max_instances", 400), 400))))
                targets=_points_in_polygon_grid(ring, spacing, seed+pidx*3253, jitter, limit)
            else:
                targets=[(float(px),float(py)) for px,py in arr[:, :2]]
            for x, y in targets:
                pr = _billboard_primitive_if_allowed(ctx, float(x), float(y), w, h, _asset_for_index(assets, item_idx))
                if pr is not None:
                    out.append(pr); item_idx += 1
                if _billboard_budget_exhausted(ctx):
                    break
        return out


class VegetationAdaptiveSymbol(ProjectedSymbol):
    """One vegetation definition usable on point, line and polygon layers.

    Point   -> one SVG billboard per point.
    Line    -> repeated SVG alignment or continuous vegetation ribbon.
    Polygon -> irregular vegetation mass or deterministic SVG stand.

    The generator deliberately remains 2.5D. No mesh, lighting or scene graph is
    introduced; it only emits the same Polygon3D/Billboard3D primitives already
    consumed by QCALVIEW's schematic painter.
    """

    generator_id = "vegetation_adaptive"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        h = max(0.05, _to_float(ctx.param("height_m", ctx.layer_height_m), max(0.05, ctx.layer_height_m)))
        w = max(0.05, _to_float(ctx.param("width_m", max(0.5, h * 0.6)), max(0.5, h * 0.6)))
        rel_svg = str(ctx.param("svg", "") or "")
        assets = _selected_assets(ctx, rel_svg)

        if ctx.gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
            return self._build_points(ctx, h, w, assets)
        if ctx.gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            return self._build_lines(ctx, h, w, assets)
        if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            return self._build_polygons(ctx, h, w, assets)
        return []

    def _build_points(self, ctx: SymbolBuildContext, h: float, w: float, assets: Sequence[str]) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        item_idx = 0
        for arr in ctx.parts:
            if _billboard_budget_exhausted(ctx):
                break
            if arr is None or arr.shape[0] < 1:
                continue
            for x, y in arr[:, :2]:
                x = float(x); y = float(y)
                pr = _billboard_primitive_if_allowed(ctx, x, y, w, h, _asset_for_index(assets, item_idx))
                if pr is not None:
                    out.append(pr); item_idx += 1
                if _billboard_budget_exhausted(ctx):
                    break
        return out

    def _build_lines(self, ctx: SymbolBuildContext, h: float, w: float, assets: Sequence[str]) -> List[Primitive3D]:
        mode = str(ctx.param("line_mode", "alignment") or "alignment").strip().lower()
        if mode in ("ribbon", "continuous", "ruban", "haie"):
            return self._build_line_ribbon(ctx, h)

        spacing = max(0.25, _to_float(ctx.param("spacing_m", max(w, 1.0)), max(w, 1.0)))
        variation = max(0.0, min(0.45, _to_float(ctx.param("size_variation", 0.08), 0.08)))
        out: List[Primitive3D] = []
        seed = _feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if _billboard_budget_exhausted(ctx):
                break
            if arr is None or arr.shape[0] < 2:
                continue
            pts = resample_polyline([(float(x), float(y)) for x, y in arr[:, :2]], spacing)
            if not pts:
                continue
            # Closed lines would otherwise duplicate the first tree at the end.
            if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < max(0.05, spacing * 0.05):
                pts = pts[:-1]
            noise_h = deterministic_noise(seed + pidx * 1619, len(pts), amplitude=variation)
            noise_w = deterministic_noise(seed + pidx * 1619 + 811, len(pts), amplitude=variation * 0.75)
            for (x, y), nh, nw in zip(pts, noise_h, noise_w):
                hh = max(0.05, h * (1.0 + float(nh)))
                ww = max(0.05, w * (1.0 + float(nw)))
                pr = _billboard_primitive_if_allowed(ctx, x, y, ww, hh, _asset_for_index(assets, len(out)))
                if pr is not None:
                    out.append(pr)
                if _billboard_budget_exhausted(ctx):
                    break
        return out

    def _build_line_ribbon(self, ctx: SymbolBuildContext, h: float) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        sample_step = max(0.25, _to_float(ctx.param("mass_sample_step_m", ctx.param("sample_step_m", 2.0)), 2.0))
        irr = max(0.0, min(0.45, _to_float(ctx.param("irregularity", 0.12), 0.12)))
        crown_min = max(0.2, min(1.0, _to_float(ctx.param("crown_min_ratio", 0.72), 0.72)))
        seed = _feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if arr is None or arr.shape[0] < 2:
                continue
            pts3 = _resample_line_with_vertex_ground(ctx, arr, sample_step)
            if len(pts3) < 2:
                continue
            noise = deterministic_noise(seed + pidx * 977, len(pts3), amplitude=irr)
            base = np.zeros((len(pts3), 3), dtype=np.float64)
            top = np.zeros((len(pts3), 3), dtype=np.float64)
            for i, ((x, y, z), n) in enumerate(zip(pts3, noise)):
                ratio = max(crown_min, 1.0 + float(n))
                base[i] = (x, y, z)
                top[i] = (x, y, z + h * ratio)
            for i in range(len(pts3) - 1):
                out.append(Polygon3D(xyz=np.vstack([base[i], base[i + 1], top[i + 1], top[i]]), role="vegetation_ribbon"))
        return out

    def _build_polygons(self, ctx: SymbolBuildContext, h: float, w: float, assets: Sequence[str]) -> List[Primitive3D]:
        mode = str(ctx.param("polygon_mode", "mass") or "mass").strip().lower()
        if mode in ("instances", "stand", "peuplement", "trees"):
            return self._build_polygon_instances(ctx, h, w, assets)
        return self._build_polygon_mass(ctx, h)

    def _build_polygon_mass(self, ctx: SymbolBuildContext, h: float) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        step = max(0.5, _to_float(ctx.param("mass_sample_step_m", 3.0), 3.0))
        irr = max(0.0, min(0.45, _to_float(ctx.param("irregularity", 0.14), 0.14)))
        crown_min = max(0.2, min(1.0, _to_float(ctx.param("crown_min_ratio", 0.72), 0.72)))
        seed = _feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if arr is None or arr.shape[0] < 3:
                continue
            ring = [(float(x), float(y)) for x, y in arr[:, :2]]
            if math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1]) > 1e-8:
                ring.append(ring[0])
            pts = resample_polyline(ring, step)
            if len(pts) < 4:
                continue
            if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) > 1e-8:
                pts.append(pts[0])
            noise = deterministic_noise(seed + pidx * 2017, len(pts) - 1, amplitude=irr)
            noise.append(noise[0] if noise else 0.0)
            base = np.zeros((len(pts), 3), dtype=np.float64)
            top = np.zeros((len(pts), 3), dtype=np.float64)
            for i, ((x, y), n) in enumerate(zip(pts, noise)):
                gz = ctx.ground_z(x, y)
                ratio = max(crown_min, 1.0 + float(n))
                base[i] = (x, y, gz)
                top[i] = (x, y, gz + h * ratio)
            # Side faces give the stand a readable solid envelope from normal
            # viewpoints; the top cap is useful on elevated/drone photographs.
            for i in range(len(pts) - 1):
                out.append(Polygon3D(xyz=np.vstack([base[i], base[i + 1], top[i + 1], top[i]]), role="vegetation_mass"))
            if top.shape[0] >= 4:
                out.append(Polygon3D(xyz=top[:-1].copy(), role="vegetation_canopy"))
        return out

    def _build_polygon_instances(self, ctx: SymbolBuildContext, h: float, w: float, assets: Sequence[str]) -> List[Primitive3D]:
        out: List[Primitive3D] = []
        spacing = max(0.25, _to_float(ctx.param("spacing_m", max(w, 1.0)), max(w, 1.0)))
        variation = max(0.0, min(0.45, _to_float(ctx.param("size_variation", 0.08), 0.08)))
        jitter = max(0.0, min(0.45, _to_float(ctx.param("position_jitter", 0.12), 0.12)))
        max_instances = max(1, int(round(_to_float(ctx.param("max_instances", 400), 400))))
        seed = _feature_seed(ctx.feature)
        for pidx, arr in enumerate(ctx.parts):
            if _billboard_budget_exhausted(ctx):
                break
            if arr is None or arr.shape[0] < 3 or len(out) >= max_instances:
                continue
            ring = [(float(x), float(y)) for x, y in arr[:, :2]]
            points = _points_in_polygon_grid(ring, spacing, seed + pidx * 3253, jitter, max_instances - len(out))
            noise_h = deterministic_noise(seed + pidx * 3253 + 101, len(points), amplitude=variation)
            noise_w = deterministic_noise(seed + pidx * 3253 + 503, len(points), amplitude=variation * 0.75)
            for (x, y), nh, nw in zip(points, noise_h, noise_w):
                hh = max(0.05, h * (1.0 + float(nh)))
                ww = max(0.05, w * (1.0 + float(nw)))
                pr = _billboard_primitive_if_allowed(ctx, x, y, ww, hh, _asset_for_index(assets, len(out)))
                if pr is not None:
                    out.append(pr)
                if _billboard_budget_exhausted(ctx):
                    break
                if len(out) >= max_instances:
                    break
        return out


def _billboard_budget_exhausted(ctx: SymbolBuildContext) -> bool:
    try:
        b=ctx.runtime('_billboard_budget_state',None)
        return isinstance(b,dict) and int(b.get('remaining',1))<=0
    except Exception:
        return False


def _billboard_primitive_if_allowed(ctx: SymbolBuildContext, x: float, y: float,
                                    width_m: float, height_m: float, svg_path: str):
    """Preview-only billboard culling and global frame budget.

    Exports and PINHOLE pass no runtime budget, therefore retain the exact
    requested instance density.  In panoramic previews we reject sub-pixel
    instances *before* sampling ground Z or creating a Billboard3D object.
    """
    try:
        x=float(x); y=float(y); h=max(0.05,float(height_m)); w=max(0.05,float(width_m))
        budget=ctx.runtime('_billboard_budget_state',None)
        if isinstance(budget,dict):
            if int(budget.get('remaining',0))<=0:
                budget['exhausted']=True
                budget['dropped_budget']=int(budget.get('dropped_budget',0))+1
                return None
            cx,cy=ctx.camera_xy
            dx=x-float(cx); dy=y-float(cy); d=math.hypot(dx,dy)
            maxdist=ctx.runtime('_maxdist_m',None)
            if maxdist is not None and float(maxdist)>0.0 and d>float(maxdist):
                budget['culled_distance']=int(budget.get('culled_distance',0))+1
                return None
            ppr=max(0.0,float(ctx.runtime('_pixels_per_rad',0.0) or 0.0))
            min_px=max(0.0,float(ctx.runtime('_min_billboard_px',0.0) or 0.0))
            if ppr>0.0 and min_px>0.0 and d>1e-6:
                # Conservative max(width,height) angular extent: avoids hiding
                # slender/tall symbols near the cylindrical vertical edges.
                ang=max(2.0*math.atan2(h*0.5,d),2.0*math.atan2(w*0.5,d))
                if ang*ppr < min_px:
                    budget['culled_lod']=int(budget.get('culled_lod',0))+1
                    return None
            budget['remaining']=max(0,int(budget.get('remaining',0))-1)
            budget['generated']=int(budget.get('generated',0))+1
        z=ctx.ground_z(x,y)
        return _billboard_primitive(ctx,x,y,z,w,h,svg_path)
    except Exception:
        return None


def _billboard_primitive(ctx: SymbolBuildContext, x: float, y: float, z: float, width_m: float, height_m: float, svg_path: str) -> Billboard3D:
    return Billboard3D(
        anchor_xyz=(float(x), float(y), float(z)),
        width_m=max(0.05, float(width_m)),
        height_m=max(0.05, float(height_m)),
        svg_path=svg_path,
        role="billboard",
        metadata={
            "symbol_id": str(ctx.definition.get("id", "") or ""),
            "fallback": str(ctx.definition.get("fallback", "generic") or "generic"),
        },
    )


def _point_in_ring(x: float, y: float, ring: Sequence[Tuple[float, float]]) -> bool:
    """Even/odd point-in-polygon test; boundary counts as inside."""
    pts = [(float(px), float(py)) for px, py in ring]
    if len(pts) < 3:
        return False
    inside = False
    j = len(pts) - 1
    eps = 1e-10
    for i in range(len(pts)):
        xi, yi = pts[i]; xj, yj = pts[j]
        # Boundary test first to avoid dropping regular rows that fall exactly
        # on a plantation polygon edge.
        dx = xj - xi; dy = yj - yi
        den = dx * dx + dy * dy
        if den > eps:
            t = ((x - xi) * dx + (y - yi) * dy) / den
            if -eps <= t <= 1.0 + eps:
                qx = xi + t * dx; qy = yi + t * dy
                if (x - qx) ** 2 + (y - qy) ** 2 <= 1e-12:
                    return True
        crosses = ((yi > y) != (yj > y))
        if crosses:
            xcross = (xj - xi) * (y - yi) / ((yj - yi) if abs(yj - yi) > eps else eps) + xi
            if x <= xcross:
                inside = not inside
        j = i
    return inside


def _points_in_polygon_grid(ring: Sequence[Tuple[float, float]], spacing: float, seed: int, jitter_ratio: float, limit: int) -> List[Tuple[float, float]]:
    """Deterministic staggered grid for orchards/plantations/woodland stands."""
    pts = [(float(x), float(y)) for x, y in ring]
    if len(pts) < 3 or limit <= 0:
        return []
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs); miny, maxy = min(ys), max(ys)
    step = max(0.25, float(spacing))
    # Local Random is deterministic and does not affect Python's global RNG.
    import random
    rnd = random.Random(int(seed) & 0xFFFFFFFF)
    out: List[Tuple[float, float]] = []
    row = 0
    y = miny + step * 0.5
    while y <= maxy + 1e-9 and len(out) < limit:
        x = minx + step * (0.5 if (row % 2 == 0) else 1.0)
        while x <= maxx + 1e-9 and len(out) < limit:
            jx = (rnd.uniform(-1.0, 1.0) * jitter_ratio * step) if jitter_ratio > 0 else 0.0
            jy = (rnd.uniform(-1.0, 1.0) * jitter_ratio * step) if jitter_ratio > 0 else 0.0
            px, py = x + jx, y + jy
            if _point_in_ring(px, py, pts):
                out.append((px, py))
            x += step
        row += 1
        y += step
    return out


class FencePerimeterSymbol(ProjectedSymbol):
    """Clôture linéaire. Sur polygone, suit uniquement l'anneau extérieur fourni.

    Il n'existe volontairement aucune dispersion surfacique pour ce générateur.
    """
    generator_id = "fence_perimeter"

    def build(self, ctx: SymbolBuildContext) -> List[Primitive3D]:
        out=[]
        h=max(0.1, _to_float(ctx.param("height_m", 1.4), 1.4))
        spacing=max(0.5, _to_float(ctx.param("post_spacing_m", 3.0), 3.0))
        post_w=max(0.01, min(0.15, _to_float(ctx.param("post_width_m", 0.08), 0.08)))
        rails=max(1, min(4, int(round(_to_float(ctx.param("rail_count", 2), 2)))))
        cx, cy = ctx.camera_xy
        for arr in ctx.parts:
            if arr is None or arr.shape[0] < 2:
                continue
            arr_work=np.asarray(arr,dtype=np.float64)
            if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry and math.hypot(arr_work[0,0]-arr_work[-1,0],arr_work[0,1]-arr_work[-1,1])>1e-8:
                arr_work=np.vstack([arr_work,arr_work[0]])
            pts3=_resample_line_with_vertex_ground(ctx,arr_work,spacing)
            if len(pts3)<2:
                continue
            if ctx.gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry and math.hypot(pts3[0][0]-pts3[-1][0],pts3[0][1]-pts3[-1][1])>1e-8:
                pts3.append(pts3[0])
            base=[]; top=[]
            for x,y,z in pts3:
                base.append((x,y,z)); top.append((x,y,z+h))
                vx,vy=float(x)-float(cx),float(y)-float(cy); dd=math.hypot(vx,vy)
                ex,ey=(1.0,0.0) if dd<=1e-9 else (-vy/dd,vx/dd); hw=0.5*post_w
                post=np.asarray([[x-ex*hw,y-ey*hw,z],[x+ex*hw,y+ey*hw,z],[x+ex*hw,y+ey*hw,z+h],[x-ex*hw,y-ey*hw,z+h]],dtype=np.float64)
                out.append(Polygon3D(xyz=post,role="fence_post",outline=True,fill=True))
            for rr in range(1,rails+1):
                frac=float(rr)/(rails+1); rail=np.asarray([[x,y,z+(zt-z)*frac] for (x,y,z),(xt,yt,zt) in zip(base,top)],dtype=np.float64)
                if rail.shape[0]>=2: out.append(Polyline3D(xyz=rail,role="fence_rail"))
        return out

_GENERATORS = {
    ExtrusionSymbol.generator_id: ExtrusionSymbol,
    VegetationRibbonSymbol.generator_id: VegetationRibbonSymbol,
    PVSurfaceSymbol.generator_id: PVSurfaceSymbol,
    WindTurbineSymbol.generator_id: WindTurbineSymbol,
    BillboardSVGSymbol.generator_id: BillboardSVGSymbol,
    VegetationAdaptiveSymbol.generator_id: VegetationAdaptiveSymbol,
    FencePerimeterSymbol.generator_id: FencePerimeterSymbol,
}


# -----------------------------------------------------------------------------
# Projection / QPainter renderer
# -----------------------------------------------------------------------------


def schematic_role_colors(style, definition: Dict[str, Any], role: str) -> Tuple[QColor, QColor]:
    """Return the exact semantic fill/outline colors used by the schematic renderer."""
    appearance = (definition or {}).get("appearance", {}) or {}
    base_fill = QColor(getattr(style, "fill_color", QColor(70, 130, 90, 200)))
    base_line = QColor(getattr(style, "color", QColor(30, 60, 40, 240)))
    op = _opacity_factor(getattr(style, "opacity", 1.0))
    # Appearance alpha is a multiplier, not a hidden replacement for the layer
    # opacity.  At layer opacity 100 % and with a fully opaque color, built-in
    # schematic symbols must therefore be opaque.  PNG/SVG intrinsic alpha is
    # preserved later by the texture renderer.
    appearance_fill_alpha = int(appearance.get("fill_alpha", 255) or 255)
    appearance_outline_alpha = int(appearance.get("outline_alpha", 255) or 255)
    fill_alpha = int(round(base_fill.alpha() * (appearance_fill_alpha / 255.0) * op))
    outline_alpha = int(round(base_line.alpha() * (appearance_outline_alpha / 255.0) * op))
    fill_alpha = int(max(0, min(255, fill_alpha)))
    outline_alpha = int(max(0, min(255, outline_alpha)))
    base_fill.setAlpha(fill_alpha); base_line.setAlpha(outline_alpha)
    if role.startswith("pv") and not bool(getattr(style, "use_qgis_style", True)):
        base_fill = QColor(55, 80, 105, fill_alpha); base_line = QColor(28, 43, 60, outline_alpha)
    elif role.startswith("wind") and not bool(getattr(style, "use_qgis_style", True)):
        base_fill = QColor(210, 215, 220, fill_alpha); base_line = QColor(90, 95, 100, outline_alpha)
    elif role.startswith("vegetation") and not bool(getattr(style, "use_qgis_style", True)):
        base_fill = QColor(75, 115, 78, fill_alpha); base_line = QColor(45, 75, 49, outline_alpha)
    if role.startswith("fence"):
        try:
            params = dict(getattr(style, "schematic_params", {}) or {})
            c = QColor(str(params.get("fence_color", "") or ""))
            if c.isValid():
                c_fill = QColor(c); c_fill.setAlpha(fill_alpha)
                c_line = QColor(c); c_line.setAlpha(outline_alpha)
                base_fill, base_line = c_fill, c_line
        except Exception:
            pass
    return base_fill, base_line


def billboard_world_quad(primitive: Billboard3D, camera_xy: Tuple[float, float]) -> np.ndarray:
    """World-space camera-facing quad used by both QPainter and depth rendering."""
    x, y, z = primitive.anchor_xyz
    cx, cy = camera_xy
    vx, vy = x - cx, y - cy
    d = math.hypot(vx, vy)
    if d <= 1e-9:
        ex, ey = 1.0, 0.0
    else:
        ex, ey = -vy / d, vx / d
    half = primitive.width_m * 0.5
    return np.asarray([
        [x - ex * half, y - ey * half, z],
        [x + ex * half, y + ey * half, z],
        [x + ex * half, y + ey * half, z + primitive.height_m],
        [x - ex * half, y - ey * half, z + primitive.height_m],
    ], dtype=np.float64)


def build_schematic_feature_primitives(feature, parts: List[np.ndarray], gtype: int, style,
                                       z_sampler, layer_height_m: float, plugin_dir: str,
                                       camera_xy: Tuple[float, float], ground_profiles=None,
                                       runtime_overrides=None):
    """Build a schematic feature without painting it.

    This is the shared entry point used by both the historical QPainter path and
    the common software z-buffer path.  It prevents rendering and occlusion from
    using different geometries.
    """
    if not style_uses_schematic(style):
        return None, []
    lib = get_symbol_library(plugin_dir)
    definition = lib.get(getattr(style, "schematic_symbol_id", ""))
    if not definition:
        return None, []
    geom_names = {QC.QgsWkbTypes_GeometryType_PointGeometry: "point", QC.QgsWkbTypes_GeometryType_LineGeometry: "line", QC.QgsWkbTypes_GeometryType_PolygonGeometry: "polygon"}
    allowed = [str(v).lower() for v in (definition.get("geometry", []) or [])]
    if allowed and geom_names.get(gtype, "") not in allowed:
        return None, []
    generator_name = str(definition.get("generator", "") or "")
    klass = _GENERATORS.get(generator_name)
    if klass is None:
        return None, []
    build_ctx = SymbolBuildContext(
        definition=definition,
        feature=feature,
        parts=parts,
        gtype=gtype,
        style=style,
        z_sampler=z_sampler,
        camera_xy=(float(camera_xy[0]), float(camera_xy[1])),
        layer_height_m=float(layer_height_m or 0.0),
        plugin_dir=plugin_dir,
        ground_profiles_override=ground_profiles,
        runtime_overrides=(runtime_overrides if isinstance(runtime_overrides, dict) else None),
    )
    return definition, (klass().build(build_ctx) or [])


class SchematicPainterRenderer:
    """Projected Polygon3D/Polyline3D/Billboard3D → QPainter renderer."""
    def __init__(self, painter, camera_ctx, style, definition, library: SymbolLibrary, width: int, height: int, visibility_test=None):
        self.painter = painter
        self.ctx = camera_ctx
        self.style = style
        self.definition = definition
        self.library = library
        self.width = int(width)
        self.height = int(height)
        self.visibility_test = visibility_test
        self.appearance = definition.get("appearance", {}) or {}
        self.lod = definition.get("lod", {}) or {}

    def _colors(self, role: str) -> Tuple[QColor, QColor]:
        return schematic_role_colors(self.style, self.definition, role)

    def _project(self, xyz: np.ndarray, dist_max: float) -> np.ndarray:
        if xyz is None or xyz.shape[0] == 0:
            return np.empty((0, 2), dtype=np.float64)
        return project_points_batch(self.ctx, np.asarray(xyz[:, :2], dtype=np.float64), np.asarray(xyz[:, 2], dtype=np.float64), dist_max=dist_max)

    def _is_primitive_visible(self, xyz: np.ndarray) -> bool:
        if self.visibility_test is None or xyz is None or xyz.shape[0] == 0:
            return True
        try:
            # A partially visible schematic primitive is kept; exact clipping can
            # be added later without changing the generator architecture.
            return any(bool(self.visibility_test(float(x), float(y), float(z))) for x, y, z in xyz)
        except Exception:
            return True

    def _lod_level(self, uv: np.ndarray) -> str:
        if uv is None or uv.shape[0] == 0:
            return "hidden"
        mask = np.isfinite(uv[:, 0]) & np.isfinite(uv[:, 1])
        if not np.any(mask):
            return "hidden"
        pts = uv[mask]
        bounds = _small_xy_bounds(pts)
        if bounds is None:
            return "hidden"
        minx, maxx, miny, maxy = bounds
        extent = max(maxx - minx, maxy - miny)
        detailed = float(self.lod.get("detailed_min_px", 18.0) or 18.0)
        simple = float(self.lod.get("simple_min_px", 2.0) or 2.0)
        if extent < simple:
            return "hidden"
        return "detailed" if extent >= detailed else "simple"

    def _panorama_primitive(self, xyz: np.ndarray, dist_max: float, *, kind: str, role: str,
                            texture_uv=None, closed=False) -> PanoramicPrimitive2D:
        return project_panorama_primitive(
            self.ctx, np.asarray(xyz, dtype=np.float64), dist_max,
            kind=kind, role=role, texture_uv=texture_uv, closed=closed,
        )

    def _draw_polygon_panorama(self, primitive: Polygon3D, dist_max: float) -> bool:
        """Seam-safe panorama polygon painter backed by a depth-carrying primitive."""
        xyz = np.asarray(primitive.xyz, dtype=np.float64)
        if xyz.shape[0] < 3 or not self._is_primitive_visible(xyz):
            return False
        pp = self._panorama_primitive(xyz, dist_max, kind="polygon", role=primitive.role, closed=True)
        uv = pp.uv
        mask = np.isfinite(uv[:, 0]) & np.isfinite(uv[:, 1]) & np.isfinite(pp.radial_depth)
        pts = uv[mask]
        if pts.shape[0] < 3:
            return False
        W = float(self.width) if bool(self.ctx.get('is360', False)) else 0.0
        copies = list(iter_viewport_copies(pts, W, closed=True)) if W > 1.0 else [pts]
        copies = [np.asarray(run, dtype=np.float64) for run in copies if np.asarray(run).shape[0] >= 3]
        if not copies:
            return False
        # LOD is evaluated on an unwrapped local copy, not on raw seam-spanning UV.
        lod = self._lod_level(copies[0])
        if lod == "hidden":
            return False
        fill, line = self._colors(primitive.role)
        pen = QPen(line); pen.setWidthF(max(0.6, float(getattr(self.style, "width", 1.0) or 1.0)))
        brush = QBrush(fill)
        pattern = str(self.appearance.get("pattern", "solid") or "solid").lower()
        do_fill = bool(primitive.fill and getattr(self.style, "fill_polygons", True))
        if primitive.role == "wall" and not bool(getattr(self.style, "fill_walls", True)):
            do_fill = False
        drawn = False
        for run in copies:
            poly = QPolygonF([QPointF(float(u), float(v)) for u, v in run])
            if poly.first() != poly.last():
                poly.append(poly.first())
            self.painter.save()
            self.painter.setPen(pen if primitive.outline else QC.Qt_PenStyle_NoPen)
            self.painter.setBrush(brush if do_fill else QC.Qt_BrushStyle_NoBrush)
            self.painter.drawPolygon(poly); drawn = True
            if lod == "detailed":
                if primitive.role.startswith("vegetation") or pattern == "foliage":
                    hatch = QColor(line); hatch.setAlpha(max(45, min(150, line.alpha())))
                    self.painter.setPen(QPen(hatch, 0.7))
                    rect = poly.boundingRect()
                    spacing = max(6.0, min(18.0, rect.height() / 4.0 if rect.height() > 0 else 8.0))
                    clip = QPainterPath(); clip.addPolygon(poly); self.painter.setClipPath(clip)
                    x = rect.left() - rect.height()
                    while x <= rect.right() + rect.height():
                        self.painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + rect.height(), rect.top()))
                        x += spacing
                elif primitive.role == "pv" or pattern == "pv":
                    rect = poly.boundingRect(); clip = QPainterPath(); clip.addPolygon(poly); self.painter.setClipPath(clip)
                    grid = QColor(line); grid.setAlpha(max(50, min(150, line.alpha())))
                    self.painter.setPen(QPen(grid, 0.65))
                    for frac in (0.25, 0.5, 0.75):
                        x = rect.left() + rect.width() * frac
                        self.painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            self.painter.restore()
        return drawn

    def _draw_polyline_panorama(self, primitive: Polyline3D, dist_max: float) -> bool:
        xyz = np.asarray(primitive.xyz, dtype=np.float64)
        if xyz.shape[0] < 2 or not self._is_primitive_visible(xyz):
            return False
        pp = self._panorama_primitive(xyz, dist_max, kind="polyline", role=primitive.role, closed=False)
        uv = pp.uv
        _, line = self._colors(primitive.role)
        pen = QPen(line); pen.setWidthF(max(0.7, float(getattr(self.style, "width", 1.0) or 1.0)))
        W = float(self.width) if bool(self.ctx.get('is360', False)) else 0.0
        drawn = False; cur = []
        self.painter.save(); self.painter.setPen(pen)
        def flush(run):
            nonlocal drawn
            if len(run) < 2:
                return
            arr = np.asarray(run, dtype=np.float64)
            copies = iter_viewport_copies(arr, W, closed=False) if W > 1.0 else (arr,)
            for cp in copies:
                for i in range(cp.shape[0] - 1):
                    self.painter.drawLine(QPointF(float(cp[i,0]), float(cp[i,1])), QPointF(float(cp[i+1,0]), float(cp[i+1,1])))
                    drawn = True
        for i, (u, v) in enumerate(uv):
            d = pp.radial_depth[i] if i < len(pp.radial_depth) else float('inf')
            if np.isfinite(u) and np.isfinite(v) and np.isfinite(d):
                cur.append((float(u), float(v)))
            else:
                flush(cur); cur = []
        flush(cur); self.painter.restore()
        return drawn

    def _draw_billboard_panorama(self, primitive: Billboard3D, dist_max: float, camera_xy: Tuple[float, float]) -> bool:
        """Scalar panorama billboard hot path (40.18.7).

        A billboard has only four corners.  Keeping it out of NumPy batch masks,
        reductions and fancy-index writes removes the repeated native tiny-array
        operations seen in crash traces while preserving the same panorama
        mapping and 0/360 seam behavior.
        """
        try:
            x,y,z=(float(primitive.anchor_xyz[0]),float(primitive.anchor_xyz[1]),float(primitive.anchor_xyz[2]))
            cx,cy=float(camera_xy[0]),float(camera_xy[1])
            vx,vy=x-cx,y-cy; d=math.hypot(vx,vy)
            ex,ey=(1.0,0.0) if d<=1e-9 else (-vy/d,vx/d)
            half=max(0.025,float(primitive.width_m)*0.5); hh=max(0.05,float(primitive.height_m))
            quad=((x-ex*half,y-ey*half,z),(x+ex*half,y+ey*half,z),
                  (x+ex*half,y+ey*half,z+hh),(x-ex*half,y-ey*half,z+hh))
            if self.visibility_test is not None:
                try:
                    if not any(bool(self.visibility_test(px,py,pz)) for px,py,pz in quad):
                        return False
                except Exception:
                    pass
            projected=[]
            for px,py,pz in quad:
                q=project_panorama_point_scalar(self.ctx,px,py,pz,dist_max=dist_max)
                if q is None:
                    return False
                projected.append((q[0],q[1]))
            W=float(self.width) if bool(self.ctx.get('is360',False)) else 0.0
            bounds=panorama_quad_rects_scalar(projected,W,float(self.width),margin_px=2.0)
            if not bounds:
                return False
            rects=[]
            for left,right,top,bottom in bounds:
                if right<0.0 or left>self.width or bottom<0.0 or top>self.height:
                    continue
                rects.append(QRectF(left,top,max(1.0,right-left),max(1.0,bottom-top)))
            if not rects:
                return False
            local_extent=max(max(float(r.width()),float(r.height())) for r in rects)
            simple=float(self.lod.get('simple_min_px',2.0) or 2.0)
            if local_extent<simple:
                return False
            fill,line=self._colors(primitive.role)
            if primitive.svg_path and os.path.isfile(primitive.svg_path):
                try:
                    path=primitive.svg_path
                    if path.lower().endswith('.png'):
                        img=_panorama_cached_image(path)
                        if img is not None and not img.isNull():
                            self.painter.save(); self.painter.setOpacity(max(0.0,min(1.0,fill.alphaF())))
                            for rect in rects: self.painter.drawImage(rect,img)
                            self.painter.restore(); return True
                    elif QSvgRenderer is not None:
                        renderer=_panorama_cached_svg_renderer(path)
                        if renderer is not None and renderer.isValid():
                            self.painter.save(); self.painter.setOpacity(max(0.0,min(1.0,fill.alphaF())))
                            for rect in rects: renderer.render(self.painter,rect)
                            self.painter.restore(); return True
                except Exception:
                    pass
            fallback=str((primitive.metadata or {}).get('fallback','generic') or 'generic').lower()
            self.painter.save(); self.painter.setPen(QPen(line,max(0.7,float(getattr(self.style,'width',1.0) or 1.0)))); self.painter.setBrush(QBrush(fill))
            for rect in rects:
                if fallback in ('tree','tree_deciduous'):
                    crown=QRectF(rect.left(),rect.top(),rect.width(),rect.height()*0.72); self.painter.drawEllipse(crown)
                    tw=max(1.0,rect.width()*0.13); self.painter.drawRect(QRectF(rect.center().x()-tw*0.5,rect.top()+rect.height()*0.58,tw,rect.height()*0.42))
                elif fallback in ('conifer','tree_conifer'):
                    poly=QPolygonF([QPointF(rect.center().x(),rect.top()),QPointF(rect.right(),rect.top()+rect.height()*0.92),QPointF(rect.left(),rect.top()+rect.height()*0.92)])
                    self.painter.drawPolygon(poly); tw=max(1.0,rect.width()*0.10); self.painter.drawRect(QRectF(rect.center().x()-tw*0.5,rect.top()+rect.height()*0.78,tw,rect.height()*0.22))
                elif fallback in ('animal','vehicle'):
                    body=QRectF(rect.left(),rect.top()+rect.height()*0.28,rect.width()*0.78,rect.height()*0.50); self.painter.drawRoundedRect(body,max(1.0,rect.height()*0.08),max(1.0,rect.height()*0.08))
                    self.painter.drawEllipse(QRectF(rect.left()+rect.width()*0.72,rect.top()+rect.height()*0.20,rect.width()*0.25,rect.height()*0.28))
                else:
                    self.painter.drawRoundedRect(rect,max(1.0,rect.height()*0.08),max(1.0,rect.height()*0.08))
            self.painter.restore(); return True
        except Exception:
            return False

    def draw_polygon(self, primitive: Polygon3D, dist_max: float) -> bool:
        """Point 3: generic Polygon3D → QPainter implementation."""
        if is_panorama_context(self.ctx):
            return self._draw_polygon_panorama(primitive, dist_max)
        xyz = np.asarray(primitive.xyz, dtype=np.float64)
        if xyz.shape[0] < 3 or not self._is_primitive_visible(xyz):
            return False
        uv = self._project(xyz, dist_max)
        lod = self._lod_level(uv)
        if lod == "hidden":
            return False
        mask = np.isfinite(uv[:, 0]) & np.isfinite(uv[:, 1])
        pts = uv[mask]
        if pts.shape[0] < 3:
            return False
        poly = QPolygonF([QPointF(float(u), float(v)) for u, v in pts])
        fill, line = self._colors(primitive.role)
        pen = QPen(line)
        pen.setWidthF(max(0.6, float(getattr(self.style, "width", 1.0) or 1.0)))
        brush = QBrush(fill)
        pattern = str(self.appearance.get("pattern", "solid") or "solid").lower()
        self.painter.save()
        self.painter.setPen(pen if primitive.outline else QC.Qt_PenStyle_NoPen)
        do_fill = bool(primitive.fill and getattr(self.style, "fill_polygons", True))
        if primitive.role == "wall" and not bool(getattr(self.style, "fill_walls", True)):
            do_fill = False
        self.painter.setBrush(brush if do_fill else QC.Qt_BrushStyle_NoBrush)
        self.painter.drawPolygon(poly)
        if lod == "detailed":
            # Lightweight semantic texture, still vector/schematic.
            if primitive.role.startswith("vegetation") or pattern == "foliage":
                hatch = QColor(line); hatch.setAlpha(max(45, min(150, line.alpha())))
                self.painter.setPen(QPen(hatch, 0.7))
                rect = poly.boundingRect()
                spacing = max(6.0, min(18.0, rect.height() / 4.0 if rect.height() > 0 else 8.0))
                clip = QPainterPath(); clip.addPolygon(poly)
                self.painter.setClipPath(clip)
                x = rect.left() - rect.height()
                while x <= rect.right() + rect.height():
                    self.painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + rect.height(), rect.top()))
                    x += spacing
            elif primitive.role == "pv" or pattern == "pv":
                # Internal module stripes only when the table is large enough.
                rect = poly.boundingRect()
                clip = QPainterPath(); clip.addPolygon(poly)
                self.painter.setClipPath(clip)
                grid = QColor(line); grid.setAlpha(max(50, min(150, line.alpha())))
                self.painter.setPen(QPen(grid, 0.65))
                for frac in (0.25, 0.5, 0.75):
                    x = rect.left() + rect.width() * frac
                    self.painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        self.painter.restore()
        return True

    def draw_polyline(self, primitive: Polyline3D, dist_max: float) -> bool:
        if is_panorama_context(self.ctx):
            return self._draw_polyline_panorama(primitive, dist_max)
        xyz = np.asarray(primitive.xyz, dtype=np.float64)
        if xyz.shape[0] < 2 or not self._is_primitive_visible(xyz):
            return False
        uv = self._project(xyz, dist_max)
        lod = self._lod_level(uv)
        if lod == "hidden":
            return False
        _, line = self._colors(primitive.role)
        pen = QPen(line); pen.setWidthF(max(0.7, float(getattr(self.style, "width", 1.0) or 1.0)))
        self.painter.save(); self.painter.setPen(pen)
        prev = None
        for u, v in uv:
            if np.isfinite(u) and np.isfinite(v):
                cur = QPointF(float(u), float(v))
                if prev is not None:
                    self.painter.drawLine(prev, cur)
                prev = cur
            else:
                prev = None
        self.painter.restore()
        return True

    def draw_billboard(self, primitive: Billboard3D, dist_max: float, camera_xy: Tuple[float, float]) -> bool:
        if is_panorama_context(self.ctx):
            return self._draw_billboard_panorama(primitive, dist_max, camera_xy)
        xyz = billboard_world_quad(primitive, camera_xy)
        if not self._is_primitive_visible(xyz):
            return False
        uv = self._project(xyz, dist_max)
        lod = self._lod_level(uv)
        if lod == "hidden":
            return False
        mask = np.isfinite(uv[:, 0]) & np.isfinite(uv[:, 1])
        if np.count_nonzero(mask) < 4:
            return False
        pts = uv[mask]
        left, right = float(np.min(pts[:, 0])), float(np.max(pts[:, 0]))
        top, bottom = float(np.min(pts[:, 1])), float(np.max(pts[:, 1]))
        rect = QRectF(left, top, max(1.0, right - left), max(1.0, bottom - top))
        fill, line = self._colors(primitive.role)
        # SVG is the actual schematic symbol, not merely a detailed texture.
        # Keep it for both detailed and simple LOD so tractors/animals never
        # collapse to the historical tree fallback. LOD still hides objects
        # below simple_min_px.
        if primitive.svg_path and os.path.isfile(primitive.svg_path):
            try:
                path = primitive.svg_path
                if path.lower().endswith('.png'):
                    img = QImage(path)
                    if not img.isNull():
                        self.painter.save(); self.painter.setOpacity(max(0.0, min(1.0, fill.alphaF())))
                        self.painter.drawImage(rect, img); self.painter.restore(); return True
                elif QSvgRenderer is not None:
                    renderer = _safe_svg_renderer(path)
                    if renderer is not None and renderer.isValid():
                        self.painter.save(); self.painter.setOpacity(max(0.0, min(1.0, fill.alphaF())))
                        renderer.render(self.painter, rect); self.painter.restore(); return True
            except Exception:
                pass

        # Generic vector fallbacks only when the SVG is unavailable/invalid.
        fallback = str((primitive.metadata or {}).get("fallback", "generic") or "generic").lower()
        self.painter.save()
        self.painter.setPen(QPen(line, max(0.7, float(getattr(self.style, "width", 1.0) or 1.0))))
        self.painter.setBrush(QBrush(fill))
        if fallback in ("tree", "tree_deciduous"):
            crown = QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.72)
            self.painter.drawEllipse(crown)
            tw = max(1.0, rect.width() * 0.13)
            trunk = QRectF(rect.center().x() - tw * 0.5, rect.top() + rect.height() * 0.58, tw, rect.height() * 0.42)
            self.painter.drawRect(trunk)
        elif fallback in ("conifer", "tree_conifer"):
            poly = QPolygonF([
                QPointF(rect.center().x(), rect.top()),
                QPointF(rect.right(), rect.bottom() * 0.92 + rect.top() * 0.08),
                QPointF(rect.left(), rect.bottom() * 0.92 + rect.top() * 0.08),
            ])
            self.painter.drawPolygon(poly)
            tw = max(1.0, rect.width() * 0.10)
            self.painter.drawRect(QRectF(rect.center().x() - tw * 0.5, rect.top() + rect.height() * 0.78, tw, rect.height() * 0.22))
        elif fallback in ("animal", "vehicle"):
            body = QRectF(rect.left(), rect.top() + rect.height() * 0.28, rect.width() * 0.78, rect.height() * 0.50)
            self.painter.drawRoundedRect(body, max(1.0, rect.height() * 0.08), max(1.0, rect.height() * 0.08))
            self.painter.drawEllipse(QRectF(rect.left() + rect.width() * 0.72, rect.top() + rect.height() * 0.20, rect.width() * 0.25, rect.height() * 0.28))
        else:
            self.painter.drawRoundedRect(rect, max(1.0, rect.height() * 0.08), max(1.0, rect.height() * 0.08))
        self.painter.restore()
        return True

    def render(self, primitives: Sequence[Primitive3D], dist_max: float, camera_xy: Tuple[float, float]) -> bool:
        drawn = False
        # Painter's algorithm inside the feature: farthest primitive first.
        def depth_key(pr):
            try:
                if isinstance(pr, Billboard3D):
                    x, y, _ = pr.anchor_xyz
                    return (x - camera_xy[0]) ** 2 + (y - camera_xy[1]) ** 2
                xyz = np.asarray(pr.xyz)
                mx, my = float(np.nanmean(xyz[:, 0])), float(np.nanmean(xyz[:, 1]))
                return (mx - camera_xy[0]) ** 2 + (my - camera_xy[1]) ** 2
            except Exception:
                return 0.0
        # Far-to-near. paint_priority is only a local tie-breaker: it must never
        # destroy inter-object depth ordering (regression observed after 40.7).
        ordered = sorted(primitives, key=lambda pr: (-float(depth_key(pr)), int((getattr(pr,'metadata',{}) or {}).get('paint_priority', 10))))
        for pr in ordered:
            if isinstance(pr, Polygon3D):
                drawn = self.draw_polygon(pr, dist_max) or drawn
            elif isinstance(pr, Polyline3D):
                drawn = self.draw_polyline(pr, dist_max) or drawn
            elif isinstance(pr, Billboard3D):
                drawn = self.draw_billboard(pr, dist_max, camera_xy) or drawn
        return drawn


def render_schematic_feature(
    painter,
    feature,
    parts: List[np.ndarray],
    gtype: int,
    style,
    cam_pt,
    cam_z: float,
    proj: str,
    width: int,
    height: int,
    yaw: float,
    pitch: float,
    roll: float,
    hfov: float,
    vfov: float,
    is360: bool,
    maxdist: float,
    z_sampler,
    layer_height_m: float,
    plugin_dir: str,
    visibility_test=None,
    runtime_budget=None,
) -> bool:
    """Build and render one selected schematic symbol. Returns True if handled."""
    if not style_uses_schematic(style):
        return False
    lib = get_symbol_library(plugin_dir)
    definition = lib.get(getattr(style, "schematic_symbol_id", ""))
    if not definition:
        return False
    geom_names = {QC.QgsWkbTypes_GeometryType_PointGeometry: "point", QC.QgsWkbTypes_GeometryType_LineGeometry: "line", QC.QgsWkbTypes_GeometryType_PolygonGeometry: "polygon"}
    allowed = [str(v).lower() for v in (definition.get("geometry", []) or [])]
    if allowed and geom_names.get(gtype, "") not in allowed:
        return False
    generator_name = str(definition.get("generator", "") or "")
    klass = _GENERATORS.get(generator_name)
    if klass is None:
        return False
    try:
        camera_xy = (float(cam_pt.x()), float(cam_pt.y()))
        pano = str(proj or '').upper() in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL")
        runtime_overrides = None
        if pano and isinstance(runtime_budget, dict):
            # Shared EQUIRECT/CYLINDRICAL preview budget.  Exports pass None and
            # therefore retain the full user-requested symbol population.
            vf = math.radians(max(1e-6, float(vfov)))
            hf = math.radians(max(1e-6, float(hfov)))
            pixels_per_rad = max(float(width) / max(1e-9, hf), float(height) / max(1e-9, vf)) * 1.20
            runtime_overrides = {
                '_billboard_budget_state': runtime_budget,
                '_pixels_per_rad': pixels_per_rad,
                '_min_billboard_px': float(runtime_budget.get('min_billboard_px', 0.0) or 0.0),
                '_maxdist_m': float(maxdist) if maxdist is not None else None,
            }
            if int(runtime_budget.get('remaining', 1)) <= 0:
                runtime_budget['exhausted'] = True
                return True
        definition, primitives = build_schematic_feature_primitives(
            feature, parts, gtype, style, z_sampler, layer_height_m, plugin_dir, camera_xy,
            runtime_overrides=runtime_overrides
        )
        if not definition or not primitives:
            # 40.18.1: a valid schematic style in a panorama must not silently
            # fall back to rendering the whole source polygon. An empty symbol
            # is treated as handled; this also prevents a second heavy path.
            return bool(str(proj or '').upper() in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL"))
        camera_ctx = build_camera_context(cam_pt, float(cam_z), proj, int(width), int(height), float(yaw), float(pitch), float(roll), float(hfov), float(vfov), bool(is360))
        renderer = SchematicPainterRenderer(painter, camera_ctx, style, definition, lib, width, height, visibility_test=visibility_test)
        renderer.render(primitives, float(maxdist), camera_xy)
        return True
    except Exception:
        # 40.18.1: in panoramas, do not cascade a schematic renderer error into
        # the historical full-polygon fallback (which can be dramatically more
        # expensive and was observed to end in a native access violation).
        if str(proj or '').upper() in ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL"):
            return True
        # PINHOLE keeps the historical fallback contract unchanged.
        return False


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else float(default)
    except Exception:
        return float(default)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return bool(int(value))
        except Exception:
            return bool(default)
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "oui", "on"):
        return True
    if text in ("0", "false", "no", "non", "off", ""):
        return False
    return bool(default)


def _opacity_factor(value: Any) -> float:
    try:
        v = float(value)
        if v > 1.0 and v <= 100.0:
            v /= 100.0
        return max(0.0, min(1.0, v))
    except Exception:
        return 1.0


def _feature_seed(feature: Any) -> int:
    try:
        return int(feature.id()) * 2654435761
    except Exception:
        return 1


def _xyz_on_ground(arr: np.ndarray, ctx: SymbolBuildContext) -> np.ndarray:
    xy = np.asarray(arr[:, :2], dtype=np.float64)
    z = np.asarray([ctx.ground_z(x, y) for x, y in xy], dtype=np.float64)
    return np.column_stack([xy, z])


def geometry_parts_in_camera_crs(geom, gtype: int, transform=None) -> List[np.ndarray]:
    """Convert a QgsGeometry to XY numpy parts in camera CRS for the slow path."""
    out: List[np.ndarray] = []
    try:
        if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
            seq = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
            pts = [transform.transform(p) if transform else p for p in seq]
            if pts:
                out.append(np.asarray([[float(p.x()), float(p.y())] for p in pts], dtype=np.float64))
        elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            seqs = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for seq in seqs:
                pts = [transform.transform(p) if transform else p for p in seq]
                if len(pts) >= 2:
                    out.append(np.asarray([[float(p.x()), float(p.y())] for p in pts], dtype=np.float64))
        elif gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                pts = [transform.transform(p) if transform else p for p in poly[0]]
                if len(pts) >= 3:
                    out.append(np.asarray([[float(p.x()), float(p.y())] for p in pts], dtype=np.float64))
    except Exception:
        return []
    return out
