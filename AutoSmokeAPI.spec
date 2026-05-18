# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec. Build with: pyinstaller AutoSmokeAPI.spec --clean --noconfirm
# (or just run build.bat).

import sys
from pathlib import Path

block_cipher = None

ROOT = Path('.').resolve()

datas = [
    (str(ROOT / 'SmokeAPI'), 'SmokeAPI'),
    (str(ROOT / 'logo'), 'logo'),
]

# Pull in CTk's bundled assets/themes/fonts and tkinterdnd2's tkdnd binaries.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
datas += collect_data_files('customtkinter')
datas += collect_data_files('tkinterdnd2')
binaries = collect_dynamic_libs('tkinterdnd2')

a = Analysis(
    ['app.py'],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=['PIL._tkinter_finder'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Trim unused stdlib + third-party packages. Smaller bundle = less
        # surface for AV heuristics to match against.
        'tkinter.test', 'unittest', 'unittest.test',
        'pydoc_data', 'pydoc',
        'distutils', 'lib2to3', 'idlelib', 'turtledemo',
        'test', 'tests',
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'IPython', 'jedi', 'parso',
        'curses',
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
    name='AutoSmokeAPI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-compressed PyInstaller exes trip AV heuristics
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'logo' / 'smokeapilogotransparanticon.ico'),
    version=str(ROOT / 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AutoSmokeAPI',
)
