# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ._i18n import tr
from ._compat import QC, dialog_exec
"""
Gestionnaire de cache multi-niveaux avec invalidation sélective.
Réduit les recalculs inutiles et améliore la réactivité UI.
"""
import hashlib
import json
from typing import Any, Dict, Optional, Tuple
from qgis.PyQt.QtCore import QObject, pyqtSignal


class CacheLevel:
    """Niveau de cache avec stratégie LRU et métriques."""
    def __init__(self, name: str, max_size: int = 10):
        self.name = name
        self.max_size = max_size
        self._data = {}
        self._access_order = []
        self.hits = 0
        self.misses = 0
    
    def get(self, key):
        if key in self._data:
            self.hits += 1
            # LRU : déplacer en fin
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            return self._data[key]
        self.misses += 1
        return None
    
    def set(self, key, value):
        # Éviction LRU si plein
        if len(self._data) >= self.max_size and key not in self._data:
            if self._access_order:
                old_key = self._access_order.pop(0)
                del self._data[old_key]
        
        self._data[key] = value
        if key not in self._access_order:
            self._access_order.append(key)
    
    def clear(self):
        self._data.clear()
        self._access_order.clear()
    
    def stats(self) -> Dict[str, int]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': hit_rate,
            'size': len(self._data)
        }


class SmartCacheManager(QObject):
    """
    Gestionnaire de cache hiérarchique avec invalidation sélective.
    
    Niveaux :
    - L1 : Image base redimensionnée (dépend de : photo_path, W, H)
    - L2 : Z_sampler (dépend de : dem_layer, cam_crs)
    - L3 : Horizon (dépend de : cam_pos, maxdist, az_step, rad_step)
    - L4 : Overlay (dépend de : tous les params UI)
    """
    
    cache_cleared = pyqtSignal(str)  # Signal avec nom du niveau
    
    def __init__(self):
        super().__init__()
        self.base_images = CacheLevel("base_images", max_size=5)
        self.z_samplers = CacheLevel("z_samplers", max_size=3)
        self.horizons = CacheLevel("horizons", max_size=3)
        self.overlays = CacheLevel("overlays", max_size=5)
        
        # Tracking des dépendances
        self._last_params = {}
    
    # --- API simple ---
    
    def get_base_image(self, photo_path: str, W: int, H: int):
        key = (photo_path, W, H)
        return self.base_images.get(key)
    
    def set_base_image(self, photo_path: str, W: int, H: int, image):
        key = (photo_path, W, H)
        self.base_images.set(key, image)
    
    def get_overlay(self, params_dict: dict):
        key = self._hash_params(params_dict)
        return self.overlays.get(key)
    
    def set_overlay(self, params_dict: dict, overlay):
        key = self._hash_params(params_dict)
        self.overlays.set(key, overlay)
    
    # --- Invalidation sélective ---
    
    def invalidate_if_changed(self, param_group: str, new_values: dict):
        """
        Invalide uniquement les caches dépendants si les params ont changé.
        
        Groups :
        - 'photo' : photo_path → invalide base_images, overlays
        - 'camera' : yaw, pitch, roll, HFOV, VFOV → invalide overlays, horizons
        - 'dem' : dem_layer, use_dem → invalide z_samplers, horizons, overlays
        - 'topo' : wireframe, skyline settings → invalide overlays seulement
        - 'layers' : couches vectorielles → invalide overlays seulement
        """
        changed = False
        
        if param_group not in self._last_params:
            self._last_params[param_group] = {}
            changed = True
        else:
            # Détection rapide des changements
            old = self._last_params[param_group]
            for k, v in new_values.items():
                if k not in old or old[k] != v:
                    changed = True
                    break
        
        if not changed:
            return False
        
        # Mise à jour
        self._last_params[param_group] = new_values.copy()
        
        # Invalidation cascade
        if param_group == 'photo':
            self.base_images.clear()
            self.overlays.clear()
            self.cache_cleared.emit('photo')
        
        elif param_group == 'camera':
            self.horizons.clear()
            self.overlays.clear()
            self.cache_cleared.emit('camera')
        
        elif param_group == 'dem':
            self.z_samplers.clear()
            self.horizons.clear()
            self.overlays.clear()
            self.cache_cleared.emit('dem')
        
        elif param_group in ('topo', 'layers', 'offsets'):
            self.overlays.clear()
            self.cache_cleared.emit(param_group)
        
        return True
    
    def clear_all(self):
        """Vide tous les caches (bouton refresh)."""
        self.base_images.clear()
        self.z_samplers.clear()
        self.horizons.clear()
        self.overlays.clear()
        self._last_params.clear()
        self.cache_cleared.emit('all')
    
    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Retourne les statistiques de tous les niveaux."""
        return {
            'base_images': self.base_images.stats(),
            'z_samplers': self.z_samplers.stats(),
            'horizons': self.horizons.stats(),
            'overlays': self.overlays.stats(),
        }
    
    def print_stats(self):
        """Affiche les stats dans les logs QGIS."""
        from qgis.core import QgsMessageLog, Qgis
        stats = self.get_stats()
        msg = ["=== QCALVIEW Cache Stats ==="]
        for level, s in stats.items():
            msg.append(f"{level}: {s['hits']} hits, {s['misses']} misses "
                      f"({s['hit_rate']:.1f}% hit rate), size={s['size']}")
        QgsMessageLog.logMessage(tr('\n'.join(msg)), "FMV_Photo", QC.Qgis_MessageLevel_Info)
    
    # --- Helpers ---
    
    @staticmethod
    def _hash_params(params: dict) -> str:
        """Hash stable d'un dict de paramètres."""
        # Trier pour stabilité
        sorted_items = sorted(params.items())
        json_str = json.dumps(sorted_items, sort_keys=True, default=str)
        return hashlib.md5(json_str.encode()).hexdigest()


# --- Intégration dans qcalview_dock.py ---

def init_cache_manager(self):
    """À appeler dans __init__ du dock."""
    self.cache_mgr = SmartCacheManager()
    
    # Logging optionnel
    self.cache_mgr.cache_cleared.connect(
        lambda level: self.lbl_info.setText(tr(f"Cache '{level}' invalidé"))
    )
    
    # Remplacer les anciens caches
    # self._base_cache = {}  # <-- SUPPRIMER
    # self._z_cache = {}     # <-- SUPPRIMER
    # self._horizon = None   # <-- GARDER mais géré par cache_mgr


def get_base_scaled_optimized(self, W: int, H: int):
    """Remplacement de _get_base_scaled avec cache manager."""
    if self.image is None:
        return None
    
    # Tentative récupération cache
    cached = self.cache_mgr.get_base_image(self.photo_path, W, H)
    if cached is not None:
        return cached
    
    # Recalcul
    img = self.image.scaled(W, H, QC.Qt_AspectRatioMode_IgnoreAspectRatio, QC.Qt_TransformationMode_SmoothTransformation)
    self.cache_mgr.set_base_image(self.photo_path, W, H, img)
    
    return img


def render_preview_optimized(self):
    """Version avec invalidation sélective."""
    # Détection changements par groupe
    
    # Groupe camera
    cam_params = {
        'yaw': self.d_yaw.value(),
        'pitch': self.d_pitch.value(),
        'roll': self.d_roll.value(),
        'HFOV': self.d_hfov.value(),
        'VFOV': self.d_vfov.value(),
    }
    self.cache_mgr.invalidate_if_changed('camera', cam_params)
    
    # Groupe DEM
    dem_params = {
        'dem_layer': id(self.cmb_dem.currentLayer()),
        'use_dem': self.cb_use_dem_z.isChecked(),
    }
    self.cache_mgr.invalidate_if_changed('dem', dem_params)
    
    # Groupe topo
    topo_params = {
        'show_dem': self.cb_show_dem.isChecked(),
        'show_skyline': self.cb_draw_skyline.isChecked(),
        'dem_step': self.spin_dem_step.value(),
    }
    self.cache_mgr.invalidate_if_changed('topo', topo_params)
    
    # Lancer le rendu normal
    self.debounce.start()
