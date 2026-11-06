"""Golden Music 3.0 — crossfade math and tick-driven fade state (Qt-free).

Equal-power (constant-power) fades: the outgoing track follows a
sqrt(1 - x) curve while the incoming track follows sqrt(x), so the summed
power (a^2 + b^2) stays constant across the overlap — no perceived volume
dip in the middle of a crossfade.

Everything here is pure python with no optional dependencies so the
EngineCoordinator (gm.audio_engines) can always rely on it.
"""
import logging
import math

log = logging.getLogger("app.audio")

#: Maximum supported fade duration in milliseconds.
MAX_FADE_MS = 12_000

#: Default tick interval the app drives CrossfadeState.tick() with.
DEFAULT_TICK_MS = 100


def equal_power_curve(steps: int, out: bool = False) -> list[float]:
    """Return an equal-power fade curve of ``steps`` points in 0..1.

    ``out=True``  -> cosine-like sqrt(1 - x): starts at 1.0, ends at 0.0
    (fade-out level).
    ``out=False`` -> sine-like sqrt(x): starts at 0.0, ends at 1.0
    (fade-in level).

    ``steps`` < 2 yields a single full-level point (no perceptible ramp).
    """
    n = int(steps)
    if n < 2:
        return [1.0]
    if out:
        return [math.sqrt(1.0 - i / (n - 1)) for i in range(n)]
    return [math.sqrt(i / (n - 1)) for i in range(n)]


def crossfade_volumes(fade_ms: int, tick_ms: int,
                      start_vol: float, end_vol: float) -> list[tuple[float, float]]:
    """Per-tick ``(out_volume, in_volume)`` pairs for an equal-power crossfade.

    The outgoing engine ramps ``start_vol`` -> 0 while the incoming engine
    ramps 0 -> ``end_vol``.  When ``start_vol == end_vol`` every pair obeys
    ``out^2 + in^2 == start_vol^2`` (constant power).

    ``fade_ms`` <= 0 (hard gapless) yields an empty list — there is nothing
    to fade, the caller just swaps engines.
    """
    try:
        fade_ms = int(fade_ms)
        tick_ms = int(tick_ms)
    except (TypeError, ValueError):
        log.warning("crossfade_volumes: non-numeric fade_ms/tick_ms")
        return []
    if fade_ms <= 0 or tick_ms <= 0:
        return []
    steps = max(1, round(fade_ms / tick_ms))
    out_curve = equal_power_curve(steps, out=True)
    in_curve = equal_power_curve(steps, out=False)
    start = float(start_vol)
    end = float(end_vol)
    return [(start * o, end * i) for o, i in zip(out_curve, in_curve)]


class CrossfadeState:
    """Tick-driven fade bookkeeping with a ``finished`` event.

    ``fade_ms`` is validated to 0..MAX_FADE_MS (0 = hard gapless, no fade).
    Drive with :meth:`tick` from the app's ~100 ms UI timer; each call
    returns the ``(out_vol, in_vol)`` pair to apply this tick, or ``None``
    when idle.  The ``finished`` event fires exactly once, on the tick that
    consumes the final pair (the old track is then silent and the new one
    at full level).
    """

    def __init__(self, fade_ms: int = 0, tick_ms: int = DEFAULT_TICK_MS) -> None:
        self._tick_ms = max(10, int(tick_ms))
        self._fade_ms = 0
        self.fade_ms = fade_ms  # validated through the property setter
        self._volumes: list[tuple[float, float]] = []
        self._index = 0
        self._listeners: dict[str, list] = {}

    # -- configuration ------------------------------------------------------
    @property
    def fade_ms(self) -> int:
        """Fade duration in ms, clamped to 0..12000 (0 = hard gapless)."""
        return self._fade_ms

    @fade_ms.setter
    def fade_ms(self, value: int) -> None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            log.warning("CrossfadeState: non-numeric fade_ms %r; using 0", value)
            value = 0
        self._fade_ms = max(0, min(MAX_FADE_MS, value))

    @property
    def tick_ms(self) -> int:
        """Tick interval in ms this state was configured for."""
        return self._tick_ms

    # -- events ---------------------------------------------------------------
    def add_listener(self, event: str, callback) -> None:
        """Register a zero-arg callback fired when ``event`` happens.

        Currently supported event: ``'finished'``.
        """
        self._listeners.setdefault(str(event), []).append(callback)

    def _fire(self, event: str) -> None:
        for cb in self._listeners.get(event, ()):
            try:
                cb()
            except Exception as exc:  # never let a listener break the fade
                log.debug("crossfade '%s' listener failed: %s", event, exc)

    # -- fade driving ----------------------------------------------------
    def start(self, start_vol: float, end_vol: float,
              fade_ms: int | None = None) -> bool:
        """Begin a fade from ``start_vol`` to ``end_vol``.

        Returns True when a fade is actually running (False for gapless /
        zero-length fades).
        """
        if fade_ms is not None:
            self.fade_ms = fade_ms
        self._volumes = crossfade_volumes(self._fade_ms, self._tick_ms,
                                          start_vol, end_vol)
        self._index = 0
        return self.active

    def tick(self) -> tuple[float, float] | None:
        """Advance one tick; return this tick's ``(out_vol, in_vol)`` pair.

        Returns ``None`` when no fade is active.  Fires ``finished`` once
        when the final pair has been consumed.
        """
        if not self.active:
            return None
        pair = self._volumes[self._index]
        self._index += 1
        if self._index >= len(self._volumes):
            self._fire("finished")
        return pair

    def reset(self) -> None:
        """Drop any in-flight fade (listeners are kept)."""
        self._volumes = []
        self._index = 0

    # -- inspection --------------------------------------------------------
    @property
    def volumes(self) -> list[tuple[float, float]]:
        """The (out, in) volume pairs of the current fade."""
        return list(self._volumes)

    @property
    def index(self) -> int:
        """How many pairs have been consumed so far."""
        return self._index

    @property
    def active(self) -> bool:
        """True while unconsumed pairs remain."""
        return self._index < len(self._volumes)

    @property
    def done(self) -> bool:
        """True once a started fade has been fully consumed."""
        return bool(self._volumes) and not self.active
