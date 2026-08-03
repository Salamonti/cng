<#
.SYNOPSIS
    Native Windows OpenClaw gateway service (no WSL).
    Used by NSSM service "openclaw".
#>
param(
    [int]$Port = 18789
)

$ErrorActionPreference = 'Stop'

$LogDir = 'C:\project-root\startup\logs'
$ServiceLog = Join-Path $LogDir 'openclaw-windows-service.log'
$StdoutLog = Join-Path $LogDir 'openclaw-windows.stdout.log'
$StderrLog = Join-Path $LogDir 'openclaw-windows.stderr.log'
$HealthUrl = "http://127.0.0.1:$Port/health"

$HomeDir = 'C:\Users\NoteGenerator'
$StateDir = Join-Path $HomeDir '.openclaw'
$ConfigPath = Join-Path $StateDir 'openclaw.json'
$NodeExe = 'C:\Program Files\nodejs\node.exe'
$OpenClawJs = Join-Path $HomeDir 'AppData\Roaming\npm\node_modules\openclaw\dist\index.js'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$ts  $Message"
    Add-Content -Path $ServiceLog -Value $line
    Write-Host $line
}

function Wait-ForHttpHealth {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 180
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0
    do {
        $attempt++
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return }
        } catch {
            if ($attempt % 5 -eq 0) {
                Write-Log "Health check attempt $attempt still waiting on $Url"
            }
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Health check failed at $Url within $TimeoutSeconds seconds."
}

if (-not (Test-Path $OpenClawJs)) {
    throw "OpenClaw not installed at $OpenClawJs. Run: npm install -g openclaw@2026.6.5"
}
if (-not (Test-Path $ConfigPath)) {
    throw "OpenClaw config missing at $ConfigPath"
}

Write-Log "=== OpenClaw Windows gateway starting on port $Port (user=$env:USERNAME) ==="

$portOwner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -First 1
if ($portOwner) {
    $ownerProc = Get-Process -Id $portOwner -ErrorAction SilentlyContinue
    if ($ownerProc -and $ownerProc.ProcessName -eq 'node') {
        Write-Log "Reclaiming port $Port from stale node PID $portOwner."
        Stop-Process -Id $portOwner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } else {
        $ownerName = if ($ownerProc) { $ownerProc.ProcessName } else { 'unknown' }
        throw "Port $Port already in use by PID $portOwner ($ownerName)."
    }
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $NodeExe
$psi.Arguments = "`"$OpenClawJs`" gateway --port $Port"
$psi.WorkingDirectory = $HomeDir
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true

$envVars = @{
    HOME = $HomeDir
    USERPROFILE = $HomeDir
    OPENCLAW_STATE_DIR = $StateDir
    OPENCLAW_CONFIG_PATH = $ConfigPath
    OPENCLAW_GATEWAY_PORT = "$Port"
    OPENCLAW_SERVICE_MARKER = 'openclaw'
    OPENCLAW_SERVICE_KIND = 'gateway'
    OPENCLAW_SERVICE_VERSION = '2026.6.5'
    OPENCLAW_WINDOWS_TASK_NAME = 'OpenClaw Gateway'
}
foreach ($key in $envVars.Keys) {
    $psi.EnvironmentVariables[$key] = $envVars[$key]
}

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.add_OutputDataReceived({ param($s, $e) if ($e.Data) { $e.Data | Out-File -FilePath $StdoutLog -Append -Encoding utf8 } })
$proc.add_ErrorDataReceived({ param($s, $e) if ($e.Data) { $e.Data | Out-File -FilePath $StderrLog -Append -Encoding utf8 } })

if (-not $proc.Start()) {
    throw 'Failed to start OpenClaw node process.'
}
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()
Write-Log "OpenClaw node PID $($proc.Id) started."

try {
    Wait-ForHttpHealth -Url $HealthUrl -TimeoutSeconds 180
    Write-Log "OpenClaw healthy at $HealthUrl - entering monitor loop."
    while ($true) {
        Start-Sleep -Seconds 15
        $proc.Refresh()
        if ($proc.HasExited) {
            throw "OpenClaw exited with code $($proc.ExitCode)."
        }
    }
}
catch {
    Write-Log "ERROR: $_"
    throw
}
finally {
    if (-not $proc.HasExited) {
        Write-Log "Stopping OpenClaw node PID $($proc.Id)."
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
