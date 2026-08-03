$ErrorActionPreference = 'Stop'
$HomeDir = 'C:\Users\NoteGenerator'
$StateDir = Join-Path $HomeDir '.openclaw'
$NodeExe = 'C:\Program Files\nodejs\node.exe'
$OpenClawJs = Join-Path $HomeDir 'AppData\Roaming\npm\node_modules\openclaw\dist\index.js'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $NodeExe
$psi.Arguments = "`"$OpenClawJs`" gateway --port 18789"
$psi.WorkingDirectory = $HomeDir
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.EnvironmentVariables['HOME'] = $HomeDir
$psi.EnvironmentVariables['USERPROFILE'] = $HomeDir
$psi.EnvironmentVariables['OPENCLAW_STATE_DIR'] = $StateDir
$psi.EnvironmentVariables['OPENCLAW_CONFIG_PATH'] = Join-Path $StateDir 'openclaw.json'

$p = New-Object System.Diagnostics.Process
$p.StartInfo = $psi
[void]$p.Start()
Start-Sleep 20
$p.Refresh()
Write-Host "HasExited=$($p.HasExited) ExitCode=$($p.ExitCode)"
if (-not $p.HasExited) {
    try {
        $code = (Invoke-WebRequest 'http://127.0.0.1:18789/health' -UseBasicParsing -TimeoutSec 5).StatusCode
        Write-Host "Health=$code"
    } catch {
        Write-Host "Health failed: $_"
    }
    Stop-Process -Id $p.Id -Force
} else {
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    Write-Host '--- STDOUT ---'
    Write-Host $out
    Write-Host '--- STDERR ---'
    Write-Host $err
}
