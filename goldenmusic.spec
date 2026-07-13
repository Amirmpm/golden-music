# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Golden Music v2.0 (PyQt6, Python 3.14).
"""
import os
from pathlib import Path

import PyQt6
PYQT_DIR = Path(PyQt6.__file__).parent
# PyQt6 plugins are under PyQt6/Qt6/plugins
QT_PLUGINS_DIR = PYQT_DIR / "Qt6" / "plugins"
print(f"PyQt6 dir: {PYQT_DIR}")
print(f"Qt plugins dir: {QT_PLUGINS_DIR}")

datas = [
    ('assets/*.png', 'assets'),
    ('assets/*.ico', 'assets'),
]

# Add Qt6 plugin directories explicitly
for plugin_name in ["mediaservice", "audio", "iconengines", "imageformats",
                     "platforms", "styles", "tls"]:
    plugin_path = str(QT_PLUGINS_DIR / plugin_name)
    if os.path.isdir(plugin_path):
        datas.append((plugin_path + '/*', os.path.join('PyQt6', 'Qt6', 'plugins', plugin_name)))
        print(f"  Including Qt6 plugin: {plugin_name}")

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtSvg',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'settings_dialog',
        'mini_player',
        'mutagen',
        'mutagen.id3',
        'mutagen.flac',
        'mutagen.oggvorbis',
        'mutagen.mp4',
        'mutagen.wave',
        'mutagen.asf',
        'mutagen.mp3',
        'mutagen.apev2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebSockets',
        'PyQt6.QtSql',
        'PyQt6.QtPrintSupport',
        'PyQt6.QtTest',
        'PyQt6.QtXml',
        'PyQt6.QtDBus',
        'PyQt6.QtNetwork',
        'PyQt6.QtOpenGL',
        'PyQt6.QtQml',
        'PyQt6.QtQuick',
        'PyQt6.QtQuickWidgets',
        'PyQt6.QtCharts',
        'PyQt6.QtDataVisualization',
        'PyQt6.QtDesigner',
        'PyQt6.QtHelp',
        'PyQt6.QtLocation',
        'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtPositioning',
        'PyQt6.QtSensors',
        'PyQt6.QtSerialPort',
        'PyQt6.QtBluetooth',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='GoldenMusic',
)
