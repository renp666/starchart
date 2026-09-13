@echo off
rem StarChart stop script — bilingual EN/中文 (UTF-8 + chcp 65001, see start.bat)
rem Matching logic lives in stop.ps1 (separate file). Works regardless of the
rem configured port, and never kills unrelated processes.
chcp 65001 >nul
cd /d %~dp0
echo Looking for StarChart processes ... / 正在查找星图进程 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 2 (
  echo No running StarChart service found. / 未发现正在运行的星图服务。
) else (
  echo StarChart stopped. / 星图已停止。
)
echo.
pause
