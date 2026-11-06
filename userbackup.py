"""
Golden Music — User data backup.

Keeps rotating backups of every file in the user-data directory
(config.json, tag_cache.json, stats.json, ...) so an update, crash or
bad write can never lose more than one session of changes.

Strategy:
  - On every app START: snapshot current files to backups/YYYY-MM-DD_HHMMSS/
  - Keep the newest N (10) snapshots; older ones are pruned
  - On app START: if a data file is missing/corrupt but exists in ANY
    backup, restore it automatically
"""
import json
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("app.backup")

KEEP_SNAPSHOTS = 10
DATA_FILES = ("config.json", "tag_cache.json", "stats.json")


def _backup_root(data_dir: Path) -> Path:
    return data_dir / "backups"


def create_startup_backup(data_dir: Path):
    """Snapshot DATA_FILES into a timestamped folder; prune old ones."""
    try:
        broot = _backup_root(data_dir)
        broot.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        snap = broot / stamp
        snap.mkdir()
        copied = 0
        for name in DATA_FILES:
            src = data_dir / name
            if src.is_file():
                shutil.copy2(src, snap / name)
                copied += 1
        # prune
        snaps = sorted([p for p in broot.iterdir() if p.is_dir()])
        for old in snaps[:-KEEP_SNAPSHOTS]:
            shutil.rmtree(old, ignore_errors=True)
        log.info(f"startup backup: {snap.name} ({copied} files)")
        return snap
    except Exception as e:
        log.warning(f"backup failed: {e}")
        return None


def restore_missing(data_dir: Path):
    """For each DATA_FILES entry that is missing OR unparseable JSON, try to
    recover the newest valid copy from any backup snapshot."""
    restored = []
    for name in DATA_FILES:
        live = data_dir / name
        ok = False
        if live.is_file():
            try:
                json.loads(live.read_text(encoding="utf-8"))
                ok = True
            except Exception:
                ok = False
        if ok:
            continue
        broot = _backup_root(data_dir)
        if not broot.is_dir():
            continue
        # newest snapshot first
        for snap in sorted((p for p in broot.iterdir() if p.is_dir()),
                           reverse=True):
            cand = snap / name
            if cand.is_file():
                try:
                    json.loads(cand.read_text(encoding="utf-8"))  # validate
                    shutil.copy2(cand, live)
                    restored.append(name)
                    log.info(f"restored {name} from {snap.name}")
                    break
                except Exception:
                    continue
    return restored
