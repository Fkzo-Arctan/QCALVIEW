



from __future__ import annotations
from ._exceptions import qcv_suppress_exception as _qcv_suppress
from ._i18n import tr
from ._compat import QC, dialog_exec

import math
import os
from typing import List, Tuple, Optional, Dict, Any

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QLineEdit,
    QDoubleSpinBox, QLabel, QMessageBox, QCheckBox
)
from qgis.core import (
    QgsProject, QgsField, QgsFeature, QgsGeometry, QgsVectorLayer,
    QgsCoordinateTransform, QgsWkbTypes, QgsPointXY, QgsMapLayerProxyModel
)
from qgis.gui import QgsMapLayerComboBox

from ._schematic_math import occlusion_hits_on_polylines
from ._schematic_symbols import get_symbol_library


def _finite_float(value, default=None):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _feature_height(feature, field_name: str, default: float) -> float:
    try:
        if field_name and field_name in feature.fields().names():
            v = _finite_float(feature[field_name], None)
            if v is not None:
                return v
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:41")
    return float(default)


def _target_sample_points(geom, gtype: int):
    
    pts = []
    try:
        if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
            pts = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
        elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
            lines = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
            for line in lines:
                if not line:
                    continue
                step = max(1, int(len(line) / 200))
                pts.extend([p for i, p in enumerate(line) if i % step == 0])
        elif gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                ring = poly[0]
                step = max(1, int(len(ring) / 250))
                pts.extend([p for i, p in enumerate(ring) if i % step == 0])
                try:
                    pts.append(geom.centroid().asPoint())
                except Exception as _qcv_exc:
                    _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:69")
    except Exception:
        return []
    return pts


def _line_parts(geom):
    try:
        return geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    except Exception:
        return []


def _find_qcalview_style(dock, layer):
    if layer is None:
        return None
    try:
        lid = layer.id()
    except Exception:
        lid = None
    for sty in list(getattr(dock, "layer_styles", []) or []):
        try:
            lyr = getattr(sty, "layer", None)
            if lyr is layer or (lid is not None and lyr is not None and lyr.id() == lid):
                return sty
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:95")
            continue
    return None


def _style_explicit_layer_height(feature, style):
    
    if style is None:
        return None
    fld = str(getattr(style, "height_field_override", "") or "").strip()
    if fld:
        try:
            if fld in feature.fields().names():
                v = _finite_float(feature[fld], None)
                if v is not None:
                    return v
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:111")
    val = getattr(style, "default_height_override", None)
    return _finite_float(val, None) if val is not None else None


def _symbol_param_value(feature, style, definition: Dict[str, Any], name: str, fallback=None):
    
    try:
        overrides = dict(getattr(style, "schematic_params", {}) or {})
    except Exception:
        overrides = {}
    if name in overrides:
        return overrides[name]
    raw = (definition.get("parameters", {}) or {}).get(name, fallback)
    if not isinstance(raw, dict):
        return raw
    if raw.get("source") == "layer_height":
        explicit = _style_explicit_layer_height(feature, style)
        if explicit is not None:
            return explicit
        return raw.get("default", fallback)
    fld = str(raw.get("field", "") or "").strip()
    if fld:
        try:
            if fld in feature.fields().names():
                val = feature[fld]
                if val not in (None, ""):
                    return val
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:140")
    return raw.get("default", fallback)


def _schematic_target_height(dock, target_layer, feature) -> Tuple[Optional[float], str]:
    
    style = _find_qcalview_style(dock, target_layer)
    if style is None or not bool(getattr(style, "schematic_enabled", False)):
        return None, ""
    symbol_id = str(getattr(style, "schematic_symbol_id", "") or "").strip()
    if not symbol_id:
        return None, ""
    try:
        plugin_dir = os.path.dirname(os.path.dirname(__file__))
        definition = get_symbol_library(plugin_dir).get(symbol_id)
    except Exception:
        definition = None
    if not definition:
        return None, ""
    generator = str(definition.get("generator", "") or "")

    if generator == "wind_turbine":
        hub = _finite_float(_symbol_param_value(feature, style, definition, "hub_height_m", 120.0), None)
        rotor = _finite_float(_symbol_param_value(feature, style, definition, "rotor_diameter_m", 160.0), None)
        if hub is not None and rotor is not None:
            return max(0.0, hub + 0.5 * rotor), f"AVR {symbol_id}: moyeu + rayon"

    if generator in ("billboard_svg", "vegetation_ribbon", "vegetation_adaptive"):
        h = _finite_float(_symbol_param_value(feature, style, definition, "height_m", None), None)
        if h is not None:
            return max(0.0, h), f"AVR {symbol_id}: hauteur"

    if generator == "pv_surface":
        low = _finite_float(_symbol_param_value(feature, style, definition, "low_height_m", 0.8), None)
        width = _finite_float(_symbol_param_value(feature, style, definition, "table_width_m", 4.5), None)
        tilt = _finite_float(_symbol_param_value(feature, style, definition, "tilt_deg", 20.0), None)
        if None not in (low, width, tilt):
            high = low + abs(width * math.sin(math.radians(tilt)))
            return max(0.0, high), f"AVR {symbol_id}: point haut table"

    if generator == "extrusion":
        h = _style_explicit_layer_height(feature, style)
        if h is not None:
            return max(0.0, h), f"AVR {symbol_id}: extrusion"

    
    params = definition.get("parameters", {}) or {}
    if "height_m" in params:
        h = _finite_float(_symbol_param_value(feature, style, definition, "height_m", None), None)
        if h is not None:
            return max(0.0, h), f"AVR {symbol_id}: hauteur"
    return None, ""


def _target_height(dock, target_layer, feature, explicit_field: str, default_h: float, use_avr: bool):
    
    fld = str(explicit_field or "").strip()
    if fld:
        try:
            if fld in feature.fields().names():
                v = _finite_float(feature[fld], None)
                if v is not None:
                    return max(0.0, v), f"champ {fld}"
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:204")
    if use_avr:
        h, src = _schematic_target_height(dock, target_layer, feature)
        if h is not None:
            return h, src
    return max(0.0, float(default_h)), "défaut"


def _hedge_min_render_ratio(dock, hedge_layer, symbol_id=None, params_override=None):
    
    style = _find_qcalview_style(dock, hedge_layer)
    sid = str(symbol_id or getattr(style, "schematic_symbol_id", "") or "").strip()
    if not sid:
        return 1.0, "aucun motif AVR détecté", True
    try:
        plugin_dir = os.path.dirname(os.path.dirname(__file__))
        definition = get_symbol_library(plugin_dir).get(sid)
    except Exception:
        definition = None
    if not definition:
        return 1.0, "motif AVR introuvable", True
    generator = str(definition.get("generator", "") or "")
    overrides = dict(params_override or getattr(style, "schematic_params", {}) or {})

    def pval(name, fallback):
        if name in overrides:
            return overrides[name]
        raw = (definition.get("parameters", {}) or {}).get(name, fallback)
        if isinstance(raw, dict):
            return raw.get("default", fallback)
        return raw

    if generator == "vegetation_adaptive":
        line_mode = str(pval("line_mode", "alignment") or "alignment").strip().lower()
        if line_mode not in ("ribbon", "continuous", "ruban", "haie"):
            return 1.0, "motif adaptatif en alignement d’individus", False
    elif generator != "vegetation_ribbon":
        return 1.0, f"motif {sid} non continu", False

    irr = max(0.0, min(0.45, _finite_float(pval("irregularity", 0.12), 0.12)))
    crown_min = max(0.2, min(1.0, _finite_float(pval("crown_min_ratio", 0.72), 0.72)))
    ratio = max(crown_min, 1.0 - irr)
    return max(0.05, min(1.0, ratio)), f"cime AVR min. {ratio:.3f} (irrégularité {irr:.3f})", True


def _diagnostic_layer(cam_crs, hedge_layer, target_layer, records):
    
    if not records:
        return None
    authid = ""
    try:
        authid = cam_crs.authid()
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:257")
    uri = "Point"
    if authid:
        uri += f"?crs={authid}"
    layer = QgsVectorLayer(uri, tr("QCALVIEW - diagnostic occultation"), "memory")
    if not layer.isValid():
        return None
    provider = layer.dataProvider()
    fields = [
        QgsField("hedge_fid", QC.QMetaType_Type_QString, "string", 40),
        QgsField("target_fid", QC.QMetaType_Type_QString, "string", 40),
        QgsField("target_h", QC.QMetaType_Type_Double, "double", 12, 3),
        QgsField("h_geom", QC.QMetaType_Type_Double, "double", 12, 3),
        QgsField("h_nominal", QC.QMetaType_Type_Double, "double", 12, 3),
        QgsField("crown_ratio", QC.QMetaType_Type_Double, "double", 8, 4),
        QgsField("z_ground", QC.QMetaType_Type_Double, "double", 14, 3),
        QgsField("z_los", QC.QMetaType_Type_Double, "double", 14, 3),
        QgsField("dist_cam", QC.QMetaType_Type_Double, "double", 14, 3),
        QgsField("part", QC.QMetaType_Type_Int, "int"),
        QgsField("segment", QC.QMetaType_Type_Int, "int"),
        QgsField("first_hit", QC.QMetaType_Type_Int, "int"),
        QgsField("controls_h", QC.QMetaType_Type_Int, "int"),
        QgsField("h_source", QC.QMetaType_Type_QString, "string", 100),
    ]
    provider.addAttributes(fields); layer.updateFields()
    features = []
    for rec in records:
        f = QgsFeature(layer.fields())
        x, y = rec["xy"]
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y))))
        f.setAttributes([
            str(rec.get("hedge_fid", "")), str(rec.get("target_fid", "")),
            float(rec.get("target_h", 0.0)), float(rec.get("height_m", 0.0)),
            float(rec.get("height_nominal_m", rec.get("height_m", 0.0))),
            float(rec.get("crown_ratio", 1.0)),
            float(rec.get("ground_z", 0.0)), float(rec.get("los_z", 0.0)),
            float(rec.get("distance_camera_m", 0.0)), int(rec.get("part_index", -1)),
            int(rec.get("segment_index", -1)), int(bool(rec.get("first_hit", False))),
            int(bool(rec.get("controls_h", False))), str(rec.get("height_source", "")),
        ])
        features.append(f)
    provider.addFeatures(features); layer.updateExtents()
    try:
        key = f"{hedge_layer.id()}::{target_layer.id()}"
        layer.setCustomProperty("qcalview/occ_diag_key", key)
        project = QgsProject.instance()
        for old in list(project.mapLayers().values()):
            try:
                if old.id() != layer.id() and old.customProperty("qcalview/occ_diag_key", "") == key:
                    project.removeMapLayer(old.id())
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:308")
        project.addMapLayer(layer)
    except Exception:
        QgsProject.instance().addMapLayer(layer)
    return layer


def calculate_hedge_occlusion_dialog(
    dock, hedge_layer, initial_output_field="qcv_h_req",
    hedge_symbol_id=None, hedge_params=None,
):
    
    if hedge_layer is None or QgsWkbTypes.geometryType(hedge_layer.wkbType()) != QC.QgsWkbTypes_GeometryType_LineGeometry:
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr("La couche de haie doit être une couche linéaire."))
        return None

    dlg = QDialog(dock)
    dlg.setWindowTitle(tr("Calculer la hauteur d'occultation"))
    dlg.resize(590, 430)
    root = QVBoxLayout(dlg)
    info = QLabel(
        tr("Calcule la hauteur nécessaire à l'intersection exacte entre chaque ligne de visée "
        "PDV→cible et la haie. Pour une même cible, seule la première intersection rencontrée "
        "depuis le PDV est utilisée.")
    )
    info.setWordWrap(True); root.addWidget(info)
    form = QFormLayout(); root.addLayout(form)
    cmb_target = QgsMapLayerComboBox(); cmb_target.setFilters(QC.QgsMapLayerProxyModel_Filter_VectorLayer)
    try:
        cmb_target.setExceptedLayerList([hedge_layer])
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:339")
    le_target_hfield = QLineEdit(); le_target_hfield.setPlaceholderText(tr("optionnel — prioritaire sur le motif AVR"))
    cb_avr_height = QCheckBox(tr("Utiliser automatiquement la hauteur du motif QCALVIEW si disponible"))
    cb_avr_height.setChecked(True)
    sp_target_h = QDoubleSpinBox(); sp_target_h.setRange(0.0, 1000.0); sp_target_h.setDecimals(2); sp_target_h.setValue(2.0); sp_target_h.setSuffix(tr(" m"))
    sp_margin = QDoubleSpinBox(); sp_margin.setRange(0.0, 50.0); sp_margin.setDecimals(2); sp_margin.setValue(0.20); sp_margin.setSuffix(tr(" m"))
    le_out = QLineEdit(str(initial_output_field or "qcv_h_req"))
    cb_compensate = QCheckBox(tr("Compenser l’irrégularité de cime du ruban AVR"))
    cb_compensate.setChecked(True)
    cb_diag = QCheckBox(tr("Créer/actualiser la couche de diagnostic des intersections"))
    cb_diag.setChecked(True)
    form.addRow(tr("Couche projet à masquer"), cmb_target)
    form.addRow(tr("Champ hauteur cible"), le_target_hfield)
    form.addRow(tr("Hauteur AVR"), cb_avr_height)
    form.addRow(tr("Hauteur cible par défaut"), sp_target_h)
    form.addRow(tr("Marge géométrique"), sp_margin)
    form.addRow(tr("Champ résultat dans la haie"), le_out)
    form.addRow(tr("Garantie visuelle"), cb_compensate)
    form.addRow(tr("Contrôle"), cb_diag)
    note = QLabel(
        tr("Priorité hauteur : champ explicite > motif AVR QCALVIEW > valeur par défaut. "
        "Pour une éolienne AVR, la cible est le bout de pale théorique (hauteur moyeu + rayon). "
        "Si la compensation AVR est activée, qcv_h_req est augmenté pour que les creux de la "
        "cime irrégulière restent au-dessus de la ligne de visée. Le champ résultat reste une "
        "hauteur unique par entité de haie : si une longue entité intercepte plusieurs cibles, "
        "le maximum est appliqué à toute cette entité.")
    )
    note.setWordWrap(True); root.addWidget(note)
    bb = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel); root.addWidget(bb)
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted:
        return None

    target_layer = cmb_target.currentLayer()
    if target_layer is None:
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr("Sélectionnez une couche cible."))
        return None
    out_field = le_out.text().strip() or "qcv_h_req"

    
    try:
        cam_layer = dock.cmb_camera.currentLayer()
        cam_feat = dock._camera_current_feature()
        if cam_layer is None or cam_feat is None or not cam_feat.isValid():
            raise RuntimeError("PDV courant indisponible")
        cam_pt, cam_crs = dock._camera_point_in_work_crs(cam_feat)
        if cam_pt is None or cam_crs is None:
            raise RuntimeError("CRS projet non métrique")
        dem = dock.cmb_dem.currentLayer() if hasattr(dock, "cmb_dem") else None
        z_sampler = None
        if dem is not None and dem.isValid():
            z_sampler, _ = dock._make_z_sampler(dem, cam_crs)
        cam_ground = float(z_sampler(cam_pt)) if z_sampler is not None else 0.0
        cam_z = cam_ground + float(dock.d_camheight.value())
    except Exception as e:
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr(f"Impossible de déterminer le PDV courant : {e}"))
        return None

    def ground_z(x, y):
        try:
            return float(z_sampler(QgsPointXY(float(x), float(y)))) if z_sampler is not None else 0.0
        except Exception:
            return 0.0

    try:
        tr_hedge = None if hedge_layer.crs() == cam_crs else QgsCoordinateTransform(hedge_layer.crs(), cam_crs, QgsProject.instance())
        tr_target = None if target_layer.crs() == cam_crs else QgsCoordinateTransform(target_layer.crs(), cam_crs, QgsProject.instance())
    except Exception as e:
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr(f"Transformation CRS impossible : {e}"))
        return None

    target_hfield = le_target_hfield.text().strip()
    target_default = float(sp_target_h.value())
    use_avr = bool(cb_avr_height.isChecked())
    margin = float(sp_margin.value())
    create_diag = bool(cb_diag.isChecked())
    compensate_crown = bool(cb_compensate.isChecked())
    crown_ratio, crown_desc, continuous_screen = _hedge_min_render_ratio(
        dock, hedge_layer, hedge_symbol_id, hedge_params
    )
    effective_ratio = crown_ratio if (compensate_crown and continuous_screen) else 1.0
    target_gtype = QgsWkbTypes.geometryType(target_layer.wkbType())

    
    targets = []
    source_counts = {}
    for feat in target_layer.getFeatures():
        h, h_source = _target_height(dock, target_layer, feat, target_hfield, target_default, use_avr)
        source_counts[h_source] = source_counts.get(h_source, 0) + 1
        for sample_idx, pt in enumerate(_target_sample_points(feat.geometry(), target_gtype)):
            try:
                p = tr_target.transform(pt) if tr_target else pt
                x, y = float(p.x()), float(p.y())
                targets.append({
                    "x": x, "y": y, "z": ground_z(x, y) + h,
                    "height_m": float(h), "height_source": h_source,
                    "fid": feat.id(), "sample_idx": sample_idx,
                })
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:438")
                continue
    if not targets:
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr("Aucun point cible exploitable dans la couche sélectionnée."))
        return None

    started_edit = False
    diagnostic_records = []
    try:
        if not hedge_layer.isEditable():
            started_edit = bool(hedge_layer.startEditing())
        if out_field not in hedge_layer.fields().names():
            if not hedge_layer.addAttribute(QgsField(out_field, QC.QMetaType_Type_Double, "double", 12, 3)):
                raise RuntimeError("Impossible de créer le champ résultat")
            hedge_layer.updateFields()
        idx_out = hedge_layer.fields().indexOf(out_field)
        if idx_out < 0:
            raise RuntimeError("Impossible de créer le champ résultat")

        updated = 0; no_cross = 0; max_value = 0.0
        camera_xy = (float(cam_pt.x()), float(cam_pt.y()))
        for hfeat in hedge_layer.getFeatures():
            parts_cam = []
            for line in _line_parts(hfeat.geometry()):
                pts = []
                for p in line:
                    try:
                        q = tr_hedge.transform(p) if tr_hedge else p
                        pts.append((float(q.x()), float(q.y())))
                    except Exception as _qcv_exc:
                        _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:467")
                if len(pts) >= 2:
                    parts_cam.append(pts)
            if not parts_cam:
                no_cross += 1
                continue

            first_hits_for_feature = []
            for target in targets:
                hits = occlusion_hits_on_polylines(
                    camera_xy, cam_z,
                    (target["x"], target["y"]), target["z"],
                    parts_cam, ground_z, margin_m=margin,
                )
                if not hits:
                    continue
                
                
                first = dict(hits[0])
                first["height_nominal_m"] = float(first["height_m"]) / max(0.05, effective_ratio)
                first["crown_ratio"] = float(effective_ratio)
                first_hits_for_feature.append((first, target))
                if create_diag:
                    for i, hit0 in enumerate(hits):
                        hit = dict(hit0)
                        hit["height_nominal_m"] = float(hit["height_m"]) / max(0.05, effective_ratio)
                        hit["crown_ratio"] = float(effective_ratio)
                        rec = dict(hit)
                        rec.update({
                            "hedge_fid": hfeat.id(), "target_fid": target["fid"],
                            "target_h": target["height_m"],
                            "height_source": target["height_source"],
                            "first_hit": i == 0, "controls_h": False,
                        })
                        diagnostic_records.append(rec)

            if not first_hits_for_feature:
                no_cross += 1
                continue

            controlling_hit, controlling_target = max(first_hits_for_feature, key=lambda item: float(item[0]["height_nominal_m"]))
            best_h = float(controlling_hit["height_nominal_m"])
            hedge_layer.changeAttributeValue(hfeat.id(), idx_out, round(best_h, 3))
            updated += 1; max_value = max(max_value, best_h)

            if create_diag:
                
                cx, cy = controlling_hit["xy"]
                for rec in diagnostic_records:
                    if (str(rec.get("hedge_fid")) == str(hfeat.id()) and
                        str(rec.get("target_fid")) == str(controlling_target["fid"]) and
                        bool(rec.get("first_hit")) and
                        abs(rec["xy"][0] - cx) < 1e-7 and abs(rec["xy"][1] - cy) < 1e-7):
                        rec["controls_h"] = True
                        break

        if started_edit:
            if not hedge_layer.commitChanges():
                raise RuntimeError("Échec de l'enregistrement des hauteurs calculées")
        hedge_layer.triggerRepaint()

        diag_layer = _diagnostic_layer(cam_crs, hedge_layer, target_layer, diagnostic_records) if create_diag else None
        src_txt = ", ".join(f"{k}: {v}" for k, v in sorted(source_counts.items())) or "inconnue"
        diag_txt = "\nCouche « QCALVIEW - diagnostic occultation » créée/actualisée." if diag_layer is not None else ""
        crown_txt = f"\nCompensation cime : {crown_desc}; ratio appliqué {effective_ratio:.3f}."
        if not continuous_screen:
            crown_txt += " ATTENTION : le motif de haie n’est pas un ruban continu; l’occultation complète n’est pas garantie."
        QMessageBox.information(
            dock, tr("Hauteur d'occultation"),
            tr(f"Calcul terminé : {updated} entité(s) renseignée(s) dans « {out_field} ».\n"
            f"Hauteur maximale calculée : {max_value:.2f} m.\n"
            f"{no_cross} entité(s) sans intersection utile avec les lignes de visée.\n"
            f"Source des hauteurs cibles — {src_txt}."
            f"{crown_txt}"
            f"{diag_txt}")
        )
        return out_field
    except Exception as e:
        try:
            if started_edit and hedge_layer.isEditable():
                hedge_layer.rollBack()
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_schematic_tools.py:549")
        QMessageBox.warning(dock, tr("Hauteur d'occultation"), tr(f"Calcul interrompu : {e}"))
        return None
