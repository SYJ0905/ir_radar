# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for iRadar — 360° proximity radar overlay for iRacing."""

import os
import sys

block_cipher = None
root = os.path.abspath(os.path.dirname(SPEC))

a = Analysis(
    [os.path.join(root, 'main.py')],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=[
        'server',
        'server.config',
        'server.telemetry',
        'server.mock_data',

        'server.radar_calc',
        'server.overlay',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='iRadar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # windowed mode (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,            # add icon path here if desired
)
