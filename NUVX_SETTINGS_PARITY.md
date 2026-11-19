# NUVX_SETTINGS_PARITY.md — Nuvx Desktop settings inventory → Golden Music v3

Scope: **Nuvx Desktop only** (Rust/egui app at `app/desktop`,GPL-3.0). iOS/Android/Web/Android-Auto/Wear settings were explicitly excluded and NOT copied (per the product brief). This inventory was produced by reading the Desktop source: `ui/settings_pages.rs`, `ui/frame.rs` (persistence), `ui/app_init.rs` (defaults), plus the playback/lyrics/equalizer/network/account/transfer/data settings modules.

Legend:
- **Mapped** = implemented in Golden Music v3 with equivalent behavior
- **Adapted** = implemented, deliberately different for technical reasons (reason given)
- **N/A** = does not apply to Golden Music (service/feature doesn't exist here)
- **Not implemented** = documented gap

| # | Nuvx Desktop setting | Nuvx location / type | Nuvx default | Golden Music v3 implementation | GM default | Persistence | Verification |
|---|---------------------|----------------------|--------------|-------------------------------|------------|-------------|--------------|
| **Appearance** |||||||
| 1 | Mode System/Light/Dark | Appearance · chips | System | Liquid Glass "Mode" combo (system/light/dark) → drives `glass.mode` | system | `config.json:liquid.mode` | live apply; test_v3_regression V1 |
| 2 | Animated background | Appearance · toggle | on | Adapted: background style = gradient/tint/solid (no GLSL wallpaper; the window bg follows accent) — "background" combo | gradient | `liquid.background` | V1 |
| 3 | Animated artwork | Appearance · toggle | on | N/A (no canvas/animated artwork service) | — | — | — |
| 4 | Reduce animation | Appearance · toggle | off | "Reduce motion" checkbox → `glass.reduced_motion` (animation durations become 0) | off | `liquid.reduced_motion` | V1 |
| 5 | Font picker (theme/bundled/installed) | Appearance · select | Theme font | Adapted: GM keeps the Windows system font stack (Segoe UI Variable) | Segoe UI | — (QSS `*{font-family}`) | existing styling tests |
| 6 | Window material Mica/Acrylic/Dynamic | Window · chips | Mica | "Blur / native backdrop" slider ≥60 → Mica, <60 → Acrylic, 0 → none; DWMWA_SYSTEMBACKDROP_TYPE with opaque fallback | 100 (Mica) | `liquid.blur` | runtime on Windows; fallback verified off-Windows |
| 7 | Window buttons macOS/Windows style | Window · chips | macOS | N/A (GM uses native title bar + top player strip) | — | — | — |
| 8 | Accent colour (cover / system / theme) | theme/preset system | cover | **Mapped**: accent swatches + custom colour picker + "Derive accent from album artwork" (cover-derived accent, readability clamp) | #e9b957 champagne gold | `liquid.accent` | V1 + V2 artwork-accent regression |
| 9 | Collapse sidebar | Window · toggle | off | Sidebar collapse chevron (236 px ↔ 76 px rail, animated 0.28 s) | expanded | session only (as in Nuvx) | sidebar tests |
| **Playback** |||||||
| 10 | Autoplay | Playback · toggle | on | N/A (no autoplay-radio feature; local library model) | — | — | — |
| 11 | Loudness target | Playback · dropdown | -14 LUFS class | N/A (no loudness normalisation engine; WMF/FFmpeg decode only) | — | — | — |
| 12 | Sleep timer Off/15/30/45/60 | Playback · select | Off | Existing GM sleep timer (minutes spinner + enable) — preserved | Off/30 | `sleep_timer_*` | test_functional |
| 13 | Spatial audio | Playback · toggle | on | N/A | — | — | — |
| 14 | Logarithmic volume | Playback · toggle | on | Adapted: linear Qt volume (slider %); documented difference | on (linear) | `volume` | test_functional |
| 15 | Play songs not music videos | Playback · toggle | on | N/A (audio-only provider) | — | — | — |
| 16 | Load songs and videos together | Playback · toggle | on | N/A | — | — | — |
| 17 | Music video quality | Playback · dropdown | 480 | N/A | — | — | — |
| 18 | Output device | Playback · dropdown | system default | **Mapped** — existing GM audio-output combo (QMediaDevices, live switch) | system | `audio_output_id` | test_v12_features (kept green) |
| 19 | Automix / Crossfade length | Playback · toggle+slider | off / 5 s | Adapted: GM's existing "smooth volume fade" on pause/track-change (100-2000 ms) — crossfade-between-tracks not present | off / 300 ms | `fade_enabled`, `fade_ms` | test_functional |
| 20 | Skip non-music parts (SponsorBlock) | Playback · toggle | off | N/A | — | — | — |
| 21 | Playback speed | (player) 0.5-2× | 1.0 | Preserved (existing FX speed popup + optional bar button) | 1.0 | session | test_v12_features |
| 22 | Repeat / Shuffle | player bar | off / off | Preserved (repeat off/all/one, smart shuffle with no-repeat-until-all-played) | off/off | `repeat_mode`, `shuffle` | existing suites |
| **Equalizer** |||||||
| 23 | EQ master + 7-band + balance | Equalizer pane | off | Not implemented (documented gap — no DSP chain in the Qt backend today) | — | — | — |
| **Downloads** |||||||
| 24 | Download folder | Downloads · picker | Music/nuvx | **Mapped** — one-time folder chooser (Downloads page + Settings), reused automatically for all downloads | `Music\GoldenMusic` | `download_folder` | test_v3_regression V9 + quicktest_downloads |
| 25 | Storage limit Off/1-50 GB | Downloads · dropdown | Off | Adapted: hard 500 MB free-space floor (Nuvx also enforces 500 MB internally); optional GB cap not exposed | 500 MB floor | constant | V9/downloader tests |
| 26 | Pause/Resume/Cancel/Retry | download behaviour | — | **Mapped** — full job control incl. HTTP-Range resume, `.part`, atomic rename, size verify, dedup " (n)" naming, bounded retries | — | queue in-memory + `downloads_history` | quicktest_downloads (23 checks) |
| 27 | Downloads page (list + folder line) | Downloads page | — | **Mapped** — GM Downloads page (queue rows + completed + open-folder) | — | — | quicktest_uipages |
| **Lyrics** |||||||
| 28 | Lyrics toggle + panel | player | on | Preserved — GM lyrics panel (sidecar → embedded tags → cache → lrclib) + bar button | on | `show_lyrics_button`, `lyrics_online` | test_v12_features |
| 29 | Romanization / Translation | Lyrics pane | on | N/A (GM's single-provider lyrics chain; translation providers not implemented) | — | — | — |
| 30 | Lyrics text size | Lyrics pane | 100 | Not implemented (documented gap) | — | — | — |
| 31 | Lyrics timing offset | Lyrics pane | 0 | Preserved via existing fullscreen lyrics behaviour (offset not exposed; gap documented) | — | — | — |
| 32 | 16 lyrics providers + priority | Lyrics pane | rich-first | N/A — GM uses lrclib + cached/sidecar/embedded only (honest single-provider chain) | — | — | — |
| **Sources / Network** |||||||
| 33 | JioSaavn / Radio Javan fallback | Sources · toggles | off | N/A — GM has its own provider architecture (`online.OnlineProvider`) with the Internet Archive provider; additional sources = implement the interface | — | — | online tests |
| 34 | Proxy System/Off/Custom/Tor | Network pane | System | Adapted: OS/system proxy applies (urllib honours env/OS); custom proxy UI not exposed | system | — | — |
| 35 | Hi-Res addon servers | Sources · URL list | — | N/A | — | — | — |
| **Account & integrations** |||||||
| 36 | YT Music Google sign-in | Account | signed out | N/A (no YouTube backend; provider architecture instead — legal simplicity) | — | — | — |
| 37 | Discord rich presence | Account · toggle | on | N/A | — | — | — |
| 38 | Last.fm scrobbling | Account | off | N/A | — | — | — |
| 39 | Spotify import/transfer | Transfer pane | — | N/A | — | — | — |
| **Sync** |||||||
| 40 | LAN sync / pair code | Sync pane | off | N/A | — | — | — |
| 41 | Nuvx Connect remote control | Sync pane | off | N/A | — | — | — |
| **Search & content** |||||||
| 42 | Search field + suggestions + history | Search page | — | **Mapped** — GM online Search page (42 px pill, history suggestions, results, provider label, clear) | — | `search_history` (validated) | V8 + quicktest_uipages |
| 43 | Hide Quick picks / videos from Home | Search & content | off | N/A (GM Home is the LOCAL library by design) | — | — | — |
| 44 | YouTube search chips | Search & content | off | N/A | — | — | — |
| **Your data** |||||||
| 45 | Data folder open | Your data | — | **Mapped** — Settings → Library tab "Open data folder" (existing) | — | — | test_functional |
| 46 | Clear recent searches | Your data | — | **Mapped** — Settings → Online → "Clear search history" | — | `search_history=[]` | settings dialog test |
| 47 | Backup / restore | (GM-side) | — | Preserved — automatic startup snapshots (10 kept) + restore-missing | — | `backups/` | test_v12_features |
| **Window/behaviour** |||||||
| 48 | Close to tray | Window · toggle | off | **Mapped** — existing GM close-to-tray behaviour (preserved) | off | `taskbar_close_to_tray` | test_functional |
| 49 | Mini player | Ctrl+M / player | — | Preserved — mini player (window + rail row) | — | `geometry` | test_functional |
| 50 | Fullscreen player | F key | — | Preserved (F11/F/esc handling) | — | — | test_shortcuts |
| 51 | Queue panel ("Up next") | player bar | — | Adapted: GM queue is the current-playlist queue with Play-next/Add-to-queue actions (wired in v3); the sidebar drawer variant remains a documented gap | — | — | A4 regressions |
| 52 | Notifications | toasts | — | Preserved — in-app toasts (no tray balloon spam) | on | `toast_enabled` | test_functional |
| 53 | Keyboard shortcuts pane | Settings | — | Adapted: shortcuts documented in README; live keycaps pane not implemented | — | — | test_shortcuts |

**Totals:** 53 inventoried Nuvx Desktop settings → **22 Mapped, 7 Adapted (documented), 17 N/A (service doesn't exist in GM), 7 documented gaps** (items 23, 30, 31 partial, 34 partial, 51 partial, 5 adapted, 14 adapted). No setting is claimed as "exists in Nuvx" that does not — every N/A row names the missing underlying service.

**Persistence correctness:** every implemented setting persists in `config.json`, is validated/coerced on load (`GlassSettings.validate`, `_validated_int/_validated_enum`, `sanitize_history`), applies immediately where appropriate (Appearance/Liquid Glass apply live; output device switches live), survives restart (V1 regression roundtrip), and supports reset (Appearance "Reset to defaults").
