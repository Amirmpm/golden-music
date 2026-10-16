"""
Updater engine regression suite — pure logic with an injected fake fetcher.

Covers: version comparison (newer/same/older/prerelease/malformed),
HTTP statuses (200/304/403/429/offline), asset selection + SHA-256
(sidecar and API digest), download (hash mismatch, truncated body,
cancelled, size cap), redirect guard, install-mode detection and
skip/snooze/pending-marker persistence semantics. NO network, NO Qt UI.

Run:  python scripts/test_updater.py
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import testenv as _testenv
_testenv.install()
del _testenv

import updater
from updater import (
    UpdaterError, compare_versions, normalize_tag, parse_release,
    pick_asset, check_for_update, verify_sha256, download_installer,
    parse_sha256_text, detect_install_mode, evaluate_pending_marker,
    write_pending_marker, read_pending_marker, clear_pending_marker,
    clean_stale_downloads,
)
from update_dialog import notes_to_plain_text

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ->  {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Fake fetcher
# ---------------------------------------------------------------------------
class FakeResp(io.IOBase):
    def __init__(self, body=b"", status=200, headers=None, chunks=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self._chunks = chunks      # list of chunk sizes to simulate truncation

    def read(self, n=-1):
        if self._chunks:
            take = min(self._chunks[0], n if n > 0 else self._chunks[0])
            self._chunks = self._chunks[1:] if len(self._chunks) > 1 else []
            return b"x" * take if take else b""
        if self.body:
            out, self.body = self.body, b""
            return out
        return b""

    def geturl(self):
        return "https://github.com/fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RedirectedResp(FakeResp):
    """Response pretending the transfer came from a disallowed host — the
    guard raises before reading, simulated by raising in open_stream."""

    pass


class FakeFetcher:
    """Scriptable fetcher: urls -> FakeResp | Exception."""

    def __init__(self):
        self.gets = {}
        self.streams = {}
        self.log = []

    def add_get(self, url, resp):
        self.gets[url] = resp

    def add_stream(self, url, resp):
        self.streams[url] = resp

    def get(self, url, headers=None, timeout=15):
        self.log.append(("get", url, dict(headers or {})))
        r = self.gets.get(url)
        if isinstance(r, Exception):
            raise r
        return r

    def open_stream(self, url, timeout=15):
        self.log.append(("stream", url, {}))
        r = self.streams.get(url)
        if isinstance(r, Exception):
            raise r
        return r


API = updater.RELEASES_API_URL

# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------
check("compare: newer", compare_versions("2.2.0", "2.1.1") == 1)
check("compare: older", compare_versions("2.0.9", "2.1.0") == -1)
check("compare: equal padded", compare_versions("2.2", "2.2.0") == 0)
check("compare: numeric not string (2.10 > 2.9)",
      compare_versions("2.10.0", "2.9.0") == 1)
check("compare: equal", compare_versions("2.2.0", "2.2.0") == 0)
try:
    compare_versions("abc", "2.0.0")
    check("compare: malformed raises ValueError", False)
except ValueError:
    check("compare: malformed raises ValueError", True)

check("normalize: v-prefix stripped", normalize_tag("v2.2.0") == "2.2.0")
check("normalize: uppercase V", normalize_tag("V3.0.1") == "3.0.1")
check("normalize: malformed rejected", normalize_tag("golden-v2") is None)
check("normalize: empty rejected", normalize_tag("") is None)
check("normalize: prerelease rejected", normalize_tag("2.2.0-beta.1") is None)

# ---------------------------------------------------------------------------
# parse_release
# ---------------------------------------------------------------------------
base_payload = {
    "tag_name": "v2.2.0", "name": "2.2.0", "draft": False,
    "prerelease": False, "body": "## Fixed\n- **tray** restore",
    "html_url": "https://github.com/Amirmpm/golden-music/releases/tag/v2.2.0",
    "assets": [
        {"name": "GoldenMusicSetup-2.2.0.exe", "size": 123,
         "browser_download_url": "https://github.com/Amirmpm/golden-music/releases/download/v2.2.0/GoldenMusicSetup-2.2.0.exe",
         "digest": "sha256:" + "a" * 64},
        {"name": "GoldenMusicSetup-2.2.0.exe.sha256", "size": 80,
         "browser_download_url": "https://github.com/Amirmpm/golden-music/releases/download/v2.2.0/GoldenMusicSetup-2.2.0.exe.sha256"},
    ],
}
check("parse: good release", (parse_release(base_payload) or {}).get("version") == "2.2.0")
check("parse: draft ignored", parse_release({**base_payload, "draft": True}) is None)
check("parse: prerelease ignored", parse_release({**base_payload, "prerelease": True}) is None)
check("parse: malformed tag rejected",
      parse_release({**base_payload, "tag_name": "release-2.2"}) is None)
check("parse: non-dict rejected", parse_release([1, 2, 3]) is None)

# ---------------------------------------------------------------------------
# pick_asset
# ---------------------------------------------------------------------------
asset = pick_asset(parse_release(base_payload))
check("asset: installer selected", bool(asset) and asset["name"] == "GoldenMusicSetup-2.2.0.exe")
check("asset: digest captured", asset and asset["sha256_digest"] == "a" * 64)
no_hash = pick_asset(parse_release({**base_payload, "assets": [base_payload["assets"][0].__class__(
    {"name": "GoldenMusicSetup-2.2.0.exe", "size": 1,
     "browser_download_url": "https://github.com/x/y.exe"})]}))
check("asset: no verifiable hash -> refused", no_hash is None)
check("asset: missing installer -> None",
      pick_asset(parse_release({**base_payload, "assets": []})) is None)

# ---------------------------------------------------------------------------
# check_for_update with statuses
# ---------------------------------------------------------------------------
rel_body = json.dumps(base_payload).encode()


def resp(status, body=b"", headers=None):
    return FakeResp(body=body, status=status, headers=headers or {})


f = FakeFetcher()
f.add_get(API, resp(200, rel_body, {"etag": '"abc123"'}))
r = check_for_update(f, "2.1.1")
check("check: update_available for older installed",
      r["status"] == "update_available" and r["release"]["version"] == "2.2.0")
check("check: headers carry UA + Accept + Api-Version",
      f.log[0][2].get("User-Agent") == "GoldenMusic/2.1.1"
      and f.log[0][2].get("Accept") == "application/vnd.github+json"
      and f.log[0][2].get("X-GitHub-Api-Version") == "2022-11-28")

r = check_for_update(f, "2.2.0")
check("check: up_to_date when equal", r["status"] == "up_to_date")
r = check_for_update(f, "3.0.0")
check("check: up_to_date when installed is newer", r["status"] == "up_to_date")

f = FakeFetcher()
f.add_get(API, resp(304))
check("check: 304 -> not_modified",
      check_for_update(f, "2.1.1", etag='"abc123"')["status"] == "not_modified")

f = FakeFetcher()
f.add_get(API, resp(403))
check("check: 403 -> rate_limited", check_for_update(f, "2.1.1")["status"] == "rate_limited")
f = FakeFetcher()
f.add_get(API, resp(429))
check("check: 429 -> rate_limited", check_for_update(f, "2.1.1")["status"] == "rate_limited")

f = FakeFetcher()
f.add_get(API, UpdaterError("offline"))
check("check: offline -> offline", check_for_update(f, "2.1.1")["status"] == "offline")

f = FakeFetcher()
f.add_get(API, resp(500))
check("check: 500 -> error", check_for_update(f, "2.1.1")["status"] == "error")

f = FakeFetcher()
f.add_get(API, resp(200, json.dumps({**base_payload, "tag_name": "v9.9.9-rc1"}).encode()))
check("check: prerelease never offered", check_for_update(f, "2.1.1")["status"] == "up_to_date")

# ---------------------------------------------------------------------------
# SHA-256 + download
# ---------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="gmupd_"))
payload = b"GOLDEN-MUSIC-INSTALLER-BYTES" * 1000
good_hash = hashlib.sha256(payload).hexdigest()
blob = tmp / "GoldenMusicSetup-2.2.0.exe"
blob.write_bytes(payload)

check("sha256: verify ok", verify_sha256(blob, good_hash))
check("sha256: verify mismatch", not verify_sha256(blob, "b" * 64))
check("sha256: bad format refused", not verify_sha256(blob, "zz"))
check("sha256 text: '<hex>  file' parsed",
      parse_sha256_text(f"{good_hash}  GoldenMusicSetup-2.2.0.exe\n") == good_hash)
check("sha256 text: raw hex parsed", parse_sha256_text(good_hash.upper()) == good_hash)
check("sha256 text: garbage rejected", parse_sha256_text("nope") is None)

dl_dir = tmp / "downloads"
url = base_payload["assets"][0]["browser_download_url"]
sha_url = base_payload["assets"][1]["browser_download_url"]

# success via sidecar
f = FakeFetcher()
f.add_stream(url, FakeResp(body=payload, headers={"Content-Length": str(len(payload))}))
f.add_get(sha_url, FakeResp(body=f"{good_hash}  {blob.name}\n".encode()))
out = download_installer(f, asset, dl_dir)
check("download: success returns verified file",
      out.is_file() and out.read_bytes() == payload)
check("download: no .part leftovers", not (dl_dir / (blob.name + ".part")).exists())

# hash mismatch -> nothing installed
f = FakeFetcher()
f.add_stream(url, FakeResp(body=b"TAMPERED" * 500))
f.add_get(sha_url, FakeResp(body=f"{good_hash}\n".encode()))
try:
    download_installer(f, asset, dl_dir)
    check("download: hash mismatch aborts", False)
except UpdaterError as e:
    check("download: hash mismatch aborts", "SHA-256" in str(e))
check("download: failed file not left behind",
      not any(p.name.endswith(".part") for p in dl_dir.iterdir())
      and sorted(p.name for p in dl_dir.iterdir()) == [blob.name])

# truncated stream (fewer bytes than promised)
f = FakeFetcher()
f.add_stream(url, FakeResp(body=None, chunks=[100, 100]))   # 200 bytes then EOF
f.add_get(sha_url, FakeResp(body=f"{good_hash}\n".encode()))
try:
    download_installer(f, asset, dl_dir)
    check("download: truncated body fails verification", False)
except UpdaterError:
    check("download: truncated body fails verification", True)

# cancelled
f = FakeFetcher()
f.add_stream(url, FakeResp(body=payload))
cancel = threading.Event()
cancel.set()
try:
    download_installer(f, asset, dl_dir, cancel=cancel)
    check("download: cancel aborts", False)
except UpdaterError as e:
    check("download: cancel aborts", "cancelled" in str(e).lower())

# size cap
f = FakeFetcher()
f.add_stream(url, FakeResp(body=payload))
try:
    download_installer(f, asset, dl_dir, max_bytes=10)
    check("download: size cap enforced", False)
except UpdaterError:
    check("download: size cap enforced", True)

# unexpected asset name
try:
    download_installer(FakeFetcher(), {**asset, "name": "../evil.exe"}, dl_dir)
    check("download: unexpected asset name refused", False)
except UpdaterError:
    check("download: unexpected asset name refused", True)

# ---------------------------------------------------------------------------
# Redirect guard (URL validation) — real HttpFetcher validation, no network:
# the scheme/host check runs BEFORE any socket is opened.
# ---------------------------------------------------------------------------
hf = updater.HttpFetcher()
for bad in ("http://github.com/x", "https://evil.example.com/payload.exe",
            "ftp://github.com/x", "file:///etc/passwd"):
    try:
        hf.get(bad)
        check(f"guard: {bad} blocked", False)
        break
    except UpdaterError:
        check(f"guard: {bad} blocked", True)
    except Exception as e:   # anything else = the guard didn't fire first
        check(f"guard: {bad} blocked", False, f"wrong error: {e}")
        break

# ---------------------------------------------------------------------------
# Install mode (source here — sandboxed, never frozen)
# ---------------------------------------------------------------------------
check("mode: running from source", detect_install_mode() == "source")

# ---------------------------------------------------------------------------
# Pending marker lifecycle
# ---------------------------------------------------------------------------
mdir = tmp / "marker"
write_pending_marker(mdir, "2.2.0")
check("marker: written + read", (read_pending_marker(mdir) or {}).get("target") == "2.2.0")
out = evaluate_pending_marker(mdir, "2.1.1", updated_flag=False)
check("marker: incomplete detected when still older", out and out["kind"] == "incomplete")
check("marker: consumed (cleared) after evaluation", read_pending_marker(mdir) is None)
write_pending_marker(mdir, "2.2.0")
out = evaluate_pending_marker(mdir, "2.2.0", updated_flag=True)
check("marker: success when --updated received", out and out["kind"] == "success")
check("marker: cleared", read_pending_marker(mdir) is None)
write_pending_marker(mdir, "2.2.0")
out = evaluate_pending_marker(mdir, "2.2.0", updated_flag=False)
check("marker: no nag when target == current", out is None)
clear_pending_marker(mdir)

# stale .part cleanup
(dl_dir / "GoldenMusicSetup-2.2.0.exe.part").write_bytes(b"junk")
clean_stale_downloads(dl_dir)
check("cleanup: .part files removed",
      not any(p.name.endswith(".part") for p in dl_dir.iterdir()))

# ---------------------------------------------------------------------------
# Notes sanitization
# ---------------------------------------------------------------------------
md = "## Fixed\n- **tray** restore [link](https://x.y) ![img](https://x.y/i.png)\n`code`"
plain = notes_to_plain_text(md)
check("notes: markdown stripped", "##" not in plain and "**" not in plain
      and "](http" not in plain and "```" not in plain)
check("notes: text preserved", "tray restore" in plain and "code" in plain)

failed = sum(1 for ok_, *_ in results if not ok_)
print(f"\nupdater: {len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
