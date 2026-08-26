"""
Golden Music — Async folder scanner.
Two-phase scan: Phase 1 counts files, Phase 2 collects paths.
Reports progress as a percentage (0-100).
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("app.scanner")

from PyQt6.QtCore import QThread, pyqtSignal

from config import AUDIO_EXTS


class FolderScanner(QThread):
    """Scans folders for audio files.
    Signals:
        progress(str, int, int): (current_folder, found_so_far, estimated_total)
        finished_scan(list): list of discovered file paths (sorted)
    """
    progress = pyqtSignal(str, int, int)
    finished_scan = pyqtSignal(list)

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = list(folders)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        # Phase 1: Quick count of total files (fast walk)
        estimated_total = 0
        for folder in self.folders:
            if self._cancel:
                break
            if not folder or not os.path.isdir(folder):
                continue
            try:
                for root, dirs, files in os.walk(folder):
                    if self._cancel:
                        break
                    for fname in files:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in AUDIO_EXTS:
                            estimated_total += 1
            except Exception as e:
                log.warning(f"Scan walk failed on {folder}: {e}")
                continue

        # Phase 2: Collect paths with progress
        found = []
        seen = set()
        for folder in self.folders:
            if self._cancel:
                break
            if not folder or not os.path.isdir(folder):
                continue
            self.progress.emit(folder, len(found), estimated_total)
            try:
                for root, dirs, files in os.walk(folder):
                    if self._cancel:
                        break
                    dirs.sort()
                    files.sort()
                    for fname in files:
                        if self._cancel:
                            break
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in AUDIO_EXTS:
                            fpath = os.path.join(root, fname)
                            if fpath not in seen:
                                seen.add(fpath)
                                found.append(fpath)
                    # Report progress every 50 files or at end of each folder
                    if len(found) % 50 == 0 or len(found) == estimated_total:
                        self.progress.emit(folder, len(found), estimated_total)
            except Exception as e:
                log.warning(f"Scan walk failed on {folder}: {e}")
                continue
            self.progress.emit(folder, len(found), estimated_total)

        found.sort(key=lambda x: os.path.basename(x).lower())
        self.finished_scan.emit(found)
