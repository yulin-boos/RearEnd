param([switch]$Reload, [switch]$Lan)
$ErrorActionPreference = 'Stop'
$visualPython = Join-Path $PSScriptRoot 'Visual\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $visualPython)) {
    throw 'Virtual environment missing. Follow README.md to install dependencies first.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $recognitionArgs = @('run.py')
    if ($Reload) { $recognitionArgs += '--reload' }
    if ($Lan) { $recognitionArgs += @('--host', '0.0.0.0') }
    & $visualPython @recognitionArgs
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
