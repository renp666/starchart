# StarChart bilingual launcher messages (EN / 中文)
# MUST be saved as UTF-8 WITH BOM so Windows PowerShell 5.1 decodes it correctly.
# Called from the .bat files (which stay pure ASCII): powershell -File msg.ps1 -Key <key>
param([string]$Key = "")

switch ($Key) {
    "py-missing" {
        Write-Host "Python not found. / 未检测到 Python。" -ForegroundColor Yellow
        Write-Host "Install Python 3.7+ and add it to PATH, then retry. / 请安装 Python 3.7+ 并加入 PATH 后重试。" -ForegroundColor Yellow
    }
    "pypinyin-fail" {
        Write-Host "[warn] pypinyin install failed: pinyin search disabled, everything else works. / pypinyin 安装失败：拼音搜索不可用，其余功能正常。" -ForegroundColor Yellow
    }
    "pystray-fail" {
        Write-Host "[warn] pystray install failed: no tray icon, the service still runs. / pystray 安装失败：无托盘图标，服务正常运行。" -ForegroundColor Yellow
    }
    "banner" {
        Write-Host "StarChart is starting in the background. / 星图正在后台启动。"
        Write-Host "  Tray icon: bottom-right. Left-click to open, right-click for menu. / 托盘图标在右下角：左键打开界面，右键出菜单。"
        Write-Host "  Global hotkey: Ctrl+Alt+S / 全局热键：Ctrl+Alt+S"
        Write-Host "  Detailed logs: run start-console.bat / 查看详细日志请运行 start-console.bat"
    }
    "stop-none" {
        Write-Host "No running StarChart service found. / 未发现正在运行的星图服务。"
    }
    "stop-ok" {
        Write-Host "StarChart stopped. / 星图已停止。"
    }
    "console-head" {
        Write-Host "Console mode: logs print below. Close this window to stop StarChart. / 控制台模式：日志在下方输出，关闭本窗口即停止星图。"
    }
    "console-exit" {
        Write-Host "StarChart has exited. / 星图已退出。"
    }
    default {
        Write-Host "StarChart"
    }
}
