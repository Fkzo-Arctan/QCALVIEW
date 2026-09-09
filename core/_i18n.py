



from __future__ import annotations
from ._exceptions import qcv_suppress_exception as _qcv_suppress

import os
import re

from qgis.PyQt.QtCore import QByteArray, QCoreApplication, QLocale, QSettings, QTranslator, QXmlStreamReader

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
            with open(ts_path, "rb") as stream:
                data = stream.read()
            reader = QXmlStreamReader()
            reader.addData(QByteArray(data))
            stack = []
            context_name = ""
            source = None
            translation = ""
            capture_context = None
            capture_source = None
            capture_translation = None
            context_parts = []
            source_parts = []
            translation_parts = []
            while not reader.atEnd():
                reader.readNext()
                if reader.isStartElement():
                    tag = str(reader.name())
                    parent = stack[-1] if stack else ""
                    stack.append(tag)
                    depth = len(stack)
                    if tag == "context":
                        context_name = ""
                    elif tag == "message":
                        source = None
                        translation = ""
                    elif tag == "name" and parent == "context":
                        capture_context = depth
                        context_parts = []
                    elif tag == "source" and parent == "message":
                        capture_source = depth
                        source_parts = []
                    elif tag == "translation" and parent == "message":
                        capture_translation = depth
                        translation_parts = []
                elif reader.isCharacters():
                    value = str(reader.text())
                    if capture_context is not None:
                        context_parts.append(value)
                    if capture_source is not None:
                        source_parts.append(value)
                    if capture_translation is not None:
                        translation_parts.append(value)
                elif reader.isEndElement():
                    tag = str(reader.name())
                    depth = len(stack)
                    if capture_context == depth and tag == "name":
                        context_name = "".join(context_parts).strip()
                        capture_context = None
                    if capture_source == depth and tag == "source":
                        source = "".join(source_parts)
                        capture_source = None
                    if capture_translation == depth and tag == "translation":
                        translation = "".join(translation_parts)
                        capture_translation = None
                    if tag == "message":
                        if source is not None and translation:
                            self._by_context[(context_name, source)] = translation
                            self._by_source.setdefault(source, translation)
                    if stack:
                        stack.pop()
            if reader.hasError():
                self._by_context.clear()
                self._by_source.clear()
                return
        except Exception:
            self._by_context.clear()
            self._by_source.clear()
            return
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
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_i18n.py:186")
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
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_i18n.py:213")
    return translated
