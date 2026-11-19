"""Golden Music v3 — proper Windows application registration.

Fixes the "Windows does not recognise Golden Music as an installed music
player" defect: the app now writes the full, correct registry surface so it
appears in **Default apps** (via RegisteredApplications + Capabilities +
FileAssociations), in the **Open with** menu (OpenWithProgids + ProgIDs +
Applications entry) — WITHOUT ever touching the protected UserChoice keys.

Policy (hard rules, enforced by design):

  * never write ``...\\UserChoice`` and never call FAssoc* APIs
  * per-user (HKCU / HKEY_CURRENT_USER) only — matches the per-user install
  * only register extensions the ACTIVE backend verifiably decodes
    (formatcaps.probe_supported_exts)
  * every key/value created here carries its own uninstall cleanup path, so
    an Inno uninstall or the in-app "Remove registration" leaves nothing
    behind; only Golden Music's own keys are ever deleted

Registry layout created::

    HKCU\\Software\\RegisteredApplications
        "Golden Music" = "Software\\GoldenMusic\\Capabilities"

    HKCU\\Software\\GoldenMusic\\Capabilities
        "ApplicationName"        = "Golden Music"
        "ApplicationDescription" = "..."
    HKCU\\Software\\GoldenMusic\\Capabilities\\FileAssociations
        ".mp3" = "GoldenMusic.AudioFile"      (verified extensions only)

    HKCU\\Software\\Classes\\GoldenMusic.AudioFile
        (default) / FriendlyTypeName / DefaultIcon / shell\\open\\command
    HKCU\\Software\\Classes\\Applications\\GoldenMusic.exe
        FriendlyAppName + SupportedTypes + shell\\open\\command
    HKCU\\Software\\Classes\\<.ext>\\OpenWithProgids
        "GoldenMusic.AudioFile" = ""
"""
from __future__ import annotations

import logging
import os
import sys

import formatcaps

log = logging.getLogger("goldenmusic.winreg_apps")

APP_NAME = "Golden Music"
REG_KEY_CAPABILITIES = "Software\\GoldenMusic\\Capabilities"
REG_KEY_REGISTERED_APPS = "Software\\RegisteredApplications"
PROGID = "GoldenMusic.AudioFile"
APP_EXE_KEY = "GoldenMusic.exe"        # basename under Classes\\Applications
DESCRIPTION = ("The modern Windows music player — local library, online "
               "search and streaming, downloads.")


def is_windows() -> bool:
    return sys.platform == "win32"


def _exe_path() -> str:
    """Best executable path for shell open commands.

    Frozen builds: the real exe. Source runs: pythonw + script. The
    installer (Inno) writes its own absolute ``{app}`` commands at install
    time, so this runtime value only matters for portable/source use.
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    script = os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else "")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    if script.lower().endswith((".pyw", ".py")) and pyw:
        return f'"{pyw}" "{script}"'
    return sys.executable


def open_command() -> str:
    exe = _exe_path()
    return f'{exe} "%1"' if exe.startswith('"') else f'"{exe}" "%1"'


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_app(exts=None, exe_command=None) -> dict:
    """Write the full registration surface under HKCU. Idempotent.

    Returns a report dict: which extensions were registered, which were
    skipped (not verified by the active backend), and how many values were
    written. Raises RuntimeError on non-Windows / registry failure so the
    caller can surface a proper error instead of a silent no-op.
    """
    if not is_windows():
        raise RuntimeError("Windows registration requires Windows")
    try:
        import winreg
    except ImportError as e:
        raise RuntimeError(f"winreg unavailable: {e}") from e

    verified = formatcaps.probe_supported_exts(exts)
    if not verified:
        raise RuntimeError("no verified audio formats — refusing to register")
    command = exe_command or open_command()
    writes = 0
    icon = _icon_path()

    def w(path, name, value):
        nonlocal writes
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            writes += 1

    # 1. RegisteredApplications -> makes the app appear in Default apps
    w(REG_KEY_REGISTERED_APPS, APP_NAME, REG_KEY_CAPABILITIES)

    # 2. Capabilities (name/description + FileAssociations)
    w(REG_KEY_CAPABILITIES, "ApplicationName", APP_NAME)
    w(REG_KEY_CAPABILITIES, "ApplicationDescription", DESCRIPTION)
    for ext in verified:
        w(REG_KEY_CAPABILITIES + "\\FileAssociations", ext, PROGID)

    # 3. ProgID
    progid_path = "Software\\Classes\\" + PROGID
    w(progid_path, None, "Golden Music Audio File")
    w(progid_path, "FriendlyTypeName", "Audio file (Golden Music)")
    if icon:
        w(progid_path + "\\DefaultIcon", None, icon)
    w(progid_path + "\\shell\\open\\command", None, command)

    # 4. Applications entry (Open with + SupportedTypes)
    app_path = "Software\\Classes\\Applications\\" + APP_EXE_KEY
    w(app_path, None, APP_NAME + " (audio)")
    w(app_path, "FriendlyAppName", APP_NAME)
    w(app_path, "SupportedTypes", " ".join(verified))
    w(app_path + "\\shell\\open\\command", None, command)

    # 5. OpenWithProgids per verified extension (non-default association)
    for ext in verified:
        w("Software\\Classes\\" + ext + "\\OpenWithProgids", PROGID, "")

    report = {
        "registered": verified,
        "skipped": [e for e in (exts or formatcaps.CANDIDATE_EXTS)
                    if e not in verified],
        "keys_written": writes,
        "command": command,
    }
    log.info(f"Windows registration updated: {report}")
    return report


def unregister_app(exts=None) -> int:
    """Remove ONLY Golden Music's own registration keys.

    Never touches foreign values: shared keys (RegisteredApplications,
    OpenWithProgids) are cleaned per-VALUE, not per-KEY.
    """
    if not is_windows():
        raise RuntimeError("Windows registration requires Windows")
    import winreg
    removed = 0
    verified = exts or formatcaps.CANDIDATE_EXTS

    def del_value(path, name):
        nonlocal removed
        try:
            with winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                  winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
                removed += 1
        except OSError:
            pass

    def del_key(path):
        nonlocal removed
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            removed += 1
        except OSError:
            pass

    del_value(REG_KEY_REGISTERED_APPS, APP_NAME)
    for ext in verified:
        del_value("Software\\Classes\\" + ext + "\\OpenWithProgids", PROGID)
        del_value(REG_KEY_CAPABILITIES + "\\FileAssociations", ext)
    del_key(REG_KEY_CAPABILITIES + "\\FileAssociations")
    del_key(REG_KEY_CAPABILITIES)
    del_key("Software\\GoldenMusic")
    del_key("Software\\Classes\\" + PROGID + "\\shell\\open\\command")
    del_key("Software\\Classes\\" + PROGID + "\\shell")
    del_key("Software\\Classes\\" + PROGID + "\\DefaultIcon")
    del_key("Software\\Classes\\" + PROGID)
    del_key("Software\\Classes\\Applications\\" + APP_EXE_KEY +
            "\\shell\\open\\command")
    del_key("Software\\Classes\\Applications\\" + APP_EXE_KEY + "\\shell")
    del_key("Software\\Classes\\Applications\\" + APP_EXE_KEY)
    log.info(f"Windows registration removed ({removed} deletions)")
    return removed


def registration_status() -> dict:
    """Report what is currently registered (for Settings display)."""
    if not is_windows():
        return {"windows": False, "registered": False,
                "extensions": [], "detail": "not Windows"}
    import winreg
    detail = ""
    exts = []
    registered = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            REG_KEY_REGISTERED_APPS) as k:
            winreg.QueryValueEx(k, APP_NAME)
        registered = True
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                REG_KEY_CAPABILITIES + "\\FileAssociations") as fk:
                i = 0
                while True:
                    try:
                        exts.append(winreg.EnumKey(fk, i))
                        i += 1
                    except OSError:
                        break
        except OSError:
            detail = "capabilities incomplete"
    except OSError:
        detail = "not registered"
    return {"windows": True, "registered": registered,
            "extensions": exts, "detail": detail}


def open_default_apps_settings() -> bool:
    """Open the Windows Default-apps page (ms-settings:defaultapps).

    The USER then picks Golden Music for audio — the app never writes the
    protected UserChoice itself.
    """
    if not is_windows():
        return False
    try:
        os.startfile("ms-settings:defaultapps")   # noqa: S606 (win32 only)
        return True
    except Exception as e:
        log.warning(f"could not open default apps page: {e}")
        return False


def _icon_path() -> str:
    """icon.ico bundled beside the exe (frozen) or in assets/ (source)."""
    if getattr(sys, "frozen", False):
        p = os.path.join(os.path.dirname(sys.executable), "assets", "icon.ico")
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "icon.ico")
    return p if os.path.isfile(p) else ""


# ---------------------------------------------------------------------------
# Installer (.iss) support: produce the Inno [Registry] fragment that
# mirrors register_app() so installed builds are registered by SETUP
# (not first-run). Tests assert the two surfaces stay equivalent.
# ---------------------------------------------------------------------------

def iss_registry_fragment(exts=None) -> str:
    """Text of an Inno Setup [Registry] section matching register_app()."""
    verified = exts or formatcaps.CANDIDATE_EXTS
    lines = []
    a = "Root: HKA; Subkey:"
    lines.append(
        f'{a} "{REG_KEY_REGISTERED_APPS}"; ValueType: string; '
        f'ValueName: "{APP_NAME}"; ValueData: "{REG_KEY_CAPABILITIES}"; '
        f'Flags: uninsdeletevalue; Tasks: fileassoc')
    lines.append(
        f'{a} "{REG_KEY_CAPABILITIES}"; ValueType: string; '
        f'ValueName: "ApplicationName"; ValueData: "{APP_NAME}"; '
        f'Flags: uninsdeletekey; Tasks: fileassoc')
    lines.append(
        f'{a} "{REG_KEY_CAPABILITIES}"; ValueType: string; '
        f'ValueName: "ApplicationDescription"; ValueData: "{DESCRIPTION}"; '
        f'Flags: uninsdeletekey; Tasks: fileassoc')
    for ext in verified:
        lines.append(
            f'{a} "{REG_KEY_CAPABILITIES}\\FileAssociations"; '
            f'ValueType: string; ValueName: "{ext}"; ValueData: "{PROGID}"; '
            f'Flags: uninsdeletekey; Tasks: fileassoc')
    return "\n".join(lines)


if __name__ == "__main__":      # smoke: report only, never writes on import
    print("golden-music winreg_apps module — use register_app()/unregister_app()")
    print("non-Windows safe:", not is_windows())
