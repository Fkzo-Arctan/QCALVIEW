

from PIL import Image, TiffImagePlugin

class ImageIFD:
    ImageDescription = 270
    Make = 271
    Model = 272
    Orientation = 274
    Artist = 315
    Copyright = 33432
    DateTime = 306

class ExifIFD:
    DateTimeOriginal = 36867
    DateTimeDigitized = 36868
    UserComment = 37510

class GPSIFD:
    GPSLatitudeRef = 1
    GPSLatitude = 2
    GPSLongitudeRef = 3
    GPSLongitude = 4
    GPSAltitudeRef = 5
    GPSAltitude = 6
    GPSImgDirectionRef = 16
    GPSImgDirection = 17

class helper:
    class UserComment:
        @staticmethod
        def dump(text, encoding='ascii'):
            s = '' if text is None else str(text)
            if encoding.lower().startswith('uni'):
                return b'UNICODE\x00' + s.encode('utf-16-be', errors='replace')
            return b'ASCII\x00\x00\x00' + s.encode('latin-1', errors='replace')


def _to_ifd_rational(v):
    if isinstance(v, TiffImagePlugin.IFDRational):
        return v
    if isinstance(v, tuple) and len(v) == 2:
        return TiffImagePlugin.IFDRational(v[0], v[1])
    return TiffImagePlugin.IFDRational(v)


def dump(exif_dict):
    ex = Image.Exif()
    zeroth = dict(exif_dict.get('0th') or {})
    exif_ifd = dict(exif_dict.get('Exif') or {})
    gps_ifd = dict(exif_dict.get('GPS') or {})
    for k, v in zeroth.items():
        ex[int(k)] = v
    if exif_ifd:
        if ExifIFD.UserComment in exif_ifd and isinstance(exif_ifd[ExifIFD.UserComment], str):
            exif_ifd[ExifIFD.UserComment] = helper.UserComment.dump(exif_ifd[ExifIFD.UserComment])
        ex[34665] = exif_ifd
    if gps_ifd:
        for tag in (GPSIFD.GPSLatitude, GPSIFD.GPSLongitude):
            if tag in gps_ifd:
                gps_ifd[tag] = tuple(_to_ifd_rational(x) for x in gps_ifd[tag])
        for tag in (GPSIFD.GPSAltitude, GPSIFD.GPSImgDirection):
            if tag in gps_ifd:
                gps_ifd[tag] = _to_ifd_rational(gps_ifd[tag])
        ex[34853] = gps_ifd
    return ex.tobytes()
