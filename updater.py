"""
Golden Music — in-app auto-update engine (UI-free).

Reads the GitHub Releases of the hardcoded repo, compares strict numeric
semver, picks the installer asset, verifies SHA-256, downloads with a
size cap into ~/.goldenmusic/updates/ (atomic .part rename) and
classifies the install mode.

Security posture:
  * HTTPS only; every redirect hop is validated against a fixed host
    allow-list (github.com, api.github.com and the two GitHub asset
    hosts) — anything else aborts before a byte is read
  * fixed User-Agent / Accept headers per GitHub API guidance
  * download size cap, fixed destination dir, validated filename,
    .part + atomic rename, mandatory SHA-256 verification — nothing
    unverified is ever handed to the OS installer
  * no shell, no code execution from release notes (plain text only),
    no secrets

Testability: all decisions live in pure functions; HTTP is behind an
injectable fetcher object (default HttpFetcher). No environment-variable
hooks in shipped code.
"""
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("app.updater")

GITHUB_REPO = "Amirmpm/golden-music"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
USER_AGENT = "GoldenMusic/{version}"
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
DOWNLOAD_SIZE_CAP = 400 * 1024 * 1024      # 400 MB hard cap
INSTALLER_NAME_TEMPLATE = "GoldenMusicSetup-{version}.exe"
SHA_NAME_TEMPLATE = INSTALLER_NAME_TEMPLATE + ".sha256"
_PENDING_MARKER = "pending_update.json"
_TIMEOUT_S = 15

_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$")
_NUM_RE = re.compile(r"^\d+$")
_INSTALLER_NAME_RE = re.compile(r"^GoldenMusicSetup-\d+\.\d+\.\d+\.exe$")


class UpdaterError(Exception):
    """Raised for any update-flow failure the UI may want to surface."""


# ---------------------------------------------------------------------------
# Pure version logic
# ---------------------------------------------------------------------------
def normalize_tag(tag) -> "str | None":
    """'v2.2.0' -> '2.2.0'; None for anything non-numeric (incl. 'x.y',
    empty, '2.2.0-beta' — prerelease tags are rejected by parse_release)."""
    if not isinstance(tag, str):
        return None
    m = _TAG_RE.match(tag.strip())
    if not m:
        return None
    return m.group(1)


def _version_key(v: str):
    """('2', '10', '1') tuple of ints — NUMERIC comparison, never string."""
    parts = v.split(".")
    while len(parts) < 3:
        parts.append("0")
    if len(parts) != 3 or not all(_NUM_RE.match(p) for p in parts):
        raise ValueError(f"malformed version: {v!r}")
    return tuple(int(p) for p in parts)


def compare_versions(a: str, b: str) -> int:
    """-1 if a<b, 0 if equal (2.2 == 2.2.0), 1 if a>b. Numeric only."""
    ka, kb = _version_key(a), _version_key(b)
    return (ka > kb) - (ka < kb)


def parse_release(payload) -> "dict | None":
    """Validate a /releases/latest payload.

    Drafts and prereleases are ignored (never offered). Malformed tags
    are rejected. Returns {version, notes, html_url, assets} or None.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None
    version = normalize_tag(payload.get("tag_name") or "")
    if version is None:
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    notes = payload.get("body") if isinstance(payload.get("body"), str) else ""
    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url.startswith("https://"):
        html_url = RELEASES_PAGE_URL
    return {"version": version, "notes": notes,
            "html_url": html_url, "assets": assets}


def pick_asset(release: dict) -> "dict | None":
    """Select GoldenMusicSetup-<version>.exe plus its SHA-256 source.

    The hash comes from a companion '<name>.sha256' asset, or from the
    API asset digest field (sha256:...) when the sidecar is missing.
    Returns None when no verifiable installer asset exists.
    """
    if not release:
        return None
    version = release.get("version", "")
    want = INSTALLER_NAME_TEMPLATE.format(version=version)
    sha_name = SHA_NAME_TEMPLATE.format(version=version)
    installer, sha_url, digest = None, None, None
    for a in release.get("assets", []):
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        url = a.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if name == want:
            installer = {"name": name, "url": url,
                         "size": int(a.get("size") or 0)}
            d = a.get("digest")
            if isinstance(d, str) and d.startswith("sha256:"):
                digest = d.split(":", 1)[1].strip().lower()
        elif name == sha_name:
            sha_url = url
    if installer is None:
        return None
    if sha_url is None and not digest:
        # No verifiable hash — refuse (never install unverified payloads).
        return None
    installer["sha256_url"] = sha_url
    installer["sha256_digest"] = digest
    return installer


# ---------------------------------------------------------------------------
# Fetching (injectable)
# ---------------------------------------------------------------------------
class FetchResult:
    __slots__ = ("status", "body", "headers", "final_url")

    def __init__(self, status, body=b"", headers=None, final_url=""):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.final_url = final_url


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    """Abort redirects that leave the https + allow-list sandbox."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlparse(newurl)
        if p.scheme != "https" or (p.hostname or "") not in ALLOWED_DOWNLOAD_HOSTS:
            raise UpdaterError(f"redirect to disallowed host blocked: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpFetcher:
    """urllib-backed fetcher. Swap with a fake in tests."""

    def _validate_url(self, url: str):
        p = urllib.parse.urlparse(url)
        if p.scheme != "https":
            raise UpdaterError(f"non-https URL blocked: {url}")
        if (p.hostname or "") not in ALLOWED_DOWNLOAD_HOSTS:
            raise UpdaterError(f"host not allowed: {p.hostname}")

    def _opener(self):
        return urllib.request.build_opener(_RedirectGuard)

    def get(self, url, headers=None, timeout=_TIMEOUT_S) -> FetchResult:
        """GET with the https/allow-list validation; returns FetchResult.
        HTTPError (304/403/429...) is mapped onto status, other errors
        raise UpdaterError."""
        self._validate_url(url)
        req = urllib.request.Request(
            url, headers=headers or {},
            method="GET")
        try:
            with self._opener().open(req, timeout=timeout) as resp:
                return FetchResult(
                    status=resp.status,
                    body=resp.read(),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    final_url=resp.geturl())
        except urllib.error.HTTPError as e:
            return FetchResult(status=e.code,
                               headers={k.lower(): v for k, v in e.headers.items()})
        except UpdaterError:
            raise
        except Exception as e:
            raise UpdaterError(f"network error: {e}") from e

    def open_stream(self, url, timeout=_TIMEOUT_S):
        """Raw https stream for downloads (validated + guarded opener)."""
        self._validate_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": "GoldenMusic"})
        try:
            return self._opener().open(req, timeout=timeout)
        except UpdaterError:
            raise
        except Exception as e:
            raise UpdaterError(f"network error: {e}") from e


# ---------------------------------------------------------------------------
# Update check
# ---------------------------------------------------------------------------
def check_for_update(fetcher, installed_version: str,
                     etag: "str | None" = None) -> dict:
    """Query /releases/latest. Never raises for HTTP conditions —
    returns a status dict the UI can trust:
      not_modified | rate_limited | offline | error | up_to_date |
      update_available{release, asset, etag}
    """
    headers = {"User-Agent": USER_AGENT.format(version=installed_version),
               **API_HEADERS}
    if etag:
        headers["If-None-Match"] = etag
    try:
        resp = fetcher.get(RELEASES_API_URL, headers=headers)
    except UpdaterError as e:
        log.info(f"update check failed: {e}")
        return {"status": "offline", "detail": str(e)}
    if resp.status == 304:
        return {"status": "not_modified"}
    if resp.status in (403, 429):
        log.info("update check rate-limited by GitHub")
        return {"status": "rate_limited"}
    if resp.status != 200:
        return {"status": "error", "code": resp.status}
    try:
        payload = json.loads(resp.body.decode("utf-8", "replace"))
    except ValueError:
        return {"status": "error", "code": "bad_json"}
    release = parse_release(payload)
    if release is None:
        return {"status": "up_to_date"}   # draft/prerelease/malformed = no offer
    asset = pick_asset(release)
    if asset is None:
        # Release exists but has no verifiable installer — treat as no offer.
        return {"status": "up_to_date"}
    try:
        cmp = compare_versions(release["version"], installed_version)
    except ValueError:
        return {"status": "up_to_date"}
    if cmp <= 0:
        return {"status": "up_to_date"}
    return {"status": "update_available", "release": release,
            "asset": asset, "etag": resp.headers.get("etag")}


# ---------------------------------------------------------------------------
# Hashing + download
# ---------------------------------------------------------------------------
def parse_sha256_text(text: str) -> "str | None":
    """Accept '<hex>' or the standard '<hex>  <filename>' checksum format."""
    if not text:
        return None
    token = text.strip().split()[0].lower()
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else None


def verify_sha256(path, expected: str) -> bool:
    """Stream-hash a file and compare against the expected digest."""
    expected = (expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        log.warning(f"sha256 read failed: {e}")
        return False
    return h.hexdigest() == expected


def clean_stale_downloads(updates_dir) -> None:
    """Remove .part leftovers and (optionally any) files older than 7 days."""
    try:
        d = Path(updates_dir)
        if not d.is_dir():
            return
        now = time.time()
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.name.endswith(".part") or now - f.stat().st_mtime > 7 * 86400:
                f.unlink(missing_ok=True)
    except Exception as e:
        log.debug(f"stale download cleanup failed: {e}")


def download_installer(fetcher, asset: dict, dest_dir,
                       progress_cb=None, cancel=None,
                       max_bytes: int = DOWNLOAD_SIZE_CAP) -> Path:
    """Stream the installer into dest_dir, verify SHA-256, return path.

    Writes <name>.part, verifies the sidecar .sha256 (or the API digest),
    then atomically renames. Raises UpdaterError on any failure; partial
    data never survives as the real filename.
    """
    name = asset.get("name", "")
    if not _INSTALLER_NAME_RE.match(name):
        raise UpdaterError(f"unexpected asset name: {name!r}")
    d = Path(dest_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    part = d / (name + ".part")
    size = int(asset.get("size") or 0)
    if size > max_bytes:
        raise UpdaterError("asset larger than the download cap")

    try:
        with fetcher.open_stream(asset["url"]) as resp, \
                open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or size or 0)
            if total > max_bytes:
                raise UpdaterError("download exceeds the size cap")
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdaterError("cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > max_bytes:
                    raise UpdaterError("download exceeds the size cap")
                out.write(chunk)
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass
    except UpdaterError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise UpdaterError(f"download failed: {e}") from e

    expected = asset.get("sha256_digest")
    if asset.get("sha256_url"):
        try:
            side = fetcher.get(asset["sha256_url"])
            if side.status == 200:
                expected = parse_sha256_text(
                    side.body.decode("utf-8", "replace")) or expected
        except UpdaterError as e:
            part.unlink(missing_ok=True)
            raise UpdaterError(f"sha256 sidecar fetch failed: {e}") from e
    if not expected or not verify_sha256(part, expected):
        part.unlink(missing_ok=True)
        raise UpdaterError("SHA-256 verification failed — file discarded")

    part.replace(dest)     # atomic publish only after verification
    return dest


# ---------------------------------------------------------------------------
# Install mode + self-update
# ---------------------------------------------------------------------------
def detect_install_mode(exe_path: "str | None" = None) -> str:
    """'installer' (frozen + Inno evidence) | 'portable' | 'source'.

    Only 'installer' may self-replace; portable/source installs get a
    notification with an open-release-page button instead.
    """
    if not getattr(sys, "frozen", False):
        return "source"
    exe_dir = Path(exe_path if exe_path else sys.executable).resolve().parent
    if (exe_dir / "goldenmusic_config.json").is_file():
        return "portable"
    if (exe_dir / "unins000.exe").is_file():
        return "installer"
    # Inno always writes an uninstaller; double-check the registry for
    # exotic layouts before enabling self-update.
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    key = winreg.OpenKey(
                        root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                              r"\{9F5E7C8D-3B2E-5C4F-AC6D-2B3C4D5E6F7A}_is1")
                    key.Close()
                    return "installer"
                except OSError:
                    continue
        except Exception:
            pass
    return "portable"


def launch_installer(installer_path) -> bool:
    """Hand the verified installer to the OS, detached, silent-ish.

    /CLOSEAPPLICATIONS lets Inno use the AppMutex; the app exits right
    after launching so the files are not locked.
    """
    if os.name != "nt":
        return False
    from PyQt6.QtCore import QProcess
    log_path = Path(tempfile.gettempdir()) / "GoldenMusic_update.log"
    args = ["/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL",
            "/CLOSEAPPLICATIONS", "/NORESTART", f"/LOG={log_path}"]
    ok = QProcess.startDetached(str(installer_path), args)
    log.info(f"installer launch: {ok} (log: {log_path})")
    return ok


# ---------------------------------------------------------------------------
# Pending-update marker (failure recovery)
# ---------------------------------------------------------------------------
def write_pending_marker(updates_dir, target_version: str) -> None:
    try:
        d = Path(updates_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / _PENDING_MARKER).write_text(json.dumps({
            "target": target_version,
            "started_at": int(time.time()),
        }), encoding="utf-8")
    except Exception as e:
        log.debug(f"pending marker write failed: {e}")


def read_pending_marker(updates_dir) -> "dict | None":
    try:
        p = Path(updates_dir) / _PENDING_MARKER
        if not p.is_file():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) and isinstance(
            obj.get("target"), str) else None
    except Exception:
        return None


def clear_pending_marker(updates_dir) -> None:
    try:
        (Path(updates_dir) / _PENDING_MARKER).unlink(missing_ok=True)
    except Exception:
        pass


def evaluate_pending_marker(updates_dir, current_version: str,
                            updated_flag: bool) -> "dict | None":
    """Decide what to tell the user about a previous update attempt.

    Returns {'kind': 'success'|'incomplete', 'target': ...} or None.
    Success clears the marker; incomplete offers recovery + clears too
    (one honest message beats nagging forever).
    """
    marker = read_pending_marker(updates_dir)
    if marker is None:
        return None
    clear_pending_marker(updates_dir)
    target = marker.get("target", "")
    try:
        newer = compare_versions(target, current_version) > 0
    except ValueError:
        newer = False
    if updated_flag:
        return {"kind": "success", "target": target}
    if newer:
        # Running version is still older than the attempted target and no
        # --updated arrived → the previous update did not complete.
        return {"kind": "incomplete", "target": target}
    return None   # marker target == current version: it actually succeeded
