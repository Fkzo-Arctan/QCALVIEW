# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative memory guard for interactive panoramic previews.

QCALVIEW 40.18.9 keeps the conservative 40.18.6 preflight and adds
procedural-instance awareness:
- it never changes saved project/UI values;
- it is active only while an interactive EQUIRECT/CYLINDRICAL preview is built;
- it purges stale rendered images *before* allocating the next panorama;
- it can temporarily reduce preview resolution, visible depth, DEM density and
  per-layer feature count when native-memory pressure becomes unsafe.

It does NOT tessellate geometry and does NOT split the renderer into additional
cache pipelines.  Those experimental 40.18.5 changes were intentionally removed.
"""
from __future__ import annotations
from ._i18n import tr

from dataclasses import dataclass
import ctypes
import gc
import json
import math
import os
import sys
from typing import Any, Dict, Optional, Tuple

from qgis.PyQt.QtWidgets import QMessageBox
from ._compat import dialog_exec

PANORAMA_NAMES = ("EQUIRECT", "EQUIRECTANGULAR", "CYLINDRICAL")
_MIB = 1024.0 * 1024.0
_GIB = 1024.0 * _MIB


@dataclass
class MemorySnapshot:
    total_bytes: int = 0
    available_bytes: int = 0
    process_bytes: int = 0

    @property
    def used_bytes(self) -> int:
        return max(0, int(self.total_bytes) - int(self.available_bytes))

    @property
    def used_ratio(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, float(self.used_bytes) / float(self.total_bytes)))


def _windows_snapshot() -> MemorySnapshot:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    total = avail = proc = 0
    try:
        stat = MEMORYSTATUSEX(); stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = int(stat.ullTotalPhys); avail = int(stat.ullAvailPhys)
    except Exception:
        pass
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX(); counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        hproc = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(hproc, ctypes.byref(counters), counters.cb):
            proc = int(counters.PrivateUsage or counters.WorkingSetSize)
    except Exception:
        pass
    return MemorySnapshot(total, avail, proc)


def _posix_snapshot() -> MemorySnapshot:
    total = avail = proc = 0
    try:
        if os.path.exists('/proc/meminfo'):
            vals = {}
            with open('/proc/meminfo', 'rt', encoding='ascii', errors='ignore') as fh:
                for line in fh:
                    if ':' not in line:
                        continue
                    k, v = line.split(':', 1)
                    try:
                        vals[k.strip()] = int(v.strip().split()[0]) * 1024
                    except Exception:
                        pass
            total = int(vals.get('MemTotal', 0))
            avail = int(vals.get('MemAvailable', vals.get('MemFree', 0)))
        if os.path.exists('/proc/self/statm'):
            with open('/proc/self/statm', 'rt', encoding='ascii', errors='ignore') as fh:
                p = fh.read().split()
            if len(p) >= 2:
                proc = int(p[1]) * int(os.sysconf('SC_PAGE_SIZE'))
    except Exception:
        pass
    return MemorySnapshot(total, avail, proc)


def memory_snapshot() -> MemorySnapshot:
    try:
        if sys.platform.startswith('win'):
            return _windows_snapshot()
    except Exception:
        pass
    return _posix_snapshot()


def qimage_bytes(image) -> int:
    try:
        if image is None or image.isNull():
            return 0
        fn = getattr(image, 'sizeInBytes', None)
        if callable(fn):
            return int(fn())
        return int(image.bytesPerLine()) * int(image.height())
    except Exception:
        return 0


def cache_bytes(cache) -> int:
    if not isinstance(cache, dict):
        return 0
    total = 0
    for value in cache.values():
        image = value.get('image') if isinstance(value, dict) else value
        total += qimage_bytes(image)
    return int(total)


_SYMBOL_INSTANCE_META = None


def _symbol_instance_meta():
    global _SYMBOL_INSTANCE_META
    if isinstance(_SYMBOL_INSTANCE_META, dict):
        return _SYMBOL_INSTANCE_META
    out = {}
    try:
        root = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'resources', 'symbols')
        for name in os.listdir(root):
            if not name.lower().endswith('.json'):
                continue
            try:
                with open(os.path.join(root, name), 'rt', encoding='utf-8') as fh:
                    d = json.load(fh)
                sid = str(d.get('id', os.path.splitext(name)[0]) or os.path.splitext(name)[0])
                gen = str(d.get('generator', '') or '')
                raw = (d.get('parameters', {}) or {}).get('max_instances', 1)
                if isinstance(raw, dict): raw = raw.get('default', 1)
                out[sid] = (gen, max(1, int(round(float(raw or 1)))))
            except Exception:
                continue
    except Exception:
        pass
    _SYMBOL_INSTANCE_META = out
    return out


def _style_instance_factor(sty, lyr) -> int:
    """Conservative per-feature billboard upper bound for Memory Guard."""
    try:
        sid = str(getattr(sty, 'schematic_symbol_id', '') or '')
        gen, default_max = _symbol_instance_meta().get(sid, ('', 1))
        params = dict(getattr(sty, 'schematic_params', {}) or {})
        mx = max(1, int(round(float(params.get('max_instances', default_max) or default_max))))
        try: gtype = int(lyr.geometryType())
        except Exception: gtype = -1
        if gen == 'billboard_svg':
            if gtype == 2: return min(mx, 1000)  # polygons scatter instances
            if gtype == 1: return min(mx, 80)    # line vertices/alignments
            return 1
        if gen == 'vegetation_adaptive':
            if gtype == 2:
                mode = str(params.get('polygon_mode', 'mass') or 'mass').lower()
                return min(mx, 1000) if mode in ('instances','stand','peuplement','trees') else 0
            if gtype == 1:
                mode = str(params.get('line_mode', 'alignment') or 'alignment').lower()
                return 0 if mode in ('ribbon','continuous','ruban','haie') else min(mx, 80)
            return 1
        return 0
    except Exception:
        return 0


def _visible_feature_estimate(owner) -> Tuple[int, int, int]:
    """Estimate visible entities and potential procedural billboard instances."""
    total = schematic = 0
    snap_used = False
    try:
        snaps = list(getattr(owner, '_budget_snapshots', []) or [])
        accepted = [s for s in snaps if bool(s.get('accepted', True))]
        counts = [max(0, int(s.get('count', 0))) for s in accepted]
        if counts:
            total = int(min(sum(counts), 50000)); snap_used = True
            schematic = int(min(sum(max(0,int(s.get('count',0))) for s in accepted if bool(s.get('schematic',False))), 20000))
    except Exception:
        pass
    try:
        styles = list(getattr(owner, 'layer_styles', []) or [])
    except Exception:
        styles = []
    provider_total = provider_schematic = instance_est = 0
    for sty in styles:
        try:
            if not bool(getattr(sty, 'visible', True)):
                continue
            lyr = getattr(sty, 'layer', None)
            if lyr is None: continue
            n = min(12000, max(0, int(lyr.featureCount())))
            provider_total += n
            is_schem = bool(getattr(sty, 'schematic_enabled', False) and str(getattr(sty, 'schematic_symbol_id', '') or '').strip())
            if is_schem:
                provider_schematic += n
                factor = _style_instance_factor(sty, lyr)
                if factor > 0:
                    # This is an upper bound before spatial/sub-pixel culling.
                    instance_est += n * factor
        except Exception:
            continue
    if not snap_used:
        total = int(min(provider_total, 50000)); schematic = int(min(provider_schematic, 20000))
    # Never allow arithmetic/pathological providers to overflow the heuristic.
    instance_est = int(min(max(0, instance_est), 5_000_000))
    return int(total), int(schematic), instance_est


def _relief_mode(owner) -> str:
    try:
        if hasattr(owner, '_relief_mode_id'):
            return str(owner._relief_mode_id() or 'none').lower()
    except Exception:
        pass
    return 'none'


def _button_role(name: str):
    scope = getattr(QMessageBox, 'ButtonRole', None)
    if scope is not None and hasattr(scope, name):
        return getattr(scope, name)
    return getattr(QMessageBox, name)


def _show_guard_dialog(owner, details: str, dangerous: bool) -> str:
    """Return 'safe', 'continue' or 'cancel'.  Safe is always the default."""
    try:
        box = QMessageBox(owner)
        box.setWindowTitle(tr('QCalView — limites de rendu'))
        if dangerous:
            box.setText(tr('Attention : limites mémoire atteintes. Un rendu non limité peut fermer QGIS sans avertissement.'))
        else:
            box.setText(tr('Attention : charge de rendu critique. Le prochain calcul présente un risque de crash.'))
        box.setInformativeText(tr(details))
        safe_btn = box.addButton('Appliquer le mode sécurisé', _button_role('AcceptRole'))
        cont_btn = box.addButton('Continuer quand même', _button_role('DestructiveRole'))
        cancel_btn = box.addButton('Annuler le rendu', _button_role('RejectRole'))
        try:
            box.setDefaultButton(safe_btn)
        except Exception:
            pass
        dialog_exec(box)
        clicked = box.clickedButton()
        if clicked is cont_btn:
            return 'continue'
        if clicked is cancel_btn:
            return 'cancel'
        return 'safe'
    except Exception:
        # If the warning UI itself cannot be created, fail safe.
        return 'safe'


def release_stale_panorama_buffers(owner, *, aggressive: bool = False) -> int:
    """Drop old rendered panorama images before the next large allocation.

    Geometry/style/DEM source caches are intentionally retained.  Only rendered
    image state from previous camera orientations is released here.
    """
    released = 0
    cache = getattr(owner, '_overlay_cache', None)
    if isinstance(cache, dict):
        released += cache_bytes(cache)
        try:
            cache.clear()
        except Exception:
            pass
    try:
        released += qimage_bytes(getattr(owner, 'overlay_image', None))
        owner.overlay_image = None
    except Exception:
        pass
    try:
        released += qimage_bytes(getattr(owner, 'last_preview', None))
        owner.last_preview = None
    except Exception:
        pass
    try:
        viewer = getattr(owner, 'viewer', None)
        if viewer is not None and viewer.isVisible():
            viewer.clear_overlay()
    except Exception:
        pass
    if aggressive:
        # A horizon is camera-orientation dependent and can contain several
        # large NumPy matrices.  Dropping it before rebuilding avoids old+new
        # copies coexisting at the peak of a critical render.
        try:
            owner._horizon = None
            owner._horizon_params = None
        except Exception:
            pass
    try:
        gc.collect()
    except Exception:
        pass
    return int(released)



def prune_base_cache_for_size(owner, width: int, height: int) -> int:
    """Keep only base-image variants matching the current panorama preview size.

    The historical base cache is useful, but 25/50/100% panorama variants can
    otherwise coexist as several large QImages.  Camera pitch does not affect
    the base image, so retaining only the current dimensions is enough.
    """
    cache = getattr(owner, '_base_cache', None)
    if not isinstance(cache, dict) or not cache:
        return 0
    W = int(width); H = int(height)
    removed = 0
    for key in list(cache.keys()):
        keep = False
        try:
            if isinstance(key, tuple):
                # Photo key: (path, W, H, mode)
                # Schematic key: ('__schematic__', W, H, color, transparent)
                if len(key) >= 3 and int(key[1]) == W and int(key[2]) == H:
                    keep = True
        except Exception:
            keep = False
        if not keep:
            try:
                img = cache.pop(key, None)
                removed += qimage_bytes(img)
            except Exception:
                pass
    return int(removed)

def _guard_signature(owner, w: int, h: int, feature_count: int, relief: str, level: str) -> tuple:
    try:
        proj = str(owner.cmb_proj.currentText()).strip().upper()
    except Exception:
        proj = ''
    try:
        maxdist = round(float(owner.d_maxdist.value()), -2)
    except Exception:
        maxdist = 0.0
    try:
        dem_step = round(float(owner.spin_dem_step.value()), 1)
    except Exception:
        dem_step = 50.0
    # Pitch/yaw/roll are deliberately excluded: manipulating the camera should
    # not open the same modal warning on every intermediate frame.
    return (proj, level, int(round(w / 256.0)), int(round(h / 256.0)),
            int(feature_count // 500), relief, maxdist, dem_step)


def prepare_panorama_preview(owner, requested_w: int, requested_h: int,
                             full_w: int, full_h: int,
                             render_quality: str = 'high') -> Dict[str, Any]:
    """Preflight an interactive panorama preview.

    This is intentionally conservative.  It estimates *peak* native memory,
    warns before allocation, and returns temporary overrides.  Saved UI/project
    values are never changed.
    """
    try:
        proj = str(owner.cmb_proj.currentText()).strip().upper()
    except Exception:
        proj = ''
    W = max(1, int(requested_w)); H = max(1, int(requested_h))
    if proj not in PANORAMA_NAMES:
        return {'active': False, 'cancel': False, 'safe_mode': False,
                'level': 'normal', 'width': W, 'height': H}

    snap = memory_snapshot()
    feat_count, schematic_count, schematic_instances = _visible_feature_estimate(owner)
    relief = _relief_mode(owner)
    try:
        maxdist_ui = float(owner.d_maxdist.value())
    except Exception:
        maxdist_ui = 0.0
    maxdist_est = float(maxdist_ui if maxdist_ui > 0.0 else 12000.0)
    try:
        dem_step = max(0.5, float(owner.spin_dem_step.value()))
    except Exception:
        dem_step = 50.0
    try:
        az_step = max(0.05, float(owner.d_az_step.value()))
        rad_step = max(1.0, float(owner.d_rad_step.value()))
    except Exception:
        az_step, rad_step = 0.5, 50.0

    pixels = int(W) * int(H)
    frame_bytes = pixels * 4
    full_pixels = max(1, int(full_w)) * max(1, int(full_h))
    full_frame_bytes = full_pixels * 4
    viewer_full = bool(getattr(owner, '_viewer_full_res', False)) and str(render_quality) == 'high'

    # Preview peak: new overlay + base/scaled image + composed image + QPixmap
    # conversion/painting headroom.  A full-res viewer may require another pair
    # if its requested size differs from the preview.
    image_peak = int(frame_bytes * 4.0)
    if viewer_full and (int(full_w) != W or int(full_h) != H):
        image_peak += int(full_frame_bytes * 2.6)

    # 40.18.9: common panorama object z-buffer.  Interactive depth is deliberately
    # lower-resolution than the display overlay (2.5/5/8 Mpx), avoiding the huge
    # 40.18.8 native allocation while still budgeting it before rendering.
    zbuffer_peak = 0
    try:
        zbuf_on = bool(getattr(owner,'cb_occ_objects',None) and owner.cb_occ_objects.isChecked())
    except Exception:
        zbuf_on = False
    if zbuf_on:
        q=str(render_quality or 'high').lower()
        target_px = 2_500_000 if q=='low' else 5_000_000 if q=='normal' else 8_000_000
        zpx=min(pixels,target_px)
        zbuffer_peak=int(zpx*8.0 + min(32.0*_MIB,zpx*0.75))
        image_peak += zbuffer_peak

    topo_units = 0.0; topo_peak = 0
    if relief == 'wireframe':
        # 40.18.4 already uses a sparse generator for pathological grids, but
        # the requested grid still indicates native-pressure risk.
        n = maxdist_est / max(0.5, dem_step)
        topo_units = (2.0 * n + 1.0) ** 2
        topo_peak = int(min(900.0 * _MIB, max(20.0 * _MIB, topo_units * 10.0)))
    elif relief in ('skyline', 'ridgelines', 'opaque'):
        topo_units = (360.0 / az_step) * (maxdist_est / rad_step)
        topo_peak = int(min(550.0 * _MIB, max(12.0 * _MIB, topo_units * 34.0)))

    # This deliberately overestimates Python/Qt face overhead.  The guard's job
    # is to preserve QGIS, not to maximize one interactive frame.
    geometry_peak = int(min(2.2 * _GIB, feat_count * 1200 + schematic_count * 1800 + schematic_instances * 520))
    predicted_increment = int(image_peak + topo_peak + geometry_peak)
    projected_free = int(snap.available_bytes - predicted_increment) if snap.available_bytes > 0 else 0
    projected_ratio = snap.used_ratio
    if snap.total_bytes > 0:
        projected_ratio = float(snap.used_bytes + predicted_increment) / float(snap.total_bytes)

    # Independent native-pressure triggers remain useful when OS memory metrics
    # are unavailable or deceptively comfortable (fragmentation / contiguous
    # allocation failures can happen well before 95% physical RAM).
    pixel_pressure = pixels / 30_000_000.0
    topo_pressure = topo_units / 3_000_000.0 if topo_units > 0 else 0.0
    entity_pressure = feat_count / 12000.0 if feat_count > 0 else 0.0
    instance_pressure = schematic_instances / 60000.0 if schematic_instances > 0 else 0.0
    proc_pressure = snap.process_bytes / (8.0 * _GIB) if snap.process_bytes > 0 else 0.0
    native_pressure = max(pixel_pressure, min(2.0, topo_pressure),
                          min(2.0, entity_pressure), min(2.0, instance_pressure),
                          min(2.0, proc_pressure))

    level = 'normal'
    reserve_danger = max(int(1.25 * _GIB), int(snap.total_bytes * 0.06) if snap.total_bytes else 0)
    reserve_critical = max(int(2.25 * _GIB), int(snap.total_bytes * 0.10) if snap.total_bytes else 0)
    if projected_ratio >= 0.90 or (projected_free and projected_free < reserve_danger) or native_pressure >= 1.55:
        level = 'dangerous'
    elif projected_ratio >= 0.80 or (projected_free and projected_free < reserve_critical) or native_pressure >= 1.00:
        level = 'critical'
    elif projected_ratio >= 0.68 or native_pressure >= 0.72:
        level = 'elevated'

    state: Dict[str, Any] = {
        'active': True, 'cancel': False, 'safe_mode': False, 'level': level,
        'width': W, 'height': H, 'requested_width': W, 'requested_height': H,
        'feature_count_est': int(feat_count), 'schematic_count_est': int(schematic_count),
        'schematic_instances_est': int(schematic_instances),
        'predicted_increment_bytes': predicted_increment,
        'projected_ratio': float(projected_ratio), 'projected_free_bytes': int(projected_free),
        'snapshot': snap, 'relief_mode': relief,
        'maxdist_cap': None, 'dem_step_min': None, 'rad_step_min': None,
        'entity_limit_per_layer': None, 'disable_viewer_full_res': False,
        'schematic_instance_budget': None, 'min_billboard_px': None,
        'zbuffer_peak_bytes': int(zbuffer_peak),
    }

    # For panoramas we never retain rendered overlays from previous camera
    # states.  This purge happens even in normal state and, critically, before
    # the next QImage is allocated.
    release_stale_panorama_buffers(owner, aggressive=(level in ('critical', 'dangerous')))

    if level in ('normal', 'elevated'):
        return state

    signature = _guard_signature(owner, W, H, feat_count, relief, level)
    decisions = getattr(owner, '_memory_guard_choices', None)
    if not isinstance(decisions, dict):
        decisions = {}
        try:
            owner._memory_guard_choices = decisions
        except Exception:
            pass
    choice = decisions.get(signature)
    if choice not in ('safe', 'continue', 'cancel'):
        mem_lines = []
        if snap.total_bytes > 0:
            mem_lines.append(f"RAM disponible : {snap.available_bytes/_GIB:.1f} Go / {snap.total_bytes/_GIB:.1f} Go")
        if snap.process_bytes > 0:
            mem_lines.append(f"Mémoire du processus QGIS : {snap.process_bytes/_GIB:.1f} Go")
        details = (
            f"Projection : {proj} — aperçu demandé {W} × {H} px\n"
            f"Entités estimées : {feat_count:,}\n"
            f"Instances procédurales potentielles : {schematic_instances:,}\n"
            f"Topographie : {relief} — portée estimée {maxdist_est/1000.0:.1f} km"
        )
        if relief == 'wireframe':
            details += f" — pas {dem_step:g} m"
        if mem_lines:
            details += "\n" + "\n".join(mem_lines)
        details += "\n\nLe mode sécurisé ne modifie pas le projet : il ne limite que l'aperçu interactif courant."
        choice = _show_guard_dialog(owner, details, level == 'dangerous')
        decisions[signature] = choice
        while len(decisions) > 8:
            try:
                decisions.pop(next(iter(decisions.keys())))
            except Exception:
                break

    if choice == 'cancel':
        state['cancel'] = True
        return state
    if choice == 'continue':
        return state

    state['safe_mode'] = True
    dangerous = level == 'dangerous'

    # 1) Reduce only interactive pixels, never export dimensions.
    target_pixels = 10_000_000 if dangerous else 18_000_000
    if pixels > target_pixels:
        s = math.sqrt(float(target_pixels) / float(max(1, pixels)))
        state['width'] = max(320, int(W * s))
        state['height'] = max(180, int(H * s))
    state['disable_viewer_full_res'] = True

    # 2) Topography limits are temporary and projection-family agnostic.
    if relief == 'wireframe':
        state['dem_step_min'] = max(dem_step, 20.0 if dangerous else 10.0, maxdist_est / (500.0 if dangerous else 800.0))
    elif relief in ('skyline', 'ridgelines', 'opaque'):
        state['rad_step_min'] = max(rad_step, 80.0 if dangerous else 50.0, maxdist_est / (120.0 if dangerous else 180.0))

    # 3) Depth/entity culling only after image/topography reductions.
    state['maxdist_cap'] = 6000.0 if dangerous else 8000.0
    if maxdist_ui > 0.0:
        state['maxdist_cap'] = min(float(maxdist_ui), float(state['maxdist_cap']))
    state['entity_limit_per_layer'] = 1000 if dangerous else 1800
    # Preview-only global billboard cap.  Dense tree stands remain available:
    # export is not capped, and normal non-critical previews use a much higher
    # renderer budget.
    state['schematic_instance_budget'] = 12000 if dangerous else 28000
    state['min_billboard_px'] = 1.15 if dangerous else 0.85
    return state


def format_guard_status(state: Optional[Dict[str, Any]]) -> str:
    if not state or not bool(state.get('active', False)):
        return ''
    level = str(state.get('level', 'normal'))
    if level == 'normal':
        return ''
    if bool(state.get('safe_mode', False)):
        bits = ['Aperçu sécurisé']
        rw = int(state.get('requested_width', 0)); rh = int(state.get('requested_height', 0))
        w = int(state.get('width', rw)); h = int(state.get('height', rh))
        if (w, h) != (rw, rh):
            bits.append(f'{w}×{h}px')
        if state.get('dem_step_min'):
            bits.append(f"topo ≥ {float(state['dem_step_min']):.1f} m")
        if state.get('rad_step_min'):
            bits.append(f"radial ≥ {float(state['rad_step_min']):.0f} m")
        if state.get('maxdist_cap'):
            bits.append(f"portée ≤ {float(state['maxdist_cap'])/1000.0:.1f} km")
        if state.get('schematic_instance_budget'):
            bits.append(f"motifs ≤ {int(state['schematic_instance_budget']):,}".replace(',', ' '))
        return ' • '.join(bits)
    if level == 'elevated':
        return 'Charge mémoire élevée'
    return 'Charge mémoire critique — rendu forcé'
