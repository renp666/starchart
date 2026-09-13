@echo off
rem StarChart launcher (tray-resident mode) — bilingual EN/中文
rem Bilingual echo works by switching the console to UTF-8 (chcp 65001) and
rem saving this file as UTF-8 WITHOUT BOM. Do not re-save it as ANSI/GBK:
rem in the wrong code page Chinese text misparses, even line boundaries break
rem and echo text can turn into phantom commands.
rem Python-side feedback (tray_app.py) is immune to the code page either way.
chcp 65001 >nul
cd /d %~dp0
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. / 未检测到 Python。
  echo Please install Python 3.7+ and add it to PATH. / 请安装 Python 3.7+ 并加入 PATH。
  echo.
  pause
  exit /b 1
)
python -c "import pypinyin" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pypinyin ... / 正在安装依赖：pypinyin ...
  pip install pypinyin -q
  if errorlevel 1 echo [warn] pypinyin install failed. Pinyin search disabled, everything else works. / [警告] pypinyin 安装失败，拼音搜索不可用，其余功能正常。
)
python -c "import pystray" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pystray ... / 正在安装依赖：pystray ...
  pip install pystray -q
  if errorlevel 1 echo [warn] pystray install failed. Falling back to no-tray mode, the service still runs. / [警告] pystray 安装失败，将无托盘图标运行，服务不受影响。
)
where pythonw >nul 2>nul
if errorlevel 1 (
  start "" python tray_app.py
) else (
  start "" pythonw tray_app.py
)
echo StarChart is starting in the background. / 星图正在后台启动。
echo   Tray icon: bottom-right corner. Left-click to open, right-click for menu. / 托盘图标在右下角：左键打开界面，右键出菜单。
echo   Global hotkey: Ctrl+Alt+S / 全局热键：Ctrl+Alt+S
echo.
echo To see detailed logs, run start-console.bat instead. / 想看详细日志请改用 start-console.bat。
echo.
timeout /t 5 >nul
