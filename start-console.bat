@echo off
rem StarChart console mode (for troubleshooting)
rem PURE ASCII (see start.bat note). Same tray-resident entry as start.bat, but
rem keeps this console window so you can see logs, dependency errors and hotkey
rem registration results. Bilingual header/footer live in msg.ps1.
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key console-head
echo.
python tray_app.py
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key console-exit
pause
