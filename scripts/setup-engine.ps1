<#
.SYNOPSIS
    安装一个 LayerLens 推理引擎：独立 Python 环境、依赖、上游代码和模型权重。

.DESCRIPTION
    每一步都会先检查，已经完成的步骤直接跳过，中断后重新运行同一条命令即可续上。
    所有文件只写到项目目录内：环境在 .local/，权重和下载缓存在 data/cache/。

.EXAMPLE
    .\scripts\setup-engine.ps1 -Engine qwen
    .\scripts\setup-engine.ps1 -Engine seethrough -SkipLama
#>
param(
    [Parameter(Mandatory)][ValidateSet('qwen', 'seethrough')][string]$Engine,
    # Only create the environment; download weights later.
    [switch]$SkipModels,
    # See-through only: skip the LaMa checkpoint used by the front/back split.
    [switch]$SkipLama
)

$ErrorActionPreference = 'Stop'

# Same wrapper as Start-LayerLens.ps1: native stderr is shown as text and the exit code decides.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ }
    }
    if ($LASTEXITCODE -ne 0) { throw "$FilePath 退出码 $LASTEXITCODE" }
}

function Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Skip([string]$Text) { Write-Host "   $Text" -ForegroundColor DarkGray }

$root = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $root
try {
    $env:UV_CACHE_DIR = Join-Path $root 'data\cache\uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.local\python'
    $env:PYTHONUTF8 = '1'
    $venv = Join-Path $root ".local\$Engine-venv"
    $python = Join-Path $venv 'Scripts\python.exe'
    $requirements = Join-Path $root "engine\adapter\requirements-$Engine.txt"

    Step '检查前置条件'
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Invoke-Native nvidia-smi '--query-gpu=name,memory.total,driver_version' '--format=csv,noheader'
    } else {
        Write-Warning '没有找到 nvidia-smi。安装可以继续，但推理需要 NVIDIA 显卡和驱动。'
    }
    if ($Engine -eq 'seethrough' -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'See-through 需要 Git 拉取上游代码和部分依赖。请先安装 Git for Windows，然后重新运行。'
    }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        $uv = Join-Path $root '.local\tools\bin\uv.exe'
        if (-not (Test-Path -LiteralPath $uv)) {
            $bootstrap = Get-Command python -ErrorAction SilentlyContinue
            if (-not $bootstrap) { throw '需要 uv 来创建 Python 3.12 环境。请先运行 winget install astral-sh.uv，或安装任意版本的 Python 后重试。' }
            Invoke-Native $bootstrap.Source -m pip install --disable-pip-version-check --target .local\tools uv
        }
    }
    Invoke-Native $uv --version

    Step "创建独立环境 .local\$Engine-venv（Python 3.12）"
    if (Test-Path -LiteralPath $python) { Skip '已存在，跳过' }
    else { Invoke-Native $uv venv --python 3.12 $venv }

    if ($Engine -eq 'seethrough') {
        Step '拉取 See-through 上游代码 engine\see-through'
        $commit = & $python -c 'from engine.adapter.catalog import SEETHROUGH_COMMIT as c; print(c)'
        $repo = & $python -c 'from engine.adapter.catalog import SEETHROUGH_REPO as r; print(r)'
        $upstream = Join-Path $root 'engine\see-through'
        if (-not (Test-Path -LiteralPath (Join-Path $upstream '.git'))) {
            Invoke-Native git clone $repo $upstream
            Invoke-Native git -C $upstream -c advice.detachedHead=false checkout $commit
        } else {
            $head = & git -c "safe.directory=$upstream" -C $upstream rev-parse HEAD
            if ($head -ne $commit) { Write-Warning "engine\see-through 当前在 $head，测试过的版本是 $commit。需要时执行 git -C engine\see-through checkout $commit" }
            else { Skip "已在固定版本 $($commit.Substring(0, 7))，跳过" }
        }
    }

    Step '安装依赖（PyTorch CUDA 12.6 与固定版本的推理库）'
    # Reinstall only when the requirement files change; a stamp in the venv records the last install.
    $files = @($requirements)
    if ($Engine -eq 'seethrough') { $files += Join-Path $root 'engine\see-through\requirements.txt' }
    $digest = ($files | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join ''
    $stamp = Join-Path $venv '.layerlens-requirements'
    if ((Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp -Raw).Trim() -eq $digest) { Skip '依赖与固定版本一致，跳过' }
    else {
        $install = @('pip', 'install', '--python', $python, '--extra-index-url', 'https://download.pytorch.org/whl/cu126',
            '--index-strategy', 'unsafe-best-match', '-r', $requirements)
        # See-through's requirements use editable paths relative to its own checkout.
        if ($Engine -eq 'seethrough') { Push-Location -LiteralPath (Join-Path $root 'engine\see-through') }
        try { Invoke-Native $uv @install } finally { if ($Engine -eq 'seethrough') { Pop-Location } }
        Set-Content -LiteralPath $stamp -Value $digest -Encoding ascii
    }

    if ($SkipModels) {
        Step '已按 -SkipModels 跳过权重下载'
    } else {
        Step '下载并校验模型权重（支持断点续传，中断后重新运行即可）'
        if ($Engine -eq 'qwen') {
            Invoke-Native $python -u scripts\engines\download_qwen.py
            Step '生成 NF4 文本编码器（一次性，需要显卡）'
            if (Test-Path -LiteralPath 'data\cache\models\qwen-image-layered-te-nf4\model.safetensors.index.json') { Skip '已存在，跳过' }
            else { Invoke-Native $python -u scripts\engines\quantize_qwen_text_encoder.py }
        } else {
            $arguments = @('-u', 'scripts\engines\download_seethrough.py')
            if ($SkipLama) { $arguments += '--skip-lama' }
            Invoke-Native $python @arguments
        }
    }

    Step '自检'
    if ($SkipModels) { Invoke-Native $python scripts\doctor.py } else { Invoke-Native $python scripts\doctor.py --require $Engine }
    Write-Host "`n安装完成。服务已在运行时，刷新网页即可使用；否则运行 .\Start-LayerLens.ps1。" -ForegroundColor Green
}
finally {
    Pop-Location
}
