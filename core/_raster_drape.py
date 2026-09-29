from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._compat import QC

import math

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsRasterLayerElevationProperties,
    QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsRectangle,
    QgsCoordinateTransform,
)


def raster_declared_as_elevation(layer):
    """Return True when QGIS explicitly marks a raster as an elevation surface."""
    if not isinstance(layer, QgsRasterLayer):
        return False
    try:
        props = layer.elevationProperties()
        enabled = getattr(props, "isEnabled", None)
        if callable(enabled) and bool(enabled()):
            return True
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:29")
    return False


def raster_looks_like_dem(layer):
    """Use QGIS' own DEM heuristic, while honoring explicit elevation settings."""
    if not isinstance(layer, QgsRasterLayer):
        return False
    try:
        if not layer.isValid():
            return False
    except Exception:
        return False
    if raster_declared_as_elevation(layer):
        return True
    try:
        return bool(QgsRasterLayerElevationProperties.layerLooksLikeDem(layer))
    except Exception:
        return False


def refresh_dem_raster_filter(self, *_args):
    """Restrict the Relief selector to probable DEMs unless the user asks for all rasters."""
    combo = getattr(self, "cmb_dem", None)
    if combo is None:
        return
    try:
        show_all = bool(getattr(self, "cb_dem_show_all_rasters", None) and self.cb_dem_show_all_rasters.isChecked())
    except Exception:
        show_all = False
    try:
        current = combo.currentLayer()
    except Exception:
        current = None
    excluded = []
    if not show_all:
        try:
            for layer in QgsProject.instance().mapLayers().values():
                if isinstance(layer, QgsRasterLayer) and not raster_looks_like_dem(layer):
                    excluded.append(layer)
        except Exception as exc:
            _qcv_suppress(exc, "core/_raster_drape.py:refresh_dem_raster_filter")
    try:
        combo.setExceptedLayerList(excluded)
    except Exception as exc:
        _qcv_suppress(exc, "core/_raster_drape.py:dem_excepted_layers")
    if current is not None:
        try:
            if show_all or current not in excluded:
                combo.setLayer(current)
            else:
                # Do not silently replace a previously selected non-DEM raster by
                # another layer when the strict filter is re-enabled.
                combo.setLayer(None)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:84")
    try:
        if show_all:
            combo.setToolTip(tr('All QGIS/GDAL raster layers in the project are shown. Check that the selected layer actually contains elevations.'))
        else:
            combo.setToolTip(tr('List limited to rasters QGIS identifies as probable DEM/DSM layers or explicitly declared elevation surfaces.'))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:91")


def raster_is_local(layer):
    """Return True for local raster sources suitable for interactive draping.

    WMS/WMTS/XYZ/WCS and URL-backed GDAL datasets are intentionally rejected:
    a map-render job on these providers can block every QCALVIEW refresh on network I/O.
    """
    if not isinstance(layer, QgsRasterLayer):
        return False
    try:
        if not layer.isValid():
            return False
    except Exception:
        return False
    try:
        provider = str(layer.providerType() or "").strip().lower()
    except Exception:
        provider = ""
    if provider in {"wms", "wcs", "arcgismapserver", "arcgisfeatureserver"}:
        return False
    try:
        source = str(layer.source() or "").strip()
    except Exception:
        source = ""
    low = source.lower()
    remote_tokens = (
        "http://", "https://", "ftp://", "ftps://",
        "/vsicurl/", "/vsis3/", "/vsigs/", "/vsiaz/", "/vsiadls/",
        "type=xyz", "service=wms", "service=wmts", "service=wcs",
    )
    if any(tok in low for tok in remote_tokens):
        return False
    # QGIS often stores local GDAL subdataset parameters after a pipe.  The source
    # itself is enough to distinguish these from network providers.  Do not require
    # os.path.exists() here because VRT/subdataset/temporary local sources may use
    # provider-specific strings which are still fully local.
    return True


def refresh_drape_raster_filter(self, *_args):
    """Show only local raster layers in the single drape selector."""
    combo = getattr(self, "cmb_drape_raster", None)
    if combo is None:
        combo = getattr(self, "cmb_drape_raster_1", None)
    if combo is None:
        return
    try:
        current = combo.currentLayer()
    except Exception:
        current = None
    excluded = []
    try:
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and not raster_is_local(layer):
                excluded.append(layer)
    except Exception as exc:
        _qcv_suppress(exc, "core/_raster_drape.py:refresh_drape_raster_filter")
    try:
        combo.setExceptedLayerList(excluded)
    except Exception as exc:
        _qcv_suppress(exc, "core/_raster_drape.py:drape_excepted_layers")
    if current is not None:
        try:
            if current not in excluded and raster_is_local(current):
                combo.setLayer(current)
            else:
                combo.setLayer(None)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:161")
    try:
        combo.setToolTip(tr('Local raster layer only. WMS/WMTS/XYZ services and other network sources are excluded to avoid slowdowns.'))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:165")


def selected_drape_layers(self):
    """Return the single valid local raster selected for terrain draping."""
    combo = getattr(self, "cmb_drape_raster", None)
    if combo is None:
        combo = getattr(self, "cmb_drape_raster_1", None)
    if combo is None:
        return []
    try:
        layer = combo.currentLayer()
    except Exception:
        layer = None
    if not raster_is_local(layer):
        return []
    return [layer]


def raster_drape_enabled(self):
    try:
        if not bool(getattr(self, "cb_drape_rasters", None) and self.cb_drape_rasters.isChecked()):
            return False
    except Exception:
        return False
    return bool(selected_drape_layers(self))


def clear_raster_drape_cache(self):
    try:
        cache = getattr(self, "_raster_drape_texture_cache", None)
        if isinstance(cache, dict):
            cache.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:199")
    try:
        mesh_cache = getattr(self, "_raster_drape_mesh_cache", None)
        if isinstance(mesh_cache, dict):
            mesh_cache.clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:205")
    try:
        getattr(self, "_overlay_cache", {}).clear()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:209")


def _disconnect_drape_watchers(self):
    conns = list(getattr(self, "_raster_drape_connections", []) or [])
    for signal, slot in conns:
        try:
            signal.disconnect(slot)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:218")
    self._raster_drape_connections = []


def refresh_raster_drape_watchers(self):
    """Refresh repaint/style listeners for the currently selected raster textures."""
    _disconnect_drape_watchers(self)
    conns = []

    def _invalidate(*_args):
        clear_raster_drape_cache(self)
        try:
            self.render_preview()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:232")

    for layer in selected_drape_layers(self):
        for sig_name in ("styleChanged", "rendererChanged", "repaintRequested"):
            try:
                sig = getattr(layer, sig_name)
                sig.connect(_invalidate)
                conns.append((sig, _invalidate))
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_raster_drape.py:241")
                continue
    self._raster_drape_connections = conns



def _layer_tree_visibility_snapshot(layer):
    """Capture checked state for a raster node and every parent group.

    QGIS distinguishes a child's own checked state from its effective visibility:
    a checked raster can still be hidden by an unchecked parent group.  QCALVIEW
    may temporarily enable both, so both must be restored exactly.
    """
    if layer is None:
        return []
    try:
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id()) if root is not None else None
    except Exception:
        node = None
    if node is None:
        return []

    snapshot = []
    current = node
    guard = 0
    while current is not None and guard < 64:
        try:
            checked = bool(current.itemVisibilityChecked())
        except Exception:
            checked = None
        if checked is not None:
            snapshot.append((current, checked))
        try:
            current = current.parent()
        except Exception:
            current = None
        guard += 1
    return snapshot


def _refresh_qgis_canvas(self):
    try:
        self.iface.mapCanvas().refresh()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:285")


def restore_drape_qgis_visibility(self, refresh=True):
    """Restore the exact layer-tree state captured before QCALVIEW changed it."""
    snapshot = getattr(self, '_drape_qgis_visibility_snapshot', None)
    self._drape_qgis_visibility_snapshot = None
    self._drape_qgis_visibility_layer_id = None
    if not snapshot:
        return False

    restored = False
    # Child first, then its ancestors.  Parent state is therefore the final
    # authority, matching the original QGIS tree exactly.
    for node, checked in snapshot:
        try:
            node.setItemVisibilityChecked(bool(checked))
            restored = True
        except Exception as _qcv_exc:
            # The layer/group may have been removed from the project while the
            # drape was active; restoration then simply skips that stale node.
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:304")
            continue
    if refresh and restored:
        _refresh_qgis_canvas(self)
    return restored


def _activate_drape_qgis_visibility(self, layer):
    """Temporarily make *layer* effectively visible, preserving its prior state."""
    if layer is None:
        restore_drape_qgis_visibility(self)
        return False
    try:
        layer_id = str(layer.id())
    except Exception:
        return False

    active_id = getattr(self, '_drape_qgis_visibility_layer_id', None)
    snapshot = getattr(self, '_drape_qgis_visibility_snapshot', None)

    if active_id != layer_id or not snapshot:
        # Switching raster: restore the previous raster/group chain before
        # capturing the new raster's true initial state.
        restore_drape_qgis_visibility(self, refresh=False)
        snapshot = _layer_tree_visibility_snapshot(layer)
        if not snapshot:
            return False
        self._drape_qgis_visibility_snapshot = snapshot
        self._drape_qgis_visibility_layer_id = layer_id

    changed = False
    for node, _initial_checked in list(getattr(self, '_drape_qgis_visibility_snapshot', None) or []):
        try:
            if not bool(node.itemVisibilityChecked()):
                node.setItemVisibilityChecked(True)
                changed = True
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_raster_drape.py:342")
            continue
    if changed or active_id != layer_id:
        _refresh_qgis_canvas(self)
    return True


def ensure_layer_visible_in_qgis(self, layer):
    """Compatibility wrapper for code paths that need managed raster visibility."""
    return _activate_drape_qgis_visibility(self, layer)


def sync_drape_qgis_visibility(self):
    """Synchronize the one draped raster with QGIS, restoring prior state as needed."""
    try:
        show_in_qgis = bool(
            getattr(self, 'cb_drape_show_in_qgis', None)
            and self.cb_drape_show_in_qgis.isChecked()
        )
    except Exception:
        show_in_qgis = False

    try:
        drape_checked = bool(
            getattr(self, 'cb_drape_rasters', None)
            and self.cb_drape_rasters.isChecked()
        )
    except Exception:
        drape_checked = False

    desired = None
    if show_in_qgis and drape_checked:
        layers = selected_drape_layers(self)
        if layers:
            desired = layers[0]

    if desired is None:
        return restore_drape_qgis_visibility(self)

    try:
        desired_id = str(desired.id())
    except Exception:
        return restore_drape_qgis_visibility(self)

    if getattr(self, '_drape_qgis_visibility_layer_id', None) != desired_id:
        return _activate_drape_qgis_visibility(self, desired)

    # Same raster: do not capture a new "initial" state.  Just make sure QCALVIEW's
    # temporary visibility remains effective if this callback was triggered again.
    return _activate_drape_qgis_visibility(self, desired)


def on_raster_drape_changed(self, *_args):
    clear_raster_drape_cache(self)
    refresh_raster_drape_watchers(self)
    try:
        sync_drape_qgis_visibility(self)
    except Exception as exc:
        _qcv_suppress(exc, 'core/_raster_drape.py:sync_drape_qgis_visibility')
    try:
        self.render_preview()
    except Exception as exc:
        _qcv_suppress(exc, 'core/_raster_drape.py:on_raster_drape_changed')


def _transformed_layer_extent(layer, destination_crs):
    try:
        ext = layer.extent()
        if ext is None or ext.isEmpty():
            return None
        src = layer.crs()
        if src == destination_crs:
            return QgsRectangle(ext)
        tr = QgsCoordinateTransform(src, destination_crs, QgsProject.instance())
        return tr.transformBoundingBox(ext)
    except Exception:
        return None


def drape_texture_extent(self, layers, cam_crs, cam_pt, maxdist):
    """Return a bounded texture/mesh extent in camera CRS."""
    if not layers:
        return None
    try:
        cx = float(cam_pt.x()); cy = float(cam_pt.y())
    except Exception:
        return None
    # A 0 (infinite) QCALVIEW range cannot be used for a finite terrain texture.
    # Cap only this drape surface to a practical 20 km radius.
    radius = float(maxdist) if maxdist is not None and float(maxdist) > 0.0 else 20000.0
    radius = max(50.0, min(radius, 50000.0))
    clip = QgsRectangle(cx - radius, cy - radius, cx + radius, cy + radius)

    union = None
    for layer in layers:
        ext = _transformed_layer_extent(layer, cam_crs)
        if ext is None or ext.isEmpty():
            continue
        if union is None:
            union = QgsRectangle(ext)
        else:
            union.combineExtentWith(ext)
    if union is None or union.isEmpty():
        return None

    xmin = max(float(clip.xMinimum()), float(union.xMinimum()))
    xmax = min(float(clip.xMaximum()), float(union.xMaximum()))
    ymin = max(float(clip.yMinimum()), float(union.yMinimum()))
    ymax = min(float(clip.yMaximum()), float(union.yMaximum()))
    if not (math.isfinite(xmin) and math.isfinite(xmax) and math.isfinite(ymin) and math.isfinite(ymax)):
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    return QgsRectangle(xmin, ymin, xmax, ymax)


def _texture_size_for_extent(owner, extent, output_width, output_height, render_quality):
    ew = max(1e-9, float(extent.width()))
    eh = max(1e-9, float(extent.height()))
    aspect = ew / eh
    preview = bool(getattr(owner, "_memory_guard_in_preview", False))
    quality = str(render_quality or "high").lower()
    if preview:
        # Interactive drape: the terrain mesh and z-buffer are already preview
        # representations, so rendering a 2K QGIS texture on every new PDV is wasteful.
        longest = 640 if quality == "low" else 1024 if quality == "normal" else 1536
    else:
        # Export: texture resolution follows useful output size, without allocating an
        # unnecessarily huge square texture for very large panoramas.
        longest = max(1536, min(6144, int(max(output_width, output_height))))
    if aspect >= 1.0:
        w = int(longest)
        h = max(64, int(round(longest / aspect)))
    else:
        h = int(longest)
        w = max(64, int(round(longest * aspect)))
    return max(64, min(6144, w)), max(64, min(6144, h))


def _layer_style_signature(layer):
    try:
        style_name = str(layer.styleManager().currentStyle() or "")
    except Exception:
        style_name = ""
    try:
        renderer = layer.renderer()
        renderer_type = str(renderer.type() if renderer is not None and hasattr(renderer, "type") else type(renderer).__name__)
    except Exception:
        renderer_type = ""
    try:
        opacity = round(float(layer.opacity()), 6)
    except Exception:
        opacity = None
    return (layer.id(), style_name, renderer_type, opacity)


def render_combined_raster_texture(self, layers, cam_crs, extent, output_width, output_height, render_quality="high"):
    """Render selected QGIS raster layers as one transparent RGBA texture."""
    if not layers or extent is None or extent.isEmpty():
        return None
    tex_w, tex_h = _texture_size_for_extent(self, extent, output_width, output_height, render_quality)
    key = (
        tuple(_layer_style_signature(layer) for layer in layers),
        str(cam_crs.authid() if cam_crs is not None else ""),
        round(float(extent.xMinimum()), 3), round(float(extent.yMinimum()), 3),
        round(float(extent.xMaximum()), 3), round(float(extent.yMaximum()), 3),
        int(tex_w), int(tex_h), str(render_quality or "high"),
    )
    cache = getattr(self, "_raster_drape_texture_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._raster_drape_texture_cache = cache
    cached = cache.get(key)
    if cached is not None and not cached.isNull():
        return cached

    settings = QgsMapSettings()
    try:
        settings.setTransformContext(QgsProject.instance().transformContext())
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:521")
    settings.setDestinationCrs(cam_crs)
    settings.setExtent(QgsRectangle(extent))
    settings.setOutputSize(QSize(int(tex_w), int(tex_h)))
    settings.setBackgroundColor(QColor(0, 0, 0, 0))
    settings.setLayers(list(layers))
    try:
        settings.setOutputDpi(96.0)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:530")

    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    if image is None or image.isNull():
        return None
    try:
        image = image.convertToFormat(QC.QImage_Format_Format_ARGB32_Premultiplied)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_raster_drape.py:541")
    cache.clear()
    cache[key] = image
    return image


def drape_layer_ids(self):
    try:
        return tuple(layer.id() for layer in selected_drape_layers(self))
    except Exception:
        return tuple()
