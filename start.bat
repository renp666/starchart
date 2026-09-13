@echo off
rem StarChart launcher (tray-resident mode)
rem This file is intentionally PURE ASCII -- bilingual text lives in msg.ps1
rem (UTF-8 with BOM) and is printed via PowerShell, which decodes it correctly
rem regardless of the console code page. Batch files are decoded with the
rem active code page and multibyte content corrupts parsing (fragments of
rem words executed as commands), so never add non-ASCII text to .bat files.
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key py-missing
  echo.
  pause
  exit /b 1
)

python -c "import pypinyin" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pypinyin ... / Installing: pypinyin ...
  pip install pypinyin -q
  if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key pypinyin-fail
)

python -c "import pystray" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pystray ... / Installing: pystray ...
  pip install pystray -q
  if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key pystray-fail
)

where pythonw >nul 2>nul
if errorlevel 1 (
  start "" python tray_app.py
) else (
  start "" pythonw tray_app.py
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key banner
echo.
timeout /t 5 >nul
