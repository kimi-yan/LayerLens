<#
.SYNOPSIS
    生成带项目 Logo 的「LayerLens 控制台.lnk」双击入口。

.DESCRIPTION
    Windows 资源管理器按文件类型给 .cmd 配图标，单个 .cmd 文件换不了图标。
    这个脚本在项目根目录生成一个快捷方式，图标用 assets\LayerLens.ico，
    双击效果与 scripts\LayerLens 控制台.cmd 完全相同。加 -Desktop 时同时在桌面放一个。
    控制台启动时发现顶层缺快捷方式也会自动调用本脚本补齐。
    快捷方式记录绝对路径：项目文件夹搬家后重跑一次本脚本即可修复。

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

if (-not (Test-Path -LiteralPath $panel)) { throw "没有找到 $panel" }
if (-not (Test-Path -LiteralPath $ico)) { Write-Warning "没有找到 $ico，快捷方式将使用系统默认图标。" }

$shell = New-Object -ComObject WScript.Shell
$targets = @(Join-Path $root 'LayerLens 控制台.lnk')
if ($Desktop) { $targets += Join-Path ([Environment]::GetFolderPath('Desktop')) 'LayerLens 控制台.lnk' }
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "' + $panel + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico, 0"
    $lnk.Description = '启动、停止和查看 LayerLens 图片拆层服务'
    $lnk.Save()
    Write-Host "已生成 $path"
}
Write-Host '双击快捷方式即可打开控制台；项目文件夹搬家后重跑本脚本修复路径。'