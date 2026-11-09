from __future__ import annotations

import os
from collections import OrderedDict

from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._log import qcv_log

from qgis.PyQt.QtCore import QObject, QTimer
from ._compat import QC
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSplitter, QWidget


MAX_VIEWERS = 4


def _camera_helpers():
    # Imported lazily to avoid making the viewer module and camera module depend
    # on each other at import time.
    from ._camera_layer_ops import _camera_collect_ui_state, _camera_apply_state
    return _camera_collect_ui_state, _camera_apply_state


def _rect_payload(widget):
    try:
        g = widget.geometry()
        return [int(g.x()), int(g.y()), int(g.width()), int(g.height())]
    except Exception:
        return []


def _apply_rect(widget, values):
    try:
        vals = list(values or [])
        if len(vals) == 4 and int(vals[2]) > 100 and int(vals[3]) > 100:
            widget.setGeometry(int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]))
            return True
    except Exception as exc:
        _qcv_suppress(exc, 'core/_multiview.py:apply-rect')
    return False


class MultiViewWindow(QDialog):
    """Container for 1..4 existing viewer widgets.

    The viewers themselves remain the source of truth. Moving from independent
    windows to Quad View only reparents them; it does not create a second render
    path or a second camera state.
    """

    def __init__(self, coordinator, parent=None):
        super().__init__(parent)
        self._coordinator = coordinator
        self._maximized_viewer = None
        self._orientation_vertical = False
        self.setModal(False)
        self.setWindowTitle(tr('QCALVIEW — Multi-View'))
        self.setMinimumSize(700, 460)
        self.resize(1500, 900)

        self._root = QVBoxLayout(self)
        self._top = QHBoxLayout()
        self._lbl = QLabel(tr('Multi-View'))
        self._btn_add = QPushButton(tr('New view'))
        self._btn_layout = QPushButton(tr('Switch layout'))
        self._btn_windows = QPushButton(tr('Separate windows'))
        self._top.addWidget(self._lbl)
        self._top.addStretch(1)
        self._top.addWidget(self._btn_add)
        self._top.addWidget(self._btn_layout)
        self._top.addWidget(self._btn_windows)
        self._root.addLayout(self._top)
        self._host = QWidget(self)
        self._root.addWidget(self._host, 1)
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(2)
        self._layout_widget = None
        self._splitters = []
        self._saved_splitter_sizes = []

        self._btn_add.clicked.connect(self._coordinator.create_viewer)
        self._btn_layout.clicked.connect(self.toggle_layout)
        self._btn_windows.clicked.connect(self._coordinator.show_independent)

    def toggle_layout(self):
        self._orientation_vertical = not bool(self._orientation_vertical)
        self.rebuild()
        self._coordinator.persist_project_state()

    def _clear_layout(self):
        layout = self._host_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.hide()
                if w not in self._coordinator.viewers:
                    try:
                        w.deleteLater()
                    except Exception as exc:
                        _qcv_suppress(exc, 'core/_multiview.py:dispose-layout')
        self._layout_widget = None
        self._splitters = []

    def rebuild(self):
        viewers = list(self._coordinator.viewers)
        self._clear_layout()
        if not viewers:
            return

        for viewer in viewers:
            viewer._set_embedded(True, self._host)
            viewer.show()

        count = len(viewers)
        try:
            self._btn_layout.setEnabled(count == 2)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:layout-button')
        if count == 1:
            root = viewers[0]
        elif count == 2:
            orient = QC.Qt_Orientation_Vertical if self._orientation_vertical else QC.Qt_Orientation_Horizontal
            split = QSplitter(orient, self._host)
            split.addWidget(viewers[0])
            split.addWidget(viewers[1])
            split.setChildrenCollapsible(False)
            self._splitters = [split]
            root = split
        elif count == 3:
            root = QSplitter(QC.Qt_Orientation_Horizontal, self._host)
            right = QSplitter(QC.Qt_Orientation_Vertical, root)
            root.addWidget(viewers[0])
            right.addWidget(viewers[1])
            right.addWidget(viewers[2])
            root.addWidget(right)
            root.setChildrenCollapsible(False)
            right.setChildrenCollapsible(False)
            self._splitters = [root, right]
            try:
                root.setSizes([900, 600])
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:three-sizes')
        else:
            root = QSplitter(QC.Qt_Orientation_Vertical, self._host)
            top = QSplitter(QC.Qt_Orientation_Horizontal, root)
            bottom = QSplitter(QC.Qt_Orientation_Horizontal, root)
            top.addWidget(viewers[0])
            top.addWidget(viewers[1])
            bottom.addWidget(viewers[2])
            bottom.addWidget(viewers[3])
            root.addWidget(top)
            root.addWidget(bottom)
            root.setChildrenCollapsible(False)
            top.setChildrenCollapsible(False)
            bottom.setChildrenCollapsible(False)
            self._splitters = [root, top, bottom]

        self._layout_widget = root
        self._host_layout.addWidget(root, 1)
        self._restore_splitter_sizes()
        for splitter in list(getattr(self, '_splitters', []) or []):
            try:
                splitter.splitterMoved.connect(lambda *_args: self._coordinator.schedule_persist())
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:splitter-persist-hook')
        self._apply_maximized_visibility()

    def splitter_sizes(self):
        out = []
        for splitter in list(getattr(self, '_splitters', []) or []):
            try:
                out.append([int(v) for v in splitter.sizes()])
            except Exception:
                out.append([])
        return out

    def _restore_splitter_sizes(self):
        saved = list(getattr(self, '_saved_splitter_sizes', []) or [])
        for idx, splitter in enumerate(list(getattr(self, '_splitters', []) or [])):
            if idx >= len(saved):
                break
            try:
                vals = [int(v) for v in list(saved[idx] or [])]
                if vals and len(vals) == splitter.count():
                    splitter.setSizes(vals)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:splitter-restore')

    def toggle_maximize(self, viewer):
        if self._maximized_viewer is viewer:
            self._maximized_viewer = None
        else:
            self._maximized_viewer = viewer
        self._apply_maximized_visibility()

    def _apply_maximized_visibility(self):
        target = self._maximized_viewer
        for viewer in self._coordinator.viewers:
            viewer.setVisible(target is None or viewer is target)

    def moveEvent(self, event):
        try:
            super().moveEvent(event)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:quad-move')
        self._coordinator.schedule_persist()

    def resizeEvent(self, event):
        try:
            super().resizeEvent(event)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:quad-resize')
        self._coordinator.schedule_persist()

    def closeEvent(self, event):
        # Closing Quad View changes presentation mode only. The viewer objects are
        # returned to independent windows and keep their camera/render state.
        try:
            self._coordinator.show_independent()
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:quad-close')
        try:
            event.accept()
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:quad-accept-close')


class MultiViewCoordinator(QObject):
    """Central render/state coordinator for QCALVIEW 40.22 Multi-View.

    The current renderer remains single-threaded and owned by QCalViewDock. All
    viewers therefore share the same heavy caches and are rendered sequentially.
    This avoids multiplying QGIS/NumPy work across concurrent renderer instances.
    """

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.viewers = []
        self.active_viewer = None
        self.quad_window = None
        self.mode = 'windows'
        self.scene_generation = 0
        self._next_id = 1
        self._internal = False
        self._closing = False
        self._restoring = False
        self._secondary_queue = []
        self._secondary_quality = 'low'
        self._secondary_generation = 0
        self._photo_cache = OrderedDict()

        self._low_timer = QTimer(owner)
        self._low_timer.setSingleShot(True)
        self._low_timer.timeout.connect(lambda: self._start_secondary_pass('low'))
        self._high_timer = QTimer(owner)
        self._high_timer.setSingleShot(True)
        self._high_timer.timeout.connect(lambda: self._start_secondary_pass('high'))
        self._queue_timer = QTimer(owner)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._render_next_secondary)
        self._persist_timer = QTimer(owner)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(300)
        self._persist_timer.timeout.connect(self.persist_project_state)

    @property
    def internal(self):
        return bool(self._internal)

    def has_viewers(self):
        return bool(self.viewers)

    @staticmethod
    def _photo_signature(path):
        try:
            st = os.stat(str(path))
            return (int(st.st_size), int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000))))
        except Exception:
            return None

    def photo_cache_get(self, path):
        key = os.path.normcase(os.path.normpath(str(path or '')))
        if not key:
            return None
        entry = self._photo_cache.get(key)
        if not isinstance(entry, dict):
            return None
        if entry.get('signature') != self._photo_signature(path):
            self._photo_cache.pop(key, None)
            return None
        try:
            self._photo_cache.move_to_end(key)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:photo-cache-touch')
        return entry

    def photo_cache_put(self, path, image, source_info, photo_meta):
        key = os.path.normcase(os.path.normpath(str(path or '')))
        if not key or image is None:
            return
        self._photo_cache[key] = {
            'signature': self._photo_signature(path),
            'image': image,
            'source_info': dict(source_info or {}),
            'photo_meta': dict(photo_meta or {}),
        }
        try:
            self._photo_cache.move_to_end(key)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:photo-cache-order')
        while len(self._photo_cache) > MAX_VIEWERS:
            try:
                self._photo_cache.popitem(last=False)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:photo-cache-trim')
                break

    def _sync_viewer_mode_buttons(self):
        label = tr('Separate windows') if self.mode == 'quad' else tr('Quad view')
        for viewer in list(self.viewers):
            try:
                btn = getattr(viewer, '_btn_quad', None)
                if btn is not None:
                    btn.setText(label)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:mode-button')

    def create_viewer(self, pdv_fid=None, camera_state=None, show=True, viewer_id=None):
        if len(self.viewers) >= MAX_VIEWERS:
            try:
                self.owner.iface.messageBar().pushInfo(tr('QCALVIEW'), tr('A maximum of four viewers can be opened.'))
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:max-viewers')
            return self.active_viewer

        from ._utils_ops import _ImageViewer

        used_ids = {int(getattr(v, '_viewer_id', 0) or 0) for v in self.viewers}
        try:
            requested_id = int(viewer_id) if viewer_id is not None else 0
        except Exception:
            requested_id = 0
        if 1 <= requested_id <= MAX_VIEWERS and requested_id not in used_ids:
            vid = requested_id
        else:
            vid = next((idx for idx in range(1, MAX_VIEWERS + 1) if idx not in used_ids), 1)
        self._next_id = max(self._next_id, vid + 1)
        viewer = _ImageViewer(self.owner.iface.mainWindow(), owner=self.owner, viewer_id=vid)
        self.viewers.append(viewer)
        try:
            viewer.imageClicked.connect(self.owner._on_viewer_image_clicked)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:viewer-click-connect')
        self._sync_viewer_mode_buttons()

        if pdv_fid is None:
            pdv_fid = getattr(self.owner, '_camera_current_fid', None)
        try:
            viewer._pdv_fid = int(pdv_fid) if pdv_fid is not None else None
        except Exception:
            viewer._pdv_fid = None
        viewer._camera_state = dict(camera_state or {})

        if self.mode == 'quad':
            self._ensure_quad_window()
            self.quad_window.rebuild()
            self.quad_window.show()
        elif show:
            viewer._set_embedded(False, self.owner.iface.mainWindow())
            viewer.show()

        self.activate_viewer(viewer, load=True, render=not self._restoring)
        self.persist_project_state()
        return viewer

    def remove_viewer(self, viewer):
        if self._closing:
            return
        if viewer not in self.viewers:
            return
        try:
            self.capture_viewer_state(viewer)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:remove-capture')
        self.viewers.remove(viewer)
        if self.active_viewer is viewer:
            self.active_viewer = self.viewers[0] if self.viewers else None
            self.owner.viewer = self.active_viewer
            if self.active_viewer is not None:
                QTimer.singleShot(0, lambda v=self.active_viewer: self.activate_viewer(v, load=True, render=False))
        if self.quad_window is not None and self.mode == 'quad':
            self.quad_window.rebuild()
        self.persist_project_state()

    def capture_viewer_state(self, viewer=None):
        viewer = viewer or self.active_viewer
        if viewer is None:
            return {}
        current = getattr(self.owner, '_camera_current_fid', None)
        if viewer is self.active_viewer and current is not None:
            try:
                viewer._pdv_fid = int(current)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:capture-fid')
        if viewer is self.active_viewer:
            collect, _apply = _camera_helpers()
            try:
                viewer._camera_state = dict(collect(self.owner) or {})
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:capture-ui')
        return dict(getattr(viewer, '_camera_state', {}) or {})

    def _load_viewer_context(self, viewer):
        if viewer is None:
            return False
        fid = getattr(viewer, '_pdv_fid', None)
        if fid is None:
            return False
        owner = self.owner
        try:
            owner._camera_select_combo_feature_by_fid(int(fid), autoload=True)
        except Exception as exc:
            qcv_log(f'Multi-View: unable to load viewpoint {fid}: {exc}', 'MULTIVIEW', 'WARNING')
            return False

        state = dict(getattr(viewer, '_camera_state', {}) or {})
        if state:
            _collect, apply_state = _camera_helpers()
            try:
                apply_state(owner, state)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:apply-state')
        # Always refresh the viewer base when its context is loaded. In 40.21 the
        # single viewer could rely on the current pixmap; Multi-View cannot, since
        # each terminal may point to a different photo/schematic source.
        try:
            if getattr(owner, 'image', None) is not None and not owner.image.isNull():
                path = str(getattr(owner, 'photo_path', '') or '')
                signature = self._photo_signature(path) if path else None
                if (str(getattr(viewer, '_source_photo_path', '') or '') != path
                        or getattr(viewer, '_source_photo_signature', None) != signature):
                    viewer.update_image(owner.image)
                    viewer._source_photo_path = path
                    viewer._source_photo_signature = signature
            else:
                viewer.update_image(owner._make_schematic_base(
                    int(owner.spin_w.value()), int(owner.spin_h.value()), for_export=False))
                viewer._source_photo_path = ''
                viewer._source_photo_signature = None
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:base-image')
        return True

    def activate_viewer(self, viewer, load=True, render=True):
        if viewer not in self.viewers:
            return False
        if self._internal:
            return False

        old = self.active_viewer
        if old is not None and old is not viewer:
            self.capture_viewer_state(old)

        self.active_viewer = viewer
        self.owner.viewer = viewer
        for item in self.viewers:
            try:
                item.set_active(item is viewer)
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:set-active')

        self._internal = True
        prev_internal_render = bool(getattr(self.owner, '_multiview_internal_render', False))
        self.owner._multiview_internal_render = True
        try:
            if load:
                self._load_viewer_context(viewer)
            # overlay_image is a single renderer field, while each Multi-View
            # terminal has an independent displayed overlay.  Restore the selected
            # terminal snapshot after loading its camera/PDV context; otherwise the
            # camera loader can leave the shared field pointing at the old viewer.
            try:
                snap = getattr(viewer, '_overlay_snapshot', None)
                if snap is not None and not snap.isNull():
                    # Restore the renderer-global state and the actual terminal.
                    # Generic PDV visual-state hooks may have touched the visible
                    # overlay while the context was loading.
                    restored = snap.copy()
                    self.owner.overlay_image = restored
                    viewer.update_overlay(restored)
                else:
                    self.owner.overlay_image = None
                    viewer.clear_overlay()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:restore-viewer-overlay')
            try:
                viewer.sync_pdv_controls()
                viewer.update_info()
                viewer.set_image_pick_active(bool(getattr(self.owner, '_image_pick_mode', None)))
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:activate-ui')
        finally:
            self.owner._multiview_internal_render = prev_internal_render
            self._internal = False

        if render:
            try:
                self.owner.render_preview()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:activate-render')
        self.persist_project_state()
        return True

    def assign_pdv(self, viewer, fid):
        if viewer not in self.viewers:
            return False
        self.capture_viewer_state(viewer)
        try:
            viewer._pdv_fid = int(fid)
        except Exception:
            return False
        # A new PDV starts from its stored camera state, not the previous viewer draft.
        viewer._camera_state = {}
        return self.activate_viewer(viewer, load=True, render=True)

    def navigate(self, viewer, delta):
        combo = getattr(viewer, '_cmb_pdv', None)
        if combo is None or combo.count() <= 0:
            return
        idx = int(combo.currentIndex())
        if idx < 0:
            idx = 0
        idx = (idx + (1 if int(delta) > 0 else -1)) % int(combo.count())
        fid = combo.itemData(idx)
        if fid is not None:
            self.assign_pdv(viewer, int(fid))

    def owner_pdv_changed(self, fid):
        if self._internal or self.active_viewer is None:
            return
        try:
            self.active_viewer._pdv_fid = int(fid) if fid is not None else None
            self.active_viewer._camera_state = {}
            self.active_viewer.sync_pdv_controls()
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:owner-pdv')
        self.persist_project_state()

    def save_viewer_state(self, viewer):
        if viewer not in self.viewers:
            return
        if self.active_viewer is not viewer:
            self.activate_viewer(viewer, load=True, render=False)
        self.capture_viewer_state(viewer)
        try:
            self.owner._camera_save_current_feature()
            self.capture_viewer_state(viewer)
            viewer.update_info()
        except Exception as exc:
            qcv_log(f'Multi-View save failed: {exc}', 'MULTIVIEW', 'WARNING')
        self.persist_project_state()

    def notify_camera_interaction(self):
        """Capture the active viewer camera without invalidating other viewers.

        Camera controls (azimuth, pitch, roll, FOV, offsets, etc.) belong to the
        active terminal. The normal QCALVIEW render scheduler refreshes that one
        viewer. Secondary viewers are deliberately left untouched: they have
        independent temporary camera states and re-rendering them would waste CPU
        while the user is adjusting only one viewpoint.
        """
        if self._internal or self.active_viewer is None:
            return
        self.capture_viewer_state(self.active_viewer)
        try:
            self.active_viewer.update_info()
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:camera-info')
        self.schedule_persist()

    def notify_scene_changed(self, reason='scene'):
        if self._internal:
            return
        self.request_scene_refresh(reason=reason)

    def _secondary_auto_allowed(self):
        """Return whether inactive viewers may be refreshed automatically.

        A panoramic raster drape is the heaviest preview path and already builds a
        depth mesh plus vector geometry.  In that configuration R5 could still launch
        a low-quality render for every inactive viewer after a scene change.  Those
        renders are sequential, but they keep large Qt/NumPy allocations cycling in
        one event-loop burst.  R6 leaves inactive views stale instead; activating or
        explicitly refreshing a viewer renders it normally.
        """
        try:
            txt=str(self.owner.cmb_proj.currentText()).strip().upper()
            pano=txt in ('EQUIRECT','EQUIRECTANGULAR','CYLINDRICAL')
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:secondary-auto-projection')
            pano=False
        if not pano:
            return True
        try:
            if bool(self.owner._raster_drape_enabled()):
                return False
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:secondary-auto-raster')
        return True

    def _secondary_high_allowed(self):
        """Return whether inactive viewers may receive an automatic HQ refinement.

        Panoramic rendering is the heavy path (terrain/raster drape, z-buffer and
        adaptive mesh projection).  Re-rendering every inactive panorama at HQ after
        each scene edit gives little interactive benefit and can push QGIS into a
        native allocation failure on Windows.  The active viewer is still rendered at
        the user's normal quality; an inactive viewer gets full quality when activated.
        """
        try:
            txt=str(self.owner.cmb_proj.currentText()).strip().upper()
            if txt in ('EQUIRECT','EQUIRECTANGULAR','CYLINDRICAL'):
                return False
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:secondary-high-projection')
        try:
            if bool(self.owner._raster_drape_enabled()):
                return False
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:secondary-high-raster')
        return True

    def cancel_secondary_work(self):
        self._queue_timer.stop()
        self._low_timer.stop()
        self._high_timer.stop()
        self._secondary_queue = []
        self._secondary_generation = int(self.scene_generation)

    def request_scene_refresh(self, reason='scene'):
        if len(self.viewers) <= 1:
            return
        self.scene_generation += 1
        # Active view is rendered by the normal QCALVIEW scheduler. Cancel any
        # obsolete secondary queue, then let the active refresh win the event loop
        # before starting cheap secondary work.
        self.cancel_secondary_work()
        if not self._secondary_auto_allowed():
            return
        self._low_timer.start(260)
        if self._secondary_high_allowed():
            self._high_timer.start(900)

    def _start_secondary_pass(self, quality):
        if self._internal or len(self.viewers) <= 1:
            return
        q=str(quality or 'low').lower()
        if q=='high' and not self._secondary_high_allowed():
            return
        self._secondary_quality = q
        self._secondary_generation = int(self.scene_generation)
        active = self.active_viewer
        self._secondary_queue = [v for v in self.viewers if v is not active]
        if self._secondary_queue:
            self._queue_timer.start(0)

    def _render_next_secondary(self):
        if not self._secondary_queue or self._internal:
            return
        if int(self._secondary_generation) != int(self.scene_generation):
            self._secondary_queue = []
            return
        # Never context-swap the single renderer while an active render reports
        # itself in progress.  This is mostly defensive on the Qt main thread, but
        # also protects against nested event loops opened by QGIS/Qt widgets.
        if bool(getattr(self.owner, '_rendering_now', False)):
            self._queue_timer.start(80)
            return
        viewer = self._secondary_queue.pop(0)
        try:
            self.render_viewer(viewer, self._secondary_quality)
        except Exception as exc:
            qcv_log(f'Multi-View secondary render failed: {exc}', 'MULTIVIEW', 'WARNING')
        if self._secondary_queue:
            self._queue_timer.start(20)

    def render_viewer(self, viewer, quality='low'):
        if viewer not in self.viewers or viewer is self.active_viewer:
            return False
        owner = self.owner
        active = self.active_viewer
        if active is None:
            return False

        # Preserve the cockpit and active PDV. The renderer remains single-owner;
        # contexts are swapped sequentially, never concurrently.
        self.capture_viewer_state(active)
        active_fid = getattr(active, '_pdv_fid', None)
        active_state = dict(getattr(active, '_camera_state', {}) or {})
        prev_quality = str(getattr(owner, '_current_render_quality', 'high'))
        prev_background = bool(getattr(owner, '_multiview_background_render', False))
        active_overlay = getattr(owner, 'overlay_image', None)
        active_last_preview = getattr(owner, 'last_preview', None)
        active_horizon = getattr(owner, '_horizon', None)
        active_horizon_params = getattr(owner, '_horizon_params', None)

        self._internal = True
        owner._multiview_internal_render = True
        owner._multiview_background_render = True
        try:
            owner.viewer = viewer
            if not self._load_viewer_context(viewer):
                return False
            owner._current_render_quality = str(quality or 'low')
            owner._render_preview_now()
            collect, _apply = _camera_helpers()
            viewer._camera_state = dict(collect(owner) or {})
            viewer._render_generation = int(self.scene_generation)
        finally:
            try:
                owner.viewer = active
                active._pdv_fid = active_fid
                active._camera_state = active_state
                self._load_viewer_context(active)
                active._camera_state = active_state
                active.sync_pdv_controls()
                active.update_info()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:restore-active')
            owner.overlay_image = active_overlay
            owner.last_preview = active_last_preview
            owner._horizon = active_horizon
            owner._horizon_params = active_horizon_params
            try:
                if active_overlay is not None and not active_overlay.isNull():
                    active.update_overlay(active_overlay)
                else:
                    active.clear_overlay()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:restore-active-overlay')
            owner._current_render_quality = prev_quality
            owner._multiview_background_render = prev_background
            owner._multiview_internal_render = False
            self._internal = False
        return True

    def on_frame_rendered(self, viewer=None):
        viewer = viewer or self.active_viewer
        if viewer is None:
            return
        try:
            viewer._render_generation = int(self.scene_generation)
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:frame-generation')
        if viewer is self.active_viewer and not self._internal:
            self.capture_viewer_state(viewer)

    def _ensure_quad_window(self):
        if self.quad_window is None:
            self.quad_window = MultiViewWindow(self, self.owner.iface.mainWindow())
        return self.quad_window

    def show_quad(self):
        if not self.viewers:
            self.create_viewer(show=False)
        self.mode = 'quad'
        self._sync_viewer_mode_buttons()
        win = self._ensure_quad_window()
        win.rebuild()
        win.show()
        win.raise_()
        win.activateWindow()
        self.persist_project_state()

    def show_independent(self):
        self.mode = 'windows'
        self._sync_viewer_mode_buttons()
        win = self.quad_window
        if win is not None:
            try:
                win.hide()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:hide-quad')
        for viewer in list(self.viewers):
            try:
                viewer._set_embedded(False, self.owner.iface.mainWindow())
                viewer.show()
            except Exception as exc:
                _qcv_suppress(exc, 'core/_multiview.py:separate')
        self.persist_project_state()

    def toggle_quad(self):
        if self.mode == 'quad':
            self.show_independent()
        else:
            self.show_quad()

    def toggle_viewer_maximize(self, viewer):
        if self.mode == 'quad' and self.quad_window is not None:
            self.quad_window.toggle_maximize(viewer)

    def schedule_persist(self):
        if self._restoring or self._closing:
            return
        try:
            self._persist_timer.start()
        except Exception:
            self.persist_project_state()

    def _resolve_saved_fid(self, slot):
        ident = str((slot or {}).get('pdv_identity') or '')
        fallback = (slot or {}).get('pdv_fid')
        if ident:
            combo = getattr(self.owner, 'cmb_cam_feature', None)
            if combo is not None:
                for idx in range(combo.count()):
                    fid = combo.itemData(idx)
                    if fid is None:
                        continue
                    try:
                        if str(self.owner._camera_pdv_identity(int(fid)) or '') == ident:
                            return int(fid)
                    except Exception as exc:
                        _qcv_suppress(exc, 'core/_multiview.py:resolve-pdv-identity')
                        continue
        try:
            return int(fallback) if fallback is not None else None
        except Exception:
            return None

    def project_payload(self):
        slots = []
        for viewer in self.viewers:
            self.capture_viewer_state(viewer)
            fid = getattr(viewer, '_pdv_fid', None)
            try:
                identity = str(self.owner._camera_pdv_identity(int(fid)) or '') if fid is not None else ''
            except Exception:
                identity = ''
            slots.append({
                'viewer_id': int(getattr(viewer, '_viewer_id', 0) or 0),
                'pdv_fid': fid,
                'pdv_identity': identity,
                'camera_state': dict(getattr(viewer, '_camera_state', {}) or {}),
                'geometry': (_rect_payload(viewer) if self.mode == 'windows'
                             else list(getattr(viewer, '_independent_rect', []) or [])),
            })
        active_id = int(getattr(self.active_viewer, '_viewer_id', 0) or 0) if self.active_viewer else 0
        quad_geometry = _rect_payload(self.quad_window) if self.quad_window is not None else []
        return {
            'version': 1,
            'mode': str(self.mode),
            'active_viewer_id': active_id,
            'quad_vertical_2': bool(getattr(self.quad_window, '_orientation_vertical', False)) if self.quad_window else False,
            'quad_geometry': quad_geometry,
            'quad_splitter_sizes': self.quad_window.splitter_sizes() if self.quad_window is not None else [],
            'slots': slots,
        }

    def persist_project_state(self):
        if self._restoring or self._closing or bool(getattr(self.owner, '_ui_initializing', False)):
            return False
        try:
            state = self.owner._project_state_read()
            state['multiview'] = self.project_payload()
            return bool(self.owner._project_state_write(state))
        except Exception as exc:
            _qcv_suppress(exc, 'core/_multiview.py:persist')
            return False

    def restore_project_state(self):
        if self.viewers or self._restoring:
            return
        try:
            data = dict((self.owner._project_state_read() or {}).get('multiview') or {})
        except Exception:
            data = {}
        slots = list(data.get('slots') or [])[:MAX_VIEWERS]
        if not slots:
            return
        self._restoring = True
        try:
            wanted_mode = str(data.get('mode') or 'windows')
            active_id = int(data.get('active_viewer_id') or 0)
            for slot in slots:
                try:
                    viewer = self.create_viewer(
                        pdv_fid=self._resolve_saved_fid(slot),
                        camera_state=slot.get('camera_state') or {},
                        show=False,
                        viewer_id=slot.get('viewer_id'),
                    )
                    viewer._saved_window_geometry = list(slot.get('geometry') or [])
                    viewer._independent_rect = list(slot.get('geometry') or [])
                except Exception as exc:
                    _qcv_suppress(exc, 'core/_multiview.py:restore-slot')
            selected = next((v for v in self.viewers if int(getattr(v, '_viewer_id', 0)) == active_id), None)
            if selected is not None:
                self.activate_viewer(selected, load=True, render=False)
            if wanted_mode == 'quad':
                self.mode = 'quad'
                self._sync_viewer_mode_buttons()
                win = self._ensure_quad_window()
                win._orientation_vertical = bool(data.get('quad_vertical_2', False))
                win._saved_splitter_sizes = list(data.get('quad_splitter_sizes') or [])
                _apply_rect(win, data.get('quad_geometry'))
                win.rebuild()
                win.show()
            else:
                self.mode = 'windows'
                self._sync_viewer_mode_buttons()
                for viewer in self.viewers:
                    viewer._set_embedded(False, self.owner.iface.mainWindow())
                    _apply_rect(viewer, getattr(viewer, '_saved_window_geometry', []))
                    viewer.show()
            if self.active_viewer is not None:
                self.owner.viewer = self.active_viewer
                self.owner.render_preview()
        finally:
            self._restoring = False

    def close_all(self):
        self.persist_project_state()
        self._closing = True
        try:
            if self.quad_window is not None:
                try:
                    self.quad_window.hide()
                except Exception as exc:
                    _qcv_suppress(exc, 'core/_multiview.py:close-all-quad')
            for viewer in list(self.viewers):
                try:
                    viewer._force_close = True
                    viewer.close()
                except Exception:
                    try:
                        viewer.hide()
                    except Exception as exc:
                        _qcv_suppress(exc, 'core/_multiview.py:close-all-hide-viewer')
            self.viewers = []
            self.active_viewer = None
            self.owner.viewer = None
            self._photo_cache.clear()
        finally:
            self._closing = False
