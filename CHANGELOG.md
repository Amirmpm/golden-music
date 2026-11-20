# Changelog

## [3.1.0] - 2026-11-20

### Complete engineering remediation

**Download manager — rebuilt**
- **Windows disk-space validation** (`free_bytes` now uses
  `shutil.disk_usage`; the previous `os.statvfs` call is POSIX-only and
  raised an uncaught `AttributeError` on Windows, breaking downloads on
  the primary platform)
- **Active-job cancellation redesigned**: cancelling a running download
  signals the worker, which closes its handle and removes the `.part`
  itself — the old code unlinked the file while the worker held it open
  (PermissionError on Windows, silent corruption elsewhere) and never set
  the cancel flag
- **Persistent job registry**: every job stays addressable by id until
  removed/cleared, so `retry()` can find FAILED jobs (v3.0 dropped them
  from the queue — retry was a silent no-op)
- **History + queue persistence**: atomic `downloads_state.json`
  (schema-validated, corrupt entries quarantined) — the v3.0 bug where
  the first completion after restart wiped the persisted history is gone
  (the manager owns the history; config-key migration on first boot)
- **Restart recovery**: unfinished jobs are restored QUEUED and resume
  from their `.part` via HTTP Range; servers that ignore Range restart
  cleanly; `.part` files larger than a known total are dropped as corrupt
- **In-flight duplicate-URL prevention**, `remove` / `clear finished`
  actions, in-place Downloads-page row updates (no more full rebuild
  twice a second per running job)

**Unified playback queue**
- ONE queue: `current_playlist` entries are local paths OR online
  tracks — Next / Previous / Play next / repeat-one / shuffle behave
  identically over both (v3.0 kept a separate `online_queue` that only
  drained after an online track finished, so an online "Play next" could
  never actually play next during local playback)
- Online queue entries persist across restarts; history/redo and
  smart-shuffle now bookkeep online tracks too
- **"Up Next" queue drawer** wired into the player bar (the previously
  dead `queue_panel.py` module) — one shared queue on every surface

**Online correctness**
- **Item-specific download eligibility** read from the Internet
  Archive's own access metadata at resolve time (`access-restricted-item`,
  dark items, private files) — search results start UNKNOWN instead of the
  v3.0 blanket `downloads_allowed=True`; restricted items refuse with a
  clear message
- **Stream-resolution races fixed** with worker generations: a stale
  resolve can no longer hijack a newer selection (last-arrival-wins)
- **Search races fixed**: request generations at delivery, stale results
  dropped, results cached under the WORKER's query (v3.0 cached under the
  live field text — wrong-key cache pollution), and the 1.5 s GUI-thread
  block while superseding a search is gone
- **Online artwork token guard**: a late artwork response for an old
  track can no longer overwrite the newer track's art (or the theme
  accent)
- **Custom HTTP(S) proxy** setting (Settings → Online), applied to
  search/stream/artwork/downloads; validated, refused loudly when invalid

**Home page**
- Album-aware cards: real album tags (background tag loader now extracts
  them), distinct album groups (was a single "Unknown album" blob)
- "Add to Playlist" acts on the selected track (opened a generic dialog
  and added nothing before); "Open Album" opens THAT album
- "See all artists" navigates (was a silent no-op)
- **Async cover loading**: artwork reads + decodes off the GUI thread
  (bounded, deduplicated, cancellable QImage pipeline; v3.0 froze the UI
  with ~70 synchronous mutagen reads per rebuild) + single-scale instead
  of double-scaling

**Settings completion (Nuvx parity)**
- Lyrics text size (85–130%) and timing offset (±5 s) — persisted,
  validated, applied live to both lyrics surfaces
- Dedicated keyboard-shortcuts page (live keymap documentation)
- Seven-band equalizer remains a documented technical blocker (Qt
  Multimedia exposes no per-band DSP; see NUVX_SETTINGS_PARITY item 23)

**Shutdown / thread safety**
- Coordinated, bounded shutdown of EVERY background worker: download
  worker (persist → cancel active transfer → join), stream resolvers,
  artwork fetcher, update probe, home cover loader, search workers —
  registered via closeEvent AND atexit (tests/diagnostic paths), fixing
  interpreter aborts at teardown
- `UpdateCheckWorker` gained a real cancel; `SearchPage` a shutdown hook

**Verification** — new `scripts/test_v31_regression.py` (62 checks: real
HTTP pause/cancel/recovery with Range semantics, registry retry,
eligibility, unified-queue state machine, race guards, async covers,
shutdown) riding `test_features` in CI (workflow list unchanged — token
scope); all 13 existing suites green.

## [3.0.0] - 2026-11-19

## [3.0.0] - 2026-11-19

### The Nuvx Desktop transformation

**UI/UX (Nuvx Desktop recreation)**
- Nuvx-style navigation sidebar (236 px, collapsible to a 76 px icon rail,
  animated); navigation is Home / Search / Library / Favorites / Albums /
  Stats / Downloads, Settings pinned at the bottom.
- The player strip moved to the TOP of the window (Nuvx layout).
- Home = your LOCAL music library in the Nuvx home layout: strapline
  shelves + bold headers + horizontal artwork cards (Recently Played,
  Recently Added, Albums, Artists, Playlists, Favorites, Recently
  Downloaded) — never online recommendations, never placeholder data.
- Search = ONLINE music search page (42 px pill field, history
  suggestions, result rows with source info, context actions).
- Golden Music branding kept throughout; the supplied logo.png drives
  the window/app icon identity.

**Liquid Glass design system (replaces the 12-theme picker)**
- One unified translucent material: accent colour (incl.
  artwork-derived accent), transparency, blur (native Mica/Acrylic on
  Windows 11 22H2+ with opaque fallback), corner radius, density,
  animation intensity, reduced-motion — all persisted, validated and
  live-applied.
- The old 12-theme picker is retired as the appearance system; existing
  configs migrate automatically (accent = your old theme's accent,
  dark/light preserved) and a rollback build still finds the legacy key.

**Online music + downloads**
- Provider-based online architecture (OnlineProvider interface);
  Internet Archive provider ships (public-domain audio, no auth,
  downloads permitted).
- Streaming through the SAME audio engine (no second playback system);
  mixed local/online queues ("Play next" / "Add to queue").
- Download manager: one-time folder configuration, queue, progress %,
  speed, sizes, pause/resume (HTTP Range), cancel, retry, duplicate
  detection (" (n)" suffixing), safe filenames, .part + atomic rename,
  size verification, 500 MB disk floor. Completed files join the existing
  local library exactly once.

**Windows integration**
- App registration fixed: RegisteredApplications + Capabilities +
  FileAssociations + ProgID + OpenWithProgids — Golden Music now APPEARS
  in Windows Default apps (never writes protected UserChoice; a Settings
  action opens the right page). Installer and app register the same
  surface; uninstall removes only Golden Music's own entries.
- Windows media controls: System Media Transport Controls (SMTC) —
  overlay/lock-screen/taskbar media session with Play/Pause/Next/Prev
  + metadata + artwork (optional winrt wheels; graceful degradation).
  Global keyboard media keys remain, tested independently.
- Runtime codec probing (QMediaFormat): only formats the ACTIVE backend
  verifiably decodes are advertised/registered. FFmpeg media plugin
  restored + WMF forcing removed (AUDIT #3: OGG/Opus were advertised but
  unplayable).

**Fixed (from the v3 technical audit)**
- HIGH: library/favorites no longer pruned when a drive is offline —
  entries survive unmounted/network drives instead of being silently
  wiped on the next save.
- MEDIUM: second-instance stale-lock race — a running-but-unreachable
  instance now gets bounded retry then a clear refusal instead of
  launching a config-corrupting second GUI.
- MEDIUM: the orphaned queue feature is wired — "Play next" and
  "Add to queue" context-menu actions.
- MEDIUM: PyInstaller hiddenimports completed (5 missing late imports +
  all v3 modules).
- MEDIUM: stale SECURITY_AUDIT claims re-verified against v3 network
  surface (GitHub updater + lrclib lyrics + archive.org provider).
- LOW: View model completed (7 pages), docs drift (tray docstring,
  build script version echoes), gm_stress dev-path hardcoded.
- CI: all 12 test suites repaired (broke at the main.py -> main.pyw
  rename) via a testenv import shim; v3 regression suite added;
  pip-audit + SBOM steps added.

**Fixed (found while producing the v3 screen recording; all suites re-run green)**
- Home showed the empty "Add music folder" state on every real boot with a
  populated library: `_init_v3_systems()` refreshed Home during `__init__`
  while the library was still empty, and the async config load never
  re-refreshed it. Home now rebuilds as soon as the library + tag cache
  are loaded.
- Downloads: the worker never tracked its in-flight job (`_current` was
  read by pause/cancel/jobs but never assigned), so pausing or cancelling
  a RUNNING download was a silent no-op; jobs() now also de-duplicates
  the running job against the queue (the Downloads page used to render
  one job as two rows).
- Downloads page: a lone active download stretched to fill the whole
  queue viewport (missing trailing stretch in the resizable scroll host).
- Settings: the Liquid Glass mode combo, sliders, density/background combos
  and checkboxes were not wired to live apply (only the accent swatches
  and reset were) — contradicting the documented behaviour and the _on_ok
  comment. All controls now apply live, and the dialog re-renders its own
  chrome when a live Light/Dark switch happens while it is open.
- Live Light/Dark switching now re-themes the v3 pages (Home cards, Search
  pill, Downloads page) instead of leaving dark-baked headings washed out
  on the light background.

### Compatibility
- Config/library/playlists/favorites/history/stats preserved; the
  theme key migrates automatically (and stays for rollback).
- Same installer/upgrade path (Inno AppId unchanged); per-user install.


All notable changes to Golden Music will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] - 2026-10-16

### Added

- **"Open with" / file-argument handling** — files passed by the OS (Explorer
  "Open with", file associations, `GoldenMusic.exe song.mp3`) queue as an
  ad-hoc playlist and start playing; works on cold start AND when the app is
  already running (second instance forwards via IPC and exits). The ad-hoc
  queue never mutates the library or the saved config.
- **Single-instance IPC** (`ipc.py`) — per-user QLocalServer/QLocalSocket
  channel with strict validation (size cap, extension allow-list, existence
  check, ack protocol). Relaunching now reveals the running instance even
  when it is hidden in the tray — the old visible-window EnumWindows
  approach could never do that. Stale locks fall back to a normal start.
- **In-app auto-update** (`updater.py`, `update_dialog.py`) — background
  check of GitHub Releases at most once per 24 h, non-modal in-app banner,
  update dialog with Update now / Install when I quit / Remind me later /
  Skip this version, SHA-256-verified downloads (sidecar `.sha256` or API
  digest) into `~/.goldenmusic/updates/`, silent self-update for installed
  editions with an `--updated` relaunch toast, pending-marker failure
  recovery, portable/source editions get a release-page link only.
  Manual "Check for updates" button in Settings → About with explicit
  feedback; auto-checks can be disabled.
- **In-app toast notifications** — every operational notification (scan
  finished, queued, removed, deleted, audio output, audio errors, sleep
  timer, track change) is now a theme-aware toast inside the window
  instead of a tray balloon.
- **Installer "Open with" integration** — optional checked task registers a
  ProgID, `Applications\GoldenMusic.exe` SupportedTypes and
  `OpenWithProgids` for all supported audio extensions under HKA
  (unins flags included). Default apps are never hijacked.
- **Version single source of truth** (`version.py`) — the installer default,
  README badge/links, About dialog and CHANGELOG must all agree
  (`scripts/test_version_sync.py` enforces it).

### Changed

- **Tray Show/Hide** — a minimized or hidden window now always comes back
  restored, raised and focused (`showNormal` + Win32 foreground nudge);
  a tray click on a minimized window no longer hides it. The tray popup
  button is relabeled "Show/Hide" to match the behavior.
- **Keyboard shortcuts** — README bindings (Ctrl+arrows, Ctrl+F/M/J, F11)
  are now the actual QShortcuts; bare keys (Space/Enter/arrows/M/F) act
  only when no list, slider or text input owns the key — pressing F with a
  list focused no longer launches the fullscreen player, and type-ahead /
  arrow navigation work in every list again.
- **Close-to-tray notice** — shown once ever (persisted in config); the
  minimize balloon and the repeated close balloons are gone.
- **Installer** — `AppMutex` + `CloseApplications=yes` so upgrades detect a
  running player; `AppVersion`/`OutputBaseFilename` derive from
  `/DAppVersion=` (CI passes version.py's value); the legacy-uninstaller
  `DelTree` cleanup now runs only for real v1.0.0 migrations in a
  different folder — it used to run on every upgrade-in-place and could
  delete the current uninstaller; silent `--updated` relaunch entry.
- **README** — live Actions CI badge replaces the static "passing" image;
  shortcut table now matches the code.

### Fixed

- Restore-from-tray leaving the window minimized behind other windows or
  hiding it instead of restoring (regression suite: `test_tray_toggle.py`).
- "Open with" doing nothing at all (regression suite: `test_open_with.py`).
- Bare-key shortcuts hijacking list type-ahead/navigation (regression
  suite: `test_shortcuts.py`).
- Second launch silently doing nothing while the app sat in the tray.

### Security

- `stats.json` and `lyrics_cache/*.json` are written with owner-only
  permissions (chmod 0600 on POSIX), matching the config.json hardening.
- Updater hardening: HTTPS-only fetcher with a redirect-host allow-list,
  400 MB size cap, validated filenames, atomic `.part` rename, mandatory
  SHA-256 verification, no shell, no code execution from release notes.
- Bandit gate (`--severity-level medium`) is clean: SHA1 in the lyrics
  cache key marked non-security, lyrics fetch validates the https scheme.

## [2.1.1] - 2026-09-12

### 🛡️ Fixed — Data-loss bug (library wiped, test tracks appeared)

- **Root cause** — the dev test suites booted the real `MainWindow`
  with fake Temp libraries and called `_save_config()`, overwriting the
  live `~/.goldenmusic/config.json` with test junk; the next boot's
  exists-prune then wiped the real library. Offscreen scripts and theme
  screenshot runs could do the same.
- **Never-wipe guards in `_save_config`** — Temp/scratch paths are never
  persisted; an empty in-memory library can no longer replace a
  non-empty saved library (disk data is kept, settings saved around it).
- **Scoped pruning** — `_on_scan_finished` only prunes entries under the
  folders that scan actually covered (+ always drops Temp paths); tracks
  on temporarily-offline drives survive.
- **Offline folders remembered** — `_startup_rescan` no longer forgets
  registered folders when a drive is briefly unavailable.
- **Test isolation** — new `scripts/testenv.py` sandboxes HOME + the
  portable dev config for every script that boots `MainWindow`; all
  suites updated. Escape hatch: `GOLDENMUSIC_ALLOW_REAL_CONFIG=1`.
- **Poisoned backups cleaned** — all test-junk snapshots removed; a good
  backup of the restored library was taken.
- **Recovery** — library rebuilt from `stats.json` live paths + a full
  scan of the music folder (3513 tracks, all verified on disk);
  `stats.json` play history untouched.

## [2.1.0] - 2026-09-12

### ✨ Added
- **Fullscreen player** — split view (cover + transport / synced lyrics),
  visualizer with theme cycling, keyboard control (F11/F/Esc/Space/arrows).
- **Synced lyrics engine** — sidecar → embedded tags → disk cache →
  lrclib.net lookup, auto-scroll highlighting in panel + fullscreen.
- **Dynamic visualizer** (`Settings → Interface`) with WMP-style themes.

### 🐞 Fixed
- **Playback-speed popup presets** — icon-less text buttons squeezed into
  ~50px so Qt elided labels ("0."). Rebuilt as a 2×3 grid (min 112×52)
  with Lucide icons + full text; active rate highlighted gold.
- **Stats ghost rows** — moved/renamed files lost their history; engine now
  resolves living twins (basename/stem search) and prunes truly-deleted
  entries instead of showing dead rows.

## [2.0.1] - 2026-08-27

### 🐞 Fixed — Light-theme readability bugs

Batch of visual bugs that made parts of the UI unreadable after selecting a
light theme (Porcelain / Ivory / Azure / Lilac / Blush / Sage), especially on
machines where Windows itself runs in dark mode.

### Fixed
- **Tray popup kept boot colors** — the tray icon popup (`TrayMenuWidget`)
  was built *before* the saved theme was read, so on any fresh boot into a
  non-default theme (e.g. Porcelain) the popup kept Royal Gold colors: brown
  dividers, gold accents and a dark glass background. The saved theme is now
  read before the tray is built, `TrayMenuWidget` gained a public
  `set_theme()` (same API as the rail / player bar) that also re-styles its
  dividers, and `MainWindow._apply_theme` calls it on every theme switch.
- **Black-on-black tooltips** — every tooltip anchored to an app icon button
  (rail, player bar) rendered with a near-black background and dark text in
  light themes. Root cause: the button's own widget-level stylesheet broke
  `QTipLabel` style resolution. `IconButton` now paints its hover/checked
  glass circle in `paintEvent` and carries no stylesheet, so the themed
  `QToolTip` rule applies again.
- **Stale colors on the Stats page** — "total listening time" and
  "Sessions opened" kept the *previous* theme's muted color (beige `#ab9776`
  from Royal Gold) on a white background after switching themes. Two fixes:
  the saved theme is now loaded *before* the UI is built (no more baking
  default-theme colors into inline styles), and `_apply_theme` runs a sweep
  that rewrites any remaining previous-theme color tokens in descendant
  widgets' stylesheets — so *every* inline-styled widget now follows theme
  switches, in both directions.
- **Unreadable Settings dialog in OS dark mode** — with Windows in dark mode
  the dialog background, tab-bar strip, and scroll-area viewport fell back to
  the system dark palette while the app theme was light, leaving dark group
  titles on dark strips. The application palette now mirrors the active
  theme (Window/Base/Text/ToolTip/Highlight roles) and Fusion is pinned to
  the matching Qt color scheme, so unstyled regions always match the theme.
- **Group-box titles** — titles used `gold_light` (tuned for dark surfaces)
  and were crossed by the frame border. They now use the theme accent with an
  opaque background patch behind the text; readable on both color families.
- Themed QSS is additionally applied at application level so tooltips, menus
  and message boxes outside the main window hierarchy pick up the theme.
- **Font warning spam eliminated** — the track-row delegate computed derived
  font sizes from an *unresolved* font (`pointSizeF() == -1`), producing
  `setPointSizeF: Point size <= 0` warnings on every repaint. The base font
  is now resolved first and every derived size is clamped to a sane minimum.

### ✅ Verification (v2.0.1)
Automated offscreen harness over **all 12 themes** (fresh boot with a saved
config, one process per theme): effective theme always equals the saved one;
Stats-page worst-label contrast 5.1–6.9:1 (WCAG AA); tooltips themed to the
correct family (light bg on light themes, dark bg on dark themes) with
13.3–15.7:1 text contrast; Settings dialog, tray popup and every descendant
stylesheet free of foreign-theme color tokens after boot and after
bidirectional theme-switch cycles.

### 📦 Installer (v2.0.1)
- `goldenmusic.iss` now sets `SetupArchitecture=x64` (Inno Setup 7+): the
  Setup program itself ships as a 64-bit binary, matching the x64-only
  payload. Compiling with Inno Setup 6 still works — just delete that line.
- Bundle slimmed 115 → 84 MB by removing files the app provably never loads:
  software-OpenGL rasterizer (`opengl32sw.dll`, 20 MB), Qt PDF engine
  (`Qt6Pdf.dll`), Qt builtin translations (7 MB), touch-gesture and
  network-information plugins. Kept: the full FFmpeg audio stack, QtNetwork
  (imported by Qt6Multimedia) and the TLS/ssl stack (the lyrics downloader
  uses urllib).
- End-to-end pipeline verified: PyInstaller bundle built and smoke-tested on
  Windows binaries, installer produced with the Inno Setup 7.1.0 x64
  command-line compiler, silent install / 12-theme boot matrix / silent
  uninstall all verified clean (repeated twice — zero issues remaining).

---

## [2.0.0] - 2026-08-27

### 🎨 Design Overhaul — Psychology Theme Suite

A landmark release rebuilding the app's entire visual identity around
color-psychology research and measurable accessibility targets.

### ✨ Added
- **12 hand-tuned themes** replacing the previous ad-hoc set: six
  black-dominant dark themes and six white-dominant light themes covering
  the full accent spectrum — black/white monochrome, gold, blue, purple,
  pink, green
- Every palette numerically verified against WCAG 2.x contrast gates before
  shipping (body text >= 4.5:1 everywhere; headings/accents >= 5:1;
  monotonic button gradients); selected-row / hover states included in the audit
- **Legacy theme migration** — configs saved under pre-2.0 theme ids map
  transparently to the closest new successor on first launch
- Regenerated README screenshots straight from the live offscreen build

### 🔧 Changed
- Fresh-install default theme is now **Royal Gold**
- *Auto theme* (follow Windows dark/light mode) now switches between
  Royal Gold and Ivory Gold
- Theme picker groups swatches into DARK / LIGHT sections (6 + 6)
- **Theme picker rebuilt** for auditioning workflows: swatches now sit
  3 per row (3+3 / 3+3) in a compact 348 px panel, selecting a theme
  no longer closes the picker — it stays open and even re-skins itself
  live so several themes can be compared back-to-back; the panel is
  dismissed only by clicking outside it or via the new round ✕ button
  (same Lucide icon set as the rest of the app). The active theme is
  marked with a persistent accent ring

### 🐞 Fixed
- Carried-in fixes from the 1.2.0-fixed engineering pack: Up Next panel
  refresh crash (NameError), global media keys never firing on PyQt6,
  WMA cover-art tagging TypeError, lyrics-loader thread destroyed mid-run


## [1.1.0] - 2026-08-24

### 🎉 Feature & Quality Release

A major upgrade focused on completing the player, hardening it, and shipping a compact installer.

### ✨ Added

#### New Features
- **Personal playlists** — create/rename/delete from the sidebar; add tracks via right-click; stored in config
- **Duplicate finder** (Settings → Library Tools) — groups same-song copies by normalized tags or filename + duration; keep one copy per group, remove the rest from the library (disk files untouched)
- **Track properties** dialog — format, duration, bitrate, sample rate, size, album, year
- **Open File Location** — reveal a track's file in Explorer from its context menu
- **Drag & drop** — drop folders or audio files from Explorer to add them
- **Global media keys** — Play/Pause/Stop/Next/Prev from keyboard/headset
- **Jump to Playing** (`Ctrl+J`) — scroll lists to the current track
- **New formats** — `.opus` and `.oga` supported end-to-end
- **Full folder tree** — all nesting depths shown (was: one level, max 20)

#### Playback Correctness
- **History-aware shuffle** — Previous retraces what actually played; Next replays forward after going back
- Playlist index integrity when removing tracks during playback

#### Reliability
- **Single-instance** — second launch focuses the running window (no error)
- **Crash guard** — unhandled exceptions are logged; the app survives
- **Rotating file log** (`~/.goldenmusic/goldenmusic.log`) incl. Qt warnings

### 🔧 Changed
- Optimized release build (~80 MB): unused Qt modules/DLLs excluded, UPX-packed with safe exclusions
- Assets slimmed 1.1 MB → 158 KB; logo/icon recompressed losslessly-in-practice
- Installer now detects previous installations and updates in place, preserving user data

### 🗑️ Removed
- Fake "Crossfade" option (no implementation existed)
- Dead "Hover to expand sidebar" setting (sidebar is collapsed by design)

### 🔒 Security
- Validated JSON config/tag-cache loading (size caps, shape checks)
- Cover-art memory guards against decompression bombs
- Path-boundary-safe folder filtering; owner-only perms on data files (POSIX)
- Bandit: 0 High / 0 Medium findings

### 🧪 Testing
- 7 offline test suites, 106 tests total (functional, deep, stability ×2,
  history-nav, features, crash)

---

## [1.0.0] - 2026-07-14

### 🎉 First Public Release

The first stable, production-ready version of Golden Music!

### ✨ Added

#### Core Features
- **14 beautiful themes**: Aurora (default), Dark Gold, Light Gold, Midnight Blue, Forest Green, Rose Gold, Carbon Black, Ocean Cyan, Sunset Pink, Emerald, Lavender, Sand, Crimson, Teal
- **Theme picker popup** — Visual swatch grid for instant theme switching
- **Two-column layout** — Track list on the left, album art on the right
- **Folder tree** — Sidebar navigation with subfolders
- **Search bar** — Instant filtering by title or artist
- **Sort options** — Title, Artist, Date Added, Filename
- **Right-click context menu** — Play, Add/Remove Favorites, Remove from Library
- **Refresh button** — Rescan folders for new or removed tracks
- **Auto-rescan** on startup (configurable)
- **Tag caching** — Instant loading on subsequent launches

#### Playback
- **All popular formats**: MP3, WAV, FLAC, OGG, M4A, AAC, WMA
- **Windows Media Foundation** backend for superior audio quality
- **Click-to-seek** progress bar — Jump anywhere instantly
- **Smooth volume control** — Click or drag
- **Shuffle & Repeat** — Off / All / One modes
- **Album cover art** — Extracted automatically from file metadata

#### User Experience
- **Mini Player** — Compact floating window (`Ctrl+M`)
- **Sleep Timer** — Auto-stop after N minutes
- **Keyboard shortcuts** — Full control without the mouse
- **Settings dialog** — 4 tabs (Appearance, Playback, Library, About)
- **Remember last track** — Resume where you left off
- **System tray integration** — Left-click to toggle, right-click for quick actions

#### Interface
- **Custom logo** — Golden play button branding
- **Modern SVG icons** — Lucide/Feather-style line-art (no emoji)
- **Always-collapsed sidebar** with tooltips
- **Custom tray menu** with icon buttons
- **Progress bars** for all heavy operations

### 🔧 Technical

#### Architecture
- **PyQt6** (Python 3.14) for modern GUI
- **Async everything** — No UI freeze, even with 2000+ songs
- **Background tag loading** — Library appears instantly
- **Crash-proof** — Every operation wrapped in error handling
- **Memory efficient** — Cached data with automatic cleanup

#### Files
- `main.py` — Main application window
- `config.py` — Themes, QSS stylesheets, helpers
- `icons.py` — SVG icon library (30+ icons)
- `audio.py` — PyQt6 QMediaPlayer audio backend
- `scanner.py` — Async folder scanner (QThread)
- `coverart.py` — Album art extraction (thread-safe)
- `widgets.py` — ClickableSlider widget
- `tray_menu.py` — Custom tray popup menu
- `theme_picker.py` — Theme swatch picker popup
- `settings_dialog.py` — Settings dialog (4 tabs)
- `mini_player.py` — Compact floating player

### 🛠️ Fixed
- UI freeze when scanning large libraries
- Cover art not displaying (thread-safe QPixmap transfer)
- Play button not exactly centered
- Inno Setup `checked` flag error
- PyInstaller `QtSvgWidgets` hidden import warning
- Crash on slider click (removed custom QStyle internals)
- Audio playback feedback loop causing 0.5s repeat
- Music playback not working (simplified audio backend)
- Metadata not reading for some files (improved fallback)

### 📦 Build System
- **PyInstaller** spec for EXE packaging
- **Inno Setup** for Windows installer
- **One-click build** script (`build_windows.bat`)
- Bundles all Qt6 plugins (mediaservice, iconengines, imageformats)

---

## Version History

### Development Versions (pre-release)

<details>
<summary>Click to expand development history</summary>

### [0.5.0] - Internal testing
- Initial PyQt5 prototype
- Basic playback controls
- 2 themes (Dark Gold, Light Gold)

### [0.7.0] - Internal testing
- Migration to PyQt6
- 6 themes
- Modern SVG icons
- Seek bar at top of player bar

### [0.8.0] - Internal testing
- 13 themes
- Theme picker popup
- Folder tree
- Search and sort
- Right-click context menu
- Keyboard shortcuts
- Mini player
- Settings dialog

### [0.9.0] - Internal testing
- Aurora theme added (14 total)
- Async config loading
- Tag caching
- Smooth sidebar animation
- Crash fixes (removed QStyle internals)

### [0.9.5] - Internal testing
- Audio backend simplification
- Feedback loop fix
- Custom logo integration
- Tray menu improvements
- Refresh button
- Metadata cleanup

</details>

---

## 🔗 Links

- [Releases](../../releases)
- [Issues](../../issues)
- [Pull Requests](../../pulls)
- [Contributing Guide](CONTRIBUTING.md)

---

<div align="center">

**Golden Music v3.1.0** — Remediation Release · Unified Queue · Download Manager Rebuild · Windows-Safe · Verified 🪟

</div>
