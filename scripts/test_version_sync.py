"""
Version-sync regression test — one source of truth (version.py) must agree
with every other place the version appears:

  * config.py re-exports version.py's APP_VERSION
  * goldenmusic.iss default `#define AppVersion` (hand builds)
  * README.md version badge + download links
  * INSTALLATION.md download links
  * CHANGELOG.md has a section for the current version

Run:  python scripts/test_version_sync.py   (no Qt needed)
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from version import APP_VERSION  # noqa: E402

_results = []


def check(name, ok, detail=""):
    _results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ->  {detail}" if detail and not ok else ""))


def main():
    v = APP_VERSION
    check("version.py is a strict X.Y.Z semver",
          bool(re.fullmatch(r"\d+\.\d+\.\d+", v)), v)

    # config.py re-export ------------------------------------------------
    # Parse APP_VERSION out of config.py without importing Qt (the version
    # line itself is a plain re-export; this also guards against someone
    # replacing the import with a stray literal).
    cfg_src = (REPO / "config.py").read_text(encoding="utf-8")
    m = re.search(r"^from version import APP_VERSION", cfg_src, re.M)
    literal = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', cfg_src, re.M)
    check("config.py re-exports version.py (no drift literal)",
          m is not None and literal is None,
          "literal APP_VERSION assignment reappeared in config.py"
          if literal else "missing 'from version import APP_VERSION'")

    # goldenmusic.iss default --------------------------------------------
    iss = (REPO / "goldenmusic.iss").read_text(encoding="utf-8")
    m = re.search(r'#ifndef AppVersion\s*\n#define\s+AppVersion\s+"([^"]+)"', iss)
    check("goldenmusic.iss default AppVersion block present", m is not None)
    if m:
        check("goldenmusic.iss default == version.py", m.group(1) == v,
              f"iss={m.group(1)} version={v}")
    check("goldenmusic.iss OutputBaseFilename uses {#AppVersion}",
          "OutputBaseFilename=GoldenMusicSetup-{#AppVersion}" in iss)

    # README ---------------------------------------------------------------
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    badge = re.search(r"badge/Version-([0-9.]+)-", readme)
    check("README version badge == version.py",
          badge is not None and badge.group(1) == v,
          f"badge={badge.group(1) if badge else None} version={v}")
    check(f"README download link mentions GoldenMusicSetup-{v}.exe",
          f"GoldenMusicSetup-{v}.exe" in readme)

    # INSTALLATION.md -------------------------------------------------------
    inst = (REPO / "INSTALLATION.md").read_text(encoding="utf-8")
    check(f"INSTALLATION.md mentions GoldenMusicSetup-{v}.exe",
          f"GoldenMusicSetup-{v}.exe" in inst)

    # CHANGELOG --------------------------------------------------------------
    chlog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    check(f"CHANGELOG.md has a [{v}] section",
          re.search(rf"^## \[{re.escape(v)}\]", chlog, re.M) is not None)
    # The footer once claimed a stale version — it must follow the release.
    foot = re.search(r"Golden Music v([0-9.]+)", chlog)
    if foot:
        check("CHANGELOG.md footer version == version.py",
              foot.group(1) == v,
              f"footer={foot.group(1)} version={v}")

    failed = sum(1 for ok, *_ in _results if not ok)
    print(f"\nversion-sync: {len(_results) - failed}/{len(_results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
