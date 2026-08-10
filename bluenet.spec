# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for BlueNet Windows application.
Run:  pyinstaller bluenet.spec
Output: dist/BlueNet/BlueNet.exe  (folder mode)
"""

import os
from PyInstaller.utils.hooks import collect_submodules

project_root = os.path.abspath(os.path.dirname(SPEC))

# ── Hidden imports ────────────────────────────────────────────────────────────
# tkinter sub-modules are not always auto-detected on Windows
hidden = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.scrolledtext",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "tkinter.simpledialog",
    "tkinter.font",
    "sqlite3",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    # bluetooth – imported lazily; stub if PyBluez absent
    "bluetooth",
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(project_root, "windows", "main.py")],
    pathex=[project_root],
    binaries=[],
    datas=[
        # Bundle the sites/ folder so the app has a default home page
        (os.path.join(project_root, "sites"), "sites"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PyBluez may not be installed – exclude gracefully
    excludes=["jnius", "android", "kivy"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

# ── Single-folder EXE (recommended for Tkinter + Pillow) ─────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlueNet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # set to "assets/icon.ico" if you add an icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BlueNet",
)
