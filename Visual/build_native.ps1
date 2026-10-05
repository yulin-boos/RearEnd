$ErrorActionPreference = 'Stop'
$visualPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $visualPython)) { throw 'Install Visual/.venv first.' }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $visualPython setup_native.py build_ext --inplace
    if ($LASTEXITCODE -ne 0) {
        throw 'C++ build failed. Install Visual Studio C++ desktop development tools and pybind11.'
    }
} finally {
    Pop-Location
}
