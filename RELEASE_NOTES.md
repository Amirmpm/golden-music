# Release Notes — Golden Music 3.2.0

3.2.0 is the Nuvx visual-accuracy release: the user-supplied 12-file UI
rewrite, grafted onto the 3.1.0 remediation base by method-level
three-way merge — the 3.1 machinery (unified queue, rebuilt download
manager, async covers, bounded shutdown) is preserved under the new
chrome — plus Golden Music's first Linux release.

## Visual accuracy pass (Nuvx UI)

- Player bar: single 56px row, LCD with elapsed/total swap, 2px seek
  line; the "Up Next" queue drawer button kept
- Mini player: one-row 400x112; queue drawer: 340px frameless glass
- Search: 42px pill field (Enter submits), frameless result rows
- Home: 168px tiles, round covers with centre-crop rendering — the
  async cover loader (3.1 fix) intact
- Sidebar / settings / tray: neutral glass, gold reserved for state;
  Nuvx radius ladder; typography 34/22/13.5
- `glass_alpha()`: Nuvx glass opacities over the theme's own colour —
  correct in dark and light mode; `_qcolor()` now parses float-alpha
  `rgba()` strings
- Update pill anchored bottom-right (player bar moved to top)

## First Linux release

- CI matrix now `windows-latest` + `ubuntu-latest`
- New `release-linux` job (ubuntu-latest): full suite + Bandit +
  PyInstaller bundle as `GoldenMusic-3.2.0-linux-x86_64.tar.gz` +
  sha256, uploaded into the release the Windows job creates
- `build_release.py` is cross-platform: offscreen smoke test on headless
  Linux, trims + `strip`, symlink-deduped size budget (260 MB Linux /
  115 MB Windows)

## Windows artifacts (per release)

- `GoldenMusicSetup-3.2.0.exe` + `.sha256` — the installer
- `GoldenMusic-3.2.0-portable.zip` — the portable edition

See CHANGELOG.md for the full list and BUG_TRACKER.md for root causes.
