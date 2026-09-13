@echo off
rem StarChart console mode (for troubleshooting) — bilingual EN/中文
rem Same tray-resident entry as start.bat, but keeps this console window so you
rem can see service logs, dependency errors and hotkey registration results.
rem UTF-8 + chcp 65001 (see start.bat note); Python-side output is code-page
rem immune either way.
chcp 65001 >nul
cd /d %~dp0
echo Console mode: logs print below. Close this window to stop StarChart. / 控制台模式：日志将在下方输出，关闭本窗口即停止星图。
echo.
python tray_app.py
echo.
echo StarChart has exited. / 星图已退出。
pause
