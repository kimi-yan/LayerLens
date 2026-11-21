<#
.SYNOPSIS
    生成带项目 Logo 的「LayerLens 控制台.lnk」双击入口。

.DESCRIPTION
    Windows 资源管理器按文件类型给 .cmd 配图标，单个 .cmd 文件换不了图标。
    这个脚本在项目根目录生成一个快捷方式，图标用 assets\LayerLens.ico，
    双击效果与 scripts\LayerLens 控制台.cmd 完全相同。加 -Desktop 时同时在桌面放一个。

    启动脚本、工作目录和图标都必须写绝对路径：双击快捷方式时，资源管理器不会按快捷方式
    所在文件夹解析相对路径（工作目录通常是 System32），相对的 -File 参数找不到脚本，
    PowerShell 会静默退出，表现为双击没反应；相对图标路径则显示成空白或 PowerShell 图标。
    所以仓库里分发的是占位版（-ForRepo 生成，相对路径），第一次通过 Start-LayerLens.ps1、
    scripts\LayerLens 控制台.cmd 或控制台启动时，会自动调用本脚本改写成当前项目的绝对路径，
    并对这个文件设置 git skip-worktree，本机路径不会出现在仓库差异里。
    项目搬家后双击失效时，重跑本脚本即可修复。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1 -Desktop
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1 -ForRepo
#>
param([switch]$Desktop, [switch]$ForRepo, [switch]$IfNeeded)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ico = Join-Path $root 'assets\LayerLens.ico'
$panel = Join-Path $root 'scripts\control-panel.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$description = '启动、停止和查看 LayerLens 图片拆层服务'

if (-not (Test-Path -LiteralPath $panel)) { throw "没有找到 $panel" }
if (-not (Test-Path -LiteralPath $ico)) { Write-Warning "没有找到 $ico，快捷方式将使用系统默认图标。" }

$shell = New-Object -ComObject WScript.Shell
$portablePath = Join-Path $root 'LayerLens 控制台.lnk'

# -IfNeeded: skip when the shortcut already launches this checkout with the right icon, so
# every console start can call this cheaply without touching the file when nothing changed.
if ($IfNeeded) {
    $ok = $false
    if (Test-Path -LiteralPath $portablePath) {
        try {
            $old = $shell.CreateShortcut($portablePath)
            $ok = ($old.WorkingDirectory -eq $root) -and
                  ($old.Arguments -like ('*-File "' + $panel + '"*')) -and
                  ($old.IconLocation -eq ($ico + ',0'))
        } catch { }
    }
    if ($ok) { return }
}

$lnk = $shell.CreateShortcut($portablePath)
$lnk.TargetPath = $powershell
if ($ForRepo) {
    # Placeholder for distribution: relative paths cannot survive a double click, so the first
    # console start (Start-LayerLens.ps1 / the inner .cmd / the panel itself) rewrites them.
    $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "scripts\control-panel.ps1"'
    $lnk.WorkingDirectory = '.'
    $lnk.IconLocation = 'assets\LayerLens.ico, 0'
} else {
    $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "' + $panel + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico, 0"
}
$lnk.Description = $description
$lnk.Save()

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($git -and (Test-Path -LiteralPath (Join-Path $root '.git'))) {
    # Keep this machine's absolute icon path out of git status; -ForRepo lifts it to commit the portable copy.
    $flag = if ($ForRepo) { '--no-skip-worktree' } else { '--skip-worktree' }
    & $git.Source -C $root update-index $flag -- 'LayerLens 控制台.lnk' 2>$null | Out-Null
}

if ($ForRepo) {
    Write-Host "已生成 $portablePath（仓库分发版，启动路径和图标为相对占位，首次启动后自动改写为本机绝对路径）"
} else {
    Write-Host "已生成 $portablePath"
}

if ($Desktop) {
    $desktopPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LayerLens 控制台.lnk'
    $lnk = $shell.CreateShortcut($desktopPath)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "' + $panel + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico, 0"
    $lnk.Description = $description
    $lnk.Save()
    Write-Host "已生成 $desktopPath（绝对路径，项目搬家后重跑本脚本修复）"
}

# Explorer caches shortcut icons; tell it the file changed so the logo shows without a restart.
Add-Type -Namespace LayerLens -Name ShellNotify -MemberDefinition '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, IntPtr a, IntPtr b);'
[LayerLens.ShellNotify]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Host '双击快捷方式即可打开控制台。'
