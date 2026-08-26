"""
Golden Music — Audio backend (PyQt6) v4.0.

Simplified, robust implementation:
- setSource + play() in one step (no complex waiting logic)
- Proper error handling with try/except everywhere
- mediaStatusChanged for detecting end-of-track
- Volume 0.0-1.0 (PyQt6 QAudioOutput uses float)
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("app.audio")

from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer, Qt
from PyQt6.QtWidgets import QApplication

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAVE_QT_AUDIO = True
except ImportError:
    HAVE_QT_AUDIO = False


class AudioBackend(QObject):
    """Simplified audio backend — set source, play, handle errors."""

    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    state_changed = pyqtSignal(int)
    error_occurred = pyqtSignal(str)
    track_finished = pyqtSignal()  # emitted when track ends naturally

    STATE_STOPPED = 0
    STATE_PLAYING = 1
    STATE_PAUSED = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = HAVE_QT_AUDIO
        self._error = None
        self._current_path = None

        if HAVE_QT_AUDIO:
            try:
                self.player = QMediaPlayer()
                self.audio_output = QAudioOutput()
                self.player.setAudioOutput(self.audio_output)
                self.audio_output.setVolume(0.8)

                self.player.positionChanged.connect(self._on_position_changed)
                self.player.durationChanged.connect(self._on_duration_changed)
                self.player.playbackStateChanged.connect(self._on_state_changed)
                self.player.mediaStatusChanged.connect(self._on_media_status)
                try:
                    self.player.errorOccurred.connect(self._on_error)
                except Exception:
                    pass
            except Exception as e:
                log.error(f"Backend init error: {e}")
                self._available = False
                self.player = None
                self.audio_output = None
        else:
            self.player = None
            self.audio_output = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error(self) -> str:
        return self._error

    def load_and_play(self, filepath: str):
        """Load a file and play it immediately."""
        if not self._available:
            self._error = "Audio backend not available"
            self.error_occurred.emit(self._error)
            return
        if not os.path.exists(filepath):
            self._error = f"File not found: {filepath}"
            self.error_occurred.emit(self._error)
            return

        self._current_path = filepath
        self._error = None
        try:
            # Stop current playback
            self.player.stop()
            # Set new source
            url = QUrl.fromLocalFile(filepath)
            self.player.setSource(url)
            # Play immediately — QMediaPlayer handles buffering internally
            self.player.play()
        except Exception as e:
            self._error = f"Play error: {e}"
            self.error_occurred.emit(self._error)
            log.error(f"load_and_play error: {e}")

    def load(self, filepath: str):
        """Load a file without playing."""
        if not self._available:
            return
        if not os.path.exists(filepath):
            return
        self._current_path = filepath
        try:
            self.player.stop()
            url = QUrl.fromLocalFile(filepath)
            self.player.setSource(url)
        except Exception as e:
            log.warning(f"load error: {e}")

    def play(self):
        if not self._available or self.player is None:
            return
        try:
            self.player.play()
        except Exception as e:
            self._error = f"Play error: {e}"
            self.error_occurred.emit(self._error)

    def pause(self):
        if not self._available or self.player is None:
            return
        try:
            self.player.pause()
        except Exception as e:
            log.debug(f"pause error: {e}")

    def stop(self):
        if not self._available or self.player is None:
            return
        try:
            self.player.stop()
        except Exception as e:
            log.debug(f"stop error: {e}")

    def set_position(self, ms: int):
        if not self._available or self.player is None:
            return
        try:
            self.player.setPosition(ms)
        except Exception:
            pass

    def position(self) -> int:
        if not self._available or self.player is None:
            return 0
        try:
            return int(self.player.position())
        except Exception:
            return 0

    def duration(self) -> int:
        if not self._available or self.player is None:
            return 0
        try:
            return int(self.player.duration())
        except Exception:
            return 0

    def set_volume(self, vol: int):
        """vol: 0-100 integer, converted to 0.0-1.0 float."""
        if not self._available or self.audio_output is None:
            return
        try:
            self.audio_output.setVolume(max(0.0, min(1.0, float(vol) / 100.0)))
        except Exception:
            pass

    def volume(self) -> int:
        if not self._available or self.audio_output is None:
            return 0
        try:
            return int(self.audio_output.volume() * 100)
        except Exception:
            return 0

    def state(self) -> int:
        if not self._available or self.player is None:
            return self.STATE_STOPPED
        try:
            s = self.player.playbackState()
            if s == QMediaPlayer.PlaybackState.PlayingState:
                return self.STATE_PLAYING
            elif s == QMediaPlayer.PlaybackState.PausedState:
                return self.STATE_PAUSED
            return self.STATE_STOPPED
        except Exception:
            return self.STATE_STOPPED

    def _on_position_changed(self, pos):
        try:
            self.position_changed.emit(int(pos))
        except Exception:
            pass

    def _on_duration_changed(self, dur):
        try:
            self.duration_changed.emit(int(dur))
        except Exception:
            pass

    def _on_state_changed(self, state):
        try:
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.state_changed.emit(self.STATE_PLAYING)
            elif state == QMediaPlayer.PlaybackState.PausedState:
                self.state_changed.emit(self.STATE_PAUSED)
            else:
                self.state_changed.emit(self.STATE_STOPPED)
        except Exception:
            pass

    def _on_media_status(self, status):
        """Handle media status changes."""
        try:
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                # Track finished naturally
                self.track_finished.emit()
            elif status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._error = "Invalid or unsupported media file"
                self.error_occurred.emit(self._error)
        except Exception as e:
            log.error(f"media status handler error: {e}")

    def _on_error(self, error=None, error_string=None):
        try:
            if error_string and str(error_string) != "" and "No Error" not in str(error_string):
                msg = str(error_string)
                self._error = msg
                self.error_occurred.emit(msg)
        except Exception:
            pass
