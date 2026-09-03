@echo off
rem StarChart stop script
rem Matching logic lives in stop.ps1 (separate file) so this batch file stays
rem pure ASCII with no pipe/quote escaping traps. Works regardless of the
rem configured port, and never kills unrelated processes.
cd /d %~dp0
echo Looking for StarChart processes ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 2 (
  echo No running StarChart service found.
) else (
  echo StarChart stopped.
)
echo.
pause
