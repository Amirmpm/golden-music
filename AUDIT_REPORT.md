# Golden Music — Technical Audit Report (v2.2.0 → v3.0.0 remediation)

**Audited baseline:** `a47bad9` (v2.2.0, main) — audited by source inspection + execution in an offscreen Qt environment; every finding below was reproduced from source or by test before being accepted. No finding was invented to fill a category.
**Remediation release:** v3.0.0 (this branch).
**Verification environment:** Linux sandbox + PyQt6 6.11 offscreen, mutagen 1.47, Python 3.12 — the same headless technique the project's own CI uses (`QT_QPA_PLATFORM=offscreen`). Windows-only surfaces (registry, SMTC, DWM, installer) were re-verified by code review and are labelled accordingly.

---

## Method

1. Full read of all 30 Python modules (~14,000 LOC), installer, PyInstaller spec, both GitHub workflows, the 22 test suites and `SECURITY_AUDIT_REPORT.md`.
2. Execution of the complete existing test battery against the audited tree.
3. Cross-verification of every "advertised" capability against actual code paths (README/INSTALLATION claims vs. implementation).
4. Every accepted finding has a reproduction or a file:line citation; fixes carry regression tests (see the matrix).

---

## Findings

### GM-01 [High] — Library/favorites silently wiped when a drive is offline
- **Category:** Music library / data integrity
- **Where:** `main.pyw:5459-5460` (sync loader) and `main.pyw:6156-6158` (async loader `_finish_config_load`) — `self.library = [p for p in self.library if os.path.exists(p)]` and the same for favorites, run on EVERY load.
- **Repro:** add tracks from a USB/network drive; unplug it; launch the app; plug the drive back. The tracks are gone from the persisted config (they were pruned at load and written back at save).
- **Expected:** library entries survive offline media; the app should show the collection and play files when the drive returns.
- **Actual:** offline drives erase the persisted library/favorites on the next save.
- **Root cause:** pruning persisted state with a filesystem predicate at load time.
- **Impact:** permanent user data loss (favorites, playlists referencing those paths remain but their content vanishes).
- **Fix:** both loaders keep entries; no `exists()` prune anywhere on persisted state. Missing files remain visible in lists (graceful audio-error skip already existed). Implemented in v3.
- **Status:** **FIXED.**
- **Regression:** `scripts/test_v3_regression.py` → "A1 async loader keeps offline entries (no exists-prune)" + "A1 favorites kept without exists-prune".

### GM-02 [High] — No Windows media-session integration (SMTC)
- **Category:** Windows media integration
- **Where:** repo-wide grep for `SystemMediaTransport|SMTC|media session` → zero hits; `mediakeys.py:103-127` implements `RegisterHotKey` global media KEYS only.
- **Repro:** play a track; press volume/media overlay on Windows — Golden Music is absent from the Windows media overlay, lock screen and taskbar flyout.
- **Expected:** a media session with Play/Pause/Next/Prev, title/artist/album, artwork, position/duration.
- **Actual:** only global hotkeys (which do not create a media session).
- **Root cause:** feature never implemented.
- **Impact:** the single most visible "not a real Windows player" symptom on modern Windows.
- **Fix:** `smtc.py` — `Windows.Media.SystemMediaTransportControls` bound through `ISystemMediaTransportControlsInterop.GetForWindow` (WinRT via optional `winrt-*` wheels), with metadata/timeline/thumbnail, button events, thread-safe update queue, and full graceful degradation. Buttons tested independently from global keys per spec. Implemented in v3.
- **Status:** **IMPLEMENTED (runtime verification pending on Windows)** — the state machine/metadata path is unit-tested cross-platform (`V3 SMTC…` checks); the WinRT binding itself requires a Windows host (documented limitation).
- **Regression:** `scripts/test_v3_regression.py` → "V3 SMTC safe on this platform" / "V3 SMTC no-crash through the update path".

### GM-03 [High] — Codec/backend mismatch: advertised formats unplayable
- **Category:** Audio & playback / build
- **Where:** `main.pyw:15-22` forces `QT_MEDIA_BACKEND=windowsmediafoundation`; `build_release.py:50` deletes `ffmpegmediaplugin.dll`; `config.py:41` + `README.md:107` advertise 9 formats incl. OGG/OGA/Opus/Vorbis ("FFmpeg-backed").
- **Repro:** play an `.ogg`/`.opus` file in a shipped build — WMF cannot decode Vorbis/Opus.
- **Expected:** every registered/advertised format plays.
- **Actual:** formats fail with InvalidMedia while still being associated with the app.
- **Root cause:** forcing the WMF backend while deleting the FFmpeg plugin that covered the remaining formats.
- **Fix (v3):** (a) WMF forcing removed (Qt default backend wins); (b) `ffmpegmediaplugin.dll` no longer trimmed (build budget 110→115 MB); (c) `formatcaps.py` probes the ACTIVE backend at runtime (`QMediaFormat.isSupported(Decode)` + codec enumeration) and registration advertises only verified formats. Implemented.
- **Status:** **FIXED (code-level); runtime format matrix re-verified on a real Windows build pending** (the probe itself is verified: it returns the honest per-machine subset on this Linux/FFmpeg host).
- **Regression:** `scripts/test_v3_regression.py` → "V5 format probe returns only known candidates"; smoke mode prints the verified list.

### GM-04 [High → fixed at v3 root] — Windows does not recognize Golden Music as a music player
- **Category:** Windows integration (special investigation, PART 2.2)
- **Where:** `goldenmusic.iss:74-130` — ProgID + `Applications\GoldenMusic.exe` + per-extension `OpenWithProgids` only; **no `RegisteredApplications`, no `Capabilities`, no `FileAssociations`.**
- **Repro (observed by user; confirmed in source):** Windows Settings → Default apps → audio — Golden Music never appears; it only shows in per-file "Open with".
- **Expected:** the app is listed in Default apps (so the user can consciously choose it), and in Open with for audio types.
- **Actual:** only the Open-with menu ever shows it.
- **Root cause:** installer wrote the Open-with surface only; the Default-apps discovery surface was never written.
- **Fix (v3):** full, policy-compliant surface —
  - Installer (`goldenmusic.iss`): `HKCU\Software\RegisteredApplications\Golden Music` → `Software\GoldenMusic\Capabilities`; `ApplicationName`/`ApplicationDescription`; `Capabilities\FileAssociations` for the 9 formats; ProgID + `Applications` + `OpenWithProgids` kept; every entry `uninsdeletevalue`/`uninsdeletekey` (uninstall removes only Golden Music's own values).
  - Runtime (`winreg_apps.py`): same surface from the app for portable/source builds + `registration_status()` + in-app "Open Windows Default apps" (`ms-settings:defaultapps`) with an explicit user-choice explanation.
  - **Policy compliance (hard rules):** per-user HKCU only; never writes `UserChoice` (protected by Windows anyway); never silently takes over existing associations; only verified formats are registered.
- **Status:** **IMPLEMENTED (installer + runtime); end-to-end Windows visual verification of the Default-apps listing still needs a clean-Windows run** — the registry surface is the documented, correct mechanism (`RegisteredApplications` is exactly what Windows uses to list apps there).
- **Regression:** `scripts/test_v3_regression.py` → "V4 register refuses non-Windows" / "V4 status degrades off-Windows" / "V4 iss fragment mirrors the app registration" (asserts the two surfaces stay in sync); `test_v3_regression` V5 keeps the registered-format set honest.

### GM-05 [Medium] — Stale-lock double-instance race
- **Category:** Windows integration / data integrity
- **Where:** `main.pyw:5874-5878` (v2.2 numbering): mutex held but IPC unreachable → "starting anyway".
- **Repro:** hold the mutex with a live process whose QLocalSocket server is down; launch again — two GUIs run, both write `config.json` (last-writer-wins corruption).
- **Root cause:** treating an unreachable IPC peer as proof of a dead owner (mutexes are released by the OS when an owner dies, so a held mutex means a LIVE owner).
- **Fix (v3):** bounded retry (3×, 0.3 s), then explicit refusal with a user-facing message — never a silent second instance. Implemented.
- **Status:** **FIXED.**
- **Regression:** `test_v3_regression.py` → "A2 stale-lock fix present" / "A2 forward retry loop present" + source-level assertions.

### GM-06 [Medium] — Orphaned queue feature (dead code + advertised interaction missing)
- **Category:** UX / code quality
- **Where:** `queue_panel.py:19,143` never imported by `main.pyw` (only by a test); its docstring advertises "Play next" which does not exist in `_on_context_menu` (`main.pyw:3751-3793`).
- **Fix (v3):** "Play next" and "Add to queue" context actions implemented on the main track lists (move-after-current / append, no duplicates); the queue panel remains importable for a future drawer.
- **Status:** **FIXED.**
- **Regression:** `test_v3_regression.py` → the three A4 checks.

### GM-07 [Medium] — Update trust model is integrity-only
- **Category:** Security
- **Where:** `updater.py:121-156` — SHA-256 sidecar comes from the SAME release as the binary; no code signing/publisher authenticity.
- **Impact:** a compromised GitHub account/dependency could publish a validly-hashed malicious build; SmartScreen warns on unsigned exes.
- **Status:** **DOCUMENTED (not fixed — no certificate available).** TLS + host allow-list + mandatory hash + release-name regex remain in place; the signing readiness section in RELEASE_NOTES.md states what a release needs (EV/OV cert + `signtool` + timestamp) and the release is published unsigned with the limitation disclosed. No signature was fabricated.
- **Regression:** existing `test_updater` suite (58 checks, hash/redirect/mode/markers) — still green.

### GM-08 [Medium] — SECURITY_AUDIT_REPORT.md is stale
- **Category:** Security documentation
- **Where:** the audit (dated 2026-08-24, v1.0.0) asserts "no network: no socket/http/requests" and "no subprocess/os.system/QProcess/openUrl" — but v2.2.0 has `updater.py:38` (GitHub HTTPS + `QProcess.startDetached` at 434), `lyrics.py:316-321` (lrclib.net via urllib), `update_dialog.py:272` (`openUrl`).
- **Fix:** the v3 audit re-ran the full security surface (see §Security below). The old report stays as a historical document; this report supersedes it.
- **Status:** **FIXED (superseded).**

### GM-09 [Medium] — PyInstaller hiddenimports incomplete by its own rule
- **Category:** Build
- **Where:** `goldenmusic.spec:52-63` — comment says late imports must be listed; `playback_fx`, `playstats`, `userbackup`, `audio_output`, `tag_editor` (late imports at `main.pyw:1755/1760/1858/5655/4000`) are missing; masked only by modulegraph's static scan + the smoke test.
- **Fix:** all five + all v3 modules added to hiddenimports.
- **Status:** **FIXED.**
- **Regression:** `test_v3_regression.py` → "A5 spec hiddenimports complete".

### GM-10 [Medium] — All 12 CI test suites were broken by the `main.py` → `main.pyw` rename
- **Category:** Testing / CI
- **Where:** commit `da0fd34` renamed the entry module but every suite does `from main import MainWindow` — Python resolves only `.py`. All suites crashed with `ModuleNotFoundError` (verified by running `test_functional.py` against the baseline: it fails before any test executes).
- **Impact:** the project's CI "green" could only have come from jobs that no longer exercised the tests; every change after the rename shipped without the safety net.
- **Fix:** `scripts/testenv.py` registers a `main` module alias for `main.pyw` (single choke point; also fixes `sys.path`). Full battery re-run: 21/21 suites green in this environment.
- **Status:** **FIXED.**
- **Regression:** this very suite run + A6 check.

### GM-11 [Medium] — View model incomplete (4-page stack, 2-value enum)
- **Category:** Architecture / UX
- **Where:** `main.pyw:127-130` `View` had only `LIBRARY`/`FAVORITES`; Albums reused `View.LIBRARY` (2883), Stats left a stale view (2890-2894); the stats auto-refresh keyed off a raw `currentIndex() == 3`.
- **Fix (v3):** `View` covers all 7 pages; `PAGE_*` index constants; navigation dispatch rewritten; stats timer keyed on `PAGE_STATS`.
- **Status:** **FIXED.**
- **Regression:** `test_v3_regression.py` → A3 block.

### GM-12 [Low] — Docs drift
- **Category:** Documentation
- **Where:** `main.pyw:7` claims "Tray right-click: Add to Favorites" (actual: custom popup); `build_windows.bat:6,69` echo `GoldenMusicSetup-1.1.0.exe`; `README.md:107` "FFmpeg-backed" claims vs. WMF-forced reality (see GM-03).
- **Status:** **FIXED** (docstring corrected, bat echoes updated, README rewritten for the actual v3 stack).

### GM-13 [Low] — `gm_stress.py` hardcodes a developer path
- **Where:** `gm_stress.py:17` — `E:\my-documents\ClaudeCode\GoldenMusic`.
- **Status:** **FIXED** (relative path).

### GM-14 [Low] — Old-Qt output-switch leak
- **Where:** `audio_output.py:64-70` — fallback path creates a new `QAudioOutput` without disposing the old one (Qt < 6.5 only).
- **Status:** **DEFERRED (documented)** — no reproducible leak on Qt 6.11 (the shipping version, pinned in requirements); fix would be dead code on the pinned Qt.

### GM-15 [Low] — IPC named pipe unauthenticated (same-user only)
- **Where:** `ipc.py:41-48` — any same-user process can enqueue files or raise the window. Input is strictly validated (absolute, existing, audio-extension, ≤100, no shell/exec), so impact is limited to nuisance.
- **Status:** **ACCEPTED RISK (documented)** — same-user ACL is the OS default for the named pipe class used; hardening (per-session token) would break "Open with" forwarding from Explorer-laanned instances.

### GM-16 [Informational] — Security posture re-verification (v3 surface)
Re-ran against the v3 code (network surface grew: archive.org provider in `online.py`, downloads in `downloads.py`):
- No `shell=True`, no `eval/exec/pickle` (grep-verified across the tree).
- All new network calls are HTTPS-only with scheme enforcement (`online.py:_http_get`, `downloads.py:_download_to_part`, `valid_stream_url` rejects credentials/ports/http).
- Bounded reads (2 MiB search / streamed download chunks), bounded retries with backoff, 10-20 s timeouts.
- Downloads: `.part` + atomic rename + size verification, never-overwrite naming, 500 MB disk floor, filename sanitization.
- New registry code writes only HKCU, only its own keys, with value-level uninstall cleanup; `UserChoice` never touched (grep: zero hits).
- `requirements.txt` pins PyQt6 6.11.0; optional `winrt-*` wheels pinned `>=4.0` on win32 only.
- CI now runs Bandit + **pip-audit** + CycloneDX **SBOM** (added in v3).
- Remaining honest gaps: code signing (GM-07), and dynamic scanners (Semgrep) not yet wired (Bandit + pip-audit are); documented in RELEASE_NOTES.

---

## Remediation matrix

| ID | Severity | Summary | Status |
|----|----------|---------|--------|
| GM-01 | High | Offline-drive library wipe | **Fixed** (A1 regression) |
| GM-02 | High | No SMTC media session | **Implemented**; runtime verify on Windows pending (V3 regressions cover logic) |
| GM-03 | High | Advertised formats unplayable | **Fixed** code-level (backend un-forced, plugin restored, runtime probe); Windows format matrix re-check pending |
| GM-04 | High | Not in Default apps | **Implemented** installer+runtime, policy-compliant; clean-Windows end-to-end visual check pending |
| GM-05 | Medium | Double-instance race | **Fixed** (A2) |
| GM-06 | Medium | Orphaned queue feature | **Fixed** (A4) |
| GM-07 | Medium | Unsigned update trust model | **Documented limitation** (no cert) |
| GM-08 | Medium | Stale security report | **Superseded** by this report + GM-16 |
| GM-09 | Medium | Spec hiddenimports gaps | **Fixed** (A5) |
| GM-10 | Medium | CI suites broken post-rename | **Fixed** (A6; 21/21 suites green) |
| GM-11 | Medium | View model incomplete | **Fixed** (A3) |
| GM-12 | Low | Docs drift | **Fixed** |
| GM-13 | Low | Hardcoded dev path | **Fixed** |
| GM-14 | Low | Qt<6.5 output leak | Deferred (dead on pinned Qt) |
| GM-15 | Low | IPC same-user nuisance surface | Accepted risk (documented) |
| GM-16 | Info | Security re-verification | Done (Bandit+pip-audit+SBOM in CI) |

**Priority items fixed:** all Critical-equivalent (High) findings (GM-01…GM-04) plus 5 of 6 Medium. Unresolved items each carry an explicit reason.

---

## What was verified by execution in this environment

- 21/21 test suites green (functional, deep, stability ×2, history-nav, features, crash, tray-toggle, open-with incl. real cross-process IPC roundtrip, shortcuts, updater 58 checks, version-sync 10 checks, smart-shuffle, theme ×3, anim, final6, qfont, and the new v3 regression suite — 46 checks).
- Real HTTP download roundtrip with a local Range-capable server: complete/pause-resume semantics/cancel/retry-after-503/duplicate naming/byte-exact verification/library integration (23 checks in the downloads/online suite).
- Full MainWindow boots offscreen with the v3 shell: 7 pages, sidebar nav, Liquid Glass theming, download manager live, SMTC degraded safely.
- Windows-only surfaces (registry/SMTC/DWM/installer) verified by code review + unit tests of their logic; flagged above wherever a real-Windows run remains the final word.
