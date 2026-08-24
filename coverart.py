"""
Golden Music — Album art extraction (thread-safe).
Returns raw bytes (not QPixmap) for safe cross-thread transfer.
"""
import io
import logging
from pathlib import Path

from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import QByteArray, Qt

log = logging.getLogger("app.coverart")

_cover_cache = {}
_CACHE_MAX = 200
# Embedded cover art is typically <1 MiB; anything far larger is either a
# bloated file or a maliciously crafted one — skip it to protect memory.
COVER_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB
DECODED_IMAGE_MAX_BYTES = 64 * 1024 * 1024  # cap decoded-pixel memory per image


def get_cover_bytes(filepath: str) -> bytes:
    if filepath in _cover_cache:
        return _cover_cache[filepath]
    data = _extract_cover_bytes(filepath)
    if data is not None and len(data) > COVER_MAX_BYTES:
        log.warning(f"Cover art too large ({len(data)} bytes), skipping: {filepath}")
        return None
    _cache_put(filepath, data)
    return data


def get_cover_pixmap(filepath: str, size: int = 0) -> QPixmap:
    data = get_cover_bytes(filepath)
    if data is None:
        return None
    ba = QByteArray(data)
    pm = QPixmap()
    if not pm.loadFromData(ba):
        return None
    if size > 0:
        pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return pm


def bytes_to_pixmap(data: bytes) -> QPixmap:
    if not data or len(data) > COVER_MAX_BYTES:
        return QPixmap()
    # Reject decompression bombs: a few MB of compressed data can decode to
    # gigabytes of pixels. Cap total decoded pixel memory.
    img = QImage.fromData(QByteArray(data))
    if img.isNull():
        return QPixmap()
    if img.width() * img.height() * 4 > DECODED_IMAGE_MAX_BYTES:
        log.warning("Cover image too large to decode "
                        f"({img.width()}x{img.height()}), skipping.")
        return QPixmap()
    return QPixmap.fromImage(img)


def _cache_put(filepath: str, data):
    if len(_cover_cache) >= _CACHE_MAX:
        try:
            _cover_cache.pop(next(iter(_cover_cache)))
        except StopIteration:
            pass
    _cover_cache[filepath] = data


def _extract_cover_bytes(filepath: str) -> bytes:
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".mp3":
            return _extract_mp3_bytes(filepath)
        elif ext == ".flac":
            return _extract_flac_bytes(filepath)
        elif ext in (".ogg", ".oga"):
            return _extract_ogg_bytes(filepath)
        elif ext in (".m4a", ".mp4", ".alac"):
            return _extract_m4a_bytes(filepath)
        elif ext == ".wma":
            return _extract_wma_bytes(filepath)
        else:
            return None
    except Exception:
        return None


def _extract_mp3_bytes(filepath: str) -> bytes:
    from mutagen.mp3 import MP3
    try:
        audio = MP3(filepath)
        if audio.tags is None:
            return None
        for key in audio.tags:
            if key.startswith("APIC:"):
                return audio.tags[key].data
        return None
    except Exception:
        return None


def _extract_flac_bytes(filepath: str) -> bytes:
    from mutagen.flac import FLAC
    try:
        audio = FLAC(filepath)
        if audio.pictures:
            return audio.pictures[0].data
        return None
    except Exception:
        return None


def _extract_ogg_bytes(filepath: str) -> bytes:
    from mutagen.oggvorbis import OggVorbis
    try:
        audio = OggVorbis(filepath)
        if "metadata_block_picture" in audio:
            from mutagen.flac import Picture
            data = audio["metadata_block_picture"][0]
            pic = Picture(data)
            return pic.data
        return None
    except Exception:
        return None


def _extract_m4a_bytes(filepath: str) -> bytes:
    from mutagen.mp4 import MP4
    try:
        audio = MP4(filepath)
        if "covr" in audio.tags:
            covr = audio.tags["covr"][0]
            return bytes(covr)
        return None
    except Exception:
        return None


def _extract_wma_bytes(filepath: str) -> bytes:
    try:
        from mutagen.asf import ASF
        audio = ASF(filepath)
        if "WM/Picture" in audio:
            pic = audio["WM/Picture"][0]
            return pic.data
        return None
    except Exception:
        return None


def clear_cache():
    _cover_cache.clear()
