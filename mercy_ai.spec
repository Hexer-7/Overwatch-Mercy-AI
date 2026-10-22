# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
# PyInstaller spec for Mercy AI: ONE windowed "Mercy AI.exe", built without OpenCV.
#
#   pyinstaller mercy_ai.spec --distpath dist --workpath build    (or: build.bat)
#
# Without OpenCV
# --------------
# cv2.pyd is more than half of an OpenCV build and cannot be trimmed. Mercy AI
# uses a handful of its operations (colour threshold, bitwise AND, Gaussian
# blur, area resize, PNG decode); mercy_ai/imageops.py and mercy_ai/png.py
# implement them with numpy and the standard library, and are used whenever cv2
# cannot be imported, which is what excluding it here arranges. The numpy blur
# differs from OpenCV's by a few 1/255, which moves the match score by under
# 2e-3, far below the smallest step the dial can express
# (docs/numpy-vs-opencv.html has the comparison).
#
# One file
# --------
# data/ and ui/web/ travel inside the exe and are unpacked to a temporary _MEI
# folder at launch, which the bootloader deletes again on exit; paths.BUNDLE_DIR
# finds them there. config.xml is written next to the exe, and the window's
# WebView2 profile goes into webview_data\ beside it while the window is open.
#
# "-cli" opens the text interface: the exe is windowed, so it opens a console
# of its own (mercy_ai/cli/console.py).

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    (Path(SPECPATH) / "mercy_ai" / "__init__.py").read_text(encoding="utf-8"),
    re.M,
).group(1)
NUMBERS = tuple(int(part) for part in (VERSION.split(".") + ["0", "0", "0"])[:4])
COPYRIGHT = "Copyright (c) 2024-2026 Hexer-7"

# What Windows shows under the exe's Properties > Details.
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Hexer-7"),
                        StringStruct("FileDescription", "Mercy AI"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "Mercy AI"),
                        StringStruct("LegalCopyright", COPYRIGHT),
                        StringStruct("OriginalFilename", "Mercy AI.exe"),
                        StringStruct("ProductName", "Mercy AI"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

EXCLUDES = [
    "cv2",
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "xmlrpc",
    "matplotlib",
    "scipy",
    "pandas",
    "PIL",
    "numpy.testing",
    "setuptools",
    "pip",
]

# "email" and "http" must stay: pywebview's bottle server needs them, and
# excluding them breaks the window in the frozen build only.
DATAS = [
    ("data", "data"),
    ("mercy_ai/ui/web", "ui/web"),
]

a = Analysis(
    ["mercy_ai/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=DATAS,
    hiddenimports=["win32timezone"],
    excludes=EXCLUDES,
    hookspath=[],
    runtime_hooks=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Mercy AI",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    # Ask for administrator rights at launch (a UAC prompt). Overwatch runs
    # elevated, and Windows silently drops key presses sent to it from a
    # program that is not.
    uac_admin=True,
    icon="mercy_ai.ico",
    version=VERSION_INFO,
)
