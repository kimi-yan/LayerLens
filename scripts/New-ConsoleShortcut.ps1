<#
.SYNOPSIS
    生成带项目 Logo 的「LayerLens 控制台.lnk」双击入口。

.DESCRIPTION
    Windows 资源管理器按文件类型给 .cmd 配图标，单个 .cmd 文件换不了图标。
    这个脚本在项目根目录生成一个快捷方式，图标用 assets\LayerLens.ico，
    双击效果与 scripts\LayerLens 控制台.cmd 完全相同。加 -Desktop 时同时在桌面放一个。

    启动脚本和工作目录用相对路径，项目文件夹放在哪里都能双击启动。
    图标必须写绝对路径：资源管理器按它自己的工作目录（通常是 System32）解析相对图标路径，
    相对写法找不到文件，快捷方式就会显示成空白或 PowerShell 图标。
    所以仓库里分发的是相对版（-ForRepo 生成），控制台每次启动检查图标路径，
    和当前项目位置不一致（首次克隆、项目搬家）时自动调用本脚本改写成绝对路径，
    并对这个文件设置 git skip-worktree，本机路径不会出现在仓库差异里。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1 -Desktop
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1 -ForRepo
#>
param([switch]$Desktop, [switch]$ForRepo)

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
$lnk = $shell.CreateShortcut($portablePath)
$lnk.TargetPath = $powershell
$lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "scripts\control-panel.ps1"'
$lnk.WorkingDirectory = '.'
$lnk.IconLocation = if ($ForRepo) { 'assets\LayerLens.ico, 0' } else { "$ico, 0" }
$lnk.Description = $description
$lnk.Save()

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($git -and (Test-Path -LiteralPath (Join-Path $root '.git'))) {
    # Keep this machine's absolute icon path out of git status; -ForRepo lifts it to commit the portable copy.
    $flag = if ($ForRepo) { '--no-skip-worktree' } else { '--skip-worktree' }
    & $git.Source -C $root update-index $flag -- 'LayerLens 控制台.lnk' 2>$null | Out-Null
}

if ($ForRepo) {
    Write-Host "已生成 $portablePath（仓库分发版，图标为相对路径，首次启动控制台后自动修正）"
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
