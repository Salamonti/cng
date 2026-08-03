$ErrorActionPreference = "Stop"

# Delegates to Python: single implementation in server/core/office_stack_launcher.py
# (see server/core/office_stack_supervisor.py). NSSM can call this script OR run Python directly:
#   Application:  ...\Clinical-Note-Generator\.venv\Scripts\python.exe
#   Arguments:    -m server.core.office_stack_supervisor
#   App directory: ...\Clinical-Note-Generator

$repoRoot = Split-Path $PSScriptRoot -Parent
$cng = Join-Path $repoRoot "Clinical-Note-Generator"
$py = $null
foreach ($c in @(
        (Join-Path $cng ".venv\Scripts\python.exe"),
        (Join-Path $cng "venv\Scripts\python.exe"),
        (Join-Path $cng "cenv\Scripts\python.exe")
    )) {
    if (Test-Path $c) {
        $py = $c
        break
    }
}
if (-not $py) {
    $py = "python"
}

Set-Location $cng
& $py -m server.core.office_stack_supervisor
exit $LASTEXITCODE
