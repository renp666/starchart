@echo off
rem StarChart console mode (for troubleshooting)
rem Same tray-resident entry as start.bat, but keeps this console window so you
rem can see service logs, dependency errors and hotkey registration results.
rem Keep this file pure ASCII (see start.bat note). Chinese output comes from
rem Python itself, which is not affected by the console code page.
cd /d %~dp0
python tray_app.py
echo.
pause
