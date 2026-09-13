@echo off
rem StarChart stop script
rem PURE ASCII (see start.bat note). Bilingual output lives in msg.ps1.
rem Matching logic lives in stop.ps1. Works regardless of the configured port,
rem and never kills unrelated processes.
cd /d %~dp0
echo Looking for StarChart processes ... / Looking for StarChart processes ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 2 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key stop-none
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0msg.ps1" -Key stop-ok
)
echo.
pause
