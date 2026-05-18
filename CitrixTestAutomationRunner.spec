# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


block_cipher = None
root = Path.cwd()
python_home = Path(sys.base_prefix)
tcl_root = python_home / "tcl"
dll_root = python_home / "DLLs"
tkinter_root = python_home / "Lib" / "tkinter"


a = Analysis(
    ["run_app.py"],
    pathex=[str(root / "src")],
    binaries=[
        (str(dll_root / "_tkinter.pyd"), "."),
        (str(dll_root / "tcl86t.dll"), "."),
        (str(dll_root / "tk86t.dll"), "."),
    ],
    datas=[
        ("version.txt", "."),
        (str(tcl_root / "tcl8.6"), "_tcl_data"),
        (str(tcl_root / "tk8.6"), "_tk_data"),
        (str(tkinter_root), "tkinter"),
    ],
    hiddenimports=[
        "_tkinter",
        "tkinter",
        "PIL._tkinter_finder",
        "pyautogui",
        "pygetwindow",
        "win32clipboard",
        "docx",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "pyi_tk_runtime.py")],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "pygame",
        "cv2",
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
    name="CitrixTestAutomationRunner",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CitrixTestAutomationRunner",
)
