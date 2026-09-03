@echo off
rem StarChart launcher (tray-resident mode)
rem Keep this file pure ASCII: cmd decodes batch files with the ACTIVE code page,
rem which differs between double-click (936) and UTF-8-configured PowerShell (65001).
rem Non-ASCII text here misparses in the "wrong" code page -- even line boundaries
rem break, turning echo text into phantom commands. Chinese feedback is printed by
rem Python itself (tray_app.py), which is immune to the console code page.
cd /d %~dp0
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Please install Python 3.7+ and add it to PATH.
  echo.
  pause
  exit /b 1
)
python -c "import pypinyin" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pypinyin ...
  pip install pypinyin -q
  if errorlevel 1 echo [warn] pypinyin install failed. Pinyin search disabled, everything else works.
)
python -c "import pystray" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pystray ...
  pip install pystray -q
  if errorlevel 1 echo [warn] pystray install failed. Falling back to no-tray mode, the service still runs.
)
where pythonw >nul 2>nul
if errorlevel 1 (
  start "" python tray_app.py
) else (
  start "" pythonw tray_app.py
)
echo StarChart is starting in the background.
echo   Tray icon: bottom-right corner. Left-click to open, right-click for menu.
echo   Global hotkey: Ctrl+Alt+S
echo.
echo To see detailed logs, run start-console.bat instead.
echo.
timeout /t 5 >nul
