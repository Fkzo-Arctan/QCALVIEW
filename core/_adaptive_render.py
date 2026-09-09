


from ._compat import QC, dialog_exec
"""
Système de rendu adaptatif avec antirebond intelligent.
Ajuste automatiquement les délais et la qualité selon la complexité.
"""
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
