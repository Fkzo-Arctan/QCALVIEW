


from ._i18n import tr
from ._compat import QC, dialog_exec
"""SPLIT-ONLY extracted implementations from qcalview_window.QCalViewDock.
Attached to the class via setattr after class definition.
"""
import os, sys, math, json, re, pathlib, functools, itertools, typing, csv, shutil, subprocess, tempfile
from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.core import *
from qgis.gui import *


try:
    from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QFont, QPixmap, QTransform
except Exception:
    QImage = QtGui.QImage; QPainter = QtGui.QPainter; QPen = QtGui.QPen
    QColor = QtGui.QColor; QFont = QtGui.QFont; QPixmap = QtGui.QPixmap
    QTransform = getattr(QtGui, 'QTransform', None)
try:
    from qgis.PyQt.QtCore import Qt, QSize, QRect, QPointF, QPoint
except Exception:
    Qt = getattr(QtCore, 'Qt', None); QSize = QtCore.QSize; QRect = QtCore.QRect
    QPointF = QtCore.QPointF; QPoint = QtCore.QPoint
try:
    from qgis.PyQt.QtWidgets import QWidget, QLabel, QFileDialog, QMessageBox
except Exception:
    QWidget = QtWidgets.QWidget; QLabel = QtWidgets.QLabel
    QFileDialog = QtWidgets.QFileDialog; QMessageBox = QtWidgets.QMessageBox



def _bool_export_metadata_enabled(self):
    try:
        cb = getattr(self, 'cb_export_metadata', None)
        return bool(cb and cb.isChecked())
    except Exception:
        return False


def _plugin_root_dir(self):
    try:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    except Exception:
        return os.getcwd()


def _get_embedded_piexif(self):
    
    plugin_root = _plugin_root_dir(self)
    vendor_dir = os.path.join(plugin_root, 'vendor')
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)
    try:
        import piexif  
        return piexif, None
    except Exception as e:
        return None, f"piexif embarqué introuvable ou non chargeable : {e}"


def _camera_point_wgs84(self, layer, feat):
    geom = feat.geometry() if feat is not None else None
    if geom is None or geom.isEmpty():
        return None
    try:
        pt = geom.asPoint()
    except Exception:
        return None
    try:
        src_crs = layer.crs() if layer is not None and hasattr(layer, 'crs') else None
        dst_crs = QgsCoordinateReferenceSystem('EPSG:4326')
        if src_crs is not None and src_crs.isValid() and src_crs.authid() != 'EPSG:4326':
            tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            xy = tr.transform(QgsPointXY(float(pt.x()), float(pt.y())))
            return float(xy.x()), float(xy.y())
        return float(pt.x()), float(pt.y())
    except Exception:
        try:
            return float(pt.x()), float(pt.y())
        except Exception:
            return None


def _format_gps_dms(value, is_lat):
    v = float(value)
    ref = ('N' if v >= 0 else 'S') if is_lat else ('E' if v >= 0 else 'W')
    v = abs(v)
    deg = int(v)
    rem = (v - deg) * 60.0
    mins = int(rem)
    secs = (rem - mins) * 60.0
    return deg, mins, secs, ref


def _fmt_num(value, decimals=1, strip_zero=False):
    try:
        v = float(value)
    except Exception:
        return ''
    s = f"{v:.{int(decimals)}f}"
    if strip_zero and '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def _camera_datetime_candidates(layer, feat, img_path):
    
    vals = []
    
    try:
        from PIL import Image, ExifTags
        with Image.open(img_path) as img:
            try:
                exif = img.getexif()
            except Exception:
                exif = None
        if exif:
            tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            for k in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
                val = tags.get(k)
                if val not in (None, ''):
                    vals.append(val)
    except Exception:
        pass
    
    try:
        if img_path and os.path.exists(img_path):
            dt = QtCore.QDateTime.fromSecsSinceEpoch(int(os.path.getmtime(img_path)))
            if dt.isValid():
                vals.append(dt.toString('yyyy:MM:dd HH:mm:ss'))
    except Exception:
        pass
    
    try:
        field_map = {str(n).lower(): n for n in layer.fields().names()}
    except Exception:
        field_map = {}
    for key in ('datetime', 'date_time', 'dateheure', 'date_heure', 'date', 'heure', 'taken_at', 'capture_dt', 'photo_date', 'qcv_upd'):
        real = field_map.get(key)
        if real:
            try:
                val = feat[real]
                if val not in (None, ''):
                    vals.append(val)
            except Exception:
                pass
    vals.append(QtCore.QDateTime.currentDateTime().toString('yyyy:MM:dd HH:mm:ss'))
    return vals

def _coerce_exif_datetime(value):
    if value in (None, ''):
        return None
    if hasattr(value, 'toString'):
        try:
            
            if hasattr(value, 'date') and hasattr(value, 'time'):
                try:
                    return value.toString('yyyy:MM:dd HH:mm:ss')
                except Exception:
                    pass
            return str(value.toString('yyyy:MM:dd HH:mm:ss'))
        except Exception:
            pass
    s = str(value).strip()
    if not s:
        return None
    s = s.replace('T', ' ')
    for src, dst in ((r'^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2}):(\d{2})', r':: ::'),
                     (r'^(\d{4})/(\d{2})/(\d{2})[ ](\d{2}):(\d{2}):(\d{2})', r':: ::'),
                     (r'^(\d{4})-(\d{2})-(\d{2})$', r':: 00:00:00'),
                     (r'^(\d{4})/(\d{2})/(\d{2})$', r':: 00:00:00')):
        m = re.match(src, s)
        if m:
            return re.sub(src, dst, s)
    m = re.match(r'^(\d{4}):(\d{2}):(\d{2})[ ](\d{2}):(\d{2}):(\d{2})', s)
    if m:
        return s[:19]
    return s[:19]


def _export_title_for_feature(self, layer, feat, export_path):
    for field_name in ('IDPTV', 'name', 'qcv_id'):
        if field_name == 'qcv_id':
            val = _camera_feature_value(layer, feat, 'qcv_id', None)
        else:
            try:
                lowered = {str(n).lower(): n for n in layer.fields().names()}
                real = lowered.get(field_name.lower())
                val = feat[real] if real else None
            except Exception:
                val = None
        if val not in (None, ''):
            return str(val).strip()
    stem = os.path.splitext(os.path.basename(str(export_path or '')))[0]
    return stem or _camera_resolve_title(self, layer, feat)


def _export_metadata_payload(self, out_path, layer, feat):
    if layer is None or feat is None:
        return None
    state = dict(_camera_state_from_feature(self, layer, feat, photo_meta=_camera_read_photo_metadata(_camera_feature_image_path(self, layer, feat)) or {}) or {})
    draft = (getattr(self, '_camera_drafts', {}) or {}).get(int(feat.id()))
    if draft:
        state.update(draft)
    coords = _camera_point_wgs84(self, layer, feat)
    if coords is None:
        return None
    lon, lat = coords
    z_ground = _camera_geom_z(feat)
    try:
        obs_h = float(state.get('qcv_alt', getattr(self, 'd_camheight', None).value() if getattr(self, 'd_camheight', None) else 1.7) or 1.7)
    except Exception:
        obs_h = 1.7
    altitude = (float(z_ground) if z_ground is not None else 0.0) + obs_h
    yaw = float(state.get('qcv_yaw', getattr(self, 'd_yaw', None).value() if getattr(self, 'd_yaw', None) else 0.0) or 0.0) % 360.0
    pitch = float(state.get('qcv_pitch', getattr(self, 'd_pitch', None).value() if getattr(self, 'd_pitch', None) else 0.0) or 0.0)
    roll = float(state.get('qcv_roll', getattr(self, 'd_roll', None).value() if getattr(self, 'd_roll', None) else 0.0) or 0.0)
    hfov = float(state.get('qcv_hfov', getattr(self, 'd_hfov', None).value() if getattr(self, 'd_hfov', None) else 0.0) or 0.0)
    vfov = float(state.get('qcv_vfov', getattr(self, 'd_vfov', None).value() if getattr(self, 'd_vfov', None) else 0.0) or 0.0)
    iw = int(state.get('qcv_iw', 0) or 0)
    ih = int(state.get('qcv_ih', 0) or 0)
    if os.path.exists(out_path):
        img = QImage(out_path)
        if not img.isNull():
            iw = img.width() or iw
            ih = img.height() or ih
    title = _export_title_for_feature(self, layer, feat, out_path)
    desc = f"{title} - Azimut {_fmt_num(yaw,1)} deg - HFOV {_fmt_num(hfov,1,True)} deg - Alt. obs. {obs_h:.2f} m"
    desc_rich = f"{title} – Azimut {_fmt_num(yaw,1)}° – HFOV {_fmt_num(hfov,1,True)}° – Alt. obs. {obs_h:.2f} m"
    comments = [
        'QCALVIEW',
        f'title={title}',
        f'id_metier={_camera_feature_value(layer, feat, "qcv_id", "") or ""}',
        f'numero_pdv={_camera_feature_value(layer, feat, "IDPTV", "") or ""}',
        f'hauteur_observateur_m={obs_h:.2f}',
        f'hfov_deg={_fmt_num(hfov,1)}',
        f'vfov_deg={_fmt_num(vfov,1)}',
        f'pitch_deg={_fmt_num(pitch,1)}',
        f'roll_deg={_fmt_num(roll,1)}',
        f'projection={state.get("qcv_proj", getattr(self, "cmb_proj", None).currentText() if getattr(self, "cmb_proj", None) else "")}',
        f'mode_projection={state.get("qcv_proj", getattr(self, "cmb_proj", None).currentText() if getattr(self, "cmb_proj", None) else "")}',
        f'dimensions_export={iw}x{ih}',
        f'azimut_centre_deg={_fmt_num(yaw,1)}',
        f'date_export={QtCore.QDateTime.currentDateTime().toString(QC.Qt_DateFormat_ISODate)}',
    ]
    dt_str = None
    img_path = _camera_feature_image_path(self, layer, feat)
    for cand in _camera_datetime_candidates(layer, feat, img_path):
        dt_str = _coerce_exif_datetime(cand)
        if dt_str:
            break
    settings = getattr(self, '_settings', None)
    try:
        author = str(settings.value('QCALVIEW/meta_author', 'Fabrice Kerzerho — ArcTan°')) if settings else 'Fabrice Kerzerho — ArcTan°'
        copyright_txt = str(settings.value('QCALVIEW/meta_copyright', '© 2026 Fabrice Kerzerho — ArcTan°')) if settings else '© 2026 Fabrice Kerzerho — ArcTan°'
        if settings:
            settings.setValue('QCALVIEW/meta_author', author)
            settings.setValue('QCALVIEW/meta_copyright', copyright_txt)
    except Exception:
        author = 'Fabrice Kerzerho — ArcTan°'; copyright_txt = '© 2026 Fabrice Kerzerho — ArcTan°'
    proj = str(state.get('qcv_proj', '') or '').upper()
    return {
        'lon': round(float(lon), 6), 'lat': round(float(lat), 6), 'altitude': altitude,
        'yaw': yaw, 'pitch': pitch, 'roll': roll, 'hfov': hfov, 'vfov': vfov,
        'iw': iw, 'ih': ih, 'title': title, 'description': desc, 'description_rich': desc_rich, 'comment': '\n'.join(comments),
        'datetime': dt_str, 'author': author, 'copyright': copyright_txt,
        'projection': proj, 'observer_height': obs_h,
        'export_datetime_iso': QtCore.QDateTime.currentDateTime().toString(QC.Qt_DateFormat_ISODate),
        'qcv_id': _camera_feature_value(layer, feat, 'qcv_id', '') or '',
        'idptv': _camera_feature_value(layer, feat, 'IDPTV', '') or '',
    }


def _deg_to_dms_rationals(value):
    from PIL import TiffImagePlugin
    v = abs(float(value))
    deg = int(v)
    rem = (v - deg) * 60.0
    mins = int(rem)
    secs = round((rem - mins) * 60.0, 10000)
    return (
        TiffImagePlugin.IFDRational(deg, 1),
        TiffImagePlugin.IFDRational(mins, 1),
        TiffImagePlugin.IFDRational(int(round(secs * 10000)), 10000),
    )



def _ascii_safe_text(value, keep_newlines=False):
    s = '' if value is None else str(value)
    s = s.replace('–', '-').replace('—', '-').replace('°', ' deg').replace('º', ' deg').replace('©', '(c)')
    if not keep_newlines:
        s = s.replace('\r', ' ').replace('\n', ' ')
    try:
        return s.encode('ascii', errors='replace').decode('ascii')
    except Exception:
        return s


def _xml_text(value):
    
    text = '' if value is None else str(value)
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))


def _insert_xmp_packet_jpeg(out_path, xmp_xml):
    with open(out_path, 'rb') as f:
        data = f.read()
    if not data.startswith(b'\xff\xd8'):
        raise ValueError('Fichier non JPEG')
    signature = b'http://ns.adobe.com/xap/1.0/' + bytes([0])
    xmp_payload = signature + xmp_xml.encode('utf-8')
    app1 = b'\xff\xe1' + (len(xmp_payload) + 2).to_bytes(2, 'big') + xmp_payload
    out = bytearray(data[:2])
    i = 2
    inserted = False
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            break
        j = i
        while j < n and data[j] == 0xFF:
            j += 1
        if j >= n:
            break
        marker = data[j]
        if marker == 0xDA:  
            if not inserted:
                out.extend(app1)
                inserted = True
            out.extend(data[i:])
            with open(out_path, 'wb') as fw:
                fw.write(out)
            return
        if marker in (0xD8, 0xD9):
            out.extend(data[i:j+1])
            i = j + 1
            continue
        if j + 2 > n:
            break
        seglen = int.from_bytes(data[j+1:j+3], 'big')
        seg_end = j + 1 + seglen
        segment = data[i:seg_end]
        payload = data[j+3:seg_end]
        if marker == 0xE1 and payload.startswith(signature):
            pass
        else:
            out.extend(segment)
        i = seg_end
    if not inserted:
        out.extend(app1)
        out.extend(data[i:])
    with open(out_path, 'wb') as fw:
        fw.write(out)


def _build_xmp_packet(self, payload):
    create_dt = (payload.get('datetime') or '').replace(' ', 'T')
    if create_dt and len(create_dt) >= 19 and create_dt[4] == ':' and create_dt[7] == ':':
        create_dt = create_dt[:4] + '-' + create_dt[5:7] + '-' + create_dt[8:10] + create_dt[10:19]
    title = _xml_text(payload.get('title', ''))
    rich_desc = _xml_text(payload.get('description_rich', payload.get('description', '')))
    rights = _xml_text(payload.get('copyright', ''))
    artist = _xml_text(payload.get('author', ''))
    comment = _xml_text(payload.get('comment', ''))
    def qv(name, val):
        return f'<qcalview:{name}>{_xml_text(val)}</qcalview:{name}>'
    qcal = ''.join([
        qv('idMetier', payload.get('qcv_id', '')),
        qv('numeroPointDeVue', payload.get('idptv', '')),
        qv('azimuthCenterDeg', _fmt_num(payload.get('yaw', 0), 1)),
        qv('hfovDeg', _fmt_num(payload.get('hfov', 0), 1)),
        qv('vfovDeg', _fmt_num(payload.get('vfov', 0), 1)),
        qv('pitchDeg', _fmt_num(payload.get('pitch', 0), 1)),
        qv('rollDeg', _fmt_num(payload.get('roll', 0), 1)),
        qv('observerHeightM', f"{float(payload.get('observer_height', 0)):.2f}"),
        qv('projection', payload.get('projection', '')),
        qv('modeProjection', payload.get('projection', '')),
        qv('exportWidth', payload.get('iw', '')),
        qv('exportHeight', payload.get('ih', '')),
        qv('dateExport', payload.get('export_datetime_iso', '')),
        qv('technicalComment', payload.get('comment', '')),
    ])
    gpano = ''
    if str(payload.get('projection', '')).upper() == 'EQUIRECTANGULAR':
        full_w = int(payload.get('iw', 0) or 0)
        full_h = int(payload.get('ih', 0) or 0)
        gpano = f'''
    <rdf:Description rdf:about=""
      xmlns:GPano="http://ns.google.com/photos/1.0/panorama/"
      GPano:ProjectionType="equirectangular"
      GPano:UsePanoramaViewer="True"
      GPano:CroppedAreaImageWidthPixels="{full_w}"
      GPano:CroppedAreaImageHeightPixels="{full_h}"
      GPano:FullPanoWidthPixels="{full_w}"
      GPano:FullPanoHeightPixels="{full_h}"
      GPano:CroppedAreaLeftPixels="0"
      GPano:CroppedAreaTopPixels="0"
      GPano:PoseHeadingDegrees="{_xml_text(_fmt_num(payload.get('yaw', 0),1))}"
      GPano:InitialViewHeadingDegrees="{_xml_text(_fmt_num(payload.get('yaw', 0),1))}"
      GPano:InitialHorizontalFOVDegrees="{_xml_text(_fmt_num(payload.get('hfov', 0),1))}" />'''
    return f'''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
      xmlns:exif="http://ns.adobe.com/exif/1.0/"
      xmlns:qcalview="https://qcalview.local/ns/1.0/">
      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
      <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{rich_desc}</rdf:li></rdf:Alt></dc:description>
      <dc:rights><rdf:Alt><rdf:li xml:lang="x-default">{rights}</rdf:li></rdf:Alt></dc:rights>
      <tiff:Artist>{artist}</tiff:Artist>
      <xmp:CreateDate>{_xml_text(create_dt)}</xmp:CreateDate>
      <xmp:ModifyDate>{_xml_text(payload.get('export_datetime_iso', ''))}</xmp:ModifyDate>
      <xmp:MetadataDate>{_xml_text(payload.get('export_datetime_iso', ''))}</xmp:MetadataDate>
      <exif:GPSLatitude>{_xml_text(payload.get('lat', ''))}</exif:GPSLatitude>
      <exif:GPSLongitude>{_xml_text(payload.get('lon', ''))}</exif:GPSLongitude>
      <exif:GPSAltitude>{_xml_text(f"{float(payload.get('altitude', 0)):.2f}")}</exif:GPSAltitude>
      <exif:GPSImgDirection>{_xml_text(_fmt_num(payload.get('yaw', 0),1))}</exif:GPSImgDirection>
      <qcalview:TechnicalBlock>{comment}</qcalview:TechnicalBlock>
      {qcal}
    </rdf:Description>{gpano}
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''


def _write_metadata_with_exiftool(self, out_path, layer, feat):
    if not _bool_export_metadata_enabled(self):
        return True, None
    ext = os.path.splitext(str(out_path or ''))[1].lower()
    if ext not in ('.jpg', '.jpeg'):
        return True, "Métadonnées EXIF ignorées : écriture non prise en charge sur ce format (JPEG uniquement)."
    payload = _export_metadata_payload(self, out_path, layer, feat)
    if not payload:
        return False, "Métadonnées non écrites : point de vue ou coordonnées indisponibles."
    piexif, err = _get_embedded_piexif(self)
    if piexif is None:
        return False, err or "piexif embarqué introuvable."
    try:
        from PIL import Image
        img = Image.open(out_path)
        exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}}
        
        exif_dict['0th'][piexif.ImageIFD.ImageDescription] = payload['description']
        exif_dict['0th'][piexif.ImageIFD.Artist] = payload['author']
        exif_dict['0th'][piexif.ImageIFD.Copyright] = payload['copyright']
        if payload.get('datetime'):
            exif_dict['0th'][piexif.ImageIFD.DateTime] = payload['datetime']
            exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = payload['datetime']
            exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = payload['datetime']
        exif_dict['Exif'][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(payload['comment'], encoding='ascii')
        
        exif_dict['GPS'][piexif.GPSIFD.GPSLatitudeRef] = 'N' if payload['lat'] >= 0 else 'S'
        exif_dict['GPS'][piexif.GPSIFD.GPSLatitude] = _deg_to_dms_rationals(payload['lat'])
        exif_dict['GPS'][piexif.GPSIFD.GPSLongitudeRef] = 'E' if payload['lon'] >= 0 else 'W'
        exif_dict['GPS'][piexif.GPSIFD.GPSLongitude] = _deg_to_dms_rationals(payload['lon'])
        exif_dict['GPS'][piexif.GPSIFD.GPSAltitudeRef] = 0
        from PIL import TiffImagePlugin
        exif_dict['GPS'][piexif.GPSIFD.GPSAltitude] = TiffImagePlugin.IFDRational(int(round(max(0.0, float(payload['altitude'])) * 100)), 100)
        exif_dict['GPS'][piexif.GPSIFD.GPSImgDirectionRef] = 'T'
        exif_dict['GPS'][piexif.GPSIFD.GPSImgDirection] = TiffImagePlugin.IFDRational(int(round((float(payload['yaw']) % 360.0) * 10)), 10)
        exif_bytes = piexif.dump(exif_dict)
        tmp_path = out_path + '.qcvtmp.jpg'
        save_kwargs = {'format': 'JPEG', 'exif': exif_bytes}
        try:
            save_kwargs['quality'] = 'keep'
            save_kwargs['subsampling'] = 'keep'
        except Exception:
            save_kwargs['quality'] = 92
        img.save(tmp_path, **save_kwargs)
        try:
            img.close()
        except Exception:
            pass
        os.replace(tmp_path, out_path)
        return True, 'Métadonnées EXIF écrites (piexif embarqué).'
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, f"Échec écriture métadonnées EXIF : {e}"

def export_legend(self):
    if len(self.layer_styles) == 0: return
    img = QImage(420, 40 + 28*len(self.layer_styles), QC.QImage_Format_Format_ARGB32_Premultiplied)
    img.fill(QColor(255,255,255,0))
    p = QPainter(img); p.setRenderHint(QC.QPainter_RenderHint_Antialiasing, True)
    y = 20
    for sty in self.layer_styles:
        pen = QPen(sty.color); pen.setWidth(10); p.setPen(pen)
        p.drawLine(20, y, 80, y)
        p.setPen(QColor(20,20,20,255)); p.setFont(QFont("Arial", 12))
        name = sty.layer.name()
        if sty.label_field:
            name += f"  (label: {sty.label_field})"
        p.drawText(100, y+5, name)
        y += 28
    p.end()
    path, _ = QFileDialog.getSaveFileName(self, tr("Exporter légende PNG"), "", tr("PNG (*.png)"))
    if path: img.save(path, "PNG")

from ._camera_layer_ops import (
    _camera_layer, _camera_current_feature, _camera_feature_image_path, _camera_state_from_feature,
    _camera_resolve_title, _camera_feature_value, _camera_geom_z, _camera_read_photo_metadata,
    _camera_set_status, _camera_visual_state_dirty
)


def _batch_visible_feature_ids(self):
    selected = _selected_batch_feature_ids(self)
    if selected:
        return selected
    combo = getattr(self, 'cmb_cam_feature', None)
    fids = []
    if combo is None:
        return fids
    for i in range(combo.count()):
        try:
            fid = combo.itemData(i)
            if fid is None:
                continue
            fids.append(int(fid))
        except Exception:
            continue
    return fids


def _safe_export_stem(text, fallback='PDV'):
    stem = str(text or '').strip()
    if not stem:
        stem = fallback
    stem = os.path.splitext(os.path.basename(stem))[0]
    stem = re.sub(r'[\\/:*?"<>|]+', '_', stem)
    stem = re.sub(r'\s+', '_', stem).strip('._ ')
    return stem or fallback




def _default_export_dir(self):
    try:
        d = str(getattr(self, '_settings', QtCore.QSettings('ArcTan', 'QCALVIEW')).value('QCALVIEW/export/default_output_dir', '') or '').strip()
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    try:
        hp = QgsProject.instance().homePath()
        if hp and os.path.isdir(hp):
            return hp
    except Exception:
        pass
    return ''

def _feature_export_name(self, layer, feat, existing=None):
    existing = existing or set()
    candidates = [
        _camera_feature_value(layer, feat, 'qcv_id', None),
        feat[_fn] if (_fn := next((n for n in layer.fields().names() if n.lower() == 'idptv'), None)) else None,
        feat[_fn] if (_fn := next((n for n in layer.fields().names() if n.lower() == 'name'), None)) else None,
        feat[_fn] if (_fn := next((n for n in layer.fields().names() if n.lower() == 'filename'), None)) else None,
    ]
    stem = None
    for cand in candidates:
        if cand not in (None, ''):
            stem = _safe_export_stem(cand, fallback=f'PDV_{int(feat.id())}')
            break
    if not stem:
        stem = _safe_export_stem(_camera_resolve_title(self, layer, feat), fallback=f'PDV_{int(feat.id())}')
    base = stem
    n = 2
    while stem.lower() in existing:
        stem = f"{base}_{n}"
        n += 1
    existing.add(stem.lower())
    return stem


def _invalidate_render_caches(self):
    try:
        getattr(self, '_overlay_cache', {}).clear()
    except Exception:
        pass
    try:
        getattr(self, '_geom_cache', {}).clear()
    except Exception:
        pass
    try:
        self._base_cache.clear()
    except Exception:
        pass
    try:
        self._z_cache.clear()
    except Exception:
        pass
    try:
        self._horizon = None
        self._horizon_params = None
    except Exception:
        pass


def _render_current_export_image(self, mode='composite', schematic_transparent=None):
    w = max(1, int(self.spin_w.value()))
    h = max(1, int(self.spin_h.value()))
    _invalidate_render_caches(self)
    prev_quality = getattr(self, '_current_render_quality', 'high')
    prev_viewer_full = bool(getattr(self, '_viewer_full_res', False))
    prev_lowlat = bool(getattr(self, 'cb_lowlat', None) and self.cb_lowlat.isChecked())
    prev_export_flag = bool(getattr(self, '_qcv_export_in_progress', False))
    try:
        self._qcv_export_in_progress = True
        self._current_render_quality = 'high'
        self._viewer_full_res = False
        if getattr(self, 'cb_lowlat', None) is not None:
            self.cb_lowlat.blockSignals(True)
            self.cb_lowlat.setChecked(False)
            self.cb_lowlat.blockSignals(False)
        overlay = self._render_overlay(width=w, height=h)
        if mode == 'overlay':
            return overlay
        if getattr(self, 'image', None) is None or self.image.isNull():
            try:
                base = self._make_schematic_base(
                    w, h, for_export=True, force_transparent=schematic_transparent)
            except Exception:
                base = None
        else:
            base = self._get_base_scaled(w, h)
        if base is None or base.isNull():
            return None
        composed = QImage(base)
        qp = QPainter(composed)
        qp.drawImage(0, 0, overlay)
        qp.end()
        return composed
    finally:
        if getattr(self, 'cb_lowlat', None) is not None:
            self.cb_lowlat.blockSignals(True)
            self.cb_lowlat.setChecked(prev_lowlat)
            self.cb_lowlat.blockSignals(False)
        self._viewer_full_res = prev_viewer_full
        self._current_render_quality = prev_quality
        self._qcv_export_in_progress = prev_export_flag


def _batch_render_feature_to_file(self, out_dir, mode, stem, layer=None, feat=None):
    is_schematic = (getattr(self, 'image', None) is None or self.image.isNull())
    
    
    schematic_alpha = bool(is_schematic and getattr(self, '_schematic_background_transparent', lambda: False)())
    img = _render_current_export_image(self, mode=mode, schematic_transparent=schematic_alpha)
    if img is None or img.isNull():
        return None
    if mode == 'overlay' or (mode == 'composite' and is_schematic):
        ext = '.png'
        out_stem = (stem + '_overlay') if mode == 'overlay' else stem
        out_path = os.path.join(out_dir, out_stem + ext)
        ok = img.save(out_path, 'PNG')
    else:
        ext = '.jpg'
        out_path = os.path.join(out_dir, stem + ext)
        ok = img.save(out_path, 'JPG', 92)
        if not ok:
            ok = img.save(out_path, 'JPEG', 92)
    if not ok:
        return None
    try:
        ok_meta, err_meta = _write_metadata_with_exiftool(self, out_path, layer, feat)
    except Exception as e:
        ok_meta, err_meta = False, str(e)
    try:
        if not ok_meta:
            _camera_set_status(self, err_meta or 'Échec écriture métadonnées.', '#c44')
        elif err_meta:
            _camera_set_status(self, err_meta, '#666')
    except Exception:
        pass
    return out_path, ok_meta, err_meta


def _batch_restore_current_feature(self, original_fid):
    if original_fid is None:
        return
    try:
        self._camera_select_combo_feature_by_fid(int(original_fid), autoload=True)
    except Exception:
        pass


def _collect_camera_export_rows(self):
    layer = _camera_layer(self)
    if layer is None:
        return [], []
    rows = []
    fids = _batch_visible_feature_ids(self)
    field_names = [f.name() for f in layer.fields()]
    names_seen = set()
    for fid in fids:
        try:
            feat = layer.getFeature(int(fid))
        except Exception:
            feat = None
        if feat is None or not feat.isValid():
            continue
        img_path = _camera_feature_image_path(self, layer, feat)
        meta = _camera_read_photo_metadata(img_path) if img_path else {}
        state = dict(_camera_state_from_feature(self, layer, feat, photo_meta=meta) or {})
        draft = (getattr(self, '_camera_drafts', {}) or {}).get(int(fid))
        if draft:
            state.update(draft)
        geom = feat.geometry()
        x = y = None
        if geom is not None and not geom.isEmpty():
            try:
                pt = geom.asPoint()
                x = float(pt.x())
                y = float(pt.y())
            except Exception:
                pass
        row = {
            'fid': int(fid),
            'pdv_title': _camera_resolve_title(self, layer, feat),
            'export_name': _feature_export_name(self, layer, feat, existing=names_seen),
            'image_path': img_path or state.get('qcv_img') or '',
            'image_exists': 1 if img_path and os.path.exists(img_path) else 0,
            'view_mode': 'photo' if img_path and os.path.exists(img_path) else 'schematic',
            'projection': state.get('qcv_proj', ''),
            'is_360': int(state.get('qcv_360', 0) or 0),
            'yaw': state.get('qcv_yaw', ''),
            'pitch': state.get('qcv_pitch', ''),
            'roll': state.get('qcv_roll', ''),
            'hfov': state.get('qcv_hfov', ''),
            'vfov': state.get('qcv_vfov', ''),
            'hfov_auto': state.get('qcv_ahf', ''),
            'altitude': state.get('qcv_alt', ''),
            'offset_h': state.get('qcv_ofh', ''),
            'offset_v': state.get('qcv_ofv', ''),
            'offset_mode': state.get('qcv_ofmd', ''),
            'max_distance': state.get('qcv_mdst', ''),
            'image_width': state.get('qcv_iw', meta.get('ImageWidth', '')),
            'image_height': state.get('qcv_ih', meta.get('ImageHeight', '')),
            'focal_mm': state.get('qcv_foc', meta.get('FocalLength', '')),
            'sensor_width_mm': state.get('qcv_sens', meta.get('SensorWidthMM', '')),
            'updated': _camera_feature_value(layer, feat, 'qcv_upd', state.get('qcv_upd', '')),
            'x': x if x is not None else '',
            'y': y if y is not None else '',
            'z_geom': _camera_geom_z(feat) if _camera_geom_z(feat) is not None else '',
            'layer_name': layer.name(),
            'layer_crs': layer.crs().authid() if layer.crs().isValid() else '',
        }
        for name in field_names:
            key = f'attr_{name}'
            try:
                val = feat[name]
            except Exception:
                val = ''
            row[key] = '' if val is None else val
        rows.append(row)
    header = list(rows[0].keys()) if rows else []
    return header, rows


def export_camera_variables_csv(self):
    layer = _camera_layer(self)
    if layer is None:
        QMessageBox.information(self, tr('QCALVIEW'), tr('Choisissez d’abord une couche caméra.'))
        return
    path, _ = QFileDialog.getSaveFileName(self, tr('Exporter CSV des variables caméra'), '', tr('CSV (*.csv)'))
    if not path:
        return
    if not path.lower().endswith('.csv'):
        path += '.csv'
    header, rows = _collect_camera_export_rows(self)
    if not rows:
        QMessageBox.information(self, tr('QCALVIEW'), tr('Aucun point de vue visible à exporter.'))
        return
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    try:
        _camera_set_status(self, f'CSV exporté : {os.path.basename(path)}', '#2b6')
    except Exception:
        pass
    QMessageBox.information(self, tr('QCALVIEW'), tr(f'CSV exporté :\n{path}'))




def export_current_composite(self):
    
    layer = _camera_layer(self)
    feat = _camera_current_feature(self)
    is_schematic = (getattr(self, 'image', None) is None or self.image.isNull())
    if layer is not None and feat is not None:
        stem = _feature_export_name(self, layer, feat, existing=set())
    else:
        stem = _safe_export_stem(os.path.splitext(os.path.basename(str(getattr(self, 'photo_path', '') or 'qcalview_export')))[0], 'qcalview_export')
    default_dir = _default_export_dir(self)
    default_ext = '.png' if is_schematic else '.jpg'
    default_path = os.path.join(default_dir, stem + default_ext) if default_dir else stem + default_ext
    filters = 'PNG (*.png);;JPEG (*.jpg *.jpeg)'
    path, selected = QFileDialog.getSaveFileName(self, tr('Exporter la vue composite'), default_path, tr(filters))
    if not path:
        return
    low = path.lower()
    if not low.endswith(('.png', '.jpg', '.jpeg')):
        path += '.png' if ('PNG' in str(selected) or is_schematic) else '.jpg'
        low = path.lower()

    
    
    use_alpha = bool(is_schematic and low.endswith('.png') and
                     getattr(self, '_schematic_background_transparent', lambda: False)())
    img = _render_current_export_image(self, mode='composite', schematic_transparent=use_alpha)
    if img is None or img.isNull():
        QMessageBox.warning(self, tr('QCALVIEW'), tr('Export impossible : rendu indisponible.'))
        return

    if low.endswith('.png'):
        ok = img.save(path, 'PNG')
    else:
        ok = img.save(path, 'JPG', 92)
        if not ok:
            ok = img.save(path, 'JPEG', 92)
    if not ok:
        QMessageBox.warning(self, tr('QCALVIEW'), tr(f'Impossible d’écrire le fichier :\n{path}'))
        return

    ok_meta = True; meta_msg = None
    try:
        ok_meta, meta_msg = _write_metadata_with_exiftool(self, path, layer, feat)
    except Exception as e:
        ok_meta, meta_msg = False, str(e)
    try:
        if ok_meta:
            _camera_set_status(self, f'Vue exportée : {os.path.basename(path)}', '#2b6')
        else:
            _camera_set_status(self, meta_msg or 'Échec écriture métadonnées.', '#c44')
    except Exception:
        pass
    msg = f'Vue exportée :\n{path}'
    if is_schematic and low.endswith('.png'):
        msg += '\n\nFond : ' + ('transparent' if use_alpha else 'opaque')
    if meta_msg and _bool_export_metadata_enabled(self):
        msg += f'\n\nMétadonnées : {meta_msg}'
    QMessageBox.information(self, tr('QCALVIEW'), tr(msg))

def _batch_checked_row_brush(table):
    
    try:
        pal = table.palette()
        base = pal.color(QtGui.QPalette.ColorRole.Base)
        hi = pal.color(QtGui.QPalette.ColorRole.Highlight)
    except Exception:
        try:
            base = table.palette().base().color()
            hi = table.palette().highlight().color()
        except Exception:
            base = QColor(255, 255, 255)
            hi = QColor(70, 150, 110)
    
    t = 0.16
    c = QColor(
        int(round(base.red()   * (1.0 - t) + hi.red()   * t)),
        int(round(base.green() * (1.0 - t) + hi.green() * t)),
        int(round(base.blue()  * (1.0 - t) + hi.blue()  * t)),
        base.alpha(),
    )
    return QtGui.QBrush(c)


def _apply_batch_row_highlight(self, row):
    table = getattr(self, 'tbl_export_pdv', None)
    if table is None or row < 0 or row >= table.rowCount():
        return
    try:
        check = table.item(int(row), 0)
        checked = bool(check is not None and check.checkState() == QC.Qt_CheckState_Checked)
    except Exception:
        checked = False
    brush = _batch_checked_row_brush(table) if checked else QtGui.QBrush()
    for c in range(table.columnCount()):
        try:
            it = table.item(int(row), int(c))
            if it is not None:
                it.setBackground(brush)
        except Exception:
            pass


def _on_batch_table_item_changed(self, item):
    
    table = getattr(self, 'tbl_export_pdv', None)
    if table is None or item is None:
        return
    try:
        if int(item.column()) != 0:
            return
        _apply_batch_row_highlight(self, int(item.row()))
        checked = len(_selected_batch_feature_ids(self))
        if hasattr(self, 'lbl_batch_status'):
            self.lbl_batch_status.setText(
                tr(f'{checked}/{table.rowCount()} point(s) de vue cochés pour export. '
                '« Brouillon » = réglage non persisté dans la couche PDV.')
            )
    except Exception:
        pass


def _set_all_batch_rows_checked(self, checked=True):
    
    table = getattr(self, 'tbl_export_pdv', None)
    if table is None:
        return
    try:
        table.blockSignals(True)
        state = QC.Qt_CheckState_Checked if bool(checked) else QC.Qt_CheckState_Unchecked
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it is not None:
                it.setCheckState(state)
            _apply_batch_row_highlight(self, r)
    finally:
        try:
            table.blockSignals(False)
        except Exception:
            pass
    try:
        count = table.rowCount() if checked else 0
        if hasattr(self, 'lbl_batch_status'):
            self.lbl_batch_status.setText(
                tr(f'{count}/{table.rowCount()} point(s) de vue cochés pour export. '
                '« Brouillon » = réglage non persisté dans la couche PDV.')
            )
    except Exception:
        pass


def _fit_export_table_height(self):
    
    table = getattr(self, 'tbl_export_pdv', None)
    if table is None:
        return
    try:
        rows = int(table.rowCount())
        header_h = max(24, int(table.horizontalHeader().height()))
        row_h = max(22, int(table.verticalHeader().defaultSectionSize()))
        content_h = header_h + max(1, rows) * row_h + 2 * int(table.frameWidth()) + 6

        
        
        sa = getattr(self, 'tab_export', None)
        viewport_h = 0
        try:
            viewport_h = int(sa.viewport().height()) if sa is not None else 0
        except Exception:
            viewport_h = 0
        if viewport_h <= 0:
            viewport_h = max(320, int(getattr(self, 'height', lambda: 700)()))
        available_h = max(120, viewport_h - 155)
        target = max(120, min(int(content_h), int(available_h)))
        table.setMinimumHeight(target)
        table.setMaximumHeight(target)
    except Exception:
        pass


def _selected_batch_feature_ids(self):
    table = getattr(self, 'tbl_export_pdv', None)
    if table is None:
        return []
    fids = []
    for r in range(table.rowCount()):
        try:
            item = table.item(r, 0)
            if item is None or item.checkState() != QC.Qt_CheckState_Checked:
                continue
            fid = item.data(QC.Qt_ItemDataRole_UserRole)
            if fid is None:
                continue
            fids.append(int(fid))
        except Exception:
            continue
    return fids


def _batch_has_unsaved_selected(self, fids):
    drafts = getattr(self, '_camera_drafts', {}) or {}
    dirty = []
    for fid in fids:
        try:
            if int(fid) in drafts:
                dirty.append(int(fid))
        except Exception:
            pass
    return dirty


def _batch_preflight_save_current_visual_state(self, fids):
    
    try:
        current_fid=getattr(self,'_camera_current_fid',None)
        current_fid=int(current_fid) if current_fid is not None else None
    except Exception:
        current_fid=None
    if current_fid is None or current_fid not in set(int(x) for x in (fids or [])):
        return True

    param_dirty=False
    try:
        param_dirty=current_fid in (getattr(self,'_camera_drafts',{}) or {})
    except Exception:
        pass
    try:
        visual_dirty=bool(_camera_visual_state_dirty(self,current_fid))
    except Exception:
        visual_dirty=True

    if not (param_dirty or visual_dirty):
        return True

    parts=[]
    if visual_dirty:
        parts.append('le thème QGIS / les couches projetées / les réglages visuels')
    if param_dirty:
        parts.append('les paramètres caméra')
    details=' et '.join(parts) if parts else 'l’état courant'
    rep=QMessageBox.warning(
        self,
        tr('QCALVIEW — état non enregistré'),
        tr('Le point de vue courant contient des modifications non enregistrées :\n'
        f'• {details}.\n\n'
        'Un export batch recharge chaque PDV depuis son état enregistré. Sans enregistrement, '
        'le thème peut donc revenir sur « aucun » et les overlays disparaître de l’export.\n\n'
        'Enregistrer maintenant « paramètres + état » avant de lancer l’export ?'),
        QC.QMessageBox_StandardButton_Yes | QC.QMessageBox_StandardButton_No,
        QC.QMessageBox_StandardButton_Yes
    )
    if rep != QC.QMessageBox_StandardButton_Yes:
        _camera_set_status(self,'Export annulé : état courant non enregistré.','#b36b00')
        return False

    try:
        self._camera_save_current_feature()
    except Exception as exc:
        QMessageBox.warning(self,tr('QCALVIEW'),tr(f"Impossible d’enregistrer l’état avant export :\n{exc}"))
        return False

    
    try:
        still_param=current_fid in (getattr(self,'_camera_drafts',{}) or {})
    except Exception:
        still_param=True
    try:
        still_visual=bool(_camera_visual_state_dirty(self,current_fid))
    except Exception:
        still_visual=True
    if still_param or still_visual:
        QMessageBox.warning(
            self,tr('QCALVIEW'),
            tr('L’état du point de vue n’a pas pu être confirmé comme enregistré.\n'
            'L’export est annulé afin d’éviter un rendu différent de l’aperçu.')
        )
        return False
    _camera_set_status(self,'État courant enregistré et validé pour l’export.','#2b6')
    return True


def _refresh_batch_pdv_table(self):
    table = getattr(self, 'tbl_export_pdv', None)
    layer = _camera_layer(self)
    if table is None:
        return
    
    prev_checked = set()
    had_rows = False
    try:
        had_rows = table.rowCount() > 0
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it and it.checkState() == QC.Qt_CheckState_Checked:
                prev_checked.add(int(it.data(QC.Qt_ItemDataRole_UserRole)))
    except Exception:
        pass
    try:
        table.blockSignals(True)
    except Exception:
        pass
    table.setRowCount(0)
    if layer is None:
        try:
            table.blockSignals(False)
        except Exception:
            pass
        return
    try:
        features = []
        for feat in layer.getFeatures():
            label = _camera_resolve_title(self, layer, feat)
            try:
                sort_key = (0, float(label))
            except Exception:
                sort_key = (1, str(label).lower())
            features.append((sort_key, feat))
        for r, (_sort, feat) in enumerate(sorted(features, key=lambda x: x[0])):
            fid = int(feat.id())
            img_path = _camera_feature_image_path(self, layer, feat)
            meta = _camera_read_photo_metadata(img_path) if img_path else {}
            state = dict(_camera_state_from_feature(self, layer, feat, photo_meta=meta) or {})
            draft = (getattr(self, '_camera_drafts', {}) or {}).get(fid)
            
            
            
            
            if draft:
                state.update(draft)
            saved = 'Brouillon' if draft else 'OK'
            status = 'photo' if img_path and os.path.exists(img_path) else 'schéma'
            vals = [
                _camera_resolve_title(self, layer, feat),
                os.path.basename(img_path) if img_path else '— schéma —',
                str(state.get('qcv_proj', '') or ''),
                _fmt_num(state.get('qcv_yaw', ''), 1) if state.get('qcv_yaw', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_pitch', ''), 1) if state.get('qcv_pitch', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_roll', ''), 1) if state.get('qcv_roll', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_hfov', ''), 1) if state.get('qcv_hfov', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_vfov', ''), 1) if state.get('qcv_vfov', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_ofh', ''), 1) if state.get('qcv_ofh', '') not in ('', None) else '',
                _fmt_num(state.get('qcv_ofv', ''), 1) if state.get('qcv_ofv', '') not in ('', None) else '',
                saved,
                status,
            ]
            table.insertRow(r)
            check = QtWidgets.QTableWidgetItem(tr(''))
            check.setFlags((check.flags() | QC.Qt_ItemFlag_ItemIsUserCheckable | QC.Qt_ItemFlag_ItemIsEnabled | QC.Qt_ItemFlag_ItemIsSelectable) & ~QC.Qt_ItemFlag_ItemIsEditable)
            check.setCheckState(QC.Qt_CheckState_Checked if ((not had_rows) or fid in prev_checked) else QC.Qt_CheckState_Unchecked)
            check.setData(QC.Qt_ItemDataRole_UserRole, fid)
            table.setItem(r, 0, check)
            for c, val in enumerate(vals, start=1):
                item = QtWidgets.QTableWidgetItem(tr(str(val)))
                item.setFlags((item.flags() | QC.Qt_ItemFlag_ItemIsEnabled | QC.Qt_ItemFlag_ItemIsSelectable) & ~QC.Qt_ItemFlag_ItemIsEditable)
                table.setItem(r, c, item)
        try:
            for _r in range(table.rowCount()):
                _apply_batch_row_highlight(self, _r)
            _fit_export_table_height(self)
            if hasattr(self, 'lbl_batch_status'):
                _checked = len(_selected_batch_feature_ids(self))
                self.lbl_batch_status.setText(tr(f'{_checked}/{table.rowCount()} point(s) de vue cochés pour export. « Brouillon » = non persisté dans la couche PDV.'))
        except Exception:
            pass
    finally:
        try:
            table.blockSignals(False)
        except Exception:
            pass


def export_batch_selected(self):
    fids = _selected_batch_feature_ids(self)
    if not fids:
        QMessageBox.information(self, tr('QCALVIEW'), tr('Cochez au moins un point de vue à exporter.'))
        return
    dirty = _batch_has_unsaved_selected(self, fids)
    
    
    
    
    try:
        _cur = int(getattr(self, '_camera_current_fid', -999999))
    except Exception:
        _cur = -999999
    dirty = [int(fid) for fid in dirty if int(fid) != _cur]
    if dirty:
        rep = QMessageBox.warning(
            self,
            tr('QCALVIEW'),
            tr('Certains autres points de vue cochés comportent des réglages caméra en brouillon.\n\n'
            'Le batch utilisera ces réglages en mémoire (projection, FOV, offsets, tangage, roulis…), '
            'mais ils ne seront pas écrits dans la couche PDV.\n\n'
            'Continuer ?'),
            QC.QMessageBox_StandardButton_Yes | QC.QMessageBox_StandardButton_No,
            QC.QMessageBox_StandardButton_Yes
        )
        if rep != QC.QMessageBox_StandardButton_Yes:
            return
    if not (bool(getattr(self, 'cb_batch_composite', None) and self.cb_batch_composite.isChecked()) or
            bool(getattr(self, 'cb_batch_overlay', None) and self.cb_batch_overlay.isChecked()) or
            bool(getattr(self, 'cb_batch_csv', None) and self.cb_batch_csv.isChecked())):
        QMessageBox.information(self, tr('QCALVIEW'), tr('Choisissez au moins un type de sortie batch.'))
        return
    return _run_batch_export(self, mode='selected')

def _run_batch_export(self, mode='composite'):
    layer = _camera_layer(self)
    if layer is None:
        QMessageBox.information(self, tr('QCALVIEW'), tr('Choisissez d’abord une couche caméra.'))
        return
    fids = _batch_visible_feature_ids(self)
    if not fids:
        QMessageBox.information(self, tr('QCALVIEW'), tr('Aucun point de vue visible à exporter.'))
        return
    
    
    
    if not _batch_preflight_save_current_visual_state(self, fids):
        return
    out_dir = QFileDialog.getExistingDirectory(self, tr('Choisir le dossier de sortie'), _default_export_dir(self))
    if not out_dir:
        return
    selected_modes = []
    if mode == 'selected':
        if bool(getattr(self, 'cb_batch_composite', None) and self.cb_batch_composite.isChecked()):
            selected_modes.append('composite')
        if bool(getattr(self, 'cb_batch_overlay', None) and self.cb_batch_overlay.isChecked()):
            selected_modes.append('overlay')
        if not selected_modes and not bool(getattr(self, 'cb_batch_csv', None) and self.cb_batch_csv.isChecked()):
            QMessageBox.information(self, tr('QCALVIEW'), tr('Choisissez au moins un type de sortie batch.'))
            return
    else:
        selected_modes = [mode]
    original_fid = getattr(self, '_camera_current_fid', None)
    saved_drafts = dict(getattr(self, '_camera_drafts', {}) or {})
    try:
        
        
        
        names_seen = set()
        prog = QtWidgets.QProgressDialog(tr('Export batch QCALVIEW…'), tr('Annuler'), 0, len(fids), self)
        prog.setWindowTitle(tr('QCALVIEW'))
        prog.setWindowModality(QC.Qt_WindowModality_WindowModal)
        prog.setMinimumDuration(0)
        exported = []
        skipped = []
        meta_ok = 0
        meta_fail = 0
        meta_notes = []
        for idx, fid in enumerate(fids, start=1):
            prog.setValue(idx - 1)
            prog.setLabelText(tr(f'Export du point {idx}/{len(fids)}…'))
            QtWidgets.QApplication.processEvents()
            if prog.wasCanceled():
                break
            if not self._camera_select_combo_feature_by_fid(int(fid), autoload=True):
                skipped.append((fid, 'sélection impossible'))
                continue
            feat = _camera_current_feature(self)
            if feat is None:
                skipped.append((fid, 'feature introuvable'))
                continue
            stem = _feature_export_name(self, layer, feat, existing=names_seen)
            for one_mode in selected_modes:
                result = _batch_render_feature_to_file(self, out_dir, one_mode, stem, layer=layer, feat=feat)
                if result:
                    out_path, ok_meta, meta_msg = result
                    exported.append(out_path)
                    if ok_meta:
                        if meta_msg and 'JPEG uniquement' not in str(meta_msg):
                            meta_ok += 1
                    else:
                        meta_fail += 1
                        if meta_msg:
                            meta_notes.append(f'{stem}: {meta_msg}')
                else:
                    skipped.append((fid, 'échec export ' + str(one_mode)))
        prog.setValue(len(fids))
    finally:
        try:
            self._camera_drafts = saved_drafts
        except Exception:
            pass
        _batch_restore_current_feature(self, original_fid)
    if mode == 'selected' and bool(getattr(self, 'cb_batch_csv', None) and self.cb_batch_csv.isChecked()):
        try:
            header, rows = _collect_camera_export_rows(self)
            rows = [r for r in rows if int(r.get('fid', -1)) in set(int(x) for x in fids)]
            if rows:
                csv_path = os.path.join(out_dir, 'qcalview_variables.csv')
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
                    writer.writeheader(); writer.writerows(rows)
                exported.append(csv_path)
        except Exception as e:
            skipped.append(('CSV', str(e)))
    summary = [f'Exports réussis : {len(exported)}']
    if skipped:
        summary.append(f'Échecs / ignorés : {len(skipped)}')
    if _bool_export_metadata_enabled(self):
        summary.append(f'Métadonnées écrites : {meta_ok}')
        if meta_fail:
            summary.append(f'Échecs métadonnées : {meta_fail}')
        if meta_notes:
            summary.extend(meta_notes[:5])
            if len(meta_notes) > 5:
                summary.append(f'… {len(meta_notes)-5} autre(s) échec(s).')
    summary.append(f'Dossier : {out_dir}')
    QMessageBox.information(self, tr('QCALVIEW'), tr('\n'.join(summary)))


def export_batch_composite(self):
    return _run_batch_export(self, mode='composite')


def export_batch_overlay_only(self):
    return _run_batch_export(self, mode='overlay')
