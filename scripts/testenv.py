"""Golden Music — test/dev isolation helper.

Any script that boots MainWindow MUST call `testenv.install()` before any
other project import::

    sys.path.insert(0, <repo root>)
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import testenv; testenv.install()

What it does:
  1. Points HOME/USERPROFILE at a fresh temp dir AND patches
     pathlib.Path.home() to return it — so config_path(), stats.json,
     tag_cache.json and goldenmusic.log all land in the sandbox.
  2. Moves an existing repo-root goldenmusic_config.json (portable dev
     config) aside for the duration of the run, so tests can neither
     read demo state nor overwrite it. Restored at exit.
  3. Restores env, Path.home and the portable config at process exit,
     then deletes the sandbox.

Background: the crash/feature suites used to boot the real MainWindow
with fake Temp tracks and call _save_config(), which overwrote the
developer's REAL ~/.goldenmusic/config.json with test junk (and the
exists-prune then wiped the real library on the next boot). This module
makes that class of accident impossible.

Escape hatch: GOLDENMUSIC_ALLOW_REAL_CONFIG=1 skips isolation entirely
(e.g. scripts/live_theme_shots.ps1, which intentionally uses live data).
"""
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

_installed = False
_tmp_home = None
_orig_home = None
_saved_env = {}
_moved_portable_backup = None

REPO_ROOT = Path(__file__).resolve().parent.parent
PORTABLE_NAME = "goldenmusic_config.json"


def install():
    """Redirect all user-data I/O to a throwaway sandbox. Idempotent."""
    global _installed, _tmp_home, _orig_home, _moved_portable_backup
    if _installed:
        return _tmp_home
    if os.environ.get("GOLDENMUSIC_ALLOW_REAL_CONFIG") == "1":
        return None
    _tmp_home = Path(tempfile.mkdtemp(prefix="gmtest_home_"))
    _orig_home = Path.home
    Path.home = classmethod(lambda cls: _tmp_home)
    for var in ("HOME", "USERPROFILE"):
        _saved_env[var] = os.environ.get(var)
        os.environ[var] = str(_tmp_home)
    portable = REPO_ROOT / PORTABLE_NAME
    if portable.is_file():
        _moved_portable_backup = _tmp_home / (PORTABLE_NAME + ".orig")
        shutil.move(str(portable), str(_moved_portable_backup))
    atexit.register(_restore)
    print(f"testenv: isolated user data -> {_tmp_home}", flush=True)
    _installed = True
    # Repair `from main import ...` after the main.py -> main.pyw rename.
    try:
        _install_main_alias()
    except Exception as e:
        print(f"testenv: main.pyw alias failed: {e}", flush=True)
    return _tmp_home


def _restore():
    """Undo install(): env, Path.home, portable config, sandbox cleanup."""
    global _installed
    if not _installed:
        return
    _installed = False
    try:
        if _orig_home is not None:
            Path.home = _orig_home
    except Exception:
        pass
    for var, val in _saved_env.items():
        try:
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        except Exception:
            pass
    try:
        portable = REPO_ROOT / PORTABLE_NAME
        if (_moved_portable_backup is not None
                and _moved_portable_backup.is_file()):
            if portable.is_file():
                portable.unlink()
            shutil.move(str(_moved_portable_backup), str(portable))
    except Exception:
        pass
    try:
        if _tmp_home is not None and _tmp_home.is_dir():
            shutil.rmtree(_tmp_home, ignore_errors=True)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# main.pyw import alias
# ---------------------------------------------------------------------------
# The app entry module was renamed main.py -> main.pyw (commit da0fd34) but
# every GUI test does `from main import MainWindow`. Python's import system
# only resolves .py — so ALL 12 CI suites crashed with ModuleNotFoundError
# after the rename. Registering the alias here (called by install(), which
# every suite invokes before importing the app) repairs them all at once.
# ---------------------------------------------------------------------------
_MAIN_ALIAS_INSTALLED = False


def _install_main_alias():
    global _MAIN_ALIAS_INSTALLED
    if _MAIN_ALIAS_INSTALLED or "main" in sys.modules:
        return
    pyw = REPO_ROOT / "main.pyw"
    if not pyw.is_file():
        return
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)   # main.pyw imports its sibling modules
    spec = importlib.util.spec_from_loader(
        "main", SourceFileLoader("main", str(pyw)))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["main"] = mod
    spec.loader.exec_module(mod)
    _MAIN_ALIAS_INSTALLED = True
