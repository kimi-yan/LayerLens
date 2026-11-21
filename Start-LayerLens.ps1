param([ValidateRange(1, 65535)][int]$Port = 8765)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 wraps redirected native stderr lines in error records, and the
# script-wide 'Stop' preference would abort on uvicorn's INFO log. Native tools run with a
# local 'Continue' preference, stderr passes through as plain text, and exit codes decide.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ }
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    # The distributed shortcut is a relative-path placeholder (and goes stale after a project
    # move); rewrite it to this checkout so the top-level double click opens the console.
    try { & (Join-Path $PSScriptRoot 'scripts\New-ConsoleShortcut.ps1') -IfNeeded *> $null } catch { }
    $backend = Join-Path $PSScriptRoot '.local\backend-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $backend)) {
        Invoke-Native python -m venv .local/backend-venv
    }
    $ErrorActionPreference = 'Continue'
    & $backend -c 'import fastapi, uvicorn, multipart, PIL, psd_tools' 2>$null
    $ready = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if (-not $ready) {
        $env:PIP_CACHE_DIR = Join-Path $PSScriptRoot 'data\cache\pip'
        Invoke-Native $backend -m pip install -r backend/requirements.txt
    }
    if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
        $env:npm_config_cache = Join-Path $PSScriptRoot 'data\cache\npm'
        Invoke-Native npm.cmd --prefix frontend ci --no-audit --no-fund
        Invoke-Native npm.cmd --prefix frontend run build
    }
    # The web app runs without any engine; say so up front instead of letting the first upload fail.
    $installed = @('qwen', 'seethrough') | Where-Object { Test-Path -LiteralPath ".local\$_-venv\Scripts\python.exe" }
    if (-not $installed) {
        Write-Host '还没有安装推理引擎：工作台可以打开、查看和导出已有结果，但不能新建拆层任务。' -ForegroundColor Yellow
        Write-Host '安装方法见 docs/engines.md，例如 .\scripts\setup-engine.ps1 -Engine qwen' -ForegroundColor Yellow
    }
    Write-Host "LayerLens: http://127.0.0.1:$Port/"
    Invoke-Native $backend -m uvicorn backend.app:app --host 127.0.0.1 --port $Port --no-access-log
}
finally {
    Pop-Location
}
