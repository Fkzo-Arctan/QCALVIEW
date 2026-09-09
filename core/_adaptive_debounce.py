


from ._compat import QC, dialog_exec

from qgis.PyQt.QtCore import QTimer, QElapsedTimer, pyqtSignal, QObject


class AdaptiveRenderScheduler(QObject):
    
    
    render_requested = pyqtSignal(str)  
    
    
    FAST_RENDER = 100      
    NORMAL_RENDER = 300    
    SLOW_RENDER = 1000     
    VERY_SLOW_RENDER = 3000  
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._execute_render)
        
        self.quality_restore_timer = QTimer(self)
        self.quality_restore_timer.setSingleShot(True)
        self.quality_restore_timer.timeout.connect(self._restore_quality)
        
        
        self.render_timer = QElapsedTimer()
        self.last_render_time = 0
        self.current_quality = 'normal'  
        self.pending_quality = 'high'    
        
        
        self.render_times = []
        self.max_history = 5
        
        
        self.base_debounce_ms = 50      
        self.adaptive_debounce = True   
        self.auto_quality = True        
    
    def schedule_render(self, quality: str = None):
        
        if quality is not None:
            self.pending_quality = quality
        
        
        if self.adaptive_debounce:
            delay = self._compute_adaptive_delay()
        else:
            delay = self.base_debounce_ms
        
        
        self.debounce_timer.stop()
        self.debounce_timer.setInterval(int(delay))
        self.debounce_timer.start()
    
    def _compute_adaptive_delay(self) -> int:
        
        if not self.render_times:
            return self.base_debounce_ms
        
        
        avg_time = sum(self.render_times) / len(self.render_times)
        
        
        if avg_time < self.FAST_RENDER:
            
            return self.base_debounce_ms
        elif avg_time < self.NORMAL_RENDER:
            
            return self.base_debounce_ms * 2
        elif avg_time < self.SLOW_RENDER:
            
            return self.base_debounce_ms * 4
        else:
            
            return self.base_debounce_ms * 8
    
    def _execute_render(self):
        
        
        self.render_timer.start()
        
        
        if self.auto_quality and self.last_render_time > self.SLOW_RENDER:
            
            effective_quality = 'low'
        else:
            effective_quality = self.pending_quality
        
        self.current_quality = effective_quality
        
        
        self.render_requested.emit(effective_quality)
    
    def mark_render_complete(self):
        
        elapsed = self.render_timer.elapsed()
        self.last_render_time = elapsed
        
        
        self.render_times.append(elapsed)
        if len(self.render_times) > self.max_history:
            self.render_times.pop(0)
        
        
        if self.current_quality == 'low' and self.pending_quality == 'high':
            
            self.quality_restore_timer.stop()
            self.quality_restore_timer.setInterval(2000)
            self.quality_restore_timer.start()
    
    def _restore_quality(self):
        
        if self.current_quality != self.pending_quality:
            self.schedule_render(quality=self.pending_quality)
    
    def get_stats(self) -> dict:
        
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




def init_adaptive_scheduler(self):
    
    self.render_scheduler = AdaptiveRenderScheduler(self)
    
    
    self.render_scheduler.render_requested.connect(self._do_render_with_quality)
    
    
    


def render_preview(self):
    
    self.render_scheduler.schedule_render(quality='high')


def _do_render_with_quality(self, quality: str):
    
    try:
        
        if quality == 'low':
            
            scale = 0.25
            antialiasing = False
            dem_step_mult = 3.0
        elif quality == 'normal':
            
            scale_idx = self.cmb_quality.currentIndex()
            scale = {0: 0.25, 1: 0.5, 2: 1.0}.get(scale_idx, 0.25)
            antialiasing = not self.cb_lowlat.isChecked()
            dem_step_mult = 1.5
        else:  
            
            scale = 1.0
            antialiasing = True
            dem_step_mult = 1.0
        
        
        W_full = self.spin_w.value()
        H_full = self.spin_h.value()
        W = max(256, int(W_full * scale))
        H = max(256, int(H_full * scale))
        
        
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
        
        
        if self.viewer and self.viewer.isVisible():
            if quality == 'high':  
                base_full = self._get_base_scaled(W_full, H_full)
                ov_full = self._render_overlay(width=W_full, height=H_full)
                self.viewer.update_image(base_full)
                self.viewer.update_overlay(ov_full)
        
    finally:
        
        self.render_scheduler.mark_render_complete()


def _render_overlay(self, width, height, dem_step_mult=1.0, antialiasing=True):
    
    overlay = QImage(width, height, QC.QImage_Format_Format_ARGB32_Premultiplied)
    overlay.fill(QColor(0,0,0,0))
    p = QPainter(overlay)
    
    
    p.setRenderHint(QC.QPainter_RenderHint_Antialiasing, antialiasing)
    
    
    
    
    p.end()
    return overlay
