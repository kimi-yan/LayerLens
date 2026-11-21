# Sequential Qwen-Image-Layered gradient benchmark on the local GPU.
# Each run writes output/qwen-bench/<name>/measurement.json; the summary goes to output/qwen-bench/summary.tsv.
$ErrorActionPreference = 'Continue'
Push-Location -LiteralPath (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
$env:HF_HUB_OFFLINE = '1'; $env:PYTHONUTF8 = '1'; $env:HF_HUB_DISABLE_PROGRESS_BARS = '1'
$python = '.local\qwen-venv\Scripts\python.exe'
$runs = @(
    @('b2-640-l4-s50-perfume', 'data/samples/product/perfume-glass.png', '--layers', '4', '--steps', '50'),
    @('b3-640-l4-s50-coffee', 'data/samples/product/coffee-poster.png', '--layers', '4', '--steps', '50'),
    @('b4-640-l4-s30-coffee', 'data/samples/product/coffee-poster.png', '--layers', '4', '--steps', '30'),
    @('b5-640-l4-s20-coffee', 'data/samples/product/coffee-poster.png', '--layers', '4', '--steps', '20'),
    @('b6-640-l4-s20-coffee-lowmem', 'data/samples/product/coffee-poster.png', '--layers', '4', '--steps', '20', '--low-cpu-mem'),
    @('b7-640-l3-s30-character', 'data/samples/character/seethrough-test_image.png', '--layers', '3', '--steps', '30'),
    @('b8-640-l6-s30-kitchen', 'data/samples/product/bottle-kitchen.png', '--layers', '6', '--steps', '30'),
    @('b9-1024-l4-s10-perfume', 'data/samples/product/perfume-glass.png', '--layers', '4', '--steps', '10', '--resolution', '1024')
)
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
