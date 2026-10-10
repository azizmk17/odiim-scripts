param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot $Python
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python interpreter not found: $pythonPath. Create the virtual environment first."
}

Push-Location $projectRoot
try {
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & $pythonPath -m PyInstaller --clean --noconfirm StackLab1D.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    Write-Host "Executable: $projectRoot\dist\StackLab1D\StackLab1D.exe"
} finally {
    Pop-Location
}
