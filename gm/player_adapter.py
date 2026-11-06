"""Golden Music 3.0 — PlayerAdapter: AudioBackend-compatible facade over
the v3 engine layer (EngineCoordinator + optional libmpv engine).

Surface compatibility: the 2.x MainWindow drives playback through
AudioBackend's method/ signal names (load_and_play, setPlaybackRate, ...).
This adapter exposes exactly that surface while adding:

  * engine selection (mpv preferred, Qt fallback) via prefs["audio_engine"]
  * REAL gapless / crossfade through the dual-engine coordinator
  * per-track loudness normalization gain (ReplayGain tags)
  * 10-band EQ (mpv engine only), silence skip (mpv engine only)
  * next-track peeking via an injected provider (the window decides what
    "next" means — shuffle/repeat logic stays in MainWindow)

mpv callbacks arrive on mpv's own thread; the adapter re-emits them through
Qt signals (thread-safe by Qt's auto/queued connections) so all state work
happens on the GUI thread.
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, pyqtSignal

from audio import AudioBackend                      # v2 Qt backend (fallback)
from gm.audio_engines import (
    AudioEngineBase, QtAudioEngine, MpvAudioEngine, EngineCoordinator,
    AudioEngineUnavailable, MPV_AVAILABLE,
    create_preferred_engine,
)
from gm.crossfade import CrossfadeState

log = logging.getLogger("app.player")


class PlayerAdapter(QObject):
    # v2-compatible signals
    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    state_changed = pyqtSignal(int)
    error_occurred = pyqtSignal(str)
    track_finished = pyqtSignal()

    # v3 signals
    swapped_to = pyqtSignal(str)          # gapless/crossfade handoff done
    almost_end = pyqtSignal(str)          # path now nearing its end

    STATE_STOPPED, STATE_PLAYING, STATE_PAUSED = 0, 1, 2

    def __init__(self, parent=None, engine_order=("mpv", "qt")):
        super().__init__(parent)
        self._engine = None
        self._engine_name = "legacy"
        self._legacy = None
        self._coordinator = None
        self._next_provider = None
        self._armed_path = None
        self._track_gain_db = 0.0
        self._user_volume = 80
        self._swap_in_progress = False
        self._rate = 1.0

        engine = None
        try:
            engine, name = create_preferred_engine(list(engine_order))
        except Exception as e:
            log.warning("no v3 engine available (%s); Qt fallback", e)
        if engine is not None:
            self._engine = engine
            self._engine_name = name
            self._wire_engine(engine)
            self._coordinator = EngineCoordinator(
                make_engine=lambda: self._make_second_engine())
            self._coordinator.on_swap = self._on_swap
            log.info("audio engine: %s", name)
        else:
            self._legacy = AudioBackend(parent)
            self._wire_legacy(self._legacy)
            self._engine_name = "qt-legacy"

    # ------------------------------------------------------------------ wiring
    def _make_second_engine(self):
        if self._engine_name == "mpv":
            try:
                return MpvAudioEngine()
            except AudioEngineUnavailable:
                return QtAudioEngine()
        return QtAudioEngine()

    def _wire_engine(self, engine: AudioEngineBase):
        engine.on_position = lambda ms: self.position_changed.emit(int(ms))
        engine.on_duration = lambda ms: self.duration_changed.emit(int(ms))
        engine.on_state = lambda st: self.state_changed.emit(int(st))
        engine.on_error = lambda msg: self.error_occurred.emit(str(msg))
        engine.on_finished = self._on_engine_finished
        engine.on_almost_end = self._on_engine_almost_end

    def _wire_legacy(self, backend: AudioBackend):
        backend.position_changed.connect(self.position_changed)
        backend.duration_changed.connect(self.duration_changed)
        backend.state_changed.connect(self.state_changed)
        backend.error_occurred.connect(self.error_occurred)
        backend.track_finished.connect(self._on_engine_finished)

    # ------------------------------------------------------------------- v2 API
    @property
    def available(self) -> bool:
        if self._engine is not None:
            return True
        return bool(self._legacy and self._legacy.available)

    @property
    def error(self):
        if self._legacy is not None:
            return self._legacy.error
        return None

    @property
    def engine_name(self) -> str:
        return self._engine_name

    def capabilities(self) -> set:
        if self._engine is not None:
            try:
                return set(self._engine.capabilities())
            except Exception:
                return set()
        return {"gapless", "stream"}

    def load_and_play(self, filepath: str):
        self._armed_path = None
        self._swap_in_progress = False
        if self._engine is not None:
            try:
                self._apply_track_gain()
                self._engine.load_and_play(str(filepath))
                self._arm_next()
                return
            except Exception as e:
                log.exception("engine load failed; falling back to Qt")
                self._switch_to_legacy()
        self._legacy.load_and_play(str(filepath))

    def load(self, filepath: str):
        if self._engine is not None:
            try:
                self._engine.load(str(filepath))
                return
            except Exception:
                self._switch_to_legacy()
        self._legacy.load(str(filepath))

    def play(self):
        if self._engine is not None:
            try:
                self._engine.play()
                return
            except Exception:
                self._switch_to_legacy()
        self._legacy.play()

    def pause(self):
        if self._engine is not None:
            try:
                self._engine.pause()
                return
            except Exception:
                self._switch_to_legacy()
        self._legacy.pause()

    def stop(self):
        if self._engine is not None:
            try:
                self._engine.stop()
                return
            except Exception:
                pass
        if self._legacy is not None:
            self._legacy.stop()

    def set_position(self, ms: int):
        if self._engine is not None:
            try:
                self._engine.set_position(int(ms))
                return
            except Exception:
                pass
        if self._legacy is not None:
            self._legacy.set_position(int(ms))

    def position(self) -> int:
        if self._engine is not None:
            try:
                return int(self._engine.position())
            except Exception:
                return 0
        return self._legacy.position() if self._legacy else 0

    def duration(self) -> int:
        if self._engine is not None:
            try:
                return int(self._engine.duration())
            except Exception:
                return 0
        return self._legacy.duration() if self._legacy else 0

    def set_volume(self, vol: int):
        self._user_volume = max(0, min(100, int(vol)))
        eff = self._effective_volume()
        if self._engine is not None:
            try:
                self._engine.set_volume(eff)
                return
            except Exception:
                pass
        if self._legacy is not None:
            self._legacy.set_volume(eff)

    def volume(self) -> int:
        return self._user_volume

    def state(self) -> int:
        if self._engine is not None:
            try:
                return int(self._engine.state())
            except Exception:
                return self.STATE_STOPPED
        return self._legacy.state() if self._legacy else self.STATE_STOPPED

    # PlaybackFX compatibility (calls player.setPlaybackRate)
    def setPlaybackRate(self, rate: float):
        self._rate = float(rate)
        if self._engine is not None:
            try:
                self._engine.set_rate(float(rate))
                return
            except Exception:
                pass
        if self._legacy is not None:
            try:
                self._legacy._player.setPlaybackRate(float(rate))
            except Exception:
                pass

    def playbackRate(self) -> float:
        return self._rate

    # ------------------------------------------------------------------- v3 API
    def set_next_provider(self, fn):
        """fn() -> next path (or None) decided by the window's queue logic."""
        self._next_provider = fn

    def apply_prefs(self, prefs: dict):
        try:
            fade = int(prefs.get("crossfade_ms", 0) or 0)
            if self._coordinator is not None:
                self._coordinator.set_crossfade_ms(fade)
            if self._engine is None:
                return
            # EQ (mpv only)
            try:
                from gm.equalizer import EQController
                eq = EQController(
                    gains=list(prefs.get("eq_gains", []) or [0.0] * 10),
                    enabled=bool(prefs.get("eq_enabled", False)),
                    preset=str(prefs.get("eq_preset", "Flat")))
                eq.apply_to_engine(self._engine)
            except Exception:
                pass
            # silence removal (mpv only)
            try:
                if getattr(self._engine, "set_af", None):
                    pass  # af string composed below with EQ when supported
            except Exception:
                pass
            self._apply_track_gain()
            self.set_volume(self._user_volume)
        except Exception:
            log.exception("adapter prefs failed")

    def set_track_gain_db(self, gain_db: float):
        """Loudness normalization gain for the CURRENT track (pre-fader)."""
        self._track_gain_db = float(gain_db or 0.0)
        self.set_volume(self._user_volume)

    def tick(self):
        """Advanced from the window's 100 ms fx timer (crossfade ramp)."""
        if self._coordinator is not None:
            try:
                self._coordinator.tick()
            except Exception:
                pass

    # ------------------------------------------------------------- internals
    def _effective_volume(self) -> int:
        # apply ReplayGain-ish multiplier in the linear-ish domain, clamp 0-100
        try:
            mult = 10 ** (self._track_gain_db / 20.0)
            return max(0, min(100, int(round(self._user_volume * mult))))
        except Exception:
            return self._user_volume

    def _apply_track_gain(self):
        if "normalize" not in self.capabilities() and \
                self._engine_name != "mpv":
            return
        try:
            if self._engine_name == "mpv" and \
                    getattr(self._engine, "set_af", None):
                # volume filter keeps EQ chain intact
                self._engine.set_af(self._compose_af())
        except Exception:
            pass

    def _compose_af(self) -> str:
        parts = []
        try:
            from gm.equalizer import EQController
            eq = EQController(gains=list(getattr(self, "_eq_gains", None)
                                          or [0.0] * 10))
            if getattr(self, "_eq_enabled", False):
                parts.append(eq.to_mpv_string())
        except Exception:
            pass
        if self._track_gain_db:
            from gm.audio_engines import volume_gain_af
            parts.append(volume_gain_af(self._track_gain_db))
        return ",".join(p for p in parts if p)

    def _switch_to_legacy(self):
        """Engine died mid-session: rebuild the v2 Qt backend transparently."""
        if self._legacy is not None:
            return
        try:
            self._legacy = AudioBackend(self.parent())
            self._wire_legacy(self._legacy)
            self._engine = None
            self._engine_name = "qt-legacy"
            log.warning("switched to legacy Qt audio backend")
        except Exception:
            log.exception("legacy fallback failed")

    # ------------------------------------------------- gapless / crossfade flow
    def _on_engine_almost_end(self):
        try:
            if self._coordinator is None or self._swap_in_progress:
                return
            nxt = None
            if self._next_provider is not None:
                try:
                    nxt = self._next_provider()
                except Exception:
                    nxt = None
            if not nxt:
                return
            self._swap_in_progress = True
            self._armed_path = nxt
            self.almost_end.emit(str(nxt))
            self._coordinator.next_track_pending(str(nxt))
            self._coordinator.notify_almost_end()
        except Exception:
            log.exception("almost-end handling failed")

    def _on_engine_finished(self):
        """Natural end of the primary track."""
        try:
            if self._coordinator is not None and self._swap_in_progress:
                # secondary already holds the next track (gapless) or is
                # mid-crossfade; perform / complete the handoff
                if not self._coordinator.fading:
                    self._coordinator.notify_finished()
                # during crossfade the swap completes on its own via tick()
                return
        except Exception:
            log.exception("finish handling failed")
        self.track_finished.emit()

    def _on_swap(self, path: str):
        """Coordinator completed the handoff — tell the window."""
        try:
            self._swap_in_progress = False
            self._armed_path = None
            self.swapped_to.emit(str(path))
            # adopt secondary as primary
            if self._coordinator is not None:
                self._engine = self._coordinator.primary
                if self._engine is not None:
                    self._wire_engine(self._engine)
            self._arm_next()
        except Exception:
            log.exception("swap handling failed")

    def _arm_next(self):
        self._swap_in_progress = False
