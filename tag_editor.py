"""
Golden Music — Real tag editor dialog.

Edits title / artist / album / year / genre and the embedded cover art,
then WRITES the changes to the actual audio file on disk via mutagen.
Supported containers: MP3 (ID3v2), FLAC, MP4/M4A, OGG/Opus/OGA, WMA/ASF.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QHBoxLayout, QFileDialog, QMessageBox, QWidget
)
from PyQt6.QtGui import QPixmap

from config import APP_NAME
from icons import Icon, render_icon, get_dpr
from trackinfo import get_track_info


def wm_picture_blob(data: bytes, mime: str = "image/jpeg",
                    picture_type: int = 3) -> bytes:
    """Pack cover bytes into a WM/Picture attribute payload (WMA/ASF).

    Layout follows the de-facto standard readers (Windows Explorer,
    Windows Media Player, TagLib) all agree on:

        BYTE  picture type (3 = front cover)
        DWORD length of MIME string incl. NUL   (LE)
        ASCII mime + NUL
        UTF-16LE description + NUL-term   (empty description here)
        DWORD length of image data              (LE)
        raw image bytes

    The previous implementation chained an int into a bytes
    concatenation, raising TypeError on every invocation - adding
    covers to WMA files never worked at all.
    """
    mime_b = mime.encode("latin-1", "replace")
    desc_term = b"\x00\x00"  # empty UTF-16LE description, NUL-terminated
    return (
        bytes([picture_type & 0xFF])
        + (len(mime_b) + 1).to_bytes(4, "little")
        + mime_b + b"\x00"
        + desc_term
        + len(data).to_bytes(4, "little")
        + data
    )


class TagEditorDialog(QDialog):
    """Edit and persist real file metadata + cover art."""

    def __init__(self, filepath, theme, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.theme = theme
        self.setWindowTitle(f"{APP_NAME} — Edit Tags")
        self.setModal(True)
        self.setMinimumWidth(460)
        info = get_track_info(filepath)
        self._cover_data = None      # new cover bytes when changed
        self._build_ui(info)
        self._apply_style()

    # ------------------------------------------------------------------
    def _build_ui(self, info):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        top = QHBoxLayout()
        # Cover preview + change button
        cover_col = QVBoxLayout()
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(120, 120)
        self.cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_lbl.setStyleSheet(
            f"border-radius: 12px; border: 1px solid {self.theme['border']};"
            f"background: {self.theme['panel_bg_2']};")
        cover_col.addWidget(self.cover_lbl)
        change_btn = QPushButton("Change Cover...")
        change_btn.clicked.connect(self._pick_cover)
        cover_col.addWidget(change_btn)
        clear_btn = QPushButton("Remove Cover")
        clear_btn.clicked.connect(self._remove_cover)
        cover_col.addWidget(clear_btn)
        top.addLayout(cover_col)

        # Text fields
        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        rows = [("Title", "title"), ("Artist", "artist"),
                ("Album", "album"), ("Year", "year"), ("Genre", "genre")]
        self.edits = {}
        for i, (label, key) in enumerate(rows):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
            edit = QLineEdit(str(info.get(key) or ""))
            edit.setPlaceholderText(label)
            form.addWidget(lab, i, 0)
            form.addWidget(edit, i, 1)
            self.edits[key] = edit
        top.addLayout(form, 1)
        lay.addLayout(top)

        self._load_current_cover()
        self._render_cover_preview()

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save to File")
        save_btn.setObjectName("GoldBtn")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        lay.addLayout(btn_row)

    def _apply_style(self):
        t = self.theme
        self.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text']}; }}
            QLabel {{ color: {t['text']}; }}
            QLineEdit {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 8px;
                padding: 7px 10px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {t['gold']}; }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 7px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {t['border']}; }}
            QPushButton#GoldBtn {{
                background: {t['gold']}; color: {t['window_bg']};
                border: none; font-weight: 600; font-size: 12px;
            }}
        """)

    # ------------------------------------------------------------------
    def _load_current_cover(self):
        import coverart
        try:
            data = coverart.get_cover_bytes(self.filepath)
            self._current_cover_data = data
        except Exception:
            self._current_cover_data = None

    def _render_cover_preview(self):
        from PyQt6.QtCore import QByteArray
        data = self._cover_data if self._cover_data is not None \
            else getattr(self, "_current_cover_data", None)
        pm = QPixmap()
        if data and pm.loadFromData(QByteArray(data)):
            self.cover_lbl.setPixmap(pm.scaled(
                118, 118, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            dpr = get_dpr()
            note = render_icon(Icon.MUSIC_NOTE, 48, self.theme["muted"], dpr)
            self.cover_lbl.setPixmap(note)

    def _pick_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cover image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                self._cover_data = f.read()
            self._render_cover_preview()
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Could not read image:\n{e}")

    def _remove_cover(self):
        self._cover_data = b""   # empty bytes = remove on save
        self._render_cover_preview()

    # ------------------------------------------------------------------
    def _save(self):
        try:
            self._write_tags()
            QMessageBox.information(self, APP_NAME,
                "Tags written to the file successfully.")
            self.accept()
        except Exception as e:
            log_msg = str(e)
            QMessageBox.critical(self, APP_NAME,
                f"Could not write tags:\n{log_msg}\n\n"
                f"(Is the file read-only or in use?)")

    def _write_tags(self):
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, APIC
        from mutagen.flac import FLAC, Picture
        from mutagen.mp4 import MP4
        from mutagen.oggvorbis import OggVorbis
        from mutagen.oggopus import OggOpus
        from mutagen.asf import ASF

        vals = {k: e.text().strip() for k, e in self.edits.items()}
        ext = os.path.splitext(self.filepath)[1].lower()

        if ext == ".mp3":
            tags = ID3(self.filepath)
            enc = 3  # UTF-8
            if vals["title"]:
                tags.setall("TIT2", [TIT2(encoding=enc, text=vals["title"])])
            if vals["artist"]:
                tags.setall("TPE1", [TPE1(encoding=enc, text=vals["artist"])])
            if vals["album"]:
                tags.setall("TALB", [TALB(encoding=enc, text=vals["album"])])
            if vals["year"]:
                tags.setall("TDRC", [TDRC(encoding=enc, text=vals["year"])])
            if vals["genre"]:
                tags.setall("TCON", [TCON(encoding=enc, text=vals["genre"])])
            if self._cover_data == b"":
                tags.delall("APIC")
            elif self._cover_data:
                tags.delall("APIC")
                tags.add(APIC(encoding=enc, mime="image/jpeg", type=3,
                              desc="Cover", data=self._cover_data))
            tags.save(self.filepath, v2_version=3)
            return

        if ext == ".flac":
            audio = FLAC(self.filepath)
            for k, vkey in (("title", "title"), ("artist", "artist"),
                            ("album", "album"), ("date", "year"),
                            ("genre", "genre")):
                v = vals[vkey]
                if v:
                    audio[k] = v
            if self._cover_data == b"":
                audio.clear_pictures()
            elif self._cover_data:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.data = self._cover_data
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()
            return

        if ext in (".m4a", ".mp4"):
            audio = MP4(self.filepath)
            map_ = {"title": "\xa9nam", "artist": "\xa9ART",
                    "album": "\xa9alb", "year": "\xa9day", "genre": "\xa9gen"}
            for k, atom in map_.items():
                v = vals[k]
                if v:
                    audio[atom] = [v]
            if self._cover_data == b"":
                audio.pop("covr", None)
            elif self._cover_data:
                audio["covr"] = [self._cover_data]
            audio.save()
            return

        if ext in (".ogg", ".oga", ".opus"):
            audio = OggVorbis(self.filepath) if ext != ".opus" else OggOpus(self.filepath)
            for k, vkey in (("title", "title"), ("artist", "artist"),
                            ("album", "album"), ("date", "year"),
                            ("genre", "genre")):
                v = vals[vkey]
                if v:
                    audio[k] = [v]
            if self._cover_data == b"":
                audio.pop("metadata_block_picture", None)
            elif self._cover_data:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.data = self._cover_data
                import base64
                audio["metadata_block_picture"] = [
                    base64.b64encode(pic.write()).decode("ascii")]
            audio.save()
            return

        if ext == ".wma":
            audio = ASF(self.filepath)
            map_ = {"Title": "title", "Author": "artist",
                    "WM/AlbumTitle": "album", "WM/Year": "year",
                    "WM/Genre": "genre"}
            for attr, vkey in map_.items():
                v = vals[vkey]
                if v:
                    audio[attr] = v
            if self._cover_data == b"":
                audio.pop("WM/Picture", None)
            elif self._cover_data:
                from mutagen.asf import ASFByteArrayAttribute
                audio["WM/Picture"] = ASFByteArrayAttribute(
                    wm_picture_blob(self._cover_data))
            audio.save()
            return

        raise ValueError(f"Unsupported format: {ext}")
