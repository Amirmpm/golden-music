# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Golden Music — optimized single-folder build.
Compression-maxed, all unused Qt modules excluded, all features intact.
"""
import os
from pathlib import Path

import PyQt6
PYQT_DIR = Path(PyQt6.__file__).parent
QT_PLUGINS_DIR = PYQT_DIR / "Qt6" / "plugins"

datas = [
    ('assets/icon.png', 'assets'),
    ('assets/logo.png', 'assets'),
    ('assets/icon.ico', 'assets'),
    ('assets/icons', 'assets/icons'),  # Lucide SVG icon set (loaded at runtime)
]

# Only the Qt plugins actually exercised by the app:
# mediaservice/audio (playback), platforms (window), imageformats+iconengines
# (covers/SVG icons), styles (Fusion), tls (not used but tiny and harmless).
for plugin_name in ["mediaservice", "audio", "platforms", "imageformats",
                     "iconengines", "styles"]:
    plugin_path = str(QT_PLUGINS_DIR / plugin_name)
    if os.path.isdir(plugin_path):
        datas.append((plugin_path + '/*', os.path.join('PyQt6', 'Qt6', 'plugins', plugin_name)))

a = Analysis(
    ['main.pyw'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtSvg',
        'mutagen',
        'mutagen.id3',
        'mutagen.flac',
        'mutagen.oggvorbis',
        'mutagen.oggopus',
        'mutagen.mp4',
        'mutagen.asf',
        'mutagen.mp3',
        'mutagen.apev2',
        'mutagen.wave',
        # Windows single-instance + media keys
        'win32api',
        'win32gui',
        'win32con',
        'win32event',
        # our own late-imported modules (imported inside functions)
        'applog',
        'lrc_cache',
        'lyrics_panel',
        'fullscreen_player',
        'mediakeys',
        'trackinfo',
        'track_info_dialog',
        'playlist_dialog',
        'duplicates',
        'duplicates_dialog',
    ],
    excludes=[
        # Web/network stacks the app never touches (QtNetwork kept: sip lazily
        # references types from it inside QtMultimedia)
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
        'PyQt6.QtWebSockets', 'PyQt6.QtNetworkAuth',
        'PyQt6.QtHttpServer',
        # Data/GUI stacks not used
        'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtXml', 'PyQt6.QtDBus',
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets', 'PyQt6.QtQuick3D',
        'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.QtGraphs',
        'PyQt6.QtDesigner', 'PyQt6.QtHelp', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
        'PyQt6.QtPositioning', 'PyQt6.QtSensors', 'PyQt6.QtSerialPort',
        'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtRemoteObjects',
        'PyQt6.QtScxml', 'PyQt6.QtStateMachine', 'PyQt6.QtTextToSpeech',
        'PyQt6.QtVirtualKeyboard', 'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
        'PyQt6.QtMultimediaWidgets', 'PyQt6.QtLocation', 'PyQt6.QtSpatialAudio',
        'PyQt6.QtGraphicalEffects', 'PyQt6.QtUml',
        # Python stdlib areas unused by a GUI app
        'tkinter', '_tkinter', 'unittest', 'pydoc_data', 'lib2to3',
        'curses', 'sqlite3', 'distutils', 'setuptools', 'pip',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GoldenMusic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        "*.pyd",
        "api-ms-win-*", "ucrtbase.dll", "kernel32.dll", "vcruntime140*.dll",
        "python3*.dll", "Qt6*.dll",
        "qwindows.dll", "qwindowsvistastyle.dll", "ffmpegmediaplugin.dll",
        "windowsmediaplugin.dll", "wmfmediaplugin.dll",
    ],
    name='GoldenMusic',
)
