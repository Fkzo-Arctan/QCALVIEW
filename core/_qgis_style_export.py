"""One-shot QCALVIEW -> QGIS cartographic style translation.

This module deliberately does not depend on QCALVIEW's photographic renderer.
It converts the current LayerStyle into a native QGIS renderer and stores it as
an independent named style on the source vector layer.
"""

from __future__ import annotations

import json
import math
import os
import re

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis,
    QgsFillSymbol,
    QgsGeometry,
    QgsGeometryGeneratorSymbolLayer,
    QgsLinePatternFillSymbolLayer,
    QgsLineSymbol,
    QgsMapLayerStyle,
    QgsMarkerLineSymbolLayer,
    QgsPalLayerSettings,
    QgsMarkerSymbol,
    QgsPointPatternFillSymbolLayer,
    QgsProject,
    QgsTextBackgroundSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)

from ._compat import QC
from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._log import qcv_log


_STYLE_PREFIX = "QCALVIEW — "


def _clamp(value, lo, hi):
    try:
        return max(float(lo), min(float(hi), float(value)))
    except Exception:
        return float(lo)


def _color(value, fallback):
    try:
        c = QColor(value)
        if c.isValid():
            return c
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:63")
    return QColor(fallback)


def _with_alpha(color, alpha):
    out = QColor(color)
    out.setAlpha(max(0, min(255, int(round(alpha)))))
    return out


def _darken(color, factor=145):
    try:
        return QColor(color).darker(int(factor))
    except Exception:
        return QColor(35, 35, 35)


def _style_width_mm(sty):
    """Translate QCALVIEW's screen-oriented line width to a sane map style width."""
    try:
        width = float(getattr(sty, "width", 2.0) or 2.0)
    except Exception:
        width = 2.0
    return _clamp(width * 0.35, 0.12, 4.0)


def _style_opacity(sty):
    try:
        opacity = float(getattr(sty, "opacity", 1.0))
        if opacity > 1.0 and opacity <= 100.0:
            opacity /= 100.0
        if not math.isfinite(opacity):
            opacity = 1.0
        return _clamp(opacity, 0.0, 1.0)
    except Exception:
        return 1.0


def _geometry_name(layer):
    try:
        gtype = QgsWkbTypes.geometryType(layer.wkbType())
    except Exception:
        return ""
    if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
        return "point"
    if gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
        return "line"
    if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
        return "polygon"
    return ""


def _definition_path(plugin_dir, symbol_id):
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "", str(symbol_id or ""))
    if not safe_id:
        return ""
    return os.path.join(plugin_dir, "resources", "symbols", safe_id + ".json")


def _load_definition(plugin_dir, symbol_id):
    path = _definition_path(plugin_dir, symbol_id)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        qcv_log(
            f"Unable to read AVR symbol {symbol_id!r} for the QGIS style: {exc}",
            "QGIS-STYLE",
            "WARNING",
        )
        return None


def _parameter_default(definition, key, fallback=None):
    try:
        raw = (definition.get("parameters", {}) or {}).get(key, fallback)
        if isinstance(raw, dict):
            return raw.get("default", fallback)
        return raw
    except Exception:
        return fallback


def _parameter_value(sty, definition, key, fallback=None):
    try:
        params = dict(getattr(sty, "schematic_params", {}) or {})
        if key in params:
            return params[key]
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:155")
    return _parameter_default(definition or {}, key, fallback)


def _symbol_display_name(definition, symbol_id):
    label = ""
    try:
        label = str((definition or {}).get("name", "") or "").strip()
    except Exception:
        label = ""
    if not label:
        label = str(symbol_id or "Style QCALVIEW").replace("_", " ").strip()
    return label or "Style QCALVIEW"


def _style_name(definition, symbol_id, schematic_enabled):
    suffix = _symbol_display_name(definition, symbol_id) if schematic_enabled else tr('Standard style')
    suffix = re.sub(r"[\r\n\t]+", " ", suffix).strip()
    return (_STYLE_PREFIX + suffix)[:120]


def _svg_asset_path(plugin_dir, definition):
    try:
        spec = (definition.get("parameters", {}) or {}).get("svg")
        if isinstance(spec, dict):
            spec = spec.get("default", "")
        spec = str(spec or "").strip()
    except Exception:
        spec = ""
    if not spec:
        return ""
    path = os.path.join(plugin_dir, "resources", "symbols", spec.replace("/", os.sep))
    return os.path.normpath(path) if os.path.isfile(path) else ""


def _simple_marker(color, outline, size_mm=3.0, shape="circle"):
    props = {
        "name": str(shape),
        "color": QColor(color).name(),
        "outline_color": QColor(outline).name(),
        "outline_width": "0.35",
        "size": str(_clamp(size_mm, 1.0, 15.0)),
    }
    layer = QgsSimpleMarkerSymbolLayer.create(props)
    return QgsMarkerSymbol([layer])


def _svg_marker(plugin_dir, definition, fallback_color, size_mm=5.0):
    path = _svg_asset_path(plugin_dir, definition or {})
    if path:
        try:
            svg_layer = QgsSvgMarkerSymbolLayer(path, _clamp(size_mm, 2.0, 16.0))
            return QgsMarkerSymbol([svg_layer])
        except Exception as exc:
            qcv_log(f"SVG QGIS non utilisable ({path}): {exc}", "QGIS-STYLE", "WARNING")
    return _simple_marker(fallback_color, _darken(fallback_color), size_mm=size_mm, shape="circle")


def _layer_extent_area_m2(layer):
    """Cheap approximate layer footprint area in square meters.

    Used only to keep cartographic repeated markers responsive. The estimate may
    overstate the real polygon area, which is intentional for a performance guard.
    """
    try:
        rect = layer.extent()
        if rect is None or rect.isNull() or rect.isEmpty():
            return 0.0
        geom = QgsGeometry.fromRect(rect)
        crs = layer.crs()
        try:
            if crs.isGeographic():
                from qgis.core import QgsDistanceArea
                da = QgsDistanceArea()
                da.setSourceCrs(crs, QgsProject.instance().transformContext())
                try:
                    da.setEllipsoid(QgsProject.instance().ellipsoid())
                except Exception as _qcv_exc:
                    _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:233")
                return max(0.0, float(da.measureArea(geom)))
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:236")
        width = abs(float(rect.width()))
        height = abs(float(rect.height()))
        if not (math.isfinite(width) and math.isfinite(height)):
            return 0.0
        return max(0.0, width * height)
    except Exception:
        return 0.0


def _performance_safe_tree_pattern(layer, marker, fallback_color, spacing):
    """Return a responsive marker/spacing pair for polygon tree populations.

    A dense PointPatternFill made from SVGs can freeze the QGIS canvas on large
    polygon layers. QCALVIEW therefore keeps the semantics (individual trees)
    but caps the approximate number of repeated markers aggressively and uses a lightweight
    cartographic tree marker for very large footprints.
    """
    spacing = max(0.25, float(spacing))
    area = _layer_extent_area_m2(layer)
    estimated = area / max(0.0625, spacing * spacing) if area > 0.0 else 0.0

    # Keep the pattern itself bounded. The estimate intentionally uses the layer
    # extent, so this is conservative for sparse/multipart polygon layers.
    if estimated > 1800.0 and area > 0.0:
        spacing = max(spacing, math.sqrt(area / 1800.0))
        estimated = area / max(0.0625, spacing * spacing)

    if estimated > 600.0:
        # A simple marker is dramatically cheaper than hundreds or thousands of SVG renders,
        # while still reading cartographically as a tree population.
        marker = _simple_marker(
            fallback_color,
            _darken(fallback_color),
            size_mm=3.0,
            shape="circle",
        )
    return marker, spacing


def _line_layer(color, width_mm, pen_style=None):
    if pen_style is None:
        pen_style = QC.Qt_PenStyle_SolidLine
    try:
        return QgsSimpleLineSymbolLayer(QColor(color), float(width_mm), pen_style)
    except Exception:
        layer = QgsSimpleLineSymbolLayer()
        layer.setColor(QColor(color))
        layer.setWidth(float(width_mm))
        try:
            layer.setPenStyle(pen_style)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:288")
        return layer


def _fill_layer(fill_color, stroke_color, stroke_width_mm, pen_style=None):
    layer = QgsSimpleFillSymbolLayer()
    try:
        layer.setColor(QColor(fill_color))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:297")
    try:
        layer.setStrokeColor(QColor(stroke_color))
        layer.setStrokeWidth(float(stroke_width_mm))
        if pen_style is not None:
            layer.setStrokeStyle(pen_style)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:304")
    return layer


def _apply_symbol_opacity(symbol, opacity):
    try:
        symbol.setOpacity(float(opacity))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:opacity")
    return symbol


def _basic_symbol(sty, layer):
    geom = _geometry_name(layer)
    line_color = _color(getattr(sty, "color", QColor(0, 180, 0)), QColor(0, 180, 0))
    fill_color = _color(getattr(sty, "fill_color", line_color), line_color)
    width_mm = _style_width_mm(sty)
    opacity = _style_opacity(sty)
    pen_style = getattr(sty, "pen_style", QC.Qt_PenStyle_SolidLine)

    if geom == "point":
        marker = _simple_marker(line_color, _darken(line_color), size_mm=max(2.6, 2.0 + width_mm * 2.0))
        return _apply_symbol_opacity(marker, opacity)
    if geom == "line":
        symbol = QgsLineSymbol([_line_layer(line_color, width_mm, pen_style)])
        return _apply_symbol_opacity(symbol, opacity)
    if geom == "polygon":
        if not bool(getattr(sty, "fill_polygons", True)):
            fill_color = _with_alpha(fill_color, 0)
        symbol = QgsFillSymbol([_fill_layer(fill_color, line_color, width_mm, pen_style)])
        return _apply_symbol_opacity(symbol, opacity)
    return None


def _hedge_symbol(sty, layer):
    if _geometry_name(layer) != "line":
        return _basic_symbol(sty, layer)
    line_color = _color(getattr(sty, "color", QColor("#2d4b31")), QColor("#2d4b31"))
    fill_color = _color(getattr(sty, "fill_color", QColor("#4f7457")), QColor("#4f7457"))
    width = max(0.8, _style_width_mm(sty) * 1.8)
    outer = _line_layer(line_color, width + 0.55, QC.Qt_PenStyle_SolidLine)
    inner = _line_layer(fill_color, width, QC.Qt_PenStyle_SolidLine)
    symbol = QgsLineSymbol([outer, inner])
    return _apply_symbol_opacity(symbol, _style_opacity(sty))


def _tree_symbol(sty, layer, plugin_dir, definition):
    geom = _geometry_name(layer)
    color = _color(getattr(sty, "color", QColor("#4f7457")), QColor("#4f7457"))
    fill = _color(getattr(sty, "fill_color", color), color)
    spacing = _clamp(_parameter_value(sty, definition, "spacing_m", 6.0), 0.25, 500.0)
    line_mode = str(_parameter_value(sty, definition, "line_mode", "alignment") or "alignment").lower()
    polygon_mode = str(_parameter_value(sty, definition, "polygon_mode", "mass") or "mass").lower()
    marker = _svg_marker(plugin_dir, definition, color, size_mm=5.5)
    opacity = _style_opacity(sty)

    if geom == "point":
        return _apply_symbol_opacity(marker, opacity)

    if geom == "line":
        if line_mode == "ribbon":
            return _hedge_symbol(sty, layer)
        base = _line_layer(_with_alpha(color, 115), max(0.18, _style_width_mm(sty) * 0.4))
        markers = QgsMarkerLineSymbolLayer(False, spacing)
        try:
            markers.setIntervalUnit(Qgis.RenderUnit.MetersInMapUnits)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:tree_interval_unit")
        markers.setSubSymbol(marker)
        symbol = QgsLineSymbol([base, markers])
        return _apply_symbol_opacity(symbol, opacity)

    if geom == "polygon":
        stroke = QColor(color)
        if polygon_mode == "instances":
            background = _fill_layer(_with_alpha(fill, 45), stroke, max(0.18, _style_width_mm(sty) * 0.65))
            marker, spacing = _performance_safe_tree_pattern(layer, marker, fill, spacing)
            pattern = QgsPointPatternFillSymbolLayer()
            pattern.setDistanceX(spacing)
            pattern.setDistanceY(spacing)
            try:
                pattern.setDistanceXUnit(Qgis.RenderUnit.MetersInMapUnits)
                pattern.setDistanceYUnit(Qgis.RenderUnit.MetersInMapUnits)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:tree_pattern_unit")
            pattern.setSubSymbol(marker)
            symbol = QgsFillSymbol([background, pattern])
        else:
            symbol = QgsFillSymbol([_fill_layer(_with_alpha(fill, 150), stroke, _style_width_mm(sty))])
        return _apply_symbol_opacity(symbol, opacity)
    return _basic_symbol(sty, layer)


def _fence_line_symbol(sty, definition):
    # Cartographic fence colors follow the common QCALVIEW style controls:
    # line color for rails/outline, fill color for posts. The legacy AVR
    # fence_color parameter is deliberately not allowed to override them.
    line_color = _color(getattr(sty, "color", QColor("#4a4a4a")), QColor("#4a4a4a"))
    fill_color = _color(getattr(sty, "fill_color", line_color), line_color)
    spacing = _clamp(_parameter_value(sty, definition, "post_spacing_m", 3.0), 0.25, 500.0)
    width = max(0.18, _style_width_mm(sty) * 0.55)
    base = _line_layer(line_color, width)
    posts = QgsMarkerLineSymbolLayer(False, spacing)
    try:
        posts.setIntervalUnit(Qgis.RenderUnit.MetersInMapUnits)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:fence_interval_unit")
    posts.setSubSymbol(_simple_marker(fill_color, line_color, size_mm=max(1.5, width * 2.8), shape="square"))
    symbol = QgsLineSymbol([base, posts])
    return _apply_symbol_opacity(symbol, _style_opacity(sty))


def _fence_symbol(sty, layer, definition):
    geom = _geometry_name(layer)
    line_symbol = _fence_line_symbol(sty, definition)
    if geom == "line":
        return line_symbol
    if geom == "polygon":
        try:
            generator = QgsGeometryGeneratorSymbolLayer.create(
                {"geometryModifier": "boundary($geometry)", "SymbolType": "Line"}
            )
            generator.setSubSymbol(line_symbol)
            return QgsFillSymbol([generator])
        except Exception as exc:
            qcv_log(f"QGIS polygon fence: falling back to simple outline ({exc})", "QGIS-STYLE", "WARNING")
            line_color = _color(getattr(sty, "color", QColor("#4a4a4a")), QColor("#4a4a4a"))
            return _apply_symbol_opacity(
                QgsFillSymbol([_fill_layer(_with_alpha(line_color, 0), line_color, _style_width_mm(sty))]),
                _style_opacity(sty),
            )
    return _basic_symbol(sty, layer)


def _pv_color(sty):
    fill = _color(getattr(sty, "fill_color", QColor("#365B73")), QColor("#365B73"))
    line = _color(getattr(sty, "color", _darken(fill, 150)), _darken(fill, 150))
    return fill, line


def _pv_symbol(sty, layer, definition, symbol_id):
    geom = _geometry_name(layer)
    fill, line = _pv_color(sty)
    opacity = _style_opacity(sty)
    table_width = _clamp(_parameter_value(sty, definition, "table_width_m", 4.5), 0.2, 100.0)
    azimuth = _clamp(_parameter_value(sty, definition, "azimuth_deg", 180.0), 0.0, 360.0)
    interval = table_width * 1.5
    if str(symbol_id) == "pv_tracker":
        interval = _clamp(_parameter_value(sty, definition, "tracker_post_spacing_m", 8.0), 0.5, 100.0)

    if geom == "line":
        base = _line_layer(line, max(0.35, _style_width_mm(sty)))
        panels = QgsMarkerLineSymbolLayer(True, interval)
        try:
            panels.setIntervalUnit(Qgis.RenderUnit.MetersInMapUnits)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:pv_interval_unit")
        panels.setSubSymbol(_simple_marker(fill, line, size_mm=3.4, shape="square"))
        symbol = QgsLineSymbol([base, panels])
        return _apply_symbol_opacity(symbol, opacity)

    if geom == "polygon":
        background = _fill_layer(_with_alpha(fill, 105), line, max(0.18, _style_width_mm(sty) * 0.75))
        stripes = QgsLinePatternFillSymbolLayer()
        stripes.setDistance(max(0.5, table_width * 1.35))
        try:
            stripes.setDistanceUnit(Qgis.RenderUnit.MetersInMapUnits)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:pv_pattern_unit")
        stripes.setLineAngle(float(azimuth))
        stripes.setSubSymbol(QgsLineSymbol([_line_layer(line, 0.30)]))
        symbol = QgsFillSymbol([background, stripes])
        return _apply_symbol_opacity(symbol, opacity)

    return _basic_symbol(sty, layer)


def build_qgis_symbol(sty, plugin_dir):
    """Builds a native QGIS symbol for the supplied QCALVIEW LayerStyle."""
    layer = getattr(sty, "layer", None)
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return None, None, ""

    schematic_enabled = bool(getattr(sty, "schematic_enabled", False))
    symbol_id = str(getattr(sty, "schematic_symbol_id", "") or "").strip()
    definition = _load_definition(plugin_dir, symbol_id) if schematic_enabled and symbol_id else None

    symbol = None
    if schematic_enabled and symbol_id:
        sid = symbol_id.lower()
        generator = str((definition or {}).get("generator", "") or "").lower()
        if sid == "hedge_generic" or generator == "vegetation_ribbon":
            symbol = _hedge_symbol(sty, layer)
        elif sid.startswith("tree_") or generator == "vegetation_adaptive":
            symbol = _tree_symbol(sty, layer, plugin_dir, definition or {})
        elif sid == "fence_generic" or generator == "fence_perimeter":
            symbol = _fence_symbol(sty, layer, definition or {})
        elif sid.startswith("pv_") or generator == "pv_surface":
            symbol = _pv_symbol(sty, layer, definition or {}, sid)

    if symbol is None:
        symbol = _basic_symbol(sty, layer)

    style_name = _style_name(definition, symbol_id, schematic_enabled)
    return symbol, definition, style_name


def _existing_label_settings(layer):
    try:
        labeling = layer.labeling()
        if labeling is not None and hasattr(labeling, "settings"):
            return QgsPalLayerSettings(labeling.settings())
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:517")
    return QgsPalLayerSettings()


def _apply_labels_from_style(layer, sty):
    """Apply manual QCALVIEW label settings to the QGIS layer.

    When ``use_qgis_labels`` is true, the layer's existing labeling is deliberately
    left untouched. This mirrors the QCALVIEW editor semantics.
    """
    if bool(getattr(sty, "use_qgis_labels", True)):
        return

    if not bool(getattr(sty, "show_labels", False)):
        try:
            layer.setLabelsEnabled(False)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:534")
        return

    settings = _existing_label_settings(layer)
    label_text = str(getattr(sty, "label_text", "") or "").strip()
    label_field = str(getattr(sty, "label_field", "") or "").strip()
    if label_text:
        # QCALVIEW manual text is literal text. Use a quoted QGIS expression.
        settings.fieldName = "'" + label_text.replace("'", "''") + "'"
        settings.isExpression = True
    elif label_field:
        settings.fieldName = label_field
        settings.isExpression = False
    else:
        try:
            layer.setLabelsEnabled(False)
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:551")
        return

    try:
        fmt = QgsTextFormat(settings.format())
    except Exception:
        fmt = QgsTextFormat()

    # Preserve an existing font family/style when available; QCALVIEW currently
    # exposes size/color but no separate font-family control.
    try:
        existing_font = fmt.font()
        if existing_font is None:
            existing_font = QFont()
        fmt.setFont(existing_font)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:567")
    try:
        fmt.setSize(max(1.0, float(getattr(sty, "label_size", 12) or 12)))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:571")
    try:
        txt = QColor(getattr(sty, "label_text_color", QColor(20, 20, 20, 255)))
        fmt.setColor(txt)
        if hasattr(fmt, "setOpacity"):
            fmt.setOpacity(max(0.0, min(1.0, txt.alphaF())))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:578")

    try:
        buf = QgsTextBufferSettings(fmt.buffer())
    except Exception:
        buf = QgsTextBufferSettings()
    try:
        halo_enabled = bool(getattr(sty, "label_halo", False))
        buf.setEnabled(halo_enabled)
        if halo_enabled:
            buf.setSize(max(0.1, float(getattr(sty, "label_halo_width", 2) or 2)))
            hcol = QColor(getattr(sty, "label_halo_color", QColor(255, 255, 255, 220)))
            buf.setColor(hcol)
            if hasattr(buf, "setOpacity"):
                buf.setOpacity(max(0.0, min(1.0, hcol.alphaF())))
        fmt.setBuffer(buf)
    except Exception as exc:
        qcv_log(f"QGIS label buffer not applied: {exc}", "QGIS-STYLE", "WARNING")

    try:
        bg = QgsTextBackgroundSettings(fmt.background())
    except Exception:
        bg = QgsTextBackgroundSettings()
    try:
        enabled = bool(getattr(sty, "label_bg", False))
        bg.setEnabled(enabled)
        if enabled:
            bcol = QColor(getattr(sty, "label_bg_color", QColor(255, 255, 255, 220)))
            bg.setFillColor(bcol)
            if hasattr(bg, "setOpacity"):
                bg.setOpacity(max(0.0, min(1.0, bcol.alphaF())))
        fmt.setBackground(bg)
    except Exception as exc:
        qcv_log(f"QGIS label background not applied: {exc}", "QGIS-STYLE", "WARNING")

    try:
        settings.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
    except Exception as exc:
        qcv_log(f"QGIS labeling not applied: {exc}", "QGIS-STYLE", "WARNING")


def _apply_style_state_to_layer(layer, renderer, sty):
    layer.setRenderer(renderer)
    _apply_labels_from_style(layer, sty)


def _install_named_style(layer, renderer, style_name, sty):
    """Install/update a named style without deleting/recreating existing styles.

    Avoiding temporary styles and remove/add cycles is important because QGIS map
    themes keep references to layer style names. Mutating the current named style
    in place leaves the native map-theme selector stable.
    """
    manager = layer.styleManager()
    if manager is None:
        _apply_style_state_to_layer(layer, renderer, sty)
        layer.triggerRepaint()
        return True, ""

    try:
        names = {str(n) for n in manager.styles()}
    except Exception:
        names = set()

    if style_name in names:
        try:
            if not bool(manager.setCurrentStyle(style_name)):
                return False, tr("Unable to activate the existing QCALVIEW style.")
            _apply_style_state_to_layer(layer, renderer, sty)
        except Exception as exc:
            return False, str(exc)
    else:
        original = QgsMapLayerStyle()
        original.readFromLayer(layer)
        try:
            _apply_style_state_to_layer(layer, renderer, sty)
            exported = QgsMapLayerStyle()
            exported.readFromLayer(layer)
        except Exception as exc:
            try:
                original.writeToLayer(layer)
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:662")
            return False, str(exc)
        try:
            original.writeToLayer(layer)
        except Exception as exc:
            return False, str(exc)
        try:
            if not bool(manager.addStyle(style_name, exported)):
                return False, tr('Unable to create the named QCALVIEW style.')
            if not bool(manager.setCurrentStyle(style_name)):
                return False, tr('The QCALVIEW style was created but could not be activated.')
        except Exception as exc:
            return False, str(exc)

    try:
        layer.triggerRepaint()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:679")
    try:
        QgsProject.instance().setDirty(True)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_qgis_style_export.py:project_dirty")
    return True, ""


def apply_qcalview_style_to_qgis(sty, plugin_dir):
    """Applies one QCALVIEW layer style to QGIS as an independent named style.

    Returns ``(ok, message, style_name)``. No automatic synchronization is
    established: this function only performs the explicit one-shot conversion.
    Map-theme updates are deliberately handled by the QCALVIEW UI through QGIS
    native theme APIs, so this module remains independent from theme selection.
    """
    layer = getattr(sty, "layer", None)
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return False, tr('The vector layer is not valid.'), ""

    symbol, definition, style_name = build_qgis_symbol(sty, plugin_dir)
    if symbol is None:
        return False, tr('Unable to build a QGIS symbol for this layer.'), style_name

    renderer = QgsSingleSymbolRenderer(symbol)
    ok, error = _install_named_style(layer, renderer, style_name, sty)
    if not ok:
        qcv_log(
            f"Unable to apply QGIS style to {layer.name()!r}: {error}",
            "QGIS-STYLE",
            "WARNING",
        )
        return False, error or tr('Error while applying the style to QGIS.'), style_name

    motif = _symbol_display_name(definition, getattr(sty, "schematic_symbol_id", "")) if bool(getattr(sty, "schematic_enabled", False)) else tr('standard style')
    qcv_log(
        f"QCALVIEW style applied to QGIS — layer={layer.name()!r}, style={style_name!r}, symbol={motif!r}",
        "QGIS-STYLE",
        "INFO",
    )
    return True, tr('Style applied to QGIS.'), style_name


__all__ = ["apply_qcalview_style_to_qgis", "build_qgis_symbol"]
