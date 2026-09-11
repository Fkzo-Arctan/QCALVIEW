"""Lightweight precomputed FOV geometry for QCALVIEW camera layers.

The visible camera layer and its QML style remain unchanged from the user's
point of view. QCALVIEW stores precomputed FOV geometries in QGIS auxiliary
storage and only replaces the five heavy GeometryGenerator expressions after
that cache has been created and validated successfully.
"""

import math
import os
import uuid

from qgis.PyQt.QtXml import QDomDocument

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsGeometryGeneratorSymbolLayer,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

from ._compat import QC
from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._log import qcv_log


FOV_CACHE_VERSION = 2
FOV_READY_PROPERTY = "QCALVIEW/fov_cache_version"
AUX_JOIN_PREFIX = "auxiliary_storage_"
AUX_KEY_FIELD = "ASPK"

AUX_FIELDS = (
    "qcv_fov_full",
    "qcv_fov_symbol",
    "qcv_fov_ring",
    "qcv_fov_axis",
    "qcv_fov_equi",
)

LIVE_VARIABLES = {
    "qcv_fov_full": "qcv_fov_full_live",
    "qcv_fov_symbol": "qcv_fov_symbol_live",
    "qcv_fov_ring": "qcv_fov_ring_live",
    "qcv_fov_axis": "qcv_fov_axis_live",
    "qcv_fov_equi": "qcv_fov_equi_live",
}

FOV_SOURCE_FIELDS = {
    "qcv_proj",
    "qcv_360",
    "qcv_yaw",
    "qcv_hfov",
    "qcv_mdst",
}


def _layer_field_name(layer, canonical_name):
    """Return a field name from the layer, case-insensitively."""
    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    try:
        names = layer.fields().names()
    except Exception:
        names = []
    lowered = {str(name).lower(): str(name) for name in names}
    return lowered.get(str(canonical_name).lower())


def _provider_field_name(layer, canonical_name):
    """Return a source-provider field name, case-insensitively."""
    try:
        provider = layer.dataProvider()
        names = provider.fields().names() if provider is not None else []
    except Exception:
        names = []
    lowered = {str(name).lower(): str(name) for name in names}
    return lowered.get(str(canonical_name).lower())


def _feature_value(layer, feature, canonical_name, default=None):
    name = _layer_field_name(layer, canonical_name)
    if not name:
        return default
    try:
        value = feature[name]
    except Exception:
        return default
    if value is None or str(value).strip().upper() == "NULL" or value == "":
        return default
    return value


def _new_uid():
    return uuid.uuid4().hex


def _hide_internal_fields(layer):
    """Hide QCALVIEW internal fields from the normal attribute table."""
    try:
        config = layer.attributeTableConfig()
        config.update(layer.fields())
        columns = config.columns()
        hidden = {"qcv_uid"}
        hidden.update(AUX_JOIN_PREFIX + name for name in AUX_FIELDS)
        for column in columns:
            if str(column.name).lower() in hidden:
                column.hidden = True
        config.setColumns(columns)
        layer.setAttributeTableConfig(config)
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:_hide_internal_fields")


def _ensure_uid_field(layer):
    """Create qcv_uid once and initialise stable unique values safely."""
    if not isinstance(layer, QgsVectorLayer):
        return None

    # A previous DEV may already have created the source field while the layer
    # field cache is stale. Check both views before any OGR schema write.
    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    uid_name = _layer_field_name(layer, "qcv_uid")
    provider_uid = _provider_field_name(layer, "qcv_uid")

    if uid_name is None and provider_uid is not None:
        try:
            layer.updateFields()
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
        uid_name = _layer_field_name(layer, provider_uid)

    if uid_name is None and provider_uid is None:
        provider = layer.dataProvider()
        if provider is None:
            return None
        field = QgsField("qcv_uid", QC.QMetaType_Type_QString)
        field.setLength(40)
        try:
            if not bool(provider.addAttributes([field])):
                qcv_log(
                    "Création du champ qcv_uid refusée par le provider; "
                    "le style 40.20.3 sera conservé.",
                    "PDV/FOV",
                    "WARNING",
                )
                return None
        except Exception as exc:
            qcv_log(
                f"Création du champ qcv_uid impossible: {exc}",
                "PDV/FOV",
                "WARNING",
            )
            return None
        try:
            layer.updateFields()
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
        uid_name = _layer_field_name(layer, "qcv_uid")

    if uid_name is None:
        return None

    # Use the source provider index for non-editable layers. This avoids
    # starting/committing an edit session solely to initialise the cache key.
    provider = layer.dataProvider()
    provider_uid = _provider_field_name(layer, uid_name)
    provider_index = -1
    try:
        if provider is not None and provider_uid is not None:
            provider_index = provider.fields().indexOf(provider_uid)
    except Exception:
        provider_index = -1

    layer_index = layer.fields().indexOf(uid_name)
    if layer_index < 0:
        return None

    used = set()
    updates = {}
    try:
        features = list(layer.getFeatures())
    except Exception:
        return None

    for feature in features:
        try:
            raw_uid = feature[uid_name]
        except Exception:
            raw_uid = None
        uid = str(raw_uid or "").strip()
        if not uid or uid.lower() == "null" or uid in used:
            uid = _new_uid()
            while uid in used:
                uid = _new_uid()
            updates[int(feature.id())] = uid
        used.add(uid)

    if not updates:
        _hide_internal_fields(layer)
        return uid_name

    try:
        if layer.isEditable():
            for fid, uid in updates.items():
                if not layer.changeAttributeValue(fid, layer_index, uid):
                    raise RuntimeError(f"écriture qcv_uid impossible pour FID {fid}")
        elif provider is not None and provider_index >= 0:
            changes = {
                int(fid): {int(provider_index): uid}
                for fid, uid in updates.items()
            }
            if not bool(provider.changeAttributeValues(changes)):
                raise RuntimeError("écriture provider qcv_uid refusée")
        else:
            raise RuntimeError("champ qcv_uid non éditable")
    except Exception as exc:
        qcv_log(
            f"Initialisation des qcv_uid impossible: {exc}; "
            "le style 40.20.3 sera conservé.",
            "PDV/FOV",
            "WARNING",
        )
        return None

    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    _hide_internal_fields(layer)
    return uid_name


def _aux_join_prefix(auxiliary):
    try:
        prefix = str(auxiliary.joinInfo().prefix() or "")
    except Exception:
        prefix = ""
    return prefix or AUX_JOIN_PREFIX


def _joined_aux_name(auxiliary, field_name):
    return _aux_join_prefix(auxiliary) + field_name


def _ensure_auxiliary_layer(layer, uid_name):
    """Create/reuse one auxiliary layer and add the five WKT cache fields."""
    try:
        auxiliary = layer.auxiliaryLayer()
    except Exception:
        auxiliary = None

    if auxiliary is None:
        try:
            storage = QgsProject.instance().auxiliaryStorage()
            uid_index = layer.fields().indexOf(uid_name)
            if storage is None or uid_index < 0 or not storage.isValid():
                return None
            auxiliary = storage.createAuxiliaryLayer(
                layer.fields().field(uid_index),
                layer,
            )
            if auxiliary is None or not auxiliary.isValid():
                return None
            layer.setAuxiliaryLayer(auxiliary)
        except Exception as exc:
            qcv_log(
                f"Création du stockage auxiliaire FOV impossible: {exc}",
                "PDV/FOV",
                "WARNING",
            )
            return None

    try:
        if not auxiliary.isEditable():
            auxiliary.startEditing()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    try:
        existing = {str(name).lower() for name in auxiliary.fields().names()}
    except Exception:
        return None

    added = False
    for name in AUX_FIELDS:
        if name.lower() in existing:
            continue
        field = QgsField(name, QC.QMetaType_Type_QString)
        # SQLite/OGR TEXT field: no artificial 50-character limit for WKT.
        try:
            field.setTypeName("TEXT")
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
        try:
            if not bool(auxiliary.addAttribute(field)):
                qcv_log(
                    f"Champ auxiliaire {name} non créé.",
                    "PDV/FOV",
                    "WARNING",
                )
                return None
            added = True
        except Exception as exc:
            qcv_log(
                f"Champ auxiliaire {name} non créé: {exc}",
                "PDV/FOV",
                "WARNING",
            )
            return None

    if added:
        try:
            if not bool(auxiliary.save()):
                qcv_log(
                    "Enregistrement du schéma auxiliaire FOV impossible.",
                    "PDV/FOV",
                    "WARNING",
                )
                return None
        except Exception as exc:
            qcv_log(
                f"Enregistrement du schéma auxiliaire FOV impossible: {exc}",
                "PDV/FOV",
                "WARNING",
            )
            return None

    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    # Do not patch a renderer unless QGIS actually exposes every joined field.
    missing = []
    layer_names = {str(name) for name in layer.fields().names()}
    for name in AUX_FIELDS:
        joined = _joined_aux_name(auxiliary, name)
        if joined not in layer_names:
            missing.append(joined)
    if missing:
        qcv_log(
            "Champs auxiliaires FOV non joints à la couche: " + ", ".join(missing),
            "PDV/FOV",
            "WARNING",
        )
        return None

    _hide_internal_fields(layer)
    return auxiliary


def _auxiliary_target_key(auxiliary, source_feature):
    try:
        key_name = str(auxiliary.joinInfo().targetFieldName() or "")
    except Exception:
        key_name = ""
    if not key_name:
        return None
    try:
        value = source_feature[key_name]
    except Exception:
        return None
    if value is None or str(value).strip().upper() in ("", "NULL"):
        return None
    return value


def _auxiliary_feature_map(auxiliary):
    mapping = {}
    try:
        for feature in auxiliary.getFeatures():
            try:
                key = feature[AUX_KEY_FIELD]
            except Exception as exc:
                _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
                continue
            if key is not None:
                mapping[str(key)] = int(feature.id())
    except Exception:
        return {}
    return mapping


def _write_auxiliary_values(auxiliary, key, values, feature_map):
    if auxiliary is None or key is None:
        return False
    key_text = str(key)
    aux_fid = feature_map.get(key_text)

    indexes = {}
    for name, value in values.items():
        index = auxiliary.fields().indexOf(name)
        if index < 0:
            return False
        indexes[int(index)] = value

    if aux_fid is not None:
        try:
            return bool(auxiliary.changeAttributeValues(int(aux_fid), indexes))
        except Exception:
            return False

    try:
        feature = QgsFeature(auxiliary.fields())
        feature.setAttribute(AUX_KEY_FIELD, key)
        for index, value in indexes.items():
            feature.setAttribute(index, value)
        if not bool(auxiliary.addFeature(feature)):
            return False
        feature_map[key_text] = int(feature.id())
        return True
    except Exception:
        return False


def _normalise_hfov(value, is360=False):
    if is360:
        return 360.0
    try:
        return max(1.0, min(360.0, float(value)))
    except Exception:
        return 60.0


def _normalise_yaw(value):
    try:
        return float(value) % 360.0
    except Exception:
        return 0.0


def _finite_center_xy(center):
    """Return finite center coordinates, or None for an invalid PDV."""
    try:
        x = float(center.x())
        y = float(center.y())
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _arc_points(center, start_deg, sweep_deg, radius, max_segment_length):
    """Return a clockwise azimuth arc (0° north) as map-coordinate points."""
    center_xy = _finite_center_xy(center)
    if center_xy is None:
        return []
    center_x, center_y = center_xy
    try:
        radius = float(radius)
        sweep_deg = float(sweep_deg)
        max_segment_length = float(max_segment_length)
    except Exception:
        return []
    if not all(
        math.isfinite(value)
        for value in (radius, sweep_deg, max_segment_length)
    ):
        return []
    radius = max(0.01, radius)
    arc_length = abs(math.radians(sweep_deg) * radius)
    max_segment_length = max(0.5, max_segment_length)
    segments = max(1, int(math.ceil(arc_length / max_segment_length)))
    segments = min(segments, 720)
    points = []
    for index in range(segments + 1):
        fraction = index / float(segments)
        azimuth = math.radians(start_deg + sweep_deg * fraction)
        x = center_x + radius * math.sin(azimuth)
        y = center_y + radius * math.cos(azimuth)
        if not (math.isfinite(x) and math.isfinite(y)):
            return []
        points.append(QgsPointXY(x, y))
    return points


def _full_circle_geometry(center, outer_radius, inner_radius=0.0):
    """Build a full circle/ring without a Python-generated point loop."""
    center_xy = _finite_center_xy(center)
    if center_xy is None:
        return QgsGeometry()
    try:
        outer_radius = float(outer_radius)
        inner_radius = float(inner_radius)
    except Exception:
        return QgsGeometry()
    if not (math.isfinite(outer_radius) and math.isfinite(inner_radius)):
        return QgsGeometry()
    outer_radius = max(0.01, outer_radius)
    inner_radius = max(0.0, min(inner_radius, outer_radius))
    center_point = QgsPointXY(center_xy[0], center_xy[1])
    center_geometry = QgsGeometry.fromPointXY(center_point)
    outer = center_geometry.buffer(outer_radius, 24)
    if inner_radius <= 0.0:
        return outer
    inner = center_geometry.buffer(inner_radius, 24)
    return outer.difference(inner)


def _sector_geometry(center, yaw, hfov, outer_radius, inner_radius=0.0):
    """Build a straight-segment sector/ring, including exact 180° and 360°."""
    outer_radius = max(0.01, float(outer_radius))
    inner_radius = max(0.0, min(float(inner_radius), outer_radius))
    hfov = _normalise_hfov(hfov)
    yaw = _normalise_yaw(yaw)

    if hfov >= 359.999:
        return _full_circle_geometry(center, outer_radius, inner_radius)

    start = yaw - hfov / 2.0
    max_segment = max(0.5, outer_radius / 32.0)
    outer = _arc_points(center, start, hfov, outer_radius, max_segment)
    if not outer:
        return QgsGeometry()

    if inner_radius <= 0.0:
        ring = [QgsPointXY(center)] + outer + [QgsPointXY(center)]
    else:
        inner = _arc_points(
            center,
            start + hfov,
            -hfov,
            inner_radius,
            max(0.5, inner_radius / 32.0),
        )
        if not inner:
            return QgsGeometry()
        ring = outer + inner
        if ring[0] != ring[-1]:
            ring.append(ring[0])
    return QgsGeometry.fromPolygonXY([ring])


def _axis_geometry(center, yaw, distance):
    angle = math.radians(_normalise_yaw(yaw))
    distance = max(0.01, float(distance))
    end = QgsPointXY(
        float(center.x()) + distance * math.sin(angle),
        float(center.y()) + distance * math.cos(angle),
    )
    return QgsGeometry.fromPolylineXY([QgsPointXY(center), end])


def _transform_geometry(geometry, source_crs, target_crs):
    if geometry is None or geometry.isEmpty():
        return QgsGeometry()
    output = QgsGeometry(geometry)
    if source_crs == target_crs:
        return output
    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    output.transform(transform)
    return output


def _point_in_work_crs(layer, feature, work_crs):
    geometry = feature.geometry()
    if geometry is None or geometry.isEmpty():
        return None
    point = QgsPointXY(geometry.asPoint())
    source_crs = layer.crs()
    if source_crs.isValid() and source_crs != work_crs:
        transform = QgsCoordinateTransform(source_crs, work_crs, QgsProject.instance())
        point = transform.transform(point)
    return point


def _geometry_to_wkt(geometry):
    if geometry is None or geometry.isEmpty():
        return ""
    try:
        return geometry.asWkt(8)
    except Exception:
        return geometry.asWkt()


def _build_values(
    layer,
    feature,
    work_crs,
    yaw,
    hfov,
    full_range,
    symbol_range,
    projection,
    is360=False,
):
    center = _point_in_work_crs(layer, feature, work_crs)
    if center is None:
        return {name: "" for name in AUX_FIELDS}

    yaw = _normalise_yaw(yaw)
    hfov = _normalise_hfov(hfov, is360=is360)
    full_range = 200.0 if float(full_range) <= 0.0 else float(full_range)
    symbol_range = 200.0 if float(symbol_range) <= 0.0 else float(symbol_range)
    ring_width = max(8.0, symbol_range * 0.06)

    full = _sector_geometry(center, yaw, hfov, full_range)
    symbol = _sector_geometry(center, yaw, hfov, symbol_range)
    ring = _sector_geometry(
        center,
        yaw,
        hfov,
        symbol_range + ring_width,
        symbol_range,
    )
    axis = _axis_geometry(center, yaw, symbol_range)

    projection = str(projection or "").strip().upper()
    equi = QgsGeometry()
    if hfov >= 359.999 and projection in ("EQUIRECT", "EQUIRECTANGULAR"):
        equi_radius = max(5.0, symbol_range * 0.11)
        equi = _sector_geometry(center, 0.0, 360.0, equi_radius)

    layer_crs = layer.crs()
    geometries = (full, symbol, ring, axis, equi)
    values = {}
    for name, geometry in zip(AUX_FIELDS, geometries):
        values[name] = _geometry_to_wkt(
            _transform_geometry(geometry, work_crs, layer_crs)
        )
    return values


def _saved_feature_values(layer, feature, symbol_range, work_crs):
    projection = str(
        _feature_value(layer, feature, "qcv_proj", "PINHOLE") or "PINHOLE"
    ).strip().upper()
    saved_hfov = _feature_value(layer, feature, "qcv_hfov", 0.0)
    try:
        saved_hfov = float(saved_hfov or 0.0)
    except Exception:
        saved_hfov = 0.0
    if saved_hfov > 0.0:
        hfov = saved_hfov
    elif int(_feature_value(layer, feature, "qcv_360", 0) or 0) == 1:
        hfov = 360.0
    else:
        hfov = 60.0

    return _build_values(
        layer=layer,
        feature=feature,
        work_crs=work_crs,
        yaw=_feature_value(layer, feature, "qcv_yaw", 0.0),
        hfov=hfov,
        full_range=_feature_value(layer, feature, "qcv_mdst", 200.0),
        symbol_range=symbol_range,
        projection=projection,
        is360=hfov >= 359.999,
    )


def _work_crs(self):
    try:
        return self._camera_metric_project_crs()
    except Exception:
        return None


def _symbol_range(self):
    try:
        value = float(self.d_symdist.value())
    except Exception:
        value = 200.0
    return 200.0 if value <= 0.0 else value


def _global_signature(layer, work_crs, symbol_range):
    try:
        work_id = str(work_crs.authid() or work_crs.toWkt())
    except Exception:
        work_id = ""
    try:
        layer_id = str(layer.crs().authid() or layer.crs().toWkt())
    except Exception:
        layer_id = ""
    return (work_id, layer_id, round(float(symbol_range), 6))


def _clear_live_variables(layer):
    try:
        from qgis.core import QgsExpressionContextUtils

        for variable in LIVE_VARIABLES.values():
            QgsExpressionContextUtils.setLayerVariable(layer, variable, "")
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
def _validate_cache(layer, auxiliary):
    """Validate joined fields and at least one cached geometry before patching."""
    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    names = set(layer.fields().names())
    joined_names = [_joined_aux_name(auxiliary, name) for name in AUX_FIELDS]
    if any(name not in names for name in joined_names):
        return False

    try:
        features = list(layer.getFeatures())
    except Exception:
        return False
    if not features:
        return True

    full_name = joined_names[0]
    for feature in features:
        try:
            text = str(feature[full_name] or "").strip()
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
            continue
        if not text:
            continue
        try:
            geometry = QgsGeometry.fromWkt(text)
            if geometry is not None and not geometry.isEmpty():
                return True
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
            continue
    return False


def qcv_fov_layer_ready(layer):
    """Fast readiness check; full WKT validation is done during preparation."""
    if not isinstance(layer, QgsVectorLayer):
        return False
    try:
        if int(layer.customProperty(FOV_READY_PROPERTY, 0) or 0) != FOV_CACHE_VERSION:
            return False
        auxiliary = layer.auxiliaryLayer()
        if auxiliary is None:
            return False
        layer.updateFields()
        names = set(layer.fields().names())
        return all(
            _joined_aux_name(auxiliary, name) in names
            for name in AUX_FIELDS
        )
    except Exception:
        return False


def _rebuild_layer(self, layer, auxiliary, work_crs, symbol_range, save=True):
    feature_map = _auxiliary_feature_map(auxiliary)
    try:
        features = list(layer.getFeatures())
    except Exception as exc:
        qcv_log(f"Lecture des PDV impossible: {exc}", "PDV/FOV", "WARNING")
        return False

    previous_guard = bool(getattr(self, "_qcv_fov_cache_write", False))
    self._qcv_fov_cache_write = True
    ok = True
    count = 0
    try:
        for feature in features:
            key = _auxiliary_target_key(auxiliary, feature)
            if key is None:
                ok = False
                continue
            try:
                values = _saved_feature_values(layer, feature, symbol_range, work_crs)
                if not _write_auxiliary_values(auxiliary, key, values, feature_map):
                    ok = False
                else:
                    count += 1
            except Exception as exc:
                qcv_log(
                    f"PDV {feature.id()}: calcul FOV impossible: {exc}",
                    "PDV/FOV",
                    "WARNING",
                )
                ok = False

        if save and auxiliary.isModified():
            try:
                if not bool(auxiliary.save()):
                    ok = False
            except Exception:
                ok = False
    finally:
        self._qcv_fov_cache_write = previous_guard

    try:
        layer.updateFields()
    except Exception as exc:
        _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    if not _validate_cache(layer, auxiliary):
        qcv_log(
            "Cache FOV créé mais non lisible depuis la couche; "
            "renderer 40.20.3 conservé.",
            "PDV/FOV",
            "WARNING",
        )
        return False

    qcv_log(f"Cache FOV validé pour {count} PDV.", "PDV/FOV", "INFO")
    return ok


def qcv_fov_prepare_layer(self, layer, rebuild=True):
    """Prepare/reuse a cache without altering the active renderer on failure."""
    if not isinstance(layer, QgsVectorLayer):
        return False
    work_crs = _work_crs(self)
    if work_crs is None:
        return False

    uid_name = _ensure_uid_field(layer)
    if uid_name is None:
        return False
    auxiliary = _ensure_auxiliary_layer(layer, uid_name)
    if auxiliary is None:
        return False

    cache_valid = _validate_cache(layer, auxiliary)
    if rebuild or not cache_valid:
        try:
            layer.setCustomProperty(FOV_READY_PROPERTY, 0)
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
        if not _rebuild_layer(
            self,
            layer,
            auxiliary,
            work_crs,
            _symbol_range(self),
            save=True,
        ):
            return False
    else:
        qcv_log(
            "Cache FOV existant réutilisé; aucune reconstruction nécessaire.",
            "PDV/FOV",
            "INFO",
        )

    try:
        layer.setCustomProperty(FOV_READY_PROPERTY, FOV_CACHE_VERSION)
    except Exception:
        return False

    signatures = getattr(self, "_qcv_fov_global_signatures", None)
    if not isinstance(signatures, dict):
        signatures = {}
        self._qcv_fov_global_signatures = signatures
    signatures[str(layer.id())] = _global_signature(
        layer,
        work_crs,
        _symbol_range(self),
    )
    _hide_internal_fields(layer)
    return True


def _geometry_expression(live_variable, joined_field):
    return (
        "if(@fov_show_all = 1 OR @qcv_current_fid = $id, "
        "with_variable('qcv_wkt', "
        f"if(@qcv_current_fid = $id AND coalesce(@{live_variable}, '') != '', "
        f"@{live_variable}, coalesce(\"{joined_field}\", '')), "
        "if(@qcv_wkt = '', NULL, geom_from_wkt(@qcv_wkt))), NULL)"
    )


def _renderer_generators(layer):
    """Return top-level GeometryGenerator symbol layers from the renderer."""
    try:
        renderer = layer.renderer()
        symbol = renderer.symbol() if renderer is not None else None
    except Exception:
        symbol = None
    if symbol is None:
        return []

    generators = []
    try:
        for index in range(symbol.symbolLayerCount()):
            symbol_layer = symbol.symbolLayer(index)
            if isinstance(symbol_layer, QgsGeometryGeneratorSymbolLayer):
                generators.append(symbol_layer)
    except Exception:
        return []
    return generators


def _generator_slot(generator):
    """Identify a QCALVIEW FOV generator without relying on layer count/order."""
    try:
        expression = str(generator.geometryExpression() or "")
    except Exception:
        expression = ""
    lowered = expression.lower()

    # Lightweight expressions are unambiguous.
    for field_name, live_variable in LIVE_VARIABLES.items():
        if live_variable.lower() in lowered:
            return field_name

    # Legacy 40.20.3 expressions: identify them by their specific operation.
    if "make_line(" in lowered:
        return "qcv_fov_axis"
    if "equirectangular" in lowered and "0.11" in lowered:
        return "qcv_fov_equi"
    if "difference(" in lowered or "inner_radius:=@r" in lowered:
        return "qcv_fov_ring"
    if "fov_range_full" in lowered:
        return "qcv_fov_full"
    if "fov_symrange" in lowered and "wedge_buffer(" in lowered:
        return "qcv_fov_symbol"
    return None


def _renderer_generator_map(layer):
    """Map known QCALVIEW FOV roles to the active generator symbol layers."""
    mapped = {}
    unknown = 0
    for generator in _renderer_generators(layer):
        slot = _generator_slot(generator)
        if slot and slot not in mapped:
            mapped[slot] = generator
        else:
            unknown += 1
    return mapped, unknown


def _legacy_geometry_expressions(automatic=True):
    """Read original 40.20.3 geometry expressions, keyed by FOV role."""
    qml_name = "STYLE-PDV.qml" if bool(automatic) else "STYLE-MOD.qml"
    qml_path = os.path.join(os.path.dirname(__file__), "style", qml_name)
    try:
        with open(qml_path, "r", encoding="utf-8") as handle:
            qml_text = handle.read()
        document = QDomDocument()
        parse_result = document.setContent(qml_text)
        parse_ok = bool(parse_result[0]) if isinstance(parse_result, tuple) else bool(parse_result)
        if not parse_ok:
            raise ValueError("QML XML parsing failed")
    except Exception as exc:
        qcv_log(
            f"Lecture du renderer de secours impossible: {exc}",
            "PDV/FOV",
            "WARNING",
        )
        return {}

    expressions = {}
    layers = document.elementsByTagName("layer")
    for layer_index in range(layers.count()):
        node = layers.at(layer_index).toElement()
        if node.isNull() or node.attribute("class") != "GeometryGenerator":
            continue
        expression = None
        options = node.elementsByTagName("Option")
        for option_index in range(options.count()):
            option = options.at(option_index).toElement()
            if option.isNull() or option.attribute("name") != "geometryModifier":
                continue
            expression = option.attribute("value")
            if not expression:
                expression = option.text()
            break
        if expression is None:
            continue

        # The same recognition rules can be used on the QML text itself.
        text = str(expression)
        lowered = text.lower()
        if "make_line(" in lowered:
            slot = "qcv_fov_axis"
        elif "equirectangular" in lowered and "0.11" in lowered:
            slot = "qcv_fov_equi"
        elif "difference(" in lowered or "inner_radius:=@r" in lowered:
            slot = "qcv_fov_ring"
        elif "fov_range_full" in lowered:
            slot = "qcv_fov_full"
        elif "fov_symrange" in lowered and "wedge_buffer(" in lowered:
            slot = "qcv_fov_symbol"
        else:
            slot = None
        if slot:
            expressions[slot] = text
    return expressions


def qcv_fov_restore_legacy_renderer(self, layer, automatic=True):
    """Restore available 40.20.3 FOV expressions, preserving visual styling."""
    if not isinstance(layer, QgsVectorLayer):
        return False
    generators, unknown = _renderer_generator_map(layer)
    expressions = _legacy_geometry_expressions(automatic=automatic)
    required = {
        "qcv_fov_full",
        "qcv_fov_symbol",
        "qcv_fov_ring",
        "qcv_fov_axis",
    }
    if not required.issubset(generators) or not required.issubset(expressions):
        qcv_log(
            "Renderer FOV de secours incomplet; aucune modification appliquée.",
            "PDV/FOV",
            "WARNING",
        )
        return False
    try:
        restored = 0
        for slot, generator in generators.items():
            expression = expressions.get(slot)
            if expression is None:
                continue
            generator.setGeometryExpression(expression)
            restored += 1
        try:
            layer.setCustomProperty(FOV_READY_PROPERTY, 0)
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
        _clear_live_variables(layer)
        layer.triggerRepaint()
        qcv_log(
            f"Renderer géométrique 40.20.3 restauré ({restored} GeometryGenerator).",
            "PDV/FOV",
            "INFO",
        )
        return restored >= 4
    except Exception as exc:
        qcv_log(
            f"Restauration du renderer 40.20.3 impossible: {exc}",
            "PDV/FOV",
            "WARNING",
        )
        return False


def qcv_fov_patch_renderer(self, layer):
    """Patch existing QCALVIEW generators after cache validation."""
    if not qcv_fov_layer_ready(layer):
        return False
    auxiliary = layer.auxiliaryLayer()
    joined_fields = {
        name: _joined_aux_name(auxiliary, name)
        for name in AUX_FIELDS
    }

    generators, unknown = _renderer_generator_map(layer)
    required = {
        "qcv_fov_full",
        "qcv_fov_symbol",
        "qcv_fov_ring",
        "qcv_fov_axis",
    }
    if not required.issubset(generators):
        qcv_log(
            f"Renderer PDV incompatible: {len(generators)} générateurs QCALVIEW "
            f"reconnus, {unknown} inconnu(s); renderer 40.20.3 conservé.",
            "PDV/FOV",
            "WARNING",
        )
        return False

    try:
        patched = 0
        for slot, generator in generators.items():
            joined = joined_fields.get(slot)
            live_variable = LIVE_VARIABLES.get(slot)
            if not joined or not live_variable:
                continue
            generator.setGeometryExpression(
                _geometry_expression(live_variable, joined)
            )
            patched += 1
        layer.triggerRepaint()
        qcv_log(
            f"Renderer FOV allégé activé ({patched} GeometryGenerator pré-calculés).",
            "PDV/FOV",
            "INFO",
        )
        return patched >= 4
    except Exception as exc:
        qcv_log(
            f"Migration du renderer FOV impossible: {exc}",
            "PDV/FOV",
            "WARNING",
        )
        return False

def qcv_fov_rebuild_layer(self, layer, save=True):
    if not isinstance(layer, QgsVectorLayer):
        return False
    work_crs = _work_crs(self)
    auxiliary = layer.auxiliaryLayer()
    if work_crs is None or auxiliary is None:
        return False
    ok = _rebuild_layer(
        self,
        layer,
        auxiliary,
        work_crs,
        _symbol_range(self),
        save=save,
    )
    if ok:
        try:
            layer.setCustomProperty(FOV_READY_PROPERTY, FOV_CACHE_VERSION)
        except Exception as exc:
            _qcv_suppress(exc, "core/_fov_geometry.py:suppressed")
    return ok


def qcv_fov_update_feature(self, layer, fid, save=True):
    if not qcv_fov_layer_ready(layer):
        return False
    try:
        feature = layer.getFeature(int(fid))
    except Exception:
        return False
    if feature is None or not feature.isValid():
        return False

    auxiliary = layer.auxiliaryLayer()
    work_crs = _work_crs(self)
    if auxiliary is None or work_crs is None:
        return False
    key = _auxiliary_target_key(auxiliary, feature)
    if key is None:
        return False

    previous_guard = bool(getattr(self, "_qcv_fov_cache_write", False))
    self._qcv_fov_cache_write = True
    try:
        values = _saved_feature_values(layer, feature, _symbol_range(self), work_crs)
        feature_map = _auxiliary_feature_map(auxiliary)
        if not _write_auxiliary_values(auxiliary, key, values, feature_map):
            return False
        if save and auxiliary.isModified():
            return bool(auxiliary.save())
        return True
    finally:
        self._qcv_fov_cache_write = previous_guard


def _live_signature(feature, yaw, hfov, full_range, symbol_range, projection, is360):
    try:
        point = feature.geometry().asPoint()
        point_key = (round(float(point.x()), 9), round(float(point.y()), 9))
    except Exception:
        point_key = (None, None)
    return (
        int(feature.id()),
        point_key,
        round(float(yaw) % 360.0, 9),
        round(float(hfov), 9),
        round(float(full_range), 6),
        round(float(symbol_range), 6),
        str(projection or "").strip().upper(),
        bool(is360),
    )


def qcv_fov_sync_live(
    self,
    layer,
    yaw,
    hfov,
    full_range,
    symbol_range,
    projection,
    is360=False,
):
    """Update precomputed WKT variables for the current feature only."""
    if not qcv_fov_layer_ready(layer):
        return False

    work_crs = _work_crs(self)
    if work_crs is None:
        _clear_live_variables(layer)
        return False

    layer_id = str(layer.id())
    global_signature = _global_signature(layer, work_crs, symbol_range)
    global_signatures = getattr(self, "_qcv_fov_global_signatures", None)
    if not isinstance(global_signatures, dict):
        global_signatures = {}
        self._qcv_fov_global_signatures = global_signatures
    if global_signatures.get(layer_id) != global_signature:
        if not qcv_fov_rebuild_layer(self, layer, save=True):
            return False
        global_signatures[layer_id] = global_signature

    current_fid = getattr(self, "_camera_current_fid", None)
    if current_fid is None:
        _clear_live_variables(layer)
        return False
    try:
        feature = layer.getFeature(int(current_fid))
    except Exception:
        feature = None
    if feature is None or not feature.isValid():
        _clear_live_variables(layer)
        return False

    signature = _live_signature(
        feature,
        yaw,
        hfov,
        full_range,
        symbol_range,
        projection,
        is360,
    )
    live_signatures = getattr(self, "_qcv_fov_live_signatures", None)
    if not isinstance(live_signatures, dict):
        live_signatures = {}
        self._qcv_fov_live_signatures = live_signatures
    if live_signatures.get(layer_id) == signature:
        return True

    try:
        values = _build_values(
            layer=layer,
            feature=feature,
            work_crs=work_crs,
            yaw=yaw,
            hfov=hfov,
            full_range=full_range,
            symbol_range=symbol_range,
            projection=projection,
            is360=is360,
        )
    except Exception as exc:
        qcv_log(f"Calcul FOV Live impossible: {exc}", "PDV/FOV", "WARNING")
        _clear_live_variables(layer)
        return False

    try:
        from qgis.core import QgsExpressionContextUtils

        for name, value in values.items():
            QgsExpressionContextUtils.setLayerVariable(
                layer,
                LIVE_VARIABLES[name],
                value,
            )
        live_signatures[layer_id] = signature
        return True
    except Exception as exc:
        qcv_log(f"Variables FOV Live impossibles: {exc}", "PDV/FOV", "WARNING")
        return False
