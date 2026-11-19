# Release Notes — Golden Music 3.0.0

**Codename: Nuvx Desktop Shell · Liquid Glass · Online Music · Downloads**

## Highlights
- **Home is your local library** in the Nuvx Desktop layout: Recently Played, Recently Added, Albums, Artists, Playlists, Favorites, Recently Downloaded — real data, never placeholder content, never online recommendations.
- **Online music**: search + streaming through a provider architecture (Internet Archive ships: public-domain audio, no auth, downloads allowed). "Play next" / "Add to queue" give mixed local/online queues on ONE playback engine.
- **Download manager**: pick the folder ONCE; then queue, progress %, speed, size, pause/resume/cancel/retry; completed files join your existing library exactly once, verified and never overwriting.
- **Liquid Glass design system** replaces the 12-theme picker: accent colour (incl. artwork-derived), transparency, native Mica/Acrylic blur (Win 11 22H2+, opaque fallback), corner radius, density, animation intensity, reduced motion. Existing configs migrate automatically.
- **Windows finally recognises Golden Music**: the app + installer now write the full Default-apps registration surface (RegisteredApplications + Capabilities + FileAssociations + Open with) — without ever touching your default-player choice (see Settings → Windows to make it the default yourself).
- **Windows media controls (SMTC)**: media overlay / lock screen / taskbar flyout with Play/Pause/Next/Prev, title/artist, artwork (optional `winrt` packages; the app degrades gracefully without them — global media keys keep working regardless).
- **Audio honesty**: the FFmpeg backend is back (all 9 formats really decode) and only formats the running machine verifiably plays are advertised to Windows.
- Nuvx-style sidebar (collapsible), top player strip, context "Play next"/"Add to queue", safer single-instance behaviour, offline-drive library protection, repaired test suites, pip-audit + SBOM in CI.

## Upgrading
Same installer identity as 2.x — install over the old version; settings, library, playlists, favorites, history and stats are preserved (the 12-theme setting migrates to a matching Liquid Glass accent/mode automatically).

## Known limitations (honest list)
- **Unsigned builds**: no code-signing certificate is available; Windows SmartScreen will warn on first run ("More info → Run anyway"). The updater verifies SHA-256 integrity of every artifact but cannot prove publisher authenticity — both are documented, nothing fabricated.
- SMTC and Default-apps listing need the optional `winrt-*` wheels (bundled in requirements for win32) and a Windows 10/11 host; both degrade gracefully otherwise.
- The Android release was **deferred** (see FINAL_ENGINEERING_REPORT): a separate Kotlin/Compose front end is the only sane path and it must not compromise this Windows release.
- EQ/DSP, translated/romanised lyrics and a keycap shortcuts pane are documented gaps (NUVX_SETTINGS_PARITY.md rows 23/29/30/53).

## Verification status
- 21 test suites green in this environment (incl. the new 46-check v3 regression suite and a real HTTP download roundtrip).
- Windows-only surfaces (registry/SMTC/DWM/installer) are code-reviewed + logic-tested; the final visual Default-apps / SMTC confirmation should be re-run once on a clean Windows machine (AUDIT GM-02/GM-04 status lines).
