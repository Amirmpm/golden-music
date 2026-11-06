"""Golden Music 3.0 — audio module tests (headless: no PyQt6 anywhere).

Covers, section by section:
  gm.audio_engines  — abstract API, FakeEngine, mpv engine via a fake python-mpv,
                      EngineCoordinator gapless/crossfade, factory fallbacks.
  gm.equalizer      — RBJ coefficients, presets, mpv string, controller, persistence.
  gm.normalize      — ReplayGain tag parsing (FLAC/ID3/MP4), clamp math, cache,
                      LUFS approximation, persistence.
  gm.crossfade      — equal-power curves, volume pairs, CrossfadeState events.
  gm.waveform       — encode/decode round-trip, decode_peaks fallbacks, fake
                      miniaudio decode, single-flight + generation cache.
  gm.spectrum       — 20 log-spaced bands, 440 Hz sine placement (FFT+Goertzel),
                      silence, attack/decay smoothing, peak hold.
  gm.silence_skip   — window detection, skip math, persistence, empty input.

Run:  cd <repo> && python3 scripts/test_audio_engines.py
"""
import array
import logging
import math
import os
import struct
import sys
import tempfile
import threading
import time
import traceback
import types
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep the never-crash loggers quiet so output stays readable.
logging.getLogger("app").setLevel(logging.CRITICAL)

passed = 0
failed = 0
errors = []


def check(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  [OK] {name}")
    except Exception as e:
        failed += 1
        errors.append((name, traceback.format_exc()))
        print(f"  [FAIL] {name}: {e}")


def _int16_bytes(values) -> bytes:
    """Encode a sequence of ints as little-endian int16 bytes."""
    arr = array.array("h", values)
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


# ============================================================================
# gm.audio_engines
# ============================================================================
print("\n=== gm.audio_engines ===")

import gm.audio_engines as ae  # noqa: E402
from gm.audio_engines import (  # noqa: E402
    AudioEngineBase,
    AudioEngineUnavailable,
    ENGINE_NAMES,
    EngineCoordinator,
    FakeEngine,
    MpvAudioEngine,
    QtAudioEngine,
    create_preferred_engine,
)
from gm.equalizer import EQController  # noqa: E402
from gm.normalize import Normalizer  # noqa: E402


def module_import_is_headless():
    # No PyQt6 submodule may be pulled in at import time (no LD_LIBRARY_PATH fix).
    assert "PyQt6.QtMultimedia" not in sys.modules
    assert issubclass(QtAudioEngine, AudioEngineBase)
    assert issubclass(MpvAudioEngine, AudioEngineBase)
    assert issubclass(FakeEngine, AudioEngineBase)
    assert set(ENGINE_NAMES) == {"qt", "mpv"}


check("import_is_headless", module_import_is_headless)


def abstract_base_is_enforced():
    class Incomplete(AudioEngineBase):
        pass

    try:
        Incomplete()
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for abstract engine")


check("abstract_base_enforced", abstract_base_is_enforced)


def fake_engine_basics():
    e = FakeEngine(duration_ms=9000)
    e.load_and_play("a.mp3")
    assert e.state() == ae.STATE_PLAYING
    assert e.loaded_paths == ["a.mp3"]
    e.set_position(1200)
    assert e.position() == 1200
    assert ("set_position", 1200) in e.calls
    e.set_volume(150)
    assert e.volume() == 100
    e.set_volume(-10)
    assert e.volume() == 0
    e.set_rate(9.0)
    assert e.rate() == 4.0
    e.pause()
    assert e.state() == ae.STATE_PAUSED
    e.stop()
    assert e.state() == ae.STATE_STOPPED and e.position() == 0
    assert e.capabilities() >= {"gapless", "stream"}
    assert e.eq is None and e.normalizer is None  # base optional features
    assert e.name() == "fake"


check("fake_engine_basics", fake_engine_basics)


def almost_end_fires_once_per_track():
    e = FakeEngine(duration_ms=10_000)
    fired = []
    e.on_almost_end = lambda: fired.append(1)
    e.crossfade_ms = 2000
    e._check_almost_end(5_000, 10_000)
    assert fired == []           # remaining 5s > 2s window
    e._check_almost_end(8_100, 10_000)
    assert fired == [1]          # remaining 1.9s <= 2s
    e._check_almost_end(8_500, 10_000)
    assert fired == [1]          # once per track
    e._reset_almost_end()
    e._check_almost_end(8_500, 10_000)
    assert len(fired) == 2         # re-armed after reset


check("almost_end_once_per_track", almost_end_fires_once_per_track)


def mpv_unavailable_raises_cleanly():
    assert ae.MPV_AVAILABLE is False  # python-mpv is not installed in this env
    try:
        MpvAudioEngine()
    except AudioEngineUnavailable as exc:
        assert "python-mpv" in str(exc)
    else:
        raise AssertionError("expected AudioEngineUnavailable")


check("mpv_unavailable_raises_cleanly", mpv_unavailable_raises_cleanly)


# ---- fake python-mpv -------------------------------------------------------
class _FakeMpvPlayer:
    """Duck-typed stand-in for mpv.MPV() instances."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.props = {"pause": False, "volume": 100.0, "speed": 1.0, "af": ""}
        self.loadfile_calls = []
        self.observed = {}
        self.event_handlers = {}
        self.seek_calls = []
        self.commands = []
        self.stopped = False

    def __setitem__(self, key, value):
        self.props[key] = value

    def __getitem__(self, key):
        return self.props.get(key)

    def loadfile(self, path, mode="replace"):
        self.loadfile_calls.append((str(path), str(mode)))

    def observe_property(self, name, cb):
        self.observed[name] = cb

    def event_callback(self, name):
        def deco(fn):
            self.event_handlers[name] = fn
            return fn
        return deco

    def seek(self, amount, reference="relative", precision="default"):
        self.seek_calls.append((float(amount), reference))

    def command(self, *args):
        self.commands.append(tuple(args))

    def stop(self):
        self.stopped = True

    def fire(self, name, value):
        cb = self.observed.get(name)
        if cb is not None:
            cb(name, value)


def _install_fake_mpv():
    mod = types.ModuleType("mpv")
    holder = {}

    def _make(**kwargs):
        player = _FakeMpvPlayer(**kwargs)
        holder["player"] = player
        return player

    mod.MPV = _make
    mod.Constants = types.SimpleNamespace(MPV_END_FILE_REASON_EOF=0)
    sys.modules["mpv"] = mod
    return holder


def _cleanup_mpv():
    sys.modules.pop("mpv", None)


def _mpv_engine():
    holder = _install_fake_mpv()
    return MpvAudioEngine(), holder["player"]


def mpv_fake_constructs_and_observs():
    try:
        engine, player = _mpv_engine()
        assert engine.name() == "mpv"
        assert {"eq", "gapless", "tempo", "stream", "normalize"} <= engine.capabilities()
        assert player.init_kwargs.get("video") is False
        assert set(player.observed) >= {"time-pos", "duration", "pause"}
        assert "end_file" in player.event_handlers
        assert isinstance(engine.eq, EQController)
        assert isinstance(engine.normalizer, Normalizer)
        assert engine.position() == 0 and engine.duration() == 0
    finally:
        _cleanup_mpv()


check("mpv_fake_constructs_and_observs", mpv_fake_constructs_and_observs)


def mpv_fake_load_play_stop():
    try:
        engine, player = _mpv_engine()
        states = []
        engine.on_state = states.append
        engine.load_and_play("song.flac")
        assert ("song.flac", "replace") in player.loadfile_calls
        assert player.props["pause"] is False
        assert engine.state() == ae.STATE_PLAYING
        assert states[-1] == ae.STATE_PLAYING
        engine.pause()
        assert player.props["pause"] is True
        assert engine.state() == ae.STATE_PAUSED
        engine.stop()
        assert player.stopped is True
        assert engine.state() == ae.STATE_STOPPED
    finally:
        _cleanup_mpv()


check("mpv_fake_load_play_stop", mpv_fake_load_play_stop)


def mpv_fake_properties():
    try:
        engine, player = _mpv_engine()
        engine.set_position(5000)
        assert player.seek_calls and abs(player.seek_calls[0][0] - 5.0) < 1e-9
        assert player.seek_calls[0][1] == "absolute"
        assert engine.position() == 5000
        engine.set_volume(55)
        assert player.props["volume"] == 55 and engine.volume() == 55
        engine.set_volume(150)
        assert player.props["volume"] == 100  # clamped to mpv's 0-100
        engine.set_rate(1.5)
        assert player.props["speed"] == 1.5 and engine.rate() == 1.5
    finally:
        _cleanup_mpv()


check("mpv_fake_properties", mpv_fake_properties)


def mpv_fake_observers_and_almost_end():
    try:
        engine, player = _mpv_engine()
        positions, durations, states = [], [], []
        engine.on_position = positions.append
        engine.on_duration = durations.append
        engine.on_state = states.append
        player.fire("duration", 240.5)
        assert durations == [240500] and engine.duration() == 240500
        player.fire("time-pos", 12.34)
        assert positions == [12340] and engine.position() == 12340
        player.fire("pause", True)
        assert engine.state() == ae.STATE_PAUSED
        assert states[-1] == ae.STATE_PAUSED
        almost = []
        engine.crossfade_ms = 5000
        engine.on_almost_end = lambda: almost.append(1)
        player.fire("time-pos", 236.0)     # remaining 4.5s <= 5s
        assert almost == [1]
        player.fire("time-pos", 237.0)
        assert almost == [1]               # fires once per track
    finally:
        _cleanup_mpv()


check("mpv_fake_observers_and_almost_end", mpv_fake_observers_and_almost_end)


def mpv_fake_end_file_mapping():
    try:
        engine, player = _mpv_engine()
        finished = []
        engine.on_finished = lambda: finished.append(1)
        handler = player.event_handlers["end_file"]
        handler({"reason": "eof"})
        assert finished == [1]
        handler({"reason": "stop"})
        handler({"reason": "quit"})
        handler({"reason": "error"})
        assert finished == [1]             # only natural eof counts
    finally:
        _cleanup_mpv()


check("mpv_fake_end_file_mapping", mpv_fake_end_file_mapping)


def mpv_fake_af_gapless_and_replaygain():
    try:
        engine, player = _mpv_engine()
        engine.enqueue("next.flac")
        assert ("next.flac", "append-play") in player.loadfile_calls
        engine.set_af("superequalizer=eq1=3.0")
        assert player.props["af"] == "superequalizer=eq1=3.0"
        assert engine.get_af() == "superequalizer=eq1=3.0"
        assert engine.set_replaygain("album", 3.0) is True
        assert player.props["replaygain"] == "album"
        assert abs(float(player.props["replaygain-preamp"]) - 3.0) < 1e-9
        assert ae.volume_gain_af(-4.082) == "volume=gain=-4.08"
        assert ae.SILENCE_REMOVE_AF.startswith("silenceremove=")
    finally:
        _cleanup_mpv()


check("mpv_fake_af_gapless_and_replaygain", mpv_fake_af_gapless_and_replaygain)


def factory_fallbacks_and_errors():
    try:
        create_preferred_engine(["mpv"])
    except AudioEngineUnavailable as exc:
        assert "mpv" in str(exc).lower()
    else:
        raise AssertionError("expected AudioEngineUnavailable (mpv missing)")
    try:
        create_preferred_engine(["bogus"])
    except AudioEngineUnavailable as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("expected AudioEngineUnavailable (unknown name)")
    try:
        create_preferred_engine([])
    except AudioEngineUnavailable:
        pass
    else:
        raise AssertionError("expected AudioEngineUnavailable (empty order)")
    try:
        _install_fake_mpv()
        engine, name = create_preferred_engine(["bogus", "mpv"])
        assert name == "mpv" and isinstance(engine, MpvAudioEngine)
    finally:
        _cleanup_mpv()


check("factory_fallbacks_and_errors", factory_fallbacks_and_errors)


# ---- EngineCoordinator -----------------------------------------------------
def coordinator_hard_gapless():
    f1 = FakeEngine(duration_ms=10_000)
    f2 = FakeEngine(duration_ms=10_000)
    coord = EngineCoordinator(primary=f1, secondary=f2, tick_ms=100, crossfade_ms=0)
    swaps = []
    coord.on_swap = lambda path: swaps.append(path)
    coord.set_user_volume(90)
    assert ("set_volume", 90) in f1.calls
    f1.load_and_play("a.mp3")
    coord.next_track_pending("b.mp3")
    f1.position_ms = 9_700          # remaining 300ms <= GAPLESS_LEAD_MS
    coord.tick()
    assert f2.loaded_paths == ["b.mp3"]       # preloaded into secondary
    assert f2.state() == ae.STATE_STOPPED     # ...but not playing yet
    assert swaps == []
    f1.position_ms = 9_950          # remaining 50ms <= SWAP_EPS_MS
    coord.tick()
    assert coord.primary is f2 and coord.secondary is f1
    assert f2.state() == ae.STATE_PLAYING
    assert f2.volume() == 90
    assert f1.state() == ae.STATE_STOPPED
    assert swaps == ["b.mp3"]
    assert coord.pending is None


check("coordinator_hard_gapless", coordinator_hard_gapless)


def coordinator_swap_on_natural_end():
    f1 = FakeEngine(duration_ms=10_000)
    f2 = FakeEngine(duration_ms=10_000)
    coord = EngineCoordinator(primary=f1, secondary=f2, tick_ms=100)
    f1.load_and_play("a.mp3")
    coord.next_track_pending("b.mp3")
    f1.position_ms = 5_000
    coord.tick()
    assert f2.loaded_paths == []    # mid-track: nothing armed yet
    f1.stop()                       # engine reports natural end
    coord.tick()
    assert coord.primary is f2
    assert f2.state() == ae.STATE_PLAYING
    assert f2.loaded_paths == ["b.mp3"]


check("coordinator_swap_on_natural_end", coordinator_swap_on_natural_end)


def coordinator_crossfade_equal_power():
    f1 = FakeEngine(duration_ms=10_000)
    f2 = FakeEngine(duration_ms=10_000)
    coord = EngineCoordinator(primary=f1, secondary=f2, tick_ms=100, crossfade_ms=600)
    swaps = []
    coord.on_swap = lambda path: swaps.append(path)
    coord.set_user_volume(80)
    f1.load_and_play("a.mp3")
    coord.next_track_pending("b.mp3")
    f1.position_ms = 9_500          # remaining 500ms <= 600 + 100 tick
    f1_trace, f2_trace = [], []
    for _ in range(6):
        coord.tick()
        f1_trace.append(f1.volume())
        f2_trace.append(f2.volume())
    assert coord.fading is False
    assert coord.primary is f2
    assert f2.state() == ae.STATE_PLAYING and f2.volume() == 80
    assert f1.state() == ae.STATE_STOPPED
    assert swaps == ["b.mp3"]
    assert f1_trace[0] == 80 and f2_trace[0] == 0
    assert f1_trace[-1] == 0 and f2_trace[-1] == 80
    for a, b in zip(f1_trace, f2_trace):    # equal power within int rounding
        assert abs(a * a + b * b - 80 * 80) <= 160, (a, b)


check("coordinator_crossfade_equal_power", coordinator_crossfade_equal_power)


def coordinator_cancel_mid_fade():
    f1 = FakeEngine(duration_ms=10_000)
    f2 = FakeEngine(duration_ms=10_000)
    coord = EngineCoordinator(primary=f1, secondary=f2, tick_ms=100, crossfade_ms=600)
    coord.set_user_volume(80)
    f1.load_and_play("a.mp3")
    coord.next_track_pending("b.mp3")
    f1.position_ms = 9_500
    coord.tick()
    coord.tick()
    assert coord.fading is True
    coord.cancel_pending()
    assert coord.pending is None and coord.fading is False
    assert f1.volume() == 80                     # primary restored
    assert f2.state() == ae.STATE_STOPPED        # secondary silenced
    assert f2.volume() == 0
    f1.position_ms = 5_000
    coord.tick()
    assert f1.state() == ae.STATE_PLAYING        # primary keeps playing
    assert len(f2.loaded_paths) == 1             # no further loads


check("coordinator_cancel_mid_fade", coordinator_cancel_mid_fade)


def coordinator_clamps_and_swap_now():
    f1 = FakeEngine(duration_ms=10_000)
    f2 = FakeEngine(duration_ms=10_000)
    coord = EngineCoordinator(primary=f1, secondary=f2)
    assert coord.set_crossfade_ms(99_999) == 12_000
    assert coord.crossfade_ms == 12_000
    assert coord.set_crossfade_ms(-5) == 0
    assert coord.crossfade_ms == 0
    swaps = []
    coord.on_swap = swaps.append
    coord.set_user_volume(70)
    f1.load_and_play("a.mp3")
    coord.next_track_pending("b.mp3")
    assert coord.swap_now() is True              # swap without preload
    assert f2.loaded_paths == ["b.mp3"]
    assert f2.state() == ae.STATE_PLAYING and f2.volume() == 70
    assert swaps == ["b.mp3"] and coord.pending is None
    assert coord.swap_now() is False             # nothing pending
    coord.next_track_pending("c.mp3")
    coord.notify_finished()                      # app reports natural end
    assert "c.mp3" in coord.primary.loaded_paths
    assert coord.primary.state() == ae.STATE_PLAYING


check("coordinator_clamps_and_swap_now", coordinator_clamps_and_swap_now)


def coordinator_from_factory():
    made = []

    def make():
        e = FakeEngine(duration_ms=1000)
        made.append(e)
        return e

    coord = EngineCoordinator(make_engine=make)
    assert len(made) == 2
    assert coord.primary is not coord.secondary
    try:
        EngineCoordinator()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError without engines")


check("coordinator_from_factory", coordinator_from_factory)


# ============================================================================
# gm.equalizer
# ============================================================================
print("\n=== gm.equalizer ===")

import gm.equalizer as eq  # noqa: E402
from gm.equalizer import (  # noqa: E402
    BAND_FREQS,
    PRESETS,
    EQController,
    apply_biquad,
    biquad_peaking_coeffs,
    clamp_gain,
    mpv_superequalizer_string,
    validate_gains,
)


def rbj_identity_at_zero_gain():
    c = biquad_peaking_coeffs(1000.0, 0.0, 1.0, 44100)
    assert abs(c[0] - 1.0) < 1e-9 and abs(c[1]) < 1e-9 and abs(c[2]) < 1e-9
    assert abs(c[3]) < 1e-9 and abs(c[4]) < 1e-9
    x = [0.3 * math.sin(2 * math.pi * 440 * i / 44100) for i in range(512)]
    y = apply_biquad(x, c)
    assert all(abs(a - b) <= 1e-12 for a, b in zip(x, y))


check("rbj_identity_at_zero_gain", rbj_identity_at_zero_gain)


def rbj_probes_boost_band_region():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return  # numpy optional: skip silently
    sr = 48000.0
    coeffs = biquad_peaking_coeffs(1000.0, 6.0, 1.0, sr)

    def gain_db_at(freq):
        n = int(sr * 0.25)
        x = [0.5 * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]
        y = apply_biquad(x, coeffs)
        m = n // 5                      # skip the filter's settle-in
        rms_x = math.sqrt(sum(v * v for v in x[m:]) / (n - m))
        rms_y = math.sqrt(sum(v * v for v in y[m:]) / (n - m))
        return 20 * math.log10(rms_y / rms_x)

    assert gain_db_at(1000.0) > 4.5, gain_db_at(1000.0)
    assert gain_db_at(200.0) < 3.0, gain_db_at(200.0)
    assert gain_db_at(5000.0) < 3.0, gain_db_at(5000.0)


check("rbj_probes_boost_band_region", rbj_probes_boost_band_region)


def rbj_sweep_boosts_band_region():
    try:
        import numpy as np
    except ImportError:
        return  # numpy optional: skip silently
    sr = 48000.0
    n = int(sr * 2)
    t = np.arange(n) / sr
    f0, f1 = 40.0, 16000.0
    k = f1 / f0
    phase = 2 * np.pi * f0 / math.log(k) * (k ** t - 1.0)   # log sine sweep
    x = 0.5 * np.sin(phase)
    coeffs = biquad_peaking_coeffs(1000.0, 6.0, 1.0, sr)
    y = np.asarray(apply_biquad(x.tolist(), coeffs))
    win = 2048
    gains, freqs = [], []
    for lo in range(0, n - win, win):
        mid = lo + win // 2
        inst = float(f0 * k ** t[mid])
        rx = float(np.sqrt(np.mean(x[lo:lo + win] ** 2)))
        ry = float(np.sqrt(np.mean(y[lo:lo + win] ** 2)))
        if rx > 1e-9:
            gains.append(20 * math.log10(ry / rx))
            freqs.append(inst)
    idx = gains.index(max(gains))
    assert 700 <= freqs[idx] <= 1400, (freqs[idx], max(gains))
    assert max(gains) > 4.0
    far = [g for f, g in zip(freqs, gains) if f < 200 or f > 6000]
    assert all(g < 3.0 for g in far), max(far)


check("rbj_sweep_boosts_band_region", rbj_sweep_boosts_band_region)


def presets_and_bands():
    assert BAND_FREQS == [31.0, 62.0, 125.0, 250.0, 500.0,
                          1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
    assert PRESETS["Flat"] == [0.0] * 10
    assert PRESETS["Bass Boost"] == [7.0, 5.0, 3.0] + [0.0] * 7
    assert PRESETS["Rock"][0] == 4.0 and PRESETS["Rock"][9] == 4.0
    for name, gains in PRESETS.items():
        assert len(gains) == 10, name
        assert all(-12 <= g <= 12 for g in gains), name
    assert set(PRESETS) >= {"Flat", "Bass Boost", "Rock", "Night",
                            "Vocal", "Treble Boost", "Custom"}


check("presets_and_bands", presets_and_bands)


def mpv_string_format():
    s = mpv_superequalizer_string([7, 5, 3, 0, 0, 0, 0, 0, 0, -2])
    expected = ("superequalizer=eq1=7.0:eq2=5.0:eq3=3.0:eq4=0.0:eq5=0.0:"
                "eq6=0.0:eq7=0.0:eq8=0.0:eq9=0.0:eq10=-2.0")
    assert s == expected, s
    s2 = mpv_superequalizer_string([99, -99] + [0] * 8)
    assert "eq1=12.0" in s2 and "eq2=-12.0" in s2
    try:
        mpv_superequalizer_string([0] * 9)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for 9 gains")


check("mpv_string_format", mpv_string_format)


def gain_validation_and_clamp():
    assert clamp_gain(25.0) == 12.0
    assert clamp_gain(-25.0) == -12.0
    assert clamp_gain(3.5) == 3.5
    assert validate_gains([20, -20] + [0] * 8) == [12.0, -12.0] + [0.0] * 8
    for bad in ([0] * 9, [0] * 11, None):
        try:
            validate_gains(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


check("gain_validation_and_clamp", gain_validation_and_clamp)


def eq_controller_behavior():
    ctrl = EQController(preset="Bass Boost")
    assert ctrl.gains == PRESETS["Bass Boost"]
    assert ctrl.set_band(0, 25.0) == 12.0
    assert ctrl.preset == "Custom"
    try:
        ctrl.set_band(10, 1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for band 10")
    fake = FakeEngine()
    ctrl.enabled = True
    assert ctrl.apply_to_engine(fake) is True
    assert fake.get_af() == mpv_superequalizer_string(ctrl.gains)
    ctrl.enabled = False
    assert ctrl.apply_to_engine(fake) is True
    assert fake.get_af() == ""                       # disabled clears af

    class Bare:
        pass

    assert ctrl.apply_to_engine(Bare()) is False     # unsupported engine
    ctrl2 = EQController()
    assert ctrl2.set_preset("Rock") is True
    assert ctrl2.gains == PRESETS["Rock"]
    assert ctrl2.set_preset("Nope") is False
    ctrl2.flat()
    assert ctrl2.gains == [0.0] * 10


check("eq_controller_behavior", eq_controller_behavior)


def eq_serialization_roundtrip():
    ctrl = EQController(gains=[1, -2, 3, 0, 5, -6, 7, 8, -9, 10],
                        enabled=False, preset="Custom")
    d = ctrl.to_dict()
    assert d["enabled"] is False and d["preset"] == "Custom"
    assert len(d["gains"]) == 10
    ctrl2 = EQController.from_dict(d)
    assert ctrl2.gains == ctrl.gains
    assert ctrl2.enabled is False and ctrl2.preset == "Custom"
    ctrl3 = EQController.from_dict({"preset": "Rock"})
    assert ctrl3.gains == PRESETS["Rock"]
    try:
        EQController.from_dict({"gains": [0] * 5})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for invalid stored gains")


check("eq_serialization_roundtrip", eq_serialization_roundtrip)


# ============================================================================
# gm.normalize
# ============================================================================
print("\n=== gm.normalize ===")

import gm.normalize as norm  # noqa: E402
from gm.normalize import (  # noqa: E402
    Normalizer,
    compute_target_gain,
    parse_db_string,
    read_replaygain_tags,
    rms_lufs_approx,
)


def _make_flac_with_rg(path, gain="-6.20 dB", peak="1.6"):
    """Craft a minimal valid FLAC, then tag it with vorbis comments."""
    sr, ch, bps = 44100, 2, 16
    si = struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00" + b"\x00\x00\x00"
    bits = (sr << 44) | (ch << 41) | ((bps - 1) << 36)
    si += bits.to_bytes(8, "big") + b"\x00" * 16
    assert len(si) == 34
    with open(path, "wb") as f:
        f.write(b"fLaC" + bytes([0x80, 0, 0, 34]) + si)
    from mutagen.flac import FLAC
    fl = FLAC(path)
    fl["replaygain_track_gain"] = [gain]
    fl["replaygain_track_peak"] = [peak]
    fl.save()


def parse_db_variants():
    assert abs(parse_db_string("-6.20 dB") + 6.2) < 1e-9
    assert abs(parse_db_string("+3.00 dB") - 3.0) < 1e-9
    assert parse_db_string("0.98") == 0.98
    assert abs(parse_db_string(b"-6.20 dB") + 6.2) < 1e-9
    assert parse_db_string(None) is None
    assert parse_db_string("") is None
    assert parse_db_string("garbage") is None
    assert parse_db_string("-") is None
    assert parse_db_string("  ") is None


check("parse_db_variants", parse_db_variants)


def read_tags_from_real_flac():
    tmp = Path(tempfile.mkdtemp(prefix="gmnorm_"))
    p = tmp / "t.flac"
    _make_flac_with_rg(str(p))
    tags = read_replaygain_tags(str(p))
    assert tags["track_gain_db"] == -6.2
    assert tags["track_peak"] == 1.6
    assert tags["album_gain_db"] is None and tags["album_peak"] is None


check("read_tags_from_real_flac", read_tags_from_real_flac)


def read_tags_none_safe():
    tmp = Path(tempfile.mkdtemp(prefix="gmnorm_"))
    wav = tmp / "plain.wav"
    with wave.open(str(wav), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * 800)
    tags = read_replaygain_tags(str(wav))
    assert tags == {"track_gain_db": None, "album_gain_db": None,
                    "track_peak": None, "album_peak": None}
    missing = read_replaygain_tags(str(tmp / "nope.mp3"))
    assert missing["track_gain_db"] is None
    # mutagen hidden away -> all None
    real = sys.modules.get("mutagen")
    sys.modules["mutagen"] = None
    try:
        hidden = read_replaygain_tags(str(wav))
    finally:
        if real is not None:
            sys.modules["mutagen"] = real
        else:
            sys.modules.pop("mutagen", None)
    assert hidden["track_gain_db"] is None


check("read_tags_none_safe", read_tags_none_safe)


def tag_extraction_across_formats():
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4FreeForm, MP4Tags
    t = ID3()
    t.add(TXXX(encoding=0, desc="replaygain_track_gain", text="-6.20 dB"))
    t.add(TXXX(encoding=0, desc="REPLAYGAIN_ALBUM_GAIN", text="+1.50 dB"))
    vals = norm._collect_rg_values(t)
    assert vals["replaygain_track_gain"] == "-6.20 dB"
    assert vals["replaygain_album_gain"] == "+1.50 dB"
    m = MP4Tags()
    m["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(data=b"-6.20 dB")]
    m["----:com.apple.iTunes:replaygain_track_peak"] = [MP4FreeForm(data=b"0.98")]
    vals2 = norm._collect_rg_values(m)
    assert vals2["replaygain_track_gain"] == "-6.20 dB"
    assert vals2["replaygain_track_peak"] == "0.98"
    vals3 = norm._collect_rg_values({"replaygain_track_gain": ["-3.00 dB"]})
    assert vals3["replaygain_track_gain"] == "-3.00 dB"
    assert norm._collect_rg_values(None) == {}


check("tag_extraction_across_formats", tag_extraction_across_formats)


def target_gain_math():
    assert compute_target_gain(-14.0) == -4.0
    assert compute_target_gain(-14.0, -16.0) == -2.0
    assert compute_target_gain(-18.0) == 0.0


check("target_gain_math", target_gain_math)


def rms_lufs_approx_values():
    assert rms_lufs_approx([0.0] * 1000, 44100) == -70.0
    assert rms_lufs_approx([], 44100) == -70.0
    sr = 44100
    sine = [0.5 * math.sin(2 * math.pi * 440 * i / sr) for i in range(sr)]
    v = rms_lufs_approx(sine, sr)
    assert -11.0 < v < -8.5, v
    raw = _int16_bytes([int(s * 32767) for s in sine])
    v2 = rms_lufs_approx(raw, sr)
    assert abs(v2 - v) < 0.05, (v, v2)


check("rms_lufs_approx_values", rms_lufs_approx_values)


def normalizer_clamp_math():
    tags = {"track_gain_db": 2.0, "album_gain_db": None,
            "track_peak": 1.6, "album_peak": None}
    n = Normalizer(mode="tags", tag_reader=lambda p: tags)
    g = n.gain_for("x.mp3")
    expected = 0.0 - 20 * math.log10(1.6)   # ceiling 0 dBFS
    assert abs(g - expected) < 1e-6, (g, expected)
    # peak 0.5 leaves +2 dB untouched
    n2 = Normalizer(mode="tags", tag_reader=lambda p: dict(tags, track_peak=0.5))
    assert abs(n2.gain_for("y.mp3") - 2.0) < 1e-9
    assert Normalizer(mode="off").gain_for("z.mp3") == 0.0
    n3 = Normalizer(mode="tags", tag_reader=lambda p: {"track_gain_db": None})
    assert n3.gain_for("z.mp3") == 0.0
    assert Normalizer(mode="bogus").mode == "off"   # invalid mode tolerated


check("normalizer_clamp_math", normalizer_clamp_math)


def normalizer_analysis_and_cache():
    n = Normalizer(mode="analysis", target_lufs=-18.0,
                   analyzer=lambda p: (-9.7, 0.9))
    assert abs(n.gain_for("a.mp3") + 8.3) < 1e-9
    n2 = Normalizer(mode="analysis", analyzer=lambda p: -20.0)  # scalar loudness
    assert abs(n2.gain_for("b.mp3") - 2.0) < 1e-9
    assert Normalizer(mode="analysis").gain_for("c.mp3") == 0.0  # no analyzer
    # fake cache short-circuits tag reads
    def boom(path):
        raise AssertionError("tag reader must not run on a cache hit")

    cache = {"song.flac": 3.5}
    n3 = Normalizer(mode="tags", cache=cache, tag_reader=boom)
    assert n3.gain_for("song.flac") == 3.5
    # computed gains populate the injected cache
    shared = {}
    n4 = Normalizer(mode="tags", cache=shared,
                    tag_reader=lambda p: {"track_gain_db": -6.2, "track_peak": 0.9})
    g4 = n4.gain_for("new.flac")
    assert abs(g4 + 6.2) < 1e-9
    assert shared["new.flac"] == g4


check("normalizer_analysis_and_cache", normalizer_analysis_and_cache)


def normalizer_serialization():
    n = Normalizer(mode="tags", preamp_db=3.0, ceiling_db=-1.0, target_lufs=-16.0)
    n2 = Normalizer.from_dict(n.to_dict())
    assert n2.mode == "tags" and n2.preamp_db == 3.0
    assert n2.ceiling_db == -1.0 and n2.target_lufs == -16.0
    n3 = Normalizer.from_dict({})
    assert n3.mode == "off" and n3.preamp_db == 0.0
    assert n3.ceiling_db == 0.0 and n3.target_lufs == -18.0


check("normalizer_serialization", normalizer_serialization)


# ============================================================================
# gm.crossfade
# ============================================================================
print("\n=== gm.crossfade ===")

from gm.crossfade import (  # noqa: E402
    CrossfadeState,
    crossfade_volumes,
    equal_power_curve,
)


def curve_endpoints():
    out = equal_power_curve(5, out=True)
    assert abs(out[0] - 1.0) < 1e-12 and abs(out[-1]) < 1e-12
    inn = equal_power_curve(5, out=False)
    assert abs(inn[0]) < 1e-12 and abs(inn[-1] - 1.0) < 1e-12
    assert all(0.0 <= v <= 1.0 for v in out + inn)
    assert equal_power_curve(1) == [1.0]
    assert equal_power_curve(0, out=True) == [1.0]


check("curve_endpoints", curve_endpoints)


def equal_power_property():
    pairs = crossfade_volumes(600, 100, 1.0, 1.0)
    assert len(pairs) == 6
    for a, b in pairs:
        assert abs(a * a + b * b - 1.0) < 1e-9
    assert abs(pairs[0][0] - 1.0) < 1e-12 and abs(pairs[0][1]) < 1e-12
    assert abs(pairs[-1][0]) < 1e-12 and abs(pairs[-1][1] - 1.0) < 1e-12
    scaled = crossfade_volumes(500, 100, 80, 80)
    for a, b in scaled:
        assert abs(a * a + b * b - 6400.0) < 1e-6
    assert crossfade_volumes(0, 100, 80, 80) == []   # gapless: no fade steps


check("equal_power_property", equal_power_property)


def crossfade_state_validation():
    cs = CrossfadeState(fade_ms=99999)
    assert cs.fade_ms == 12000
    cs.fade_ms = -5
    assert cs.fade_ms == 0
    cs.fade_ms = 3000
    assert cs.fade_ms == 3000
    cs.fade_ms = "abc"                       # non-numeric -> 0
    assert cs.fade_ms == 0


check("crossfade_state_validation", crossfade_state_validation)


def crossfade_state_tick_and_finished_event():
    cs = CrossfadeState(fade_ms=400, tick_ms=100)
    events = []
    cs.add_listener("finished", lambda: events.append(1))
    assert cs.start(100, 100) is True
    assert len(cs.volumes) == 4
    seen = []
    for _ in range(4):
        pair = cs.tick()
        assert pair is not None
        seen.append(pair)
    assert events == [1]                     # fired exactly once
    assert cs.done is True and cs.active is False
    assert cs.tick() is None
    assert events == [1]
    assert abs(seen[0][0] - 100.0) < 1e-9 and abs(seen[0][1]) < 1e-9
    assert abs(seen[-1][0]) < 1e-9 and abs(seen[-1][1] - 100.0) < 1e-9
    cs.reset()
    assert cs.active is False and cs.done is False
    assert cs.tick() is None
    assert cs.start(100, 100, fade_ms=0) is False   # gapless: no fade


check("crossfade_state_tick_and_finished_event",
      crossfade_state_tick_and_finished_event)


# ============================================================================
# gm.waveform
# ============================================================================
print("\n=== gm.waveform ===")

import gm.waveform as wf  # noqa: E402
from gm.waveform import (  # noqa: E402
    WaveformCache,
    decode_peaks,
    decode_peaks_blob,
    encode_peaks,
)


def peaks_roundtrip():
    values = [0.0, 1.0, 0.5, 0.25, 0.75]
    blob = encode_peaks(values)
    assert isinstance(blob, bytes) and len(blob) == 5
    back = decode_peaks_blob(blob)
    assert back[0] == 0.0 and back[1] == 1.0
    for a, b in zip(values, back):
        assert abs(a - b) <= 1.0 / 255.0 + 1e-9
    assert encode_peaks([-1.0, 2.0]) == b"\x00\xff"   # clamped


check("peaks_roundtrip", peaks_roundtrip)


def decode_peaks_without_miniaudio():
    peaks, dur = decode_peaks("whatever.mp3")     # not installed in this env
    assert peaks is None and dur is None
    sys.modules["miniaudio"] = None               # forced-missing even if installed
    try:
        assert decode_peaks("x.mp3") == (None, None)
    finally:
        sys.modules.pop("miniaudio", None)
    assert decode_peaks("x.mp3", engine="ffmpeg") == (None, None)
    assert decode_peaks("x.mp3", buckets=0) == (None, None)


check("decode_peaks_without_miniaudio", decode_peaks_without_miniaudio)


def decode_peaks_with_fake_miniaudio():
    sr = 44100
    total = 2 * sr
    samples = [30000 if i < total // 2 else 3000 for i in range(total)]
    raw = _int16_bytes(samples)

    def make_module(payload):
        mod = types.ModuleType("miniaudio")
        mod.SampleFormat = types.SimpleNamespace(SIGNED16="s16")

        def decode_file(path, output_format=None, nchannels=2,
                        sample_rate=44100, dither=None):
            return types.SimpleNamespace(samples=payload)

        mod.decode_file = decode_file
        return mod

    sys.modules["miniaudio"] = make_module(raw)
    try:
        peaks, dur = decode_peaks("song.flac", buckets=200)
        assert dur == 2000
        assert len(peaks) == 200
        assert max(peaks) == 255
        assert peaks[0] == 255                     # loud half normalises to 255
        assert peaks[99] > 200
        assert abs(peaks[199] - 26) <= 2           # quiet half ~ 3000/30000*255
        assert peaks[100] < 60
        # 10-minute analysis cap on an 11-minute file
        big = b"\x20\x4e" * (11 * 60 * sr)         # int16 20000 LE
        sys.modules["miniaudio"] = make_module(big)
        peaks2, dur2 = decode_peaks("long.flac", buckets=100)
        assert dur2 == 600000
        assert len(peaks2) == 100 and max(peaks2) == 255
        # pure-python path (numpy disabled) gives identical peaks
        saved_np = wf._np
        wf._np = None
        try:
            sys.modules["miniaudio"] = make_module(raw)
            peaks3, dur3 = decode_peaks("song.flac", buckets=200)
            assert peaks3 == peaks and dur3 == dur
        finally:
            wf._np = saved_np
    finally:
        sys.modules.pop("miniaudio", None)


check("decode_peaks_with_fake_miniaudio", decode_peaks_with_fake_miniaudio)


def decode_peaks_broken_decoder():
    mod = types.ModuleType("miniaudio")
    mod.SampleFormat = types.SimpleNamespace(SIGNED16="s16")

    def boom(*args, **kwargs):
        raise RuntimeError("decoder exploded")

    mod.decode_file = boom
    sys.modules["miniaudio"] = mod
    try:
        assert decode_peaks("bad.flac") == (None, None)
    finally:
        sys.modules.pop("miniaudio", None)


check("decode_peaks_broken_decoder", decode_peaks_broken_decoder)


def cache_hit_avoids_compute():
    db = {"hit.mp3": encode_peaks([0.0, 1.0, 0.5])}

    def compute(path):
        raise AssertionError("compute must not run on a cache hit")

    cache = WaveformCache(get=lambda p: db.get(p),
                          store=lambda p, b: db.__setitem__(p, b))
    result = cache.load("hit.mp3", compute=compute)
    assert result is not None and len(result) == 3
    assert result[0] == 0.0 and abs(result[1] - 1.0) < 1e-9


check("cache_hit_avoids_compute", cache_hit_avoids_compute)


def cache_single_flight():
    db = {}
    counter = {"n": 0}
    started = threading.Event()
    release = threading.Event()
    guard = threading.Lock()

    def slow_compute(path):
        with guard:
            counter["n"] += 1
        started.set()
        release.wait(2.0)
        return encode_peaks([0.5, 1.0, 0.25])

    cache = WaveformCache(get=lambda p: db.get(p),
                          store=lambda p, b: db.__setitem__(p, b))
    results = {}

    def worker(i):
        results[i] = cache.load("song.flac", compute=slow_compute)

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    assert started.wait(1.0)
    t2.start()
    time.sleep(0.15)          # second thread parks on the per-path lock
    release.set()
    t1.join(2.0)
    t2.join(2.0)
    assert counter["n"] == 1, counter            # exactly one compute
    assert results[0] == results[1] and results[0] is not None
    assert db["song.flac"] == encode_peaks([0.5, 1.0, 0.25])


check("cache_single_flight", cache_single_flight)


def cache_generation_drops_stale():
    db = {}
    began = threading.Event()
    release = threading.Event()

    def slow_compute(path):
        began.set()
        release.wait(2.0)
        return encode_peaks([1.0])

    cache = WaveformCache(get=lambda p: db.get(p),
                          store=lambda p, b: db.__setitem__(p, b))
    out = {}
    t = threading.Thread(
        target=lambda: out.update(r=cache.load("x.mp3", compute=slow_compute)))
    t.start()
    assert began.wait(1.0)
    cache.invalidate()         # generation bumps mid-compute
    release.set()
    t.join(2.0)
    assert out.get("r") is None          # stale result dropped
    assert "x.mp3" not in db             # and never stored
    r2 = cache.load("x.mp3", compute=lambda p: encode_peaks([1.0]))
    assert r2 == [1.0] and "x.mp3" in db  # fresh load after the bump is kept


check("cache_generation_drops_stale", cache_generation_drops_stale)


def cache_failures_tolerated():
    def bad_store(path, blob):
        raise RuntimeError("DB exploded")

    cache = WaveformCache(get=lambda p: None, store=bad_store)
    r = cache.load("y.mp3", compute=lambda p: encode_peaks([0.5]))
    assert r is not None and abs(r[0] - 0.5) <= 1.0 / 255.0 + 1e-9

    def boom(path):
        raise RuntimeError("nope")

    cache2 = WaveformCache(get=lambda p: None, store=lambda p, b: None)
    assert cache2.load("z.mp3", compute=boom) is None


check("cache_failures_tolerated", cache_failures_tolerated)


# ============================================================================
# gm.spectrum
# ============================================================================
print("\n=== gm.spectrum ===")

import gm.spectrum as sp  # noqa: E402
from gm.spectrum import BANDS, BAND_FREQS, SpectrumAnalyzer, band_magnitudes  # noqa: E402


def band_frequencies_log_spaced():
    assert BANDS == 20
    assert len(BAND_FREQS) == 20
    assert BAND_FREQS[0] == 40.0 and BAND_FREQS[-1] == 16000.0
    for a, b in zip(BAND_FREQS, BAND_FREQS[1:]):
        assert b > a
    ratio = (16000.0 / 40.0) ** (1 / 19)
    for i, f in enumerate(BAND_FREQS):
        assert abs(f - 40.0 * ratio ** i) < 1e-6 * f


check("band_frequencies_log_spaced", band_frequencies_log_spaced)


def _sine(freq, seconds=1.0, amp=0.8, sr=44100):
    n = int(sr * seconds)
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def tone_lands_in_its_band():
    sr = 44100
    edges = sp._band_edges()
    inside = next(i for i in range(BANDS) if edges[i] <= 440.0 < edges[i + 1])
    nearest = min(range(BANDS), key=lambda i: abs(BAND_FREQS[i] - 440.0))
    assert inside == nearest          # both definitions agree for 440 Hz
    for backend in ("auto", "python"):
        bands = band_magnitudes(_sine(440.0), sr, backend)
        assert len(bands) == 20
        idx = bands.index(max(bands))
        assert idx == inside, (backend, idx)
        assert abs(bands[idx] - 1.0) < 1e-9       # normalised by max band
        assert bands[idx] > 1.8 * max(bands[inside - 1], 1e-9)
        assert bands[idx] > 1.8 * max(bands[inside + 1], 1e-9)


check("tone_lands_in_its_band", tone_lands_in_its_band)


def silence_is_all_zero():
    bands = band_magnitudes([0.0] * 2048, 44100)
    assert len(bands) == 20 and all(v == 0.0 for v in bands)
    bands2 = band_magnitudes(b"\x00\x00" * 2048, 44100)
    assert all(v == 0.0 for v in bands2)


check("silence_is_all_zero", silence_is_all_zero)


def smoothing_rise_fast_fall_slow():
    an = SpectrumAnalyzer()
    sr = 44100
    loud = an.update(_sine(440.0, 0.1), sr)
    assert 0.5 <= max(loud) <= 0.7, max(loud)          # rise 0.6 from zero
    idx = loud.index(max(loud))
    peaks_after_loud = an.peaks
    quiet = an.update([0.0] * 4096, sr)
    assert 0.4 < max(quiet) < 0.86, max(quiet)         # fall 0.15 of previous
    assert all(c <= p + 1e-9 for c, p in zip(quiet, loud))
    # peak-hold decays slowly (0.02) and never below the smoothed value
    assert abs(an.peaks[idx] - (peaks_after_loud[idx] - 0.02)) < 1e-9
    assert all(p >= v - 1e-9 for p, v in zip(an.peaks, quiet))
    prev = quiet
    for _ in range(3):                                 # monotonic decay
        cur = an.update([0.0] * 4096, sr)
        assert all(c <= p + 1e-9 for c, p in zip(cur, prev))
        prev = cur
    an.reset()
    assert an.peaks == [0.0] * 20


check("smoothing_rise_fast_fall_slow", smoothing_rise_fast_fall_slow)


# ============================================================================
# gm.silence_skip
# ============================================================================
print("\n=== gm.silence_skip ===")

import gm.silence_skip as ss  # noqa: E402
from gm.silence_skip import SilenceSkipper, detect_silence_windows  # noqa: E402


def _tone(seconds, sr=44100, amp=0.5, freq=440.0):
    n = int(sr * seconds)
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def detect_windows_synthetic():
    sr = 44100
    samples = ([0.0] * sr) + _tone(1.0) + ([0.0] * (2 * sr)) + _tone(1.0)
    wins = detect_silence_windows(samples, sr)
    assert len(wins) == 2, wins
    (s1, e1), (s2, e2) = wins
    assert abs(s1 - 0) <= 100 and abs(e1 - 1000) <= 100, wins[0]
    assert abs(s2 - 2000) <= 100 and abs(e2 - 4000) <= 100, wins[1]
    # min_ms filtering
    assert detect_silence_windows(samples, sr, min_ms=1500) == [wins[1]]
    assert detect_silence_windows(samples, sr, min_ms=3000) == []
    # int16 bytes input detects the same windows
    raw = _int16_bytes([int(v * 32767) for v in samples])
    assert detect_silence_windows(raw, sr) == wins


check("detect_windows_synthetic", detect_windows_synthetic)


def skip_math():
    sk = SilenceSkipper(enabled=True, min_ms=700, threshold_db=-40.0)
    wins = [(0, 1000), (2000, 4000)]
    assert sk.next_skip_position(0, wins) == 1000
    assert sk.next_skip_position(500, wins) == 1000
    assert sk.next_skip_position(999, wins) == 1000
    assert sk.next_skip_position(2500, wins) == 4000
    assert sk.next_skip_position(1500, wins) is None    # between windows
    assert sk.next_skip_position(4000, wins) is None    # at end boundary
    assert sk.next_skip_position(100, []) is None
    sk.enabled = False
    assert sk.next_skip_position(500, wins) is None


check("skip_math", skip_math)


def silence_serialization():
    sk = SilenceSkipper(enabled=True, min_ms=900, threshold_db=-35.0)
    d = sk.to_dict()
    assert d == {"enabled": True, "min_ms": 900, "threshold_db": -35.0}
    sk2 = SilenceSkipper.from_dict(d)
    assert sk2.enabled and sk2.min_ms == 900
    assert abs(sk2.threshold_db + 35.0) < 1e-9
    sk3 = SilenceSkipper.from_dict({})
    assert not sk3.enabled and sk3.min_ms == 700


check("silence_serialization", silence_serialization)


def silence_empty_and_invalid():
    assert detect_silence_windows([], 44100) == []
    assert detect_silence_windows(b"", 44100) == []
    assert detect_silence_windows([0.1] * 100, 0) == []


check("silence_empty_and_invalid", silence_empty_and_invalid)


# ============================================================================
# summary
# ============================================================================
print("\n" + "=" * 50)
print(f"AUDIO-ENGINES SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL AUDIO-ENGINE TESTS PASSED!")
