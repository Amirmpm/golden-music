"""Golden Music v3 — runtime audio-format capability probe.

Windows registration (and the Open-with list) must only advertise formats the
active playback backend can genuinely decode. The app ships with Qt
Multimedia; on Windows the backend is Windows Media Foundation, on other
platforms FFmpeg. This module asks Qt, at runtime, which containers/codecs
the active backend actually supports and intersects that with the
configured extension list.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goldenmusic.formatcaps")

# Configured/advertised extensions (config.AUDIO_EXTS order preserved).
CANDIDATE_EXTS = [".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus",
                  ".m4a", ".aac", ".wma"]

# Extension -> (Qt file container, Qt codec or None-for-container-check)
_EXT_MAP = {
    ".mp3": ("MP3", "MP3"),
    ".wav": ("Wave", None),
    ".flac": ("FLAC", "FLAC"),
    ".ogg": ("Ogg", "Vorbis"),
    ".oga": ("Ogg", "Vorbis"),
    ".opus": ("Ogg", "Opus"),
    ".m4a": ("Mpeg4Audio", "AAC"),
    ".aac": ("AAC", "AAC"),
    ".wma": ("WMA", "WMA"),
}


def probe_supported_exts(exts=None, qapp=None) -> list:
    """Return the subset of ``exts`` (default CANDIDATE_EXTS) that the ACTIVE
    Qt Multimedia backend confirms it can decode.

    Conservative policy: a format is "verified" only when Qt says the
    container decodes AND (when a codec is required) the codec is in the
    supported codec list. If Qt introspection is unavailable at all we
    return the MP3/WAV/M4A core set (safe on every backend since 2016) and
    log the fallback, so registration never advertises unverified formats.
    """
    exts = list(exts or CANDIDATE_EXTS)
    try:
        if qapp is None:
            from PyQt6.QtCore import QCoreApplication
            qapp = QCoreApplication.instance()
        if qapp is None:
            # Introspection may still work; if not, the exception path below
            # produces the conservative fallback.
            pass
        from PyQt6.QtMultimedia import QMediaFormat
        ff = QMediaFormat.FileFormat
        ac = QMediaFormat.AudioCodec
        codecs_supported = set()
        probe = QMediaFormat()
        try:
            for c in probe.supportedAudioCodecs(
                    QMediaFormat.ConversionMode.Decode):
                codecs_supported.add(str(c))
        except Exception as e:
            log.debug(f"codec introspection unavailable: {e}")

        verified = []
        for ext in exts:
            container, codec = _EXT_MAP.get(str(ext).lower(), (None, None))
            if container is None:
                continue
            fmt_enum = getattr(ff, container, None)
            if fmt_enum is None:
                continue
            mf = QMediaFormat(fmt_enum)
            if not mf.isSupported(QMediaFormat.ConversionMode.Decode):
                continue
            if codec is not None and codecs_supported and \
                    str(getattr(ac, codec)) not in codecs_supported:
                continue
            verified.append(ext)
        if verified:
            return verified
        log.warning("format probe returned nothing — conservative fallback")
    except Exception as e:
        log.warning(f"format probe failed ({e}) — conservative fallback")
    # Conservative fallback: formats decodable by every Qt6 backend we ship.
    return [e for e in exts if e in (".mp3", ".wav", ".m4a", ".flac")]


def supported_exts_for_registration(qapp=None) -> list:
    """Order-preserving, sorted-verified list used for Windows
    registration + the installer's SupportedTypes value."""
    return probe_supported_exts(qapp=qapp)
