"""Golden Music 3.0 — pluggable audio engine layer (Qt-free at import time).

- :class:`AudioEngineBase` — abstract engine API with plain-callable hooks
  (on_position/on_duration/on_state/on_error/on_finished/on_almost_end);
  the app bridges them to Qt signals and marshals cross-thread callbacks.
- :class:`QtAudioEngine` — QMediaPlayer/QAudioOutput wrapper; imports
  PyQt6.QtMultimedia *lazily inside __init__* so this module stays
  importable in headless environments.
- :class:`MpvAudioEngine` — optional python-mpv engine (EQ via
  ``superequalizer``, loudness normalization via replaygain/af volume,
  silence skip via ``silenceremove``, native gapless via append-play,
  streams incl. HLS).  When python-mpv is missing the class exists but the
  constructor raises :class:`AudioEngineUnavailable`.
- :class:`EngineCoordinator` — two-engine gapless/crossfade handoff driven
  by the app's 100 ms timer, using the equal-power curves from
  gm.crossfade.
- :func:`create_preferred_engine` — factory trying engine names in order.
"""
import abc
import logging
import os

from gm.crossfade import CrossfadeState
from gm.equalizer import EQController
from gm.normalize import Normalizer

log = logging.getLogger("app.audio")

#: Engine states (mirror the legacy audio.AudioBackend mapping).
STATE_STOPPED = 0
STATE_PLAYING = 1
STATE_PAUSED = 2

#: mpv af fragment that skips long silences (used by the app when composing
#: the engine's af string).
SILENCE_REMOVE_AF = "silenceremove=stop_periods=1:stop_length=0.5:threshold=-35dB"

#: mpv ``af`` volume=gain fragment for loudness normalization in dB.
def volume_gain_af(gain_db: float) -> str:
    """mpv af fragment applying a flat gain in dB (e.g. ``volume=gain=-4.08``)."""
    return f"volume=gain={float(gain_db):.2f}"

# -- optional python-mpv detection ------------------------------------------
try:
    import mpv as _mpv_module  # noqa: F401  (module-level availability probe)
    MPV_AVAILABLE: bool = True
except Exception:  # ImportError or libmpv shared-library load failure
    _mpv_module = None
    MPV_AVAILABLE = False


class AudioEngineUnavailable(Exception):
    """Raised when an engine cannot be constructed (optional dependency
    missing, init failure, ...)."""


class AudioEngineBase(abc.ABC):
    """Abstract audio engine.

    Callback hooks are *plain callables*, not Qt signals — the application
    bridges them (and marshals cross-thread callbacks to the UI thread):

    - ``on_position(ms: int)``
    - ``on_duration(ms: int)``
    - ``on_state(state: int)``           (STATE_* constants)
    - ``on_error(message: str)``
    - ``on_finished()``                  (natural end of track)
    - ``on_almost_end()``                (gapless/crossfade handoff hint)

    ``on_almost_end`` fires once per loaded track when
    ``duration - position <= crossfade_ms`` (set ``crossfade_ms`` to the
    handoff lead time; 0 fires only at the very last moment, so
    coordinators usually raise it).
    """

    def __init__(self) -> None:
        self.on_position = None
        self.on_duration = None
        self.on_state = None
        self.on_error = None
        self.on_finished = None
        self.on_almost_end = None
        self.crossfade_ms: int = 0
        self._almost_fired: bool = False

    # -- abstract playback API -------------------------------------------
    @abc.abstractmethod
    def load(self, path: str) -> None:
        """Load a track without starting playback."""

    @abc.abstractmethod
    def load_and_play(self, path: str) -> None:
        """Load a track and start playback immediately."""

    @abc.abstractmethod
    def play(self) -> None:
        """Resume (or start) playback."""

    @abc.abstractmethod
    def pause(self) -> None:
        """Pause playback."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop playback (position resets)."""

    @abc.abstractmethod
    def set_position(self, ms: int) -> None:
        """Seek to an absolute position in milliseconds."""

    @abc.abstractmethod
    def position(self) -> int:
        """Current playback position in milliseconds."""

    @abc.abstractmethod
    def duration(self) -> int:
        """Current track duration in milliseconds (0 while unknown)."""

    @abc.abstractmethod
    def set_volume(self, vol: int) -> None:
        """Set volume 0-100."""

    @abc.abstractmethod
    def volume(self) -> int:
        """Current volume 0-100."""

    @abc.abstractmethod
    def set_rate(self, rate: float) -> None:
        """Set playback rate (tempo)."""

    @abc.abstractmethod
    def rate(self) -> float:
        """Current playback rate."""

    @abc.abstractmethod
    def state(self) -> int:
        """STATE_STOPPED / STATE_PLAYING / STATE_PAUSED."""

    @abc.abstractmethod
    def name(self) -> str:
        """Engine short name ("qt", "mpv", "fake", ...)."""

    @abc.abstractmethod
    def capabilities(self) -> set[str]:
        """Feature hints, e.g. {"eq","crossfade","gapless","tempo","stream"}."""

    # -- optional features -------------------------------------------------
    @property
    def eq(self) -> EQController | None:
        """EQ controller when the engine supports EQ, else None."""
        return None

    @property
    def normalizer(self) -> Normalizer | None:
        """Loudness normalizer when supported, else None."""
        return None

    # -- helpers for subclasses ---------------------------------------------
    def _fire(self, cb, *args) -> None:
        """Invoke a callback hook, never raising."""
        if cb is None:
            return
        try:
            cb(*args)
        except Exception as exc:
            log.debug("engine callback error: %s", exc)

    def _reset_almost_end(self) -> None:
        """Re-arm the almost-end hint (call on load)."""
        self._almost_fired = False

    def _check_almost_end(self, position_ms: int, duration_ms: int) -> None:
        """Fire on_almost_end once when remaining time <= crossfade_ms."""
        if self.on_almost_end is None or self._almost_fired:
            return
        if duration_ms <= 0 or position_ms < 0:
            return
        if duration_ms - position_ms <= max(1, int(self.crossfade_ms)):
            self._almost_fired = True
            self._fire(self.on_almost_end)


class QtAudioEngine(AudioEngineBase):
    """QMediaPlayer/QAudioOutput engine.

    PyQt6.QtMultimedia is imported lazily inside ``__init__`` (never at
    module import) so headless tooling can import this module safely.
    State mapping, EndOfMedia -> on_finished and InvalidMedia -> on_error
    semantics mirror the legacy audio.AudioBackend; ``_on_error`` keeps the
    tolerant ``(error, errorString)`` signature.
    """

    def __init__(self, parent=None) -> None:
        super().__init__()
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception as exc:  # ImportError or missing system libs (EGL...)
            raise AudioEngineUnavailable(
                f"PyQt6.QtMultimedia unavailable: {exc}") from exc
        try:
            from PyQt6.QtCore import QUrl
        except Exception as exc:
            raise AudioEngineUnavailable(f"PyQt6.QtCore unavailable: {exc}") from exc
        self._QMediaPlayer = QMediaPlayer
        self._QUrl = QUrl
        try:
            self._player = QMediaPlayer(parent)
            self._output = QAudioOutput(parent)
            self._player.setAudioOutput(self._output)
            self._player.positionChanged.connect(self._on_position_changed)
            self._player.durationChanged.connect(self._on_duration_changed)
            self._player.playbackStateChanged.connect(self._on_state_changed)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            try:
                self._player.errorOccurred.connect(self._on_error)
            except Exception as exc:  # signal signature varies across PyQt6 builds
                log.debug("errorOccurred connect failed: %s", exc)
        except Exception as exc:
            raise AudioEngineUnavailable(f"QMediaPlayer init failed: {exc}") from exc
        self._duration_ms = 0
        self._volume = 100
        self._rate = 1.0

    # -- state mapping -----------------------------------------------------
    def _map_state(self, state) -> int:
        try:
            ps = self._QMediaPlayer.PlaybackState
            if state == ps.PlayingState:
                return STATE_PLAYING
            if state == ps.PausedState:
                return STATE_PAUSED
        except Exception as exc:
            log.debug("state mapping failed: %s", exc)
        return STATE_STOPPED

    # -- AudioEngineBase API ------------------------------------------------
    def load(self, path: str) -> None:
        try:
            self._player.stop()
            self._player.setSource(self._QUrl.fromLocalFile(str(path)))
            self._duration_ms = 0
            self._reset_almost_end()
        except Exception as exc:
            log.warning("qt load failed: %s", exc)

    def load_and_play(self, path: str) -> None:
        try:
            if not os.path.exists(path):
                self._fire(self.on_error, f"File not found: {path}")
                return
        except Exception as exc:
            log.debug("existence check failed for %r: %s", path, exc)
        self.load(path)
        self.play()

    def play(self) -> None:
        try:
            self._player.play()
        except Exception as exc:
            self._fire(self.on_error, f"play failed: {exc}")

    def pause(self) -> None:
        try:
            self._player.pause()
        except Exception as exc:
            log.debug("qt pause failed: %s", exc)

    def stop(self) -> None:
        try:
            self._player.stop()
        except Exception as exc:
            log.debug("qt stop failed: %s", exc)

    def set_position(self, ms: int) -> None:
        try:
            self._player.setPosition(int(ms))
        except Exception as exc:
            log.debug("qt setPosition failed: %s", exc)

    def position(self) -> int:
        try:
            return int(self._player.position())
        except Exception:
            return 0

    def duration(self) -> int:
        try:
            d = int(self._player.duration())
            if d > 0:
                self._duration_ms = d
        except Exception:
            pass
        return self._duration_ms

    def set_volume(self, vol: int) -> None:
        try:
            v = max(0, min(100, int(vol)))
            self._volume = v
            self._output.setVolume(v / 100.0)
        except Exception as exc:
            log.debug("qt setVolume failed: %s", exc)

    def volume(self) -> int:
        try:
            return int(round(self._output.volume() * 100))
        except Exception:
            return self._volume

    def set_rate(self, rate: float) -> None:
        r = max(0.25, min(4.0, float(rate)))
        try:
            self._player.setPlaybackRate(r)
            self._rate = r
        except Exception as exc:
            log.warning("qt setPlaybackRate failed: %s", exc)

    def rate(self) -> float:
        try:
            r = float(self._player.playbackRate())
            return r if r > 0 else self._rate
        except Exception:
            return self._rate

    def state(self) -> int:
        try:
            return self._map_state(self._player.playbackState())
        except Exception:
            return STATE_STOPPED

    def name(self) -> str:
        return "qt"

    def capabilities(self) -> set[str]:
        # gapless via preload handoff handled by the EngineCoordinator.
        return {"gapless", "stream"}

    # -- Qt slots ----------------------------------------------------------
    def _on_position_changed(self, pos) -> None:
        try:
            ms = int(pos)
            self._fire(self.on_position, ms)
            self._check_almost_end(ms, self._duration_ms)
        except Exception as exc:
            log.debug("position handler error: %s", exc)

    def _on_duration_changed(self, dur) -> None:
        try:
            self._duration_ms = int(dur)
            self._fire(self.on_duration, self._duration_ms)
        except Exception as exc:
            log.debug("duration handler error: %s", exc)

    def _on_state_changed(self, state) -> None:
        self._fire(self.on_state, self._map_state(state))

    def _on_media_status(self, status) -> None:
        try:
            ms = self._QMediaPlayer.MediaStatus
            if status == ms.EndOfMedia:
                self._fire(self.on_finished)
            elif status == ms.InvalidMedia:
                self._fire(self.on_error, "Invalid or unsupported media file")
        except Exception as exc:
            log.debug("media status handler error: %s", exc)

    def _on_error(self, error=None, error_string=None) -> None:
        """Tolerant of PyQt6's (error, errorString) and older signatures."""
        try:
            msg = error_string if error_string not in (None, "") else error
            if msg is None:
                return
            text = str(msg)
            if not text or "No Error" in text:
                return
            self._fire(self.on_error, text)
        except Exception:
            pass


class MpvAudioEngine(AudioEngineBase):
    """libmpv engine via python-mpv (optional).

    Threading: python-mpv runs its own event loop on a background thread
    when created through ``mpv.MPV()`` — every callback registered here
    (``observe_property`` for time-pos/duration/pause and the ``end_file``
    event) arrives on *mpv's thread*.  The application must marshal
    on_position/on_state/on_finished/... to the UI thread (the Qt bridge
    does this); all mpv interactions below are wrapped defensively.

    Playback: ``loadfile(path, "replace")``; :meth:`enqueue` uses
    ``append-play`` for mpv's native gapless.  ``speed`` is pitch-
    preserving by default (the af chain stays empty).  EQ rides on the
    ``af`` property (superequalizer), loudness normalization on the
    ``replaygain``/``replaygain-preamp`` properties or an af ``volume``
    gain, silence skip on af ``silenceremove`` (see SILENCE_REMOVE_AF).
    URLs (incl. HLS) work natively through loadfile.
    """

    def __init__(self, extra_options: dict | None = None) -> None:
        super().__init__()
        try:
            import mpv as mpv_mod  # re-import: honours sys.modules fakes too
        except Exception as exc:  # ImportError or libmpv load failure
            raise AudioEngineUnavailable(
                "python-mpv is not installed (pip install python-mpv and "
                "make sure libmpv is available)") from exc
        options = {"video": False, "ytdl": False}
        if extra_options:
            options.update(dict(extra_options))
        try:
            self._mpv = mpv_mod.MPV(**options)
        except Exception as exc:
            raise AudioEngineUnavailable(f"libmpv init failed: {exc}") from exc
        self._mpv_mod = mpv_mod
        self._current_path: str | None = None
        self._position_ms = 0
        self._duration_ms = 0
        self._volume = 100
        self._rate = 1.0
        self._state = STATE_STOPPED
        self._af = ""
        self._eq: EQController | None = None
        self._normalizer: Normalizer | None = None
        # eof reasons: new API strings + old numeric constant when exposed
        self._eof_reasons = {"eof"}
        try:
            self._eof_reasons.add(int(mpv_mod.Constants.MPV_END_FILE_REASON_EOF))
        except Exception:
            self._eof_reasons.add(0)
        try:
            self._mpv.observe_property("time-pos", self._on_time_pos)
            self._mpv.observe_property("duration", self._on_duration_prop)
            self._mpv.observe_property("pause", self._on_pause_prop)
        except Exception as exc:
            log.warning("mpv observe_property failed: %s", exc)
        try:
            self._mpv.event_callback("end_file")(self._on_end_file)
        except Exception as exc:
            log.warning("mpv end_file callback registration failed: %s", exc)

    # -- mpv event handlers (called on mpv's thread!) ---------------------
    def _on_time_pos(self, name, value) -> None:
        if value is None:
            return
        try:
            pos = int(float(value) * 1000.0)
        except (TypeError, ValueError):
            return
        self._position_ms = pos
        self._fire(self.on_position, pos)
        self._check_almost_end(pos, self._duration_ms)

    def _on_duration_prop(self, name, value) -> None:
        try:
            dur = int(float(value) * 1000.0) if value is not None else 0
        except (TypeError, ValueError):
            return
        self._duration_ms = dur
        self._fire(self.on_duration, dur)

    def _on_pause_prop(self, name, value) -> None:
        self._state = STATE_PAUSED if value else STATE_PLAYING
        self._fire(self.on_state, self._state)

    def _on_end_file(self, event) -> None:
        """Map MPV_EVENT_END_FILE (reason eof) -> on_finished."""
        reason = self._event_field(event, "reason")
        if reason in self._eof_reasons:
            self._state = STATE_STOPPED
            self._fire(self.on_finished)

    @staticmethod
    def _event_field(event, *names):
        """Read a field from an mpv event (dict-like, MpvEvent or object)."""
        try:
            data = event.as_dict() if hasattr(event, "as_dict") else None
        except Exception:
            data = None
        if not data and isinstance(event, dict):
            data = event
        if data:
            for n in names:
                if n in data:
                    return data[n]
        for n in names:
            if hasattr(event, n):
                try:
                    return getattr(event, n)
                except Exception:
                    pass
            try:
                if n in event:  # MpvEvent supports __getitem__
                    return event[n]
            except Exception:
                pass
        return None

    # -- AudioEngineBase API -----------------------------------------------
    def load(self, path: str) -> None:
        try:
            self._mpv.loadfile(str(path), "replace")
            self._current_path = str(path)
            self._position_ms = 0
            self._reset_almost_end()
        except Exception as exc:
            self._fire(self.on_error, f"mpv loadfile failed: {exc}")

    def load_and_play(self, path: str) -> None:
        self.load(path)
        self.play()

    def play(self) -> None:
        try:
            self._mpv["pause"] = False
            self._state = STATE_PLAYING
            self._fire(self.on_state, self._state)
        except Exception as exc:
            self._fire(self.on_error, f"mpv play failed: {exc}")

    def pause(self) -> None:
        try:
            self._mpv["pause"] = True
            self._state = STATE_PAUSED
            self._fire(self.on_state, self._state)
        except Exception as exc:
            log.debug("mpv pause failed: %s", exc)

    def stop(self) -> None:
        try:
            stop_fn = getattr(self._mpv, "stop", None)
            if callable(stop_fn):
                stop_fn()
            else:
                self._mpv.command("stop")
            self._state = STATE_STOPPED
            self._fire(self.on_state, self._state)
        except Exception as exc:
            log.debug("mpv stop failed: %s", exc)

    def set_position(self, ms: int) -> None:
        try:
            seconds = max(0.0, float(ms) / 1000.0)
            self._mpv.seek(seconds, "absolute")
            self._position_ms = int(ms)
        except Exception as exc:
            log.debug("mpv seek failed: %s", exc)

    def position(self) -> int:
        return self._position_ms

    def duration(self) -> int:
        return self._duration_ms

    def set_volume(self, vol: int) -> None:
        try:
            v = max(0, min(100, int(vol)))
            self._mpv["volume"] = v  # mpv volume is 0-100 linear
            self._volume = v
        except Exception as exc:
            log.debug("mpv set volume failed: %s", exc)

    def volume(self) -> int:
        return self._volume

    def set_rate(self, rate: float) -> None:
        try:
            r = max(0.25, min(4.0, float(rate)))
            self._mpv["speed"] = r  # pitch-preserving: af stays empty
            self._rate = r
        except Exception as exc:
            log.debug("mpv set speed failed: %s", exc)

    def rate(self) -> float:
        return self._rate

    def state(self) -> int:
        return self._state

    def name(self) -> str:
        return "mpv"

    def capabilities(self) -> set[str]:
        return {"eq", "gapless", "tempo", "stream", "normalize"}

    # -- optional features -----------------------------------------------------
    @property
    def eq(self) -> EQController:
        if self._eq is None:
            self._eq = EQController()
        return self._eq

    @property
    def normalizer(self) -> Normalizer:
        if self._normalizer is None:
            self._normalizer = Normalizer()
        return self._normalizer

    # -- mpv-specific API -----------------------------------------------------
    def enqueue(self, path: str) -> None:
        """Queue the next track for mpv's native gapless (append-play)."""
        try:
            self._mpv.loadfile(str(path), "append-play")
        except Exception as exc:
            log.warning("mpv append-play failed: %s", exc)

    def set_af(self, af: str) -> None:
        """Set the whole mpv af chain string ("" clears it).

        The app composes one string from the parts it wants, e.g.
        ``eq.to_mpv_string()``, :func:`volume_gain_af` and
        :data:`SILENCE_REMOVE_AF`, joined with commas.
        """
        self._af = str(af)
        try:
            self._mpv["af"] = self._af
        except Exception as exc:
            log.warning("mpv set af failed: %s", exc)

    def get_af(self) -> str:
        """Last af string applied through :meth:`set_af`."""
        return self._af

    def set_replaygain(self, mode: str = "track",
                       preamp_db: float = 0.0) -> bool:
        """Enable mpv's replaygain-tag normalization ("track"/"album"/"no").

        Returns True when both properties were accepted.
        """
        try:
            self._mpv["replaygain"] = str(mode)
            self._mpv["replaygain-preamp"] = float(preamp_db)
            return True
        except Exception as exc:
            log.warning("mpv replaygain setup failed: %s", exc)
            return False


class FakeEngine(AudioEngineBase):
    """Scriptable test double: records every call, position/duration are set
    directly (``engine.position_ms = ...``), so the EngineCoordinator can be
    driven deterministically in headless tests."""

    def __init__(self, duration_ms: int = 0, name: str = "fake") -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.loaded_paths: list[str] = []
        self._name = str(name)
        self._pos = 0
        self._dur = int(duration_ms)
        self._vol = 100
        self._rate = 1.0
        self._state = STATE_STOPPED
        self._path: str | None = None
        self._af = ""

    # -- scripting --------------------------------------------------------
    @property
    def position_ms(self) -> int:
        """Scriptable playback position (ms)."""
        return self._pos

    @position_ms.setter
    def position_ms(self, ms: int) -> None:
        self._pos = int(ms)

    @property
    def duration_ms(self) -> int:
        """Scriptable track duration (ms)."""
        return self._dur

    @duration_ms.setter
    def duration_ms(self, ms: int) -> None:
        self._dur = int(ms)

    def _rec(self, op: str, *args) -> None:
        self.calls.append((op,) + args)

    def calls_named(self, op: str) -> list[tuple]:
        """All recorded calls with the given op name."""
        return [c for c in self.calls if c[0] == op]

    # -- AudioEngineBase API ------------------------------------------------
    def load(self, path: str) -> None:
        self._rec("load", str(path))
        self._path = str(path)
        self.loaded_paths.append(str(path))
        self._pos = 0
        self._reset_almost_end()

    def load_and_play(self, path: str) -> None:
        self.load(path)
        self.play()

    def play(self) -> None:
        self._rec("play")
        self._state = STATE_PLAYING
        self._fire(self.on_state, self._state)

    def pause(self) -> None:
        self._rec("pause")
        self._state = STATE_PAUSED
        self._fire(self.on_state, self._state)

    def stop(self) -> None:
        self._rec("stop")
        self._state = STATE_STOPPED
        self._pos = 0
        self._fire(self.on_state, self._state)

    def set_position(self, ms: int) -> None:
        self._rec("set_position", int(ms))
        self._pos = int(ms)

    def position(self) -> int:
        return self._pos

    def duration(self) -> int:
        return self._dur

    def set_volume(self, vol: int) -> None:
        v = max(0, min(100, int(vol)))
        self._rec("set_volume", v)
        self._vol = v

    def volume(self) -> int:
        return self._vol

    def set_rate(self, rate: float) -> None:
        r = max(0.25, min(4.0, float(rate)))
        self._rec("set_rate", r)
        self._rate = r

    def rate(self) -> float:
        return self._rate

    def state(self) -> int:
        return self._state

    def name(self) -> str:
        return self._name

    def capabilities(self) -> set[str]:
        return {"gapless", "stream", "eq", "tempo"}

    # -- mpv-ish extras so FakeEngine doubles in EQ tests -------------------
    def set_af(self, af: str) -> None:
        self._rec("set_af", str(af))
        self._af = str(af)

    def get_af(self) -> str:
        return self._af


#: Preload window for hard-gapless handoff (ms before the track end).
GAPLESS_LEAD_MS = 400

#: Swap when the old track is this close to its end (ms).
SWAP_EPS_MS = 90


class EngineCoordinator:
    """Two-engine gapless/crossfade coordinator.

    Owns a **primary** and a **secondary** engine slot (same class, built by
    the injected ``make_engine`` factory or passed directly) and implements
    REAL gapless and crossfade on any engine:

    - ``crossfade_ms == 0`` (hard gapless): when the primary is within
      :data:`GAPLESS_LEAD_MS` of its end, the pending track is preloaded
      into the secondary; the swap happens instantly once the remaining
      time drops below :data:`SWAP_EPS_MS` (or when the primary reports a
      natural end through :meth:`notify_finished`).
    - ``crossfade_ms > 0``: an equal-power fade from gm.crossfade — the
      primary fades out while the secondary (already playing the pending
      track from volume 0) fades in; the old engine is stopped and the
      slots swapped at the end of the curve.

    The app drives :meth:`tick` from its 100 ms UI timer; engine-specific
    work beyond the standard API rides on the ``on_swap(path)`` lambda the
    app injects.  Volume curves are pure python (see gm.crossfade).
    """

    def __init__(self, make_engine=None, primary=None, secondary=None,
                 tick_ms: int = 100, crossfade_ms: int = 0) -> None:
        if primary is None and callable(make_engine):
            primary = make_engine()
        if secondary is None and callable(make_engine):
            secondary = make_engine()
        if primary is None or secondary is None:
            raise ValueError("EngineCoordinator needs make_engine or both "
                             "primary and secondary engines")
        self.primary: AudioEngineBase = primary
        self.secondary: AudioEngineBase = secondary
        self.tick_ms = max(10, int(tick_ms))
        self._crossfade_ms = 0
        self._fade = CrossfadeState(fade_ms=crossfade_ms, tick_ms=self.tick_ms)
        self.crossfade_ms = crossfade_ms  # validated through the setter
        self._base_volume = 100
        self._pending: str | None = None
        self._preloaded = False
        self._fading = False
        self._saw_playing = False
        # hook the app injects (small lambda for engine-specific wiring)
        self.on_swap = None

    # -- configuration ------------------------------------------------------
    @property
    def crossfade_ms(self) -> int:
        """Crossfade duration in ms (0 = hard gapless), clamped 0..12000."""
        return self._crossfade_ms

    @crossfade_ms.setter
    def crossfade_ms(self, value: int) -> None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            log.warning("non-numeric crossfade_ms %r; using 0", value)
            value = 0
        self._crossfade_ms = max(0, min(12_000, value))
        self._fade.fade_ms = self._crossfade_ms

    def set_crossfade_ms(self, ms: int) -> int:
        """Set the crossfade duration and return the clamped value."""
        self.crossfade_ms = int(ms)
        return self._crossfade_ms

    def set_user_volume(self, vol: int) -> None:
        """Track the app-level volume so fades ramp toward it."""
        v = max(0, min(100, int(vol)))
        self._base_volume = v
        if not self._fading:
            try:
                self.primary.set_volume(v)
            except Exception as exc:
                log.debug("set_user_volume failed: %s", exc)

    # -- pending track --------------------------------------------------------
    @property
    def pending(self) -> str | None:
        """The armed next-track path (None when idle)."""
        return self._pending

    @property
    def fading(self) -> bool:
        """True while a crossfade is in flight."""
        return self._fading

    @property
    def preloaded(self) -> bool:
        """True once the pending track sits in the secondary engine."""
        return self._preloaded

    def next_track_pending(self, path: str | None) -> None:
        """Arm the next track (None disarms without touching playback)."""
        self._pending = str(path) if path else None
        self._preloaded = False
        self._fading = False
        self._saw_playing = False
        self._fade.reset()
        # keep the engine-level almost-end hint aligned with the handoff
        try:
            self.primary.crossfade_ms = max(self._crossfade_ms, GAPLESS_LEAD_MS)
        except Exception as exc:
            log.debug("could not set primary crossfade_ms: %s", exc)

    def cancel_pending(self) -> None:
        """Disarm the pending track; abort an in-flight fade gracefully."""
        if self._fading:
            try:
                self.primary.set_volume(self._base_volume)
            except Exception as exc:
                log.debug("cancel: restore volume failed: %s", exc)
            try:
                self.secondary.set_volume(0)
                self.secondary.stop()
            except Exception as exc:
                log.debug("cancel: secondary stop failed: %s", exc)
            self._fading = False
            self._fade.reset()
        self._pending = None
        self._preloaded = False
        self._saw_playing = False

    # -- handoff driving ------------------------------------------------------
    def tick(self) -> None:
        """Advance the handoff state machine; call from a ~100 ms timer."""
        if self._fading:
            self._tick_fade()
            return
        if not self._pending:
            return
        p = self.primary
        try:
            st = p.state()
            pos = p.position()
            dur = p.duration()
        except Exception as exc:
            log.debug("coordinator tick read failed: %s", exc)
            return
        if st != STATE_PLAYING:
            if st == STATE_STOPPED and self._saw_playing:
                # primary ended naturally before the swap window closed
                self.swap_now()
            return
        self._saw_playing = True
        if dur <= 0:
            return
        remaining = dur - pos
        if self._crossfade_ms <= 0:
            if not self._preloaded and remaining <= GAPLESS_LEAD_MS:
                self._preload()
            if self._preloaded and remaining <= SWAP_EPS_MS:
                self.swap_now()
        elif remaining <= self._crossfade_ms + self.tick_ms:
            self._begin_fade()
            self._tick_fade()

    def notify_almost_end(self) -> None:
        """Engine-level almost_end hook (app-wired); same logic as tick()."""
        self.tick()

    def notify_finished(self) -> None:
        """The app calls this when the primary reports a natural track end."""
        if self._pending:
            self.swap_now()

    def swap_now(self) -> bool:
        """Immediately hand playback over to the secondary engine."""
        if not self._pending:
            return False
        path = self._pending
        sec = self.secondary
        try:
            if not self._preloaded:
                sec.load(path)
            sec.set_volume(self._base_volume)
            sec.play()
        except Exception as exc:
            log.warning("swap_now failed: %s", exc)
            return False
        old = self.primary
        self.primary, self.secondary = sec, old
        self._pending = None
        self._preloaded = False
        self._fading = False
        self._saw_playing = False
        self._fade.reset()
        try:
            old.stop()
        except Exception as exc:
            log.debug("post-swap cleanup failed: %s", exc)
        self._fire_swap(path)
        return True

    # -- internals ---------------------------------------------------------
    def _preload(self) -> None:
        try:
            self.secondary.load(self._pending)
            self._preloaded = True
        except Exception as exc:
            log.warning("preload failed: %s", exc)

    def _begin_fade(self) -> None:
        path = self._pending
        try:
            self.secondary.load(path)
            self.secondary.set_volume(0)
            self.secondary.play()
            self._fade.start(self._base_volume, self._base_volume)
            self._fading = True
        except Exception as exc:
            log.warning("crossfade begin failed: %s", exc)
            self._fading = False

    def _tick_fade(self) -> None:
        if not self._fading:
            return
        pair = self._fade.tick()
        if pair is None:
            self._finish_fade()
            return
        out_vol, in_vol = pair
        try:
            self.primary.set_volume(int(round(out_vol)))
        except Exception as exc:
            log.debug("fade out volume failed: %s", exc)
        try:
            self.secondary.set_volume(int(round(in_vol)))
        except Exception as exc:
            log.debug("fade in volume failed: %s", exc)
        if not self._fade.active:
            self._finish_fade()

    def _finish_fade(self) -> None:
        if not self._fading:
            return
        self._fading = False
        path = self._pending or ""
        old = self.primary
        self.primary, self.secondary = self.secondary, old
        self._pending = None
        self._preloaded = False
        self._saw_playing = False
        self._fade.reset()
        try:
            old.stop()
        except Exception as exc:
            log.debug("post-fade cleanup failed: %s", exc)
        self._fire_swap(path)

    def _fire_swap(self, path: str) -> None:
        if self.on_swap is None:
            return
        try:
            self.on_swap(path)
        except Exception as exc:
            log.warning("on_swap callback failed: %s", exc)


#: Known engine names for the factory ("qt", "mpv").
ENGINE_NAMES = ("qt", "mpv")


def create_preferred_engine(order: list[str], parent=None) -> tuple[AudioEngineBase, str]:
    """Try engine names in ``order``; return ``(engine, name)``.

    Known names: ``"qt"`` (QtAudioEngine, receives ``parent``) and
    ``"mpv"`` (MpvAudioEngine).  Unknown names are skipped with a log line.
    Raises :class:`AudioEngineUnavailable` when nothing in ``order`` could
    be constructed.
    """
    registry = {"qt": QtAudioEngine, "mpv": MpvAudioEngine}
    tried: list[str] = []
    for entry in order or []:
        key = str(entry).strip().lower()
        cls = registry.get(key)
        if cls is None:
            tried.append(f"{entry}(unknown)")
            log.debug("unknown engine name %r", entry)
            continue
        try:
            if key == "qt":
                engine = cls(parent=parent)
            else:
                engine = cls()
            return engine, key
        except AudioEngineUnavailable as exc:
            tried.append(f"{key}: {exc}")
            log.info("engine %r unavailable: %s", key, exc)
        except Exception as exc:
            tried.append(f"{key}: {exc}")
            log.warning("engine %r failed to init: %s", key, exc)
    raise AudioEngineUnavailable(
        "No usable audio engine. Tried: " + ("; ".join(tried) or "(none)"))
