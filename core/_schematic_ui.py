# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small dialogs for the schematic symbol library.

Qt Designer owns the static dialog layouts. Python only populates the dynamic
parameter forms and connects their behaviour to QCALVIEW.
"""
from __future__ import annotations
from ._i18n import tr
from ._compat import QC, dialog_exec
import os, shutil
from qgis.PyQt import uic
from ._log import qcv_log

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QListWidget, QListWidgetItem,
    QDialogButtonBox, QDoubleSpinBox, QLineEdit, QPushButton, QHBoxLayout, QCheckBox, QComboBox, QFileDialog, QMessageBox, QColorDialog
)
from qgis.PyQt.QtGui import QColor

from qgis.core import QgsApplication
from ._schematic_symbols import get_symbol_library


# -----------------------------------------------------------------------------
# Taxonomie de bibliothèque
# -----------------------------------------------------------------------------

MOTIF_TAXONOMY = {
    "vegetation": {
        "label": "Végétation",
        "families": [
            ("all", "Toutes végétations"),
            ("conifers", "Résineux"),
            ("deciduous", "Caducs"),
            ("shrubs_groves", "Arbustes / bosquets"),
            ("hedges", "Haies"),
        ],
    },
    "animals": {
        "label": "Animaux",
        "families": [
            ("all", "Tous animaux"),
            ("cattle", "Bovins"),
            ("sheep", "Ovins"),
            ("horses", "Chevaux"),
            ("poultry", "Volailles"),
            ("other_animals", "Autres animaux"),
        ],
    },
    "people": {
        "label": "Personnages",
        "families": [
            ("all", "Tous personnages"),
            ("men", "Homme"),
            ("women", "Femme"),
            ("groups", "Groupe"),
            ("silhouettes", "Silhouettes diverses"),
        ],
    },
    "agriculture_objects": {
        "label": "Agriculture / mobilier / objets",
        "families": [
            ("all", "Tous objets / mobilier"),
            ("hay_bales", "Bottes de foin"),
            ("fences", "Clôtures"),
            ("farm_equipment", "Matériel agricole"),
            ("furniture_equipment", "Mobilier / équipements"),
            ("buildings_structures", "Bâtiments / ouvrages"),
            ("other_objects", "Autres objets"),
        ],
    },
    "vehicles": {
        "label": "Véhicules",
        "families": [
            ("all", "Tous véhicules"),
            ("cars", "Voiture"),
            ("tractors", "Tracteur"),
            ("harvesters", "Moissonneuse"),
            ("utility", "Utilitaire"),
            ("other_vehicles", "Autres véhicules"),
        ],
    },
    "solar_panels": {
        "label": "Panneaux solaires",
        "families": [
            ("all", "Tous panneaux solaires"),
            ("pv_tables", "Tables photovoltaïques fixes"),
            ("vertical_panels", "Panneaux verticaux"),
            ("trackers", "Trackers mono-axe"),
            ("other_solar", "Autres modèles"),
        ],
    },
    "wind_turbines": {
        "label": "Éoliennes",
        "families": [
            ("all", "Tous modèles d’éoliennes"),
            ("standard_3_blade", "Standard 3 pales"),
            ("silhouettes", "Silhouettes personnalisées"),
            ("other_turbines", "Autres modèles"),
        ],
    },
}

# Compatibilité avec les définitions historiques qui ne disposent pas encore
# des clés taxonomy_type / taxonomy_family dans leur JSON.
_SYMBOL_TAXONOMY_FALLBACK = {
    "tree_conifer_pine": ("vegetation", "conifers"),
    "tree_conifer_spruce": ("vegetation", "conifers"),
    "tree_deciduous": ("vegetation", "deciduous"),
    "shrub_generic": ("vegetation", "shrubs_groves"),
    "hedge_generic": ("vegetation", "hedges"),
    "cattle": ("animals", "cattle"),
    "sheep": ("animals", "sheep"),
    "horse": ("animals", "horses"),
    "chicken": ("animals", "poultry"),
    "person_scale": ("people", "silhouettes"),
    "car_scale": ("vehicles", "cars"),
    "tractor": ("vehicles", "tractors"),
    "combine_harvester": ("vehicles", "harvesters"),
    "wind_turbine": ("wind_turbines", "standard_3_blade"),
    "pv_table": ("solar_panels", "pv_tables"),
    "pv_vertical": ("solar_panels", "vertical_panels"),
    "pv_tracker": ("solar_panels", "trackers"),
    "volume": ("agriculture_objects", "buildings_structures"),
}

_INTERNAL_ASSET_TAXONOMY = {
    "tree_conifer_pine.svg": [("vegetation", "conifers")],
    "tree_conifer_spruce.svg": [("vegetation", "conifers")],
    "tree_deciduous.svg": [("vegetation", "deciduous")],
    "shrub.svg": [("vegetation", "shrubs_groves")],
    "cattle.svg": [("animals", "cattle")],
    "sheep.svg": [("animals", "sheep")],
    "horse.svg": [("animals", "horses")],
    "chicken.svg": [("animals", "poultry")],
    "person.svg": [("people", "silhouettes")],
    "car.svg": [("vehicles", "cars")],
    "tractor.svg": [("vehicles", "tractors"), ("agriculture_objects", "farm_equipment")],
    "combine_harvester.svg": [("vehicles", "harvesters"), ("agriculture_objects", "farm_equipment")],
}

# Types/familles proposés selon la géométrie de la couche.
# "all" reste disponible : le filtre de définition élimine ensuite les générateurs
# incompatibles. Les clôtures polygonales sont explicitement périmétriques.
_GEOMETRY_TAXONOMY = {
    "point": {
        "vegetation": {"all","conifers","deciduous","shrubs_groves"},
        "animals": None, "people": None,
        "agriculture_objects": {"all","hay_bales","farm_equipment","furniture_equipment","buildings_structures","other_objects"},
        "vehicles": None, "wind_turbines": None,
    },
    "line": {
        "vegetation": None,
        "agriculture_objects": {"all","fences","furniture_equipment","buildings_structures","other_objects"},
        "solar_panels": {"all","pv_tables","vertical_panels","trackers","other_solar"},
    },
    "polygon": {
        "vegetation": {"all","conifers","deciduous","shrubs_groves"}, "animals": None, "people": None,
        "agriculture_objects": None, "vehicles": None, "solar_panels": {"all","pv_tables","vertical_panels","other_solar"}, "wind_turbines": None,
    },
}

def _allowed_types_for_geometry(geometry_name: str):
    rec=_GEOMETRY_TAXONOMY.get(str(geometry_name or '').lower())
    return set(rec.keys()) if rec is not None else set(MOTIF_TAXONOMY.keys())

def _allowed_families_for_geometry(geometry_name: str, type_code: str):
    rec=_GEOMETRY_TAXONOMY.get(str(geometry_name or '').lower())
    if rec is None: return None
    return rec.get(str(type_code or ''))

def _ui_path(name: str, plugin_dir: str = ''):
    base = plugin_dir or os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base, 'ui', name)

def taxonomy_label(type_code: str) -> str:
    return str((MOTIF_TAXONOMY.get(str(type_code or "")) or {}).get("label", type_code or ""))

def family_label(type_code: str, family_code: str) -> str:
    for code, label in (MOTIF_TAXONOMY.get(str(type_code or ""), {}).get("families") or []):
        if code == str(family_code or ""):
            return label
    return str(family_code or "")

def symbol_taxonomy(definition):
    if not definition:
        return "", ""
    t = str(definition.get("taxonomy_type", "") or "").strip()
    f = str(definition.get("taxonomy_family", "") or "").strip()
    if t:
        return t, f or "all"
    sid = str(definition.get("id", "") or "")
    return _SYMBOL_TAXONOMY_FALLBACK.get(sid, ("", ""))

def populate_type_combo(combo, current_type: str = "", geometry_name: str = ""):
    combo.blockSignals(True)
    try:
        combo.clear()
        allowed=_allowed_types_for_geometry(geometry_name)
        for code, rec in MOTIF_TAXONOMY.items():
            if code in allowed:
                combo.addItem(tr(rec["label"]), code)
        idx = combo.findData(str(current_type or ""))
        combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
    finally:
        combo.blockSignals(False)

def populate_family_combo(combo, type_code: str, current_family: str = "", geometry_name: str = ""):
    combo.blockSignals(True)
    try:
        combo.clear()
        families = list((MOTIF_TAXONOMY.get(str(type_code or "")) or {}).get("families") or [])
        allowed=_allowed_families_for_geometry(geometry_name, type_code)
        for code, label in families:
            if allowed is None or code in allowed:
                combo.addItem(tr(label), code)
        idx = combo.findData(str(current_family or ""))
        combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
    finally:
        combo.blockSignals(False)

def _definition_matches(definition, type_code: str = "", family_code: str = "", geometry_name: str = ""):
    if geometry_name:
        geoms = [str(g).lower() for g in (definition.get("geometry", []) or [])]
        if str(geometry_name).lower() not in geoms:
            return False
    dt, df = symbol_taxonomy(definition)
    if type_code and dt != str(type_code):
        return False
    fam = str(family_code or "")
    # Une famille précise ne doit jamais hériter d'un générateur "all" : cela
    # évite notamment qu'un objet générique à dispersion polygonale soit proposé
    # dans « Clôtures », où le comportement doit rester strictement périmétrique.
    if fam and fam != "all" and df not in (fam, "*"):
        return False
    return True


def populate_symbol_combo(combo, plugin_dir: str, current_id: str = "", geometry_name: str = "", type_code: str = "", family_code: str = ""):
    lib = get_symbol_library(plugin_dir)
    combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(tr("— Rendu géométrique classique —"), "")
        selected = 0
        defs = [d for d in lib.definitions() if _definition_matches(d, type_code, family_code, geometry_name)]
        for idx, definition in enumerate(defs, start=1):
            sid = str(definition.get("id", "") or "")
            name = str(definition.get("name", sid))
            _t, _f = symbol_taxonomy(definition)
            fam_label = family_label(_t, _f)
            combo.addItem(tr(f"{fam_label} — {name}" if fam_label else name), sid)
            if sid == str(current_id or ""):
                selected = idx
        combo.setCurrentIndex(selected)
    finally:
        combo.blockSignals(False)
    return lib

def browse_symbol_library(parent, combo, plugin_dir: str, geometry_name: str = "", type_code: str = "", family_code: str = ""):
    lib = get_symbol_library(plugin_dir)
    dlg = QDialog(parent)
    uic.loadUi(_ui_path('schematic_library_dialog.ui', plugin_dir), dlg)
    header = dlg.lblHeader; lst = dlg.lstSymbols; desc = dlg.lblDescription
    header.setText(tr(f"{taxonomy_label(type_code)} · {family_label(type_code, family_code)}"))
    defs = [d for d in lib.definitions() if _definition_matches(d, type_code, family_code, geometry_name)]
    current = str(combo.currentData() or "")
    selected_row = 0
    for i, definition in enumerate(defs):
        sid = str(definition.get("id", "") or "")
        name = str(definition.get("name", sid))
        dt, df = symbol_taxonomy(definition)
        item = QListWidgetItem(f"{family_label(dt, df)} — {name}")
        item.setData(32, sid)
        item.setToolTip(tr(str(definition.get("description", "") or "")))
        lst.addItem(tr(item))
        if sid == current:
            selected_row = i

    def update_desc(row):
        if 0 <= row < len(defs):
            d = defs[row]
            geom = ", ".join(d.get("geometry", []) or [])
            dt, df = symbol_taxonomy(d)
            desc.setText(tr(f"{d.get('description','')}\nType : {taxonomy_label(dt)} · Famille : {family_label(dt, df)} · Géométrie : {geom} · Générateur : {d.get('generator','')}"))
        else:
            desc.clear()

    lst.currentRowChanged.connect(update_desc)
    if defs:
        lst.setCurrentRow(selected_row); update_desc(selected_row)
    bb = dlg.buttonBox
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    lst.itemDoubleClicked.connect(lambda _it: dlg.accept())
    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted:
        return None
    row = lst.currentRow()
    if not (0 <= row < len(defs)):
        return None
    sid = str(defs[row].get("id", "") or "")
    idx = combo.findData(sid)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    return sid

def edit_symbol_params(parent, definition, current_params):
    """Edit optional per-layer numeric overrides.

    Empty/reset values keep the JSON definition, including its field-driven
    mapping. This is intentionally small: editing the library JSON remains the
    advanced path for adding new parameter schemas.
    """
    if not definition:
        return dict(current_params or {})
    dlg = QDialog(parent)
    plugin_dir = os.path.dirname(os.path.dirname(__file__))
    uic.loadUi(_ui_path('schematic_params_dialog.ui', plugin_dir), dlg)
    dlg.setWindowTitle(tr(f"Paramètres — {definition.get('name', definition.get('id',''))}"))
    dlg.lblTitle.setText(tr("Les valeurs ci-dessous remplacent les valeurs de la bibliothèque pour cette couche. Laisser sans surcharge conserve les champs/défauts définis dans le JSON."))
    form = dlg.paramsForm
    widgets = {}
    params = definition.get("parameters", {}) or {}
    current = dict(current_params or {})
    for name, raw in params.items():
        default = raw.get("default") if isinstance(raw, dict) else raw
        source = raw.get("source") if isinstance(raw, dict) else None
        if source == "layer_height" and isinstance(default, (int, float)):
            # A billboard/object height must remain independently adjustable.
            # By default QCALVIEW keeps the existing 2.5D layer-height behaviour;
            # unchecking "Hauteur couche" stores an explicit symbol override.
            row = QHBoxLayout()
            sp = QDoubleSpinBox(); sp.setRange(0.01, 100000.0); sp.setDecimals(3)
            has_override = name in current
            sp.setValue(float(current.get(name, default or 0.0)))
            cb_layer = QCheckBox(tr("Hauteur couche"))
            cb_layer.setToolTip(tr("Utilise le champ/hauteur 2,5D spécifique de la couche s'il est défini ; sinon le défaut de la bibliothèque."))
            cb_layer.setChecked(not has_override)
            sp.setEnabled(has_override)
            cb_layer.toggled.connect(lambda checked, s=sp: s.setEnabled(not checked))
            reset = QPushButton(tr("Défaut"))
            def _reset_layer_height(_=False, s=sp, c=cb_layer, v=float(default or 0.0)):
                s.setValue(v); c.setChecked(True)
            reset.clicked.connect(_reset_layer_height)
            row.addWidget(sp, 1); row.addWidget(cb_layer); row.addWidget(reset)
            widgets[name] = ("layer_height_number", sp, default, cb_layer)
            field = raw.get("field") if isinstance(raw, dict) else None
            display_name = str(raw.get("label", name) if isinstance(raw, dict) else name)
            label = display_name + (f"  [champ JSON : {field}]" if field else "")
            form.addRow(tr(label), row)
            continue
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if choices and isinstance(default, str):
            cmb = QComboBox()
            normalized = []
            for choice in choices:
                if isinstance(choice, dict):
                    value = str(choice.get("value", ""))
                    label = str(choice.get("label", value))
                else:
                    value = str(choice); label = value
                normalized.append((value, label))
                cmb.addItem(tr(label), value)
            current_value = str(current.get(name, default))
            idx = cmb.findData(current_value)
            if idx < 0:
                idx = cmb.findData(str(default))
            if idx >= 0:
                cmb.setCurrentIndex(idx)
            widgets[name] = ("choice", cmb, default)
            display_name = str(raw.get("label", name))
            form.addRow(tr(display_name), cmb)
            continue
        if isinstance(default, bool):
            cb = QCheckBox()
            cb.setChecked(bool(current.get(name, default)))
            widgets[name] = ("bool", cb, default)
            display_name = str(raw.get("label", name) if isinstance(raw, dict) else name)
            form.addRow(tr(display_name), cb)
            continue
        param_type = str(raw.get("type", "") if isinstance(raw, dict) else "").strip().lower()
        if isinstance(default, str) and param_type == "color":
            btn = QPushButton()
            def _set_color_button(b, value):
                c = QColor(str(value or default))
                if not c.isValid(): c = QColor(str(default or "#000000"))
                b.setProperty("qcv_color", c.name())
                b.setText(tr(c.name().upper()))
                b.setStyleSheet("QPushButton { background-color: %s; }" % c.name())
            _set_color_button(btn, current.get(name, default))
            def _pick_color(_=False, b=btn, d=default):
                base = QColor(str(b.property("qcv_color") or d or "#000000"))
                c = QColorDialog.getColor(base, dlg, "Choisir la couleur")
                if c.isValid(): _set_color_button(b, c.name())
            btn.clicked.connect(_pick_color)
            widgets[name] = ("color", btn, default)
            form.addRow(tr(str(raw.get("label", name) if isinstance(raw, dict) else name)), btn)
            continue
        if isinstance(default, (int, float)):
            row = QHBoxLayout()
            sp = QDoubleSpinBox()
            pmin = float(raw.get("min", -100000.0)) if isinstance(raw, dict) else -100000.0
            pmax = float(raw.get("max", 100000.0)) if isinstance(raw, dict) else 100000.0
            if pmax < pmin: pmin, pmax = pmax, pmin
            sp.setRange(pmin, pmax)
            sp.setDecimals(int(raw.get("decimals", 3)) if isinstance(raw, dict) else 3)
            if isinstance(raw, dict) and raw.get("step") is not None:
                try: sp.setSingleStep(float(raw.get("step")))
                except Exception: pass
            sp.setValue(float(current.get(name, default or 0.0)))
            reset = QPushButton(tr("Défaut"))
            reset.clicked.connect(lambda _=False, s=sp, v=float(default or 0.0): s.setValue(v))
            row.addWidget(sp, 1); row.addWidget(reset)
            widgets[name] = ("number", sp, default)
            field = raw.get("field") if isinstance(raw, dict) else None
            display_name = str(raw.get("label", name) if isinstance(raw, dict) else name)
            label = display_name + (f"  [champ JSON : {field}]" if field else "")
            form.addRow(tr(label), row)
        elif isinstance(default, str) and name != "svg":
            le = QLineEdit(str(current.get(name, default)))
            widgets[name] = ("text", le, default)
            form.addRow(tr(str(raw.get("label", name) if isinstance(raw, dict) else name)), le)
    bb = dlg.buttonBox
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    def reset_all():
        for _name, item in widgets.items():
            kind, w, default = item[:3]
            if kind == "number":
                w.setValue(float(default or 0.0))
            elif kind == "layer_height_number":
                w.setValue(float(default or 0.0))
                item[3].setChecked(True)
            elif kind == "choice":
                idx = w.findData(str(default or ""))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif kind == "bool":
                w.setChecked(bool(default))
            elif kind == "color":
                c = QColor(str(default or "#000000"))
                w.setProperty("qcv_color", c.name()); w.setText(tr(c.name().upper()))
                w.setStyleSheet("QPushButton { background-color: %s; }" % c.name())
            else:
                w.setText(tr(str(default or "")))
    bb.button(QC.QDialogButtonBox_StandardButton_Reset).clicked.connect(reset_all)
    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted:
        return dict(current_params or {})
    out = {}
    for name, item in widgets.items():
        kind, w, default = item[:3]
        if kind == "number":
            value = float(w.value())
            # Store only actual overrides so field-driven JSON remains active by default.
            try:
                if abs(value - float(default)) > 1e-9:
                    out[name] = value
            except Exception:
                out[name] = value
        elif kind == "layer_height_number":
            cb_layer = item[3]
            if not cb_layer.isChecked():
                # Manual intent matters even when equal to the library default: it
                # must override a layer-specific 2.5D height if one exists.
                out[name] = float(w.value())
        elif kind == "choice":
            value = str(w.currentData() or "")
            if value != str(default or ""):
                out[name] = value
        elif kind == "bool":
            value = bool(w.isChecked())
            if value != bool(default):
                out[name] = value
        elif kind == "color":
            value = str(w.property("qcv_color") or default or "").strip()
            if value.lower() != str(default or "").strip().lower():
                out[name] = value
        else:
            value = w.text().strip()
            if value != str(default or ""):
                out[name] = value
    return out


def user_symbol_dir():
    root=os.path.join(QgsApplication.qgisSettingsDirPath(),'QCALVIEW','symbols')
    os.makedirs(root,exist_ok=True)
    return root

def _user_catalog_path():
    return os.path.join(user_symbol_dir(), 'catalog.json')

def _load_user_catalog():
    path = _user_catalog_path()
    try:
        import json
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_user_catalog(data):
    try:
        import json
        with open(_user_catalog_path(), 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def _asset_memberships(path: str, plugin_dir: str):
    name = os.path.basename(str(path or ''))
    internal = os.path.normcase(os.path.normpath(os.path.join(plugin_dir,'resources','symbols','assets')))
    pnorm = os.path.normcase(os.path.normpath(str(path or '')))
    try:
        if os.path.commonpath([internal, pnorm]) == internal:
            return list(_INTERNAL_ASSET_TAXONOMY.get(name, []))
    except Exception:
        pass
    rec = _load_user_catalog().get(name, {})
    memberships = rec.get('memberships') if isinstance(rec, dict) else None
    out=[]
    for item in memberships or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    if not out and isinstance(rec, dict):
        t=str(rec.get('type','') or ''); f=str(rec.get('family','') or '')
        if t: out=[(t, f or 'all')]
    return out

def asset_matches_context(path: str, plugin_dir: str, type_code: str, family_code: str):
    memberships = _asset_memberships(path, plugin_dir)
    if not memberships:
        return False
    fam = str(family_code or 'all')
    for t, f in memberships:
        if t != str(type_code or ''):
            continue
        if fam == 'all' or f in (fam, 'all', '*'):
            return True
    return False

def filter_assets_for_context(paths, plugin_dir: str, type_code: str, family_code: str):
    return [p for p in (paths or []) if asset_matches_context(p, plugin_dir, type_code, family_code)][:3]

def _choose_import_family(parent, type_code: str, family_code: str):
    if family_code and family_code != 'all':
        return family_code
    dlg=QDialog(parent)
    plugin_dir=os.path.dirname(os.path.dirname(__file__))
    uic.loadUi(_ui_path('schematic_import_category_dialog.ui', plugin_dir), dlg)
    dlg.lblType.setText(tr(f"Type : {taxonomy_label(type_code)}"))
    combo=dlg.cmbFamily
    for code,label in (MOTIF_TAXONOMY.get(type_code,{}).get('families') or []):
        if code != 'all': combo.addItem(tr(label),code)
    bb=dlg.buttonBox
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted:
        return ''
    return str(combo.currentData() or '')

def select_symbol_assets(parent, plugin_dir: str, current_paths=None, type_code: str = '', family_code: str = ''):
    """Select up to three SVG/PNG models, filtered by motif type/family."""
    current=[os.path.normpath(str(x)) for x in (current_paths or []) if x]
    internal=os.path.join(plugin_dir,'resources','symbols','assets')
    user=user_symbol_dir()
    dlg=QDialog(parent)
    uic.loadUi(_ui_path('schematic_models_dialog.ui', plugin_dir), dlg)
    title=dlg.lblTitle; hint=dlg.lblHint; lst=dlg.lstModels
    title.setText(tr(f"{taxonomy_label(type_code)} · {family_label(type_code, family_code)}"))
    hint.setText(tr('Seuls les modèles compatibles avec ce type de motif sont affichés. Vous pouvez en sélectionner jusqu’à 3 ; ils seront alternés de façon stable le long des alignements.'))
    files_cache=[]
    def reload_list():
        nonlocal files_cache
        lst.clear(); files_cache=[]
        for label,folder in [('Interne',internal),('Utilisateur',user)]:
            if not os.path.isdir(folder): continue
            for name in sorted(os.listdir(folder)):
                if not name.lower().endswith(('.svg','.png')): continue
                path=os.path.normpath(os.path.join(folder,name))
                if not asset_matches_context(path, plugin_dir, type_code, family_code):
                    continue
                files_cache.append((label,name,path))
        for label,name,path in files_cache:
            it=QListWidgetItem(f'{label} — {name}'); it.setData(32,path)
            it.setFlags(it.flags() | QC.Qt_ItemFlag_ItemIsUserCheckable)
            it.setCheckState(QC.Qt_CheckState_Checked if path in current else QC.Qt_CheckState_Unchecked)
            lst.addItem(tr(it))
    reload_list()
    btn_import=dlg.btnImport
    def do_import():
        fam = _choose_import_family(dlg, type_code, family_code)
        if not fam:
            return
        paths,_=QFileDialog.getOpenFileNames(dlg,'Importer des modèles',user,'Images vectorielles ou PNG (*.svg *.png)')
        if not paths:
            return
        catalog=_load_user_catalog()
        for src in paths:
            try:
                dst=os.path.join(user,os.path.basename(src)); shutil.copy2(src,dst)
                catalog[os.path.basename(dst)]={'type':type_code,'family':fam,'memberships':[[type_code,fam]]}
            except Exception as exc:
                qcv_log(f"Échec import {src}: {exc}", 'LIBRARY', 'WARNING')
        _save_user_catalog(catalog); reload_list()
        qcv_log(f"Bibliothèque utilisateur mise à jour dans {user}", 'LIBRARY', 'SUCCESS')
    btn_import.clicked.connect(do_import)
    bb=dlg.buttonBox
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    if dialog_exec(dlg) != QC.QDialog_DialogCode_Accepted: return list(current_paths or [])
    out=[]
    for i in range(lst.count()):
        it=lst.item(i)
        if it.checkState()==QC.Qt_CheckState_Checked: out.append(str(it.data(32)))
    if len(out)>3:
        QMessageBox.warning(parent,tr('QCALVIEW'),tr('Sélection limitée à 3 modèles. Les trois premiers ont été conservés.'))
        out=out[:3]
    return out

