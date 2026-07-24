# -*- mode: python ; coding: utf-8 -*-
#
# Canonical PyInstaller spec for QForge. Used by both `build.sh` (local
# macOS builds) and `.github/workflows/build-release.yml` (macOS/Windows/
# Linux CI builds) so there is exactly one build definition instead of two
# that can drift apart.
import re
import sys
from pathlib import Path

block_cipher = None


def _read_app_version() -> str:
    text = (Path(SPECPATH) / "utils" / "updater.py").read_text()
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find APP_VERSION in utils/updater.py")
    return match.group(1)


APP_VERSION = _read_app_version()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui/*.py', 'ui'),
        ('services/*.py', 'services'),
        ('utils/*.py', 'utils'),
        ('logo.png', '.'),
    ],
    hiddenimports=[
        'pymysql',
        'psycopg2',
        'pandas',
        'numpy',
        'sqlparse',
        'sshtunnel',
        'paramiko',
        'openpyxl',
        'pyarrow',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# EXE() icon= only takes effect on Windows (embeds it in QForge.exe); it's a
# no-op on macOS/Linux, where BUNDLE()'s icon= (below) and the in-app
# QIcon(logo.png) set at runtime are what's actually shown.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QForge',
)

# BUNDLE() (the .app wrapper) is a macOS-only PyInstaller concept. On
# Windows/Linux, COLLECT()'s onedir output above (dist/QForge/) is the final
# artifact — it just gets zipped/tarred by CI instead of DMG'd.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='QForge.app',
        icon='assets/icon.icns',
        bundle_identifier='com.qforge.app',
        version=APP_VERSION,
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
            'LSMinimumSystemVersion': '10.13.0',
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
        },
    )
