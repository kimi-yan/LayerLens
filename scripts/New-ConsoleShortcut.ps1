<#
.SYNOPSIS
    生成带项目 Logo 的「LayerLens 控制台.lnk」双击入口。

.DESCRIPTION
    Windows 资源管理器按文件类型给 .cmd 配图标，单个 .cmd 文件换不了图标。
    这个脚本在项目根目录生成一个快捷方式，图标用 assets\LayerLens.ico，
    双击效果与 scripts\LayerLens 控制台.cmd 完全相同。加 -Desktop 时同时在桌面放一个。
    控制台启动时发现顶层缺快捷方式也会自动调用本脚本补齐。

    项目根目录内的快捷方式用相对路径（脚本、工作目录、图标都相对 .lnk 所在目录解析），
    项目文件夹放在哪里、叫什么名字都能用，这份 .lnk 随仓库分发。
    桌面快捷方式在项目目录之外，相对路径解析不到，记录绝对路径；
    项目搬家后重跑一次本脚本（加 -Desktop）即可修复桌面入口。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\New-ConsoleShortcut.ps1 -Desktop
#>
param([switch]$Desktop)

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
$lnk.IconLocation = 'assets\LayerLens.ico, 0'
$lnk.Description = $description
$lnk.Save()
Write-Host "已生成 $portablePath（相对路径，随仓库可用）"

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
Write-Host '双击快捷方式即可打开控制台。'