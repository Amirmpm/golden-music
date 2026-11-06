# Golden Music — Architecture & Decision Log (2.3.0)

Non-obvious technical decisions made during the 2.3.0 cycle, and why.
Numbered for reference from code comments and discussions.

---

## #d1 — Library data in SQLite (FTS5), settings stay in config.json

**Decision:** tracks/tags/favorites/playlists/play-events/bookmarks/radio/
waveform/replaygain live in one `library.db` (WAL, single-writer lock,
per-thread connections). Scalar settings stay in the validated
`config.json`.

**Why:** the JSON files were the 100k-track bottleneck (full-file rewrite
on every save, linear scans for search). SQLite + FTS5 gives prefix search
over title/artist/album/genre with diacritics folding at millisecond scale
and incremental writes. Settings are tiny, human-inspectable, and already
covered by the existing validated-loader + never-wipe machinery — moving
them bought nothing and risked the 2.x downgrade path.

**Migration contract** (gm/migration.py): backup-first
(`backups/pre_sqlite_<ts>/`), one atomic transaction, verified row counts
against the JSON sources, `done` flag only written after verification, and
any mismatch/exception → rollback → the app runs the whole session in
legacy JSON mode. Original JSON files are never deleted (a 2.x downgrade
keeps working; `_save_config` still mirrors library data into config.json
for that reason — a conscious write-cost tradeoff).

**Never-wipe adaptation:** the scratch-path filter (test sandboxes must
never leak into real user data) is disabled when the app's own data dir is
itself inside a scratch root (testenv) — otherwise the sandbox could not
exercise persistence at all. `sync_library` is conservative: a path missing
from the in-memory list is dropped only when the file is gone AND its
parent dir still exists (offline drives keep their rows).

## #d2 — Audio: adapter facade over an engine layer (mpv optional, Qt fallback)

**Decision:** `gm/player_adapter.py` exposes exactly the v2 `AudioBackend`
surface (so ~100 MainWindow call sites survived the 2.3 refactor) over
`EngineCoordinator` with two engine slots. Engine order from settings:
`mpv` (python-mpv + libmpv) → `qt` (QMediaPlayer). `PlayerAdapter`
degrades mid-session to the legacy backend if an engine dies.

**Why:** QMediaPlayer cannot do EQ/crossfade/silence-removal, and its
rate change is not reliably pitch-preserving. libmpv does all of it natively
(`superequalizer`, `volume=gain`, `silenceremove`, tempo-preserving
`speed`). But making libmpv a hard dependency would break the "pip install
and run" story — hence capability flags and silent capability loss instead
of hard failures. Real gapless/crossfade works on ANY engine via the
dual-slot coordinator (secondary engine preloads the next track; equal-power
fade curves from gm/crossfade.py).

**mpv callbacks** arrive on mpv's thread; the adapter re-emits them through
Qt signals (auto→queued) so all player state is only ever touched on the
GUI thread.

## #d3 — Platform layer; Android documented, not shipped

`gm/platforms.py` centralizes every OS touchpoint (file reveal, URL open,
foreground, DWM glass, dark-mode detection, SMTC/MPRIS availability, tray,
default dirs). Windows keeps pywin32 paths; Linux gets MPRIS via
`gm/mpris.py` (dbus-python optional); the app itself already runs on Linux
(CI now tests it).

**Android:** PyQt6 has no Android wheels; Qt for Python ships Android via
**PySide6**. Porting = swapping the import surface (PyQt6→PySide6 shims)
+ touch-first UI variants + python-for-android/Qt CI, which exceeds this
cycle's budget. Documented in INSTALLATION.md as the recommended route;
the platform layer keeps the code ready for it. Honest status: not shipped
in 2.3.0.

## #d4 — Settings as a flat validated `prefs` dict

All new toggles live under one `config.json["prefs"]` key, overlaid on
`DEFAULT_PREFS` with enum whitelists and range clamps (gm/prefs.py).
Unknown keys are dropped, invalid values fall back — the same hardening
philosophy as the v2 config loader, one level down.

## #d5 — Opt-in smart playlists

The five built-in smart playlists (Most Played / Recently Added / Never
Played / Not Heard Recently / Top Rated) are plain DB-backed playlist rows
with a JSON query spec — but they only exist while their Settings toggle is
on (`_sync_smart_playlists` creates/removes them on apply). This keeps the
Playlists page exactly as clean as the user wants, per the spec.

## #d6 — Lyrics: candidate model with user confirmation

Existing lyrics are never silently replaced. The quality verdict
(gm/lyrics_quality.py) flags short/spam/URL-laden text; online search
results are staged as candidates and saved only after an explicit
"Replace". Manual offsets are per-track and persisted (SQLite + prefs
fallback). The Persian search chain: filename/tag cleanup
(`01 vaveyla` → `vaveyla`, site spam, mojibake repair cp1252→cp1256,
Arabic/Persian normalization) → variant queries → fuzzy scoring with
duration proximity → early-exit above the GOOD threshold.

## #d7 — Ratings: DB first, tags best-effort

Ratings are authoritative in SQLite (instant, works for any format); the
file tag writeback (POPM / Vorbis RATING / MP4 rate) is attempted
best-effort and never fails playback. Read path tolerates foreign POPM
scales by bucketing.

## #d8 — Spectrum visualizer is content-driven, not output-loopback

A literal output-device loopback (WASAPI loopback / PulseAudio monitor)
needs per-OS native code we don't ship. Instead `ContentTap` decodes short
windows just ahead of the playback position and runs a REAL FFT
(gm/spectrum.py) over them: the spectrum is computed from the actual audio
being played. Documented here so nobody mistakes it for a decorative fake;
when no decoder is available the widget hides rather than pretending.

## #d9 — Web remote on stdlib only

`gm/webremote.py` is `http.server.ThreadingHTTPServer` + a token
(constant-time compare) + per-IP rate limiting + strict action whitelist —
no aiohttp/fastapi dependency, LAN-only by choice, off by default. The
single-page UI is served inline (no CDN — works on isolated LANs, respects
RTL). The QR pairing page is the only unauthenticated route (it exists to
bootstrap trust on the local machine) and the token rotates from Settings.

## #d10 — Command palette and shortcuts share one action table

`mw.shortcut_defs()` is the single registry the palette, the shortcut
editor and `QShortcut` rebinding read from; custom bindings persist as
`prefs["custom_shortcuts"]` (only non-default entries). Conflicts are
detected at edit time in the settings dialog.

## #d11 — Queued, marshaled remotes

Web/Telegram threads never touch Qt objects directly: bridges queue every
action onto the GUI thread via `QTimer.singleShot(0)`; status snapshots
are plain dicts built from atomically-readable attributes.

## #d12 — Keep tag-cache shape, add columns

The in-memory `mw._tag_cache` keeps the v2 `path -> (title, artist)`
shape (the entire list/delegate/stats surface depends on it); richer
columns (album/genre/duration/bitrate/rating) live in the DB rows and are
consumed by the new pages/table directly. `persist_tags` is UPDATE-only so
a stale cache entry can never resurrect a removed track.

## #d13 — main.py shim

`main.pyw` stays the double-click entry but is 7 lines; the importable
module is `main.py` (re-export surface for scripts/tests). This fixed a
portability wart: `.pyw` is only importable on Windows CPython, which kept
Linux CI from ever running the GUI suites.

## #d14 — Tray volume/seek: one signal per control, guarded syncs

The tray popup's new volume row and seek bar reuse the player-bar slider
QSS (same objectNames) and route user edits through exactly one signal
each (`volume_changed` / `seek_requested`) into the window's SINGLE
volume/seek code paths. Every programmatic push back into the popup goes
through `_syncing`-guarded `update_*()` methods, so no signal echo loop is
possible (regression-tested with counters in `test_tray_av`). Position
pushes are skipped while the popup is hidden and re-synced wholesale on
open — zero idle cost. Mute is "volume 0" (the main-bar convention), which
keeps audio, both sliders, the icon and the persisted config from ever
disagreeing. Streams (duration 0) disable the seek row instead of showing
a lying slider.

## #d15 — Release renumbered 3.0.0 → 2.3.0

The big rewrite was developed under the working name "3.0.0", but the
maintainer shipped it as **2.3.0**. `version.py` stays the single source
of truth; `test_version_sync.py` (which checks the installer default,
README badge/links, INSTALLATION.md and the CHANGELOG section) guarantees
no stale "3.0.0" survives anywhere user-visible.

## #d16 — New brand icon pipeline

The new black-and-gold "G" mark is generated once from the source art by
`scripts/make_icons.py` (kept outside the repo's script set — it is a
build-time tool): every size 16–256 px PNG, the 160 px settings logo and
a multi-size `.ico` are palette-quantized (256 colors, dithering) so the
whole set stays under `test_stability_v2`'s asset budget (< 300 KB total,
< 80 KB icon.png, < 60 KB logo.png) while looking identical at taskbar
sizes.
