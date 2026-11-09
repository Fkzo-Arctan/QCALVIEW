"""QCALVIEW-local translations. Never change QGIS/Qt application translators."""
from __future__ import annotations

import os
import re
import json
import logging
from qgis.PyQt.QtCore import QLocale, QSettings

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_I18N_DIR = os.path.join(_PLUGIN_ROOT, 'i18n')
_CONTEXT = 'QCALVIEW'
_translation_memory = None
_active_language = 'en'
_LOGGER = logging.getLogger(__name__)


def _normalise_locale(value):
    try:
        return str(value or '').strip().replace('-', '_')
    except Exception:
        return ''


def resolve_language():
    """Respect the effective QGIS locale, not a stale disabled override."""
    try:
        settings = QSettings()
        flag = settings.value('locale/overrideFlag', False)
        enabled = str(flag).strip().lower() in ('true', '1', 'yes', 'on')
        if enabled:
            chosen = _normalise_locale(settings.value('locale/userLocale', ''))
            if chosen:
                return 'fr' if chosen.lower().startswith('fr') else 'en'
    except (RuntimeError, TypeError, ValueError) as exc:
        _LOGGER.debug('Unable to read QGIS locale override: %s', exc)
    try:
        locale_name = _normalise_locale(QLocale().name())
    except Exception:
        locale_name = ''
    return 'fr' if locale_name.lower().startswith('fr') else 'en'


def current_language():
    return _active_language


def _placeholder_pattern(source):
    matches = list(re.finditer(r'%([1-9][0-9]*)', source))
    if not matches:
        return None, []
    parts, groups, pos = [], [], 0
    for match in matches:
        parts.extend((re.escape(source[pos:match.start()]), '(.*?)'))
        groups.append(int(match.group(1)))
        pos = match.end()
    parts.append(re.escape(source[pos:]))
    return re.compile('^' + ''.join(parts) + '$', re.S), groups


class _JsonRuntimeCatalog:
    """Load the plugin's compiled, bundled translation map as JSON.

    The editable Qt Linguist .ts file is converted to JSON at packaging time.
    No XML is parsed within QGIS and no global translator is installed.
    """

    def __init__(self, catalog_path, parent=None):
        self._by_context = {}
        self._by_source = {}
        self._patterns = []
        self._load_json(catalog_path)

    def isEmpty(self):
        return not bool(self._by_source)

    def _load_json(self, catalog_path):
        try:
            with open(catalog_path, 'r', encoding='utf-8') as handle:
                contexts = json.load(handle)
            if not isinstance(contexts, dict):
                raise ValueError('Translation catalog must be a JSON object')
            for context_name, entries in contexts.items():
                if not isinstance(context_name, str) or not isinstance(entries, dict):
                    raise ValueError('Invalid translation context')
                for source, translated in entries.items():
                    if not isinstance(source, str) or not isinstance(translated, str):
                        raise ValueError('Invalid translation pair')
                    if not translated:
                        continue
                    self._by_context[(context_name, source)] = translated
                    self._by_source.setdefault(source, translated)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            _LOGGER.warning('Cannot load QCALVIEW translations: %s', exc)
            self._by_context.clear()
            self._by_source.clear()
            return
        for source, translated in self._by_source.items():
            if source == translated:
                continue
            pattern, indices = _placeholder_pattern(source)
            if pattern is not None:
                specificity = len(re.sub(r'%[1-9][0-9]*', '', source))
                self._patterns.append((specificity, pattern, indices, translated))
        self._patterns.sort(key=lambda p: p[0], reverse=True)

    @staticmethod
    def _apply_groups(translation, groups, match):
        values = {number: match.group(i) for i, number in enumerate(groups, 1)}
        # A captured user string is never processed as another translation key.
        return re.sub(r'%([1-9][0-9]*)',
                      lambda m: values.get(int(m.group(1)), m.group()), translation)

    def lookup_text(self, sourceText, context=_CONTEXT):
        if sourceText is None:
            return ''
        source = str(sourceText)
        exact = self._by_context.get((str(context or ''), source))
        if exact is None:
            exact = self._by_source.get(source)
        if exact is not None:
            return exact
        for _specificity, pattern, groups, translation in self._patterns:
            match = pattern.match(source)
            if match:
                return self._apply_groups(translation, groups, match)
        # Important: NO substring/phrase replacement! It corrupted mixed UI
        # strings and could translate names, paths and user-entered content.
        return source


def install_qcalview_translator(parent=None):
    """Retained API name; installs NOTHING into QCoreApplication.

    The TS catalog is accessed only by this module's explicit tr() helper.
    """
    global _translation_memory, _active_language
    _active_language = resolve_language()
    _translation_memory = None
    if _active_language == 'fr':
        path = os.path.join(_I18N_DIR, 'QCALVIEW_fr.json')
        catalog = _JsonRuntimeCatalog(path)
        if not catalog.isEmpty():
            _translation_memory = catalog
    return None


def remove_qcalview_translator():
    global _translation_memory, _active_language
    _translation_memory = None
    _active_language = 'en'


def tr(value, context=_CONTEXT):
    if isinstance(value, list):
        return [tr(v, context) for v in value]
    if isinstance(value, tuple):
        return tuple(tr(v, context) for v in value)
    if not isinstance(value, str) or _active_language != 'fr':
        return value
    if _translation_memory is None:
        return value
    return _translation_memory.lookup_text(value, context)


def translate_designer_shell(root):
    """Translate UI-designer labels without touching combo values or GIS data."""
    if _active_language != 'fr' or root is None:
        return
    from qgis.PyQt.QtWidgets import QAbstractButton, QGroupBox, QLabel, QLineEdit, QTabWidget, QWidget
    widgets = [root] + root.findChildren(QWidget)
    for widget in widgets:
        if isinstance(widget, QLabel) or isinstance(widget, QAbstractButton):
            widget.setText(tr(widget.text()))
        if isinstance(widget, QGroupBox):
            widget.setTitle(tr(widget.title()))
        if isinstance(widget, QLineEdit):
            widget.setPlaceholderText(tr(widget.placeholderText()))
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                widget.setTabText(index, tr(widget.tabText(index)))
        if widget.toolTip():
            widget.setToolTip(tr(widget.toolTip()))
