



from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET  

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings, QTranslator

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_I18N_DIR = os.path.join(_PLUGIN_ROOT, "i18n")
_CONTEXT = "QCALVIEW"
_active_translator = None
_translation_memory = None
_active_language = "en"


def _normalise_locale(value):
    try:
        text = str(value or "").strip().replace("-", "_")
    except Exception:
        text = ""
    return text


def resolve_language():
    
    locale_name = ""
    try:
        locale_name = _normalise_locale(QSettings().value("locale/userLocale", ""))
    except Exception:
        locale_name = ""
    if not locale_name:
        try:
            locale_name = _normalise_locale(QLocale.system().name())
        except Exception:
            locale_name = ""
    return "fr" if locale_name.lower().startswith("fr") else "en"


def current_language():
    return _active_language


def _placeholder_pattern(source):
    
    matches = list(re.finditer(r"%([1-9][0-9]*)", source))
    if not matches:
        return None, []
    parts = []
    groups = []
    pos = 0
    for m in matches:
        parts.append(re.escape(source[pos:m.start()]))
        parts.append("(.*?)")
        groups.append(int(m.group(1)))
        pos = m.end()
    parts.append(re.escape(source[pos:]))
    try:
        return re.compile("^" + "".join(parts) + "$", re.S), groups
    except Exception:
        return None, []


class _TsRuntimeTranslator(QTranslator):
    

    def __init__(self, ts_path, parent=None):
        super().__init__(parent)
        self._by_context = {}
        self._by_source = {}
        self._patterns = []
        self._phrases = []
        self._load_ts(ts_path)

    def isEmpty(self):  
        return not bool(self._by_source)

    def _load_ts(self, ts_path):
        try:
            root = ET.parse(ts_path).getroot()  
        except Exception:
            return
        for ctx in root.findall("context"):
            context_name = (ctx.findtext("name") or "").strip()
            for msg in ctx.findall("message"):
                source = msg.findtext("source")
                trans_node = msg.find("translation")
                translation = "" if trans_node is None else "".join(trans_node.itertext())
                if source is None or not translation:
                    continue
                self._by_context[(context_name, source)] = translation
                self._by_source.setdefault(source, translation)
        for source, translation in self._by_source.items():
            if source == translation:
                continue
            pattern, groups = _placeholder_pattern(source)
            if pattern is not None:
                self._patterns.append((pattern, groups, translation))
            elif len(source) >= 4 and "\n" not in source and "<" not in source:
                self._phrases.append((source, translation))
        self._phrases.sort(key=lambda item: len(item[0]), reverse=True)

    @staticmethod
    def _apply_groups(translation, groups, match):
        values = {}
        for capture_index, placeholder_index in enumerate(groups, start=1):
            values[placeholder_index] = match.group(capture_index)
        out = translation
        for idx in sorted(values, reverse=True):
            out = out.replace("%%%d" % idx, values[idx])
        return out

    def lookup_text(self, sourceText, context=_CONTEXT):
        
        if sourceText is None:
            return ""
        source = str(sourceText)
        exact = self._by_context.get((str(context or ""), source))
        if exact is None:
            exact = self._by_source.get(source)
        if exact is not None:
            return exact
        for pattern, groups, translation in self._patterns:
            m = pattern.match(source)
            if m:
                return self._apply_groups(translation, groups, m)
        
        out = source
        changed = False
        for src, dst in self._phrases:
            if src in out:
                out = out.replace(src, dst)
                changed = True
        return out if changed else source

    def translate(self, context, sourceText, disambiguation=None, n=-1):  
        return self.lookup_text(sourceText, context)


def install_qcalview_translator(parent=None):
    
    global _active_translator, _translation_memory, _active_language
    if _active_translator is not None:
        return _active_translator
    _active_language = resolve_language()
    if _active_language == "fr":
        return None

    qm_path = os.path.join(_I18N_DIR, "QCALVIEW_en.qm")
    ts_path = os.path.join(_I18N_DIR, "QCALVIEW_en.ts")
    translator = None
    if os.path.isfile(ts_path):
        try:
            memory = _TsRuntimeTranslator(ts_path, parent)
            if not memory.isEmpty():
                _translation_memory = memory
        except Exception:
            _translation_memory = None
    try:
        if os.path.isfile(qm_path):
            native = QTranslator(parent)
            if native.load(qm_path):
                translator = native
    except Exception:
        translator = None
    if translator is None and _translation_memory is not None:
        translator = _translation_memory
    if translator is not None:
        try:
            QCoreApplication.installTranslator(translator)
            _active_translator = translator
        except Exception:
            _active_translator = None
    return _active_translator


def remove_qcalview_translator():
    global _active_translator, _translation_memory
    if _active_translator is not None:
        try:
            QCoreApplication.removeTranslator(_active_translator)
        except Exception:
            pass
        _active_translator = None
    _translation_memory = None


def tr(value, context=_CONTEXT):
    
    if isinstance(value, list):
        return [tr(v, context) for v in value]
    if isinstance(value, tuple):
        return tuple(tr(v, context) for v in value)
    if not isinstance(value, str):
        return value
    if _active_language == "fr":
        return value
    translated = value
    try:
        translated = QCoreApplication.translate(context, value)
    except Exception:
        translated = value
    
    
    
    if translated == value and _translation_memory is not None:
        try:
            translated = _translation_memory.lookup_text(value, context)
        except Exception:
            pass
    return translated
