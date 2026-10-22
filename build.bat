@echo off
rem SPDX-License-Identifier: Apache-2.0
rem Copyright (c) 2024-2026 Hexer-7
rem Build Mercy AI from this folder, without OpenCV.
rem Output is one file: dist\Mercy AI.exe, next to this file.
setlocal
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" -m pip install --disable-pip-version-check -q -r requirements-build.txt pyinstaller || goto :fail
"%PY%" -m PyInstaller mercy_ai.spec --noconfirm --distpath dist --workpath build || goto :fail
echo Built dist\Mercy AI.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
