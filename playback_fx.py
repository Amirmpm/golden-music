"""
Golden Music — Playback effects: speed, A-B repeat, fade.

Speed:      QMediaPlayer.setPlaybackRate (0.5x – 2.0x)
A-B repeat: when both markers are set, position is looped back to A after B.
Fade:       volume ramps down before pause / track change and back up after,
            driven by a QTimer in small steps so it survives backend quirks.
"""
import logging

from PyQt6.QtCore import QTimer

log = logging.getLogger("app.fx")

RATES = [1.0, 1.25, 1.5, 2.0, 0.75, 0.5]  # cycle order for the rate button


class PlaybackFX:
    """Owns speed / A-B / fade state; wired to the app's AudioBackend."""

    def __init__(self, audio_backend):
        self.audio = audio_backend
        self.enabled_fade = True
        self.fade_ms = 300
        # speed
        self.rate_index = 0
        # A-B repeat
        self.a_ms = None
        self.b_ms = None
        # fade machinery
        self._fade_timer = QTimer()
        self._fade_timer.setInterval(30)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_dir = 0          # -1 out, +1 in, 0 idle
        self._fade_target_vol = None
        self._fade_action = None    # callable fired at fade-out completion

    # ---------------- speed ----------------
    def cycle_rate(self) -> float:
        self.rate_index = (self.rate_index + 1) % len(RATES)
        rate = RATES[self.rate_index]
        try:
            if self.audio.available and self.audio.player is not None:
                self.audio.player.setPlaybackRate(rate)
        except Exception as e:
            log.warning(f"setPlaybackRate failed: {e}")
        return rate

    def set_rate(self, rate: float) -> float:
        rate = max(0.5, min(2.0, rate))
        try:
            if self.audio.available and self.audio.player is not None:
                self.audio.player.setPlaybackRate(rate)
        except Exception as e:
            log.warning(f"setPlaybackRate failed: {e}")
        return rate

    def current_rate(self) -> float:
        try:
            if self.audio.available and self.audio.player is not None:
                return float(self.audio.player.playbackRate() or 1.0)
        except Exception:
            pass
        return RATES[self.rate_index]

    # ---------------- A-B repeat ----------------
    def mark_a(self):
        pos = self.audio.position()
        self.a_ms = pos if self.a_ms is None else None   # toggle off on re-press
        if self.a_ms is None:
            self.b_ms = None
        return self.a_ms

    def mark_b(self):
        if self.a_ms is None:
            return None
        pos = self.audio.position()
        if pos <= self.a_ms:
            return self.b_ms
        self.b_ms = pos
        return self.b_ms

    def clear_ab(self):
        self.a_ms = None
        self.b_ms = None

    def ab_active(self) -> bool:
        return self.a_ms is not None and self.b_ms is not None

    def tick(self):
        """Call from the UI timer — enforces the A-B loop."""
        if not self.ab_active():
            return
        pos = self.audio.position()
        if pos >= self.b_ms or pos < self.a_ms - 400:
            self.audio.set_position(self.a_ms)

    # ---------------- fade ----------------
    def fade_out_then(self, action=None):
        """Fade to silence, then run `action` (pause / load next)."""
        if not self.enabled_fade:
            action()
            return
        self._fade_action = action
        self._fade_dir = -1
        # Track the level internally so the ramp is immune to backends whose
        # volume() readback lags or is stubbed.
        try:
            self._fade_level = float(self.audio.volume())
        except Exception:
            self._fade_level = 80.0
        self._fade_target_vol = self._fade_level
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def fade_in_from_silence(self):
        """Restore volume smoothly (after un-pause / new track start)."""
        if not self.enabled_fade:
            return
        self._fade_dir = +1
        try:
            cur = float(self.audio.volume())
        except Exception:
            cur = 0.0
        prev_target = getattr(self, "_fade_target_vol", None)
        target = cur if not prev_target else max(cur, prev_target)
        self._fade_target_vol = target or 80.0
        self._fade_level = min(cur, 5.0)   # start near silence
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _fade_step(self):
        step = max(6, int(self.fade_ms / 30))     # ~10 steps per fade
        if self._fade_dir < 0:
            self._fade_level = max(0, self._fade_level - step * 3)
            self.audio.set_volume(int(self._fade_level))
            if self._fade_level <= 0:
                self.audio.set_volume(0)
                self._fade_dir = 0
                self._fade_timer.stop()
                act = self._fade_action
                self._fade_action = None
                if act:
                    act()
                    # restore original volume after the swap, then ramp in
                    restore = self._fade_target_vol or 80
                    QTimer.singleShot(60, lambda: (
                        self.fade_in_from_silence(),
                        setattr(self, '_post_restore', restore)))
        elif self._fade_dir > 0:
            self._fade_level = min(self._fade_target_vol or 80,
                                   self._fade_level + step * 3)
            self.audio.set_volume(int(self._fade_level))
            if self._fade_level >= (self._fade_target_vol or 80):
                self._fade_dir = 0
                self._fade_timer.stop()
        else:
            self._fade_timer.stop()
