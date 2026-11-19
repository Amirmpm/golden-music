# Golden Music — Architecture (v3.0.0)

## Stack
- **Python 3.11-3.14** (CPython), **PyQt6 6.11** (Widgets; Qt Multimedia playback), **mutagen** (tags/art), optional **pywin32** (single-instance mutex + global media keys) and optional **winrt-\*** wheels (SMTC).
- One playback engine for local files AND online streams (`QMediaPlayer.setSource` handles `https://` natively) — there is deliberately NO second playback system.
- No new language was introduced: the Rust/Kotlin/C++ options were evaluated and rejected — the existing PyQt6 codebase (14k LOC, 21 green test suites) meets every requirement with less risk, and all Windows-native needs (SMTC, DWM, registry, hotkeys) are reachable through supported Python bindings.

## Module map (v3)

```
main.pyw            MainWindow: v3 Nuvx shell (sidebar + top player strip +
                    7-page stack), playback control, IPC server, tray, updater
config.py           config/tag-cache I/O, legacy 12-theme table (rollback),
                    QSS builder, path helpers
liquidglass.py      NEW  unified design system: GlassSettings (validated),
                    theme-dict generation (QColor-safe hex + glass rgba),
                    migration from the 12-theme config, artwork-accent
                    extraction, DWM Mica/Acrylic backdrop
sidebar.py          NEW  Nuvx sidebar (236/76 px, animated collapse, nav rows)
home_page.py        NEW  Home = LOCAL library (shelves: Recently Played,
                    Recently Added, Albums, Artists, Playlists, Favorites,
                    Recently Downloaded; cards + Nuvx interactions; intents)
search_page.py      NEW  Online search UI + QThread search/resolve workers
downloads_page.py   NEW  Download manager UI (queue rows, folder-once banner)
online.py           NEW  OnlineProvider interface + Internet Archive provider
                    (search/resolve/artwork), TTL cache, history validation,
                    https-only URL rules
downloads.py        NEW  DownloadManager/DownloadWorker: queue, progress/speed,
                    pause/resume (Range), cancel, retry, .part+atomic rename,
                    verification, dedup naming, disk floor
smtc.py             NEW  Windows SMTC binding (WinRT, optional) with a
                    thread-safe update queue + graceful degradation
winreg_apps.py      NEW  Windows app registration (RegisteredApplications +
                    Capabilities + FileAssociations + ProgIDs + OpenWithProgids)
                    with policy-compliant uninstall cleanup; iss-fragment parity
formatcaps.py       NEW  runtime codec probing (QMediaFormat) — registration
                    only advertises verified formats
audio.py            AudioBackend (local + play_url streaming) · audio_output.py
scanner.py          FolderScanner (QThread) · duplicates.py · trackinfo.py
playback_fx.py      speed / A-B repeat / fades · playstats.py listening stats
lyrics.py + lrc_cache.py + lyrics_panel.py   lyrics chain
mini_player.py / fullscreen_player.py / tray_menu.py / widgets.py
settings_dialog.py  7 tabs incl. v3 Appearance (Liquid Glass), Downloads,
                    Online, Windows integration
ipc.py              single-instance QLocalSocket protocol (validated, bounded)
updater.py          GitHub-release updater (TLS allow-list, SHA-256 mandatory)
userbackup.py       startup snapshots + auto-restore
applog / icons / coverart / theme_picker (legacy, retained for rollback)
```

## Threading model (no long work on the GUI thread)
- Scanner, tag loader, cover loader, albums worker, lyrics loader: pre-existing QThreads (unchanged).
- New v3: `OnlineSearchWorker`/`StreamResolveWorker` (network), `FnWorker` (artwork), `DownloadWorker` (a dedicated QThread with QMutex/QWaitCondition control: submit/pause/resume/cancel/retry).
- SMTC updates are queued by worker threads and flushed by a GUI-timer (WinRT apartment rule).

## Data
- `%USERPROFILE%\.goldenmusic\` (portable mode: `goldenmusic_config.json` beside the exe wins): `config.json` (+ `liquid` section), `tag_cache.json`, `stats.json`, `lyrics_cache/`, `backups/` (10 snapshots + restore-missing), `updates/`, `downloads.json` records in config, log file.
- Migration: pre-v3 configs migrate on first load (`theme` → `liquid` accent/mode; the legacy `theme` key is preserved for rollback builds). Library/favorites/playlists/stats untouched.

## Windows integration surface
- Installer: Inno per-user (HKCU), AppId `{9F5E7C8D-3B2E-5C4F-AC6D-2B3C4D5E6F7A}` (unchanged → in-place upgrades), registration as in `winreg_apps.py`, `AppMutex=GoldenMusic_SingleInstance_Mutex`, user data never touched by uninstall.
- App: mutex single-instance + IPC forwarding (validated), global media keys (RegisterHotKey), SMTC (winrt), Mica/Acrylic backdrop, one-instance file hand-off, `ms-settings:defaultapps` helper.

## Deliberate non-goals (documented, not silently dropped)
- YouTube/innertube backends, DRMed/subscription services, Discord/Last.fm integrations, LAN sync, EQ/DSP chain, mobile-only patterns — out of scope for this product shape; provider interface exists for future licensed sources.
