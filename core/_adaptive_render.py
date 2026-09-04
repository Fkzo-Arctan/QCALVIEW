# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ._compat import QC, dialog_exec
"""
Système de rendu adaptatif avec antirebond intelligent.
Ajuste automatiquement les délais et la qualité selon la complexité.
"""
from qgis.PyQt.QtCore import QTimer, QElapsedTimer, pyqtSignal, QObject


class AdaptiveRenderScheduler(QObject):
    """
    Planificateur de rendu adaptatif qui :
    - Ajuste le délai d'antirebond selon la charge
    - Dégrade progressivement la qualité si rendu trop lent
    - Restaure la qualité haute après stabilisation
    """
    
    render_requested = pyqtSignal(str)  # Signal avec niveau qualité
    
    # Seuils de performance (ms)
    FAST_RENDER = 100      # < 100ms : instantané
    NORMAL_RENDER = 300    # < 300ms : fluide
    SLOW_RENDER = 1000     # < 1s : acceptable
    VERY_SLOW_RENDER = 3000  # > 3s : problème
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Timers
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._execute_render)
        
        self.quality_restore_timer = QTimer(self)
        self.quality_restore_timer.setSingleShot(True)
        self.quality_restore_timer.timeout.connect(self._restore_quality)
        
        # État
        self.render_timer = QElapsedTimer()
        self.last_render_time = 0
        self.current_quality = 'normal'  # 'low', 'normal', 'high'
        self.pending_quality = 'high'    # Qualité cible après stabilisation
        
        # Historique des temps de rendu (pour moyenne mobile)
        self.render_times = []
        self.max_history = 5
        
        # Configuration
        self.base_debounce_ms = 50      # Délai de base
        self.adaptive_debounce = True   # Activer adaptation
        self.auto_quality = True        # Dégradation auto qualité
    
    def schedule_render(self, quality: str = None):
        """
        Programme un rendu avec antirebond adaptatif.
        
        Args:
            quality: 'low', 'normal', 'high' ou None (auto)
        """
        if quality is not None:
            self.pending_quality = quality
        
        # Calcul délai adaptatif
        if self.adaptive_debounce:
            delay = self._compute_adaptive_delay()
        else:
            delay = self.base_debounce_ms
        
        # Redémarrer le timer
        self.debounce_timer.stop()
        self.debounce_timer.setInterval(int(delay))
        self.debounce_timer.start()
    
    def _compute_adaptive_delay(self) -> int:
        """Calcule le délai d'antirebond selon les perfs récentes."""
        if not self.render_times:
            return self.base_debounce_ms
        
        # Moyenne mobile des derniers rendus
        avg_time = sum(self.render_times) / len(self.render_times)
        
        # Ajustement progressif du délai
        if avg_time < self.FAST_RENDER:
            # Rendu rapide : délai minimal
            return self.base_debounce_ms
        elif avg_time < self.NORMAL_RENDER:
            # Rendu normal : délai modéré
            return self.base_debounce_ms * 2
        elif avg_time < self.SLOW_RENDER:
            # Rendu lent : délai augmenté
            return self.base_debounce_ms * 4
        else:
            # Rendu très lent : délai maximal
            return self.base_debounce_ms * 8
    
    def _execute_render(self):
        """Exécute le rendu et mesure les performances."""
        # Démarrer chronomètre
        self.render_timer.start()
        
        # Déterminer qualité effective
        if self.auto_quality and self.last_render_time > self.SLOW_RENDER:
            # Dégrader si le dernier rendu était lent
            effective_quality = 'low'
        else:
            effective_quality = self.pending_quality
        
        self.current_quality = effective_quality
        
        # Émettre signal (le dock fera le rendu)
        self.render_requested.emit(effective_quality)
    
    def mark_render_complete(self):
        """À appeler après la fin du rendu pour mesurer le temps."""
        elapsed = self.render_timer.elapsed()
        self.last_render_time = elapsed
        
        # Mise à jour historique
        self.render_times.append(elapsed)
        if len(self.render_times) > self.max_history:
            self.render_times.pop(0)
        
        # Programmer restauration qualité si dégradée
        if self.current_quality == 'low' and self.pending_quality == 'high':
            # Attendre 2s de stabilité avant restauration
            self.quality_restore_timer.stop()
            self.quality_restore_timer.setInterval(2000)
            self.quality_restore_timer.start()
    
    def _restore_quality(self):
        """Restaure la qualité haute après stabilisation."""
        if self.current_quality != self.pending_quality:
            self.schedule_render(quality=self.pending_quality)
    
    def get_stats(self) -> dict:
        """Retourne les statistiques de performance."""
        if not self.render_times:
            return {
                'avg_render_ms': 0,
                'last_render_ms': 0,
                'current_quality': self.current_quality
            }
        
        return {
            'avg_render_ms': sum(self.render_times) / len(self.render_times),
            'last_render_ms': self.last_render_time,
            'current_quality': self.current_quality,
            'adaptive_delay_ms': self._compute_adaptive_delay()
        }


# --- Intégration dans qcalview_dock.py ---

def init_adaptive_scheduler(self):
    """À appeler dans __init__ du dock."""
    self.render_scheduler = AdaptiveRenderScheduler(self)
    
    # Connecter au rendu effectif
    self.render_scheduler.render_requested.connect(self._do_render_with_quality)
    
    # Remplacer self.debounce
    # self.debounce = QTimer(self)  # <-- SUPPRIMER


def render_preview(self):
    """Nouvelle version utilisant le scheduler."""
    self.render_scheduler.schedule_render(quality='high')


def _do_render_with_quality(self, quality: str):
    """Exécute le rendu avec la qualité spécifiée."""
    try:
        # Adapter les paramètres selon qualité
        if quality == 'low':
            # Basse qualité : rendu rapide
            scale = 0.25
            antialiasing = False
            dem_step_mult = 3.0
        elif quality == 'normal':
            # Qualité normale
            scale_idx = self.cmb_quality.currentIndex()
            scale = {0: 0.25, 1: 0.5, 2: 1.0}.get(scale_idx, 0.25)
            antialiasing = not self.cb_lowlat.isChecked()
            dem_step_mult = 1.5
        else:  # high
            # Haute qualité
            scale = 1.0
            antialiasing = True
            dem_step_mult = 1.0
        
        # Calculer dimensions
        W_full = self.spin_w.value()
        H_full = self.spin_h.value()
        W = max(256, int(W_full * scale))
        H = max(256, int(H_full * scale))
        
        # Rendu effectif (logique existante)
        overlay = self._render_overlay(
            width=W, 
            height=H,
            dem_step_mult=dem_step_mult,
            antialiasing=antialiasing
        )
        
        base = self._get_base_scaled(W, H)
        composed = QImage(base)
        qp = QPainter(composed)
        qp.drawImage(0, 0, overlay)
        qp.end()
        
        self.last_preview = composed
        self.preview.setPixmap(
            QPixmap.fromImage(composed).scaled(
                self.preview.size(), 
                QC.Qt_AspectRatioMode_KeepAspectRatio, 
                QC.Qt_TransformationMode_SmoothTransformation
            )
        )
        
        # Viewer si ouvert
        if self.viewer and self.viewer.isVisible():
            if quality == 'high':  # Pleine résolution seulement en haute qualité
                base_full = self._get_base_scaled(W_full, H_full)
                ov_full = self._render_overlay(width=W_full, height=H_full)
                self.viewer.update_image(base_full)
                self.viewer.update_overlay(ov_full)
        
    finally:
        # IMPORTANT : marquer fin du rendu pour mesure
        self.render_scheduler.mark_render_complete()


def _render_overlay(self, width, height, dem_step_mult=1.0, antialiasing=True):
    """
    Version étendue avec paramètres de qualité.
    
    Args:
        dem_step_mult: multiplicateur du pas DEM (>1 = moins dense)
        antialiasing: activer l'antialiasing (coûteux)
    """
    overlay = QImage(width, height, QC.QImage_Format_Format_ARGB32_Premultiplied)
    overlay.fill(QColor(0,0,0,0))
    p = QPainter(overlay)
    
    # Appliquer paramètres qualité
    p.setRenderHint(QC.QPainter_RenderHint_Antialiasing, antialiasing)
    
    # ... reste de la logique existante ...
    # Ajuster self.spin_dem_step.value() * dem_step_mult pour le wireframe
    
    p.end()
    return overlay
