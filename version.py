"""
Golden Music — single source of truth for the version string.

Consumed by config.py (APP_VERSION re-export), verified against the
installer default and the README badge by scripts/test_version_sync.py,
and injected into the Inno Setup installer via /DAppVersion= in CI
(goldenmusic.iss falls back to the matching default when built by hand).
"""

APP_VERSION = "2.2.0"
