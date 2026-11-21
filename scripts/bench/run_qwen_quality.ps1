# Quantization, step-count and offload comparison for the 2026-11-21 Qwen review.
# Waits for the LayerLens queue to drain, then runs each case sequentially on the GPU.
$ErrorActionPreference = 'Continue'
Push-Location -LiteralPath (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
$env:HF_HUB_OFFLINE = '1'; $env:PYTHONUTF8 = '1'; $env:HF_HUB_DISABLE_PROGRESS_BARS = '1'
$python = '.local\qwen-venv\Scripts\python.exe'
$robot = '.tmpfiles/qa/robot.jpg'
$runs = @(
    @('t1-robot-q4-s6-g1', $robot, '--layers', '5', '--steps', '6', '--low-cpu-mem'),
    @('t2-robot-q4-s6-g1-pinned', $robot, '--layers', '5', '--steps', '6'),
    @('t3-robot-q4-s6-g4', $robot, '--layers', '5', '--steps', '6', '--low-cpu-mem', '--blocks-per-group', '4'),
    @('t4-robot-q8-s6-g1', $robot, '--layers', '5', '--steps', '6', '--low-cpu-mem', '--quant', 'Q8_0'),
    @('q6-robot-l5-s30', $robot, '--layers', '5', '--steps', '30', '--low-cpu-mem', '--quant', 'Q6_K'),
    @('q8-robot-l5-s30', $robot, '--layers', '5', '--steps', '30', '--low-cpu-mem', '--quant', 'Q8_0'),
    @('q4-robot-l5-s50', $robot, '--layers', '5', '--steps', '50', '--low-cpu-mem'),
    @('q4-robot-l6-s30', $robot, '--layers', '6', '--steps', '30', '--low-cpu-mem')
)
while ((Invoke-RestMethod 'http://127.0.0.1:8765/api/v1/jobs') | Where-Object { $_.status -in 'queued', 'running' }) { Start-Sleep -Seconds 20 }
foreach ($run in $runs) {
    $name = $run[0]
    $out = "output/qwen-bench/$name"
    if (Test-Path "$out/measurement.json") { continue }
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
    $arguments = @('scripts/bench/measure_qwen.py', '--image', $run[1], '--out', $out) + $run[2..($run.Count - 1)]
    "START $name $(Get-Date -Format s)"
    & $python @arguments 2>&1 | ForEach-Object { "$_" } | Select-String -Pattern 'MEASUREMENT|Error|Traceback' | ForEach-Object { "$name $_" }
    & $python scripts/bench/qwen_contact_sheet.py $out 2>&1 | Out-Null
    "END $name exit=$LASTEXITCODE $(Get-Date -Format s)"
}
Pop-Location
