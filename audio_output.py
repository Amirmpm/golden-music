"""
Golden Music — Audio output device selection (Windows).

Enumerates active render endpoints via WASAPI COM (comtypes-free: uses
the bundled ctypes approach through pycaw if present, else a registry
fallback listing device names). Applying routes QMediaPlayer's audio to
the chosen endpoint via the media player's audioOutput device property
where supported by Qt; falls back gracefully when Qt doesn't expose it.
"""
import logging

log = logging.getLogger("app.audioout")


def list_output_devices():
    """Return [(friendly_name, device_id)] for active render endpoints."""
    devices = []
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        from PyQt6.QtCore import QByteArray
        outs = QMediaDevices.audioOutputs()
        for dev in outs:
            devices.append((dev.description(), dev.id().data().decode("utf-8", "replace")))
    except Exception as e:
        log.debug(f"QMediaDevices enumeration failed: {e}")
    return devices


def apply_output_device(audio_backend, device_id: str):
    """Route the app's audio output. Empty id = system default.

    PyQt6 ≥ 6.5 supports QAudioOutput.setDevice(QAudioDevice)."""
    if not device_id:
        return True
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        for dev in QMediaDevices.audioOutputs():
            if dev.id().data().decode("utf-8", "replace") == device_id:
                out = getattr(audio_backend, "audio_output", None)
                if out is not None and hasattr(out, "setDevice"):
                    out.setDevice(dev)
                    log.info(f"audio output -> {dev.description()}")
                    return True
                # Recreate path for older Qt: replace the QAudioOutput
                player = getattr(audio_backend, "player", None)
                if player is not None:
                    from PyQt6.QtMultimedia import QAudioOutput
                    new_out = QAudioOutput(dev)
                    player.setAudioOutput(new_out)
                    audio_backend.audio_output = new_out
                    log.info(f"audio output (recreated) -> {dev.description()}")
                    return True
    except Exception as e:
        log.warning(f"apply_output_device failed: {e}")
    return False
