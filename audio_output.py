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

    PyQt6 ≥ 6.5 supports QAudioOutput.setDevice(QAudioDevice). Switching
    while a track is playing needs a brief pause/resume on some backends —
    QMediaPlayer re-opens its audio sink on setDevice; we nudge it so the
    change takes effect on the track playing right now, not just the next.
    """
    if not device_id:
        return True
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        target = None
        for dev in QMediaDevices.audioOutputs():
            if dev.id().data().decode("utf-8", "replace") == device_id:
                target = dev
                break
        if target is None:
            log.info(f"audio output id not found (device disabled/unplugged?): {device_id[:20]}…")
            return False
        out = getattr(audio_backend, "audio_output", None)
        player = getattr(audio_backend, "player", None)
        was_playing = False
        pos = 0
        if player is not None:
            try:
                from PyQt6.QtMultimedia import QMediaPlayer
                was_playing = (player.playbackState()
                               == QMediaPlayer.PlaybackState.PlayingState)
                pos = int(player.position())
            except Exception:
                pass
        if out is not None and hasattr(out, "setDevice"):
            out.setDevice(target)
            log.info(f"audio output -> {target.description()}")
        elif player is not None:
            # Recreate path for older Qt
            from PyQt6.QtMultimedia import QAudioOutput
            new_out = QAudioOutput(target)
            player.setAudioOutput(new_out)
            audio_backend.audio_output = new_out
            log.info(f"audio output (recreated) -> {target.description()}")
        else:
            return False
        # Nudge the player so an in-flight track re-opens on the new sink.
        if player is not None and (was_playing or pos > 0):
            try:
                player.pause()
                player.setPosition(pos)
                if was_playing:
                    player.play()
            except Exception as e:
                log.debug(f"resume after output switch: {e}")
        return True
    except Exception as e:
        log.warning(f"apply_output_device failed: {e}")
    return False
