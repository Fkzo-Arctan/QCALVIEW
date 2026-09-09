



from __future__ import annotations
from ._exceptions import qcv_suppress_exception as _qcv_suppress

import math
import os
from contextlib import contextmanager

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QImageReader

from ._log import qcv_log

_MIB = 1024 * 1024
_DEFAULT_PROXY_MAX_PIXELS = 16_000_000   
_DEFAULT_PROXY_MAX_DIM = 12_000          
_DIRECT_SCALED_MAX_PIXELS = 24_000_000   


class PhotoReadError(RuntimeError):
    pass


def _fmt_mib(nbytes: int) -> str:
    try:
        return f"{float(nbytes) / float(_MIB):.1f} MiB"
    except Exception:
        return "? MiB"


def _reader_format(reader: QImageReader) -> str:
    try:
        raw = reader.format()
        b = bytes(raw)
        return b.decode('ascii', errors='ignore').upper() or '?'
    except Exception:
        return '?'


def probe_image(path: str) -> dict:
    
    path = str(path or '')
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path)
    reader = QImageReader(path)
    try:
        reader.setAutoTransform(True)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_image_io.py:50")
    size = reader.size()
    w = int(size.width()) if size is not None and size.isValid() else 0
    h = int(size.height()) if size is not None and size.isValid() else 0
    if w <= 0 or h <= 0:
        
        
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = map(int, im.size)
        except Exception as e:
            err = ''
            try:
                err = reader.errorString()
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_image_io.py:66")
            raise PhotoReadError(err or str(e) or 'Dimensions image illisibles')
    file_bytes = 0
    try:
        file_bytes = int(os.path.getsize(path))
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_image_io.py:72")
    return {
        'path': path,
        'width': w,
        'height': h,
        'pixels': int(w) * int(h),
        'decoded_bytes_est': int(w) * int(h) * 4,
        'file_bytes': file_bytes,
        'format': _reader_format(reader),
    }


def bounded_size(width: int, height: int, max_pixels: int = _DEFAULT_PROXY_MAX_PIXELS,
                 max_dim: int = _DEFAULT_PROXY_MAX_DIM) -> tuple[int, int]:
    w, h = max(1, int(width)), max(1, int(height))
    scale = 1.0
    if max_dim and max(w, h) > int(max_dim):
        scale = min(scale, float(max_dim) / float(max(w, h)))
    if max_pixels and (w * h) > int(max_pixels):
        scale = min(scale, math.sqrt(float(max_pixels) / float(w * h)))
    if scale >= 1.0:
        return w, h
    return max(1, int(round(w * scale))), max(1, int(round(h * scale)))


@contextmanager
def _temporary_qt_allocation_limit(required_bytes: int, enabled: bool):
    
    old = None
    setter = getattr(QImageReader, 'setAllocationLimit', None)
    getter = getattr(QImageReader, 'allocationLimit', None)
    changed = False
    try:
        if enabled and callable(setter) and callable(getter):
            try:
                old = int(getter())
                required_mib = max(1, int(math.ceil(float(required_bytes) * 1.20 / _MIB)))
                
                if old > 0 and required_mib > old:
                    setter(required_mib)
                    changed = True
            except Exception:
                old = None
        yield
    finally:
        if changed and old is not None:
            try:
                setter(int(old))
            except Exception as _qcv_exc:
                _qcv_suppress(_qcv_exc, "core/_image_io.py:121")


def _enough_memory_for_large_read(decoded_bytes: int) -> bool:
    
    try:
        from ._memory_guard import memory_snapshot
        snap = memory_snapshot()
        avail = int(getattr(snap, 'available_bytes', 0) or 0)
        if avail <= 0:
            return True  
        
        reserve = max(768 * _MIB, int(decoded_bytes * 1.75))
        return avail > reserve
    except Exception:
        return True


def read_qimage(path: str, width: int | None = None, height: int | None = None,
                *, allow_large: bool = False) -> QImage:
    
    info = probe_image(path)
    tw = int(width) if width is not None else int(info['width'])
    th = int(height) if height is not None else int(info['height'])
    tw, th = max(1, tw), max(1, th)
    target_bytes = int(tw) * int(th) * 4

    if allow_large and target_bytes > 256 * _MIB and not _enough_memory_for_large_read(target_bytes):
        raise PhotoReadError(
            f"Mémoire disponible insuffisante pour décoder {tw}×{th} "
            f"(~{_fmt_mib(target_bytes)} en QImage, hors buffers de rendu)."
        )

    reader = QImageReader(str(path))
    try:
        reader.setAutoTransform(True)
    except Exception as _qcv_exc:
        _qcv_suppress(_qcv_exc, "core/_image_io.py:158")
    
    
    if tw != int(info['width']) or th != int(info['height']):
        try:
            reader.setScaledSize(QSize(tw, th))
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_image_io.py:165")

    with _temporary_qt_allocation_limit(target_bytes, bool(allow_large)):
        img = reader.read()
    if img is None or img.isNull():
        err = ''
        try:
            err = str(reader.errorString() or '')
        except Exception as _qcv_exc:
            _qcv_suppress(_qcv_exc, "core/_image_io.py:174")
        raise PhotoReadError(err or f"Qt n'a pas pu décoder l'image {tw}×{th}.")
    return img


def load_working_image(path: str) -> tuple[QImage, dict]:
    
    info = probe_image(path)
    w, h = int(info['width']), int(info['height'])
    pw, ph = bounded_size(w, h)
    proxy = (pw != w or ph != h)
    try:
        img = read_qimage(path, pw, ph, allow_large=False)
    except Exception as first_error:
        
        
        pw2, ph2 = bounded_size(w, h, max_pixels=8_000_000, max_dim=8_000)
        if (pw2, ph2) == (pw, ph):
            raise
        qcv_log(
            f"Premier décodage photo échoué ({first_error}); nouvel essai proxy {pw2}×{ph2}.",
            'PHOTO/IO', 'WARNING')
        img = read_qimage(path, pw2, ph2, allow_large=False)
        pw, ph, proxy = pw2, ph2, True

    info = dict(info)
    info.update({
        'proxy': bool(proxy),
        'proxy_width': int(pw),
        'proxy_height': int(ph),
    })
    qcv_log(
        f"Photo chargée: {w}×{h}, fichier {_fmt_mib(info.get('file_bytes', 0))}, "
        f"QImage plein ~{_fmt_mib(info.get('decoded_bytes_est', 0))}; "
        + (f"proxy interactif {pw}×{ph}." if proxy else "décodage direct."),
        'PHOTO/IO', 'INFO')
    return img, info


def read_scaled_for_owner(owner, width: int, height: int, *, for_export: bool = False) -> QImage:
    
    path = str(getattr(owner, 'photo_path', '') or '')
    if not path:
        return QImage()
    w, h = max(1, int(width)), max(1, int(height))
    source = getattr(owner, '_photo_source_info', {}) or {}
    proxy = bool(source.get('proxy', False))
    if for_export:
        
        
        return read_qimage(path, w, h, allow_large=True)
    if proxy and (w * h) <= _DIRECT_SCALED_MAX_PIXELS and max(w, h) <= 16_384:
        try:
            return read_qimage(path, w, h, allow_large=False)
        except Exception as e:
            qcv_log(f"Décodage preview direct {w}×{h} impossible: {e}; utilisation du proxy.",
                    'PHOTO/IO', 'WARNING')
    img = getattr(owner, 'image', None)
    if img is None or img.isNull():
        return QImage()
    return img.scaled(w, h)
