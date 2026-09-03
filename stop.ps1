# 星图 StarChart 停止逻辑
# 按入口脚本命令行精确匹配（tray_app.py / server.py），不依赖端口、不误杀其他程序。
# 单独成文件的原因：批处理里内嵌 PowerShell 会被 cmd 的引号/管道转义规则破坏
# （" 转义打乱引号配对后，| 会被 cmd 当成自己的管道把命令拆碎）。
$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
$found = $false
foreach ($p in $procs) {
    $cl = $p.CommandLine
    if ($cl -and ($cl -like '*starchart*tray_app.py*' -or $cl -like '*starchart*server.py*')) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Output ("stopped pid " + $p.ProcessId)
        } catch {
            Write-Output ("pid " + $p.ProcessId + " already gone")
        }
        $found = $true
    }
}
if ($found) { exit 0 } else { exit 2 }
