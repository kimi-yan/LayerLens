param(
    [Parameter(Mandatory)]
    [ValidateSet(1024, 1280)]
    [int] $Resolution,

    [Parameter(Mandatory)]
    [ValidatePattern('^test_image[1-4]?\.png$')]
    [string] $Sample
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$engine = Join-Path $workspace 'engine\see-through'
$python = Join-Path $workspace '.local\seethrough-venv\Scripts\python.exe'
$profile = Join-Path $PSScriptRoot 'measure_m1.py'
$verify = Join-Path $PSScriptRoot 'verify_m1_psd.py'
$models = Join-Path $workspace 'data\cache\models'
$stem = [System.IO.Path]::GetFileNameWithoutExtension($Sample)
$output = Join-Path $workspace "data\m1\$Resolution-$stem-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path $output | Out-Null
$env:HF_HOME = Join-Path $workspace 'data\cache\hf'
$env:HF_ENDPOINT = 'https://hf-mirror.com'
$env:HF_HUB_OFFLINE = '1'
$env:TORCH_HOME = Join-Path $workspace 'data\cache\torch'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free --format=csv |
    Set-Content -LiteralPath (Join-Path $output 'gpu-before.csv')

Push-Location -LiteralPath $engine
try {
    $cliArgs = @(
        '--measurement', (Join-Path $output 'measurement.json'),
        'inference/scripts/inference_psd.py',
        '--srcp', "common/assets/$Sample",
        '--save_dir', $output,
        '--resolution', $Resolution,
        '--resolution_depth', 768,
        '--inference_steps', 30,
        '--inference_steps_depth', -1,
        '--seed', 42,
        '--repo_id_layerdiff', (Join-Path $models 'layerdiff'),
        '--repo_id_depth', (Join-Path $models 'marigold'),
        '--group_offload', '--save_to_psd'
    )
    Write-Output "M1 output directory $output"
    & $python -u $profile @cliArgs 2>&1 |
        Tee-Object -FilePath (Join-Path $output 'inference.log')
    if ($LASTEXITCODE -ne 0) { throw "Upstream inference exited with code $LASTEXITCODE" }
    & $python $verify (Join-Path $output "$stem.psd") $Resolution
    if ($LASTEXITCODE -ne 0) { throw "PSD validation exited with code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
