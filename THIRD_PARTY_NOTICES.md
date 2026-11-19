# Third-Party Notices — Golden Music 3.0.0

Golden Music is MIT-licensed (LICENSE). It was **not** derived from Nuvx code: the Nuvx Desktop
repository (GPL-3.0) was inspected as a *design reference only* (layout proportions, interaction
patterns, settings inventory); Golden Music's implementation is original PyQt6 code, and no
GPL-licensed source, assets, fonts or artwork from Nuvx were copied into this project.

## Bundled / runtime dependencies
| Component | License | Use |
|-----------|---------|-----|
| PyQt6 / Qt 6.11 (LGPL-3, dynamically linked, unmodified wheels) | LGPL-3.0 | entire UI, multimedia |
| Python 3.11-3.14 (CPython) | PSF | runtime |
| mutagen | GPL-2.0-or-later (used as a library via pip; no code copied into the repo) | tag/cover extraction |
| pywin32 (win32, optional; win32-only) | PSF | mutex, hotkeys |
| winrt-runtime / winrt-Windows.Media / winrt-Windows.Storage.Streams / winrt-Windows.Foundation (optional; win32-only) | MIT | SMTC media controls |
| PyInstaller 6.x (build-time only) | GPL with bootloader exception | packaging |

## Icons and artwork
- **Lucide icon set** (`assets/icons/lucide/*.svg`, ISC License — permits use, modification and
  redistribution with the notice below). Includes the `house.svg` and `download.svg` glyphs added
  in v3, drawn in the standard Lucide style.
  Notice: "This project reuses icons from Lucide (https://lucide.dev), ISC License
  (https://en.wikipedia.org/wiki/ISC_license)."
- `assets/logo.png`, `assets/icon*.png`, `assets/icon.ico` — Golden Music's own logo, from which
  all platform icon variants are generated (multi-resolution .ico, aspect preserved).

## Network services used (no credentials stored)
- `api.github.com` / `github.com` / release assets — update checks/downloads (TLS + hash verified).
- `lrclib.net` — lyrics lookups (public API, https only).
- `archive.org` (advancedsearch + metadata + download endpoints) — online search, streaming,
  downloads of public-domain/freely-licensed audio per the Internet Archive's terms. The user
  agent identifies the application.
No DRM, subscription or protected content is accessed; nothing bypasses authentication.
