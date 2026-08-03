$ErrorActionPreference = 'Stop'

$logFile = 'C:\project-root\startup\logs\cloudflared.log'
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content $logFile "$timestamp - $Message"
}

function Wait-ForPort {
    param(
        [string]$Name,
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $listening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        if ($listening) {
            Write-Log "$Name listening on port $Port"
            return
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "$Name did not start on port $Port"
}

function Stop-Cloudflared {
    param([System.Diagnostics.Process]$ProcessRef)
    try {
        if ($ProcessRef -and -not $ProcessRef.HasExited) {
            Stop-Process -Id $ProcessRef.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {}
}

Write-Log 'Cloudflared startup script beginning'

$maxWait = 120
$waited = 0
while ($waited -lt $maxWait) {
    try {
        $null = Resolve-DnsName 'cloudflare.com' -ErrorAction Stop
        Write-Log "DNS ready after ${waited}s"
        break
    }
    catch {
        Start-Sleep -Seconds 5
        $waited += 5
    }
}

if ($waited -ge $maxWait) {
    Write-Log "WARNING: Network not ready after ${maxWait}s, starting anyway"
}

Start-Sleep -Seconds 5

$cloudflared = $null
$cloudflaredStarted = $false
for ($i = 1; $i -le 3; $i++) {
    try {
        Write-Log "Starting Cloudflared (attempt $i)"
        $cloudflared = Start-Process `
            -FilePath 'C:\Cloudflared\bin\cloudflared.exe' `
            -ArgumentList '--config C:\Windows\System32\config\systemprofile\.cloudflared\config.yml tunnel run office-prod' `
            -WorkingDirectory 'C:\Cloudflared\bin' `
            -PassThru `
            -WindowStyle Hidden

        Wait-ForPort -Name 'CloudflaredMetrics' -Port 20241 -TimeoutSeconds 30
        $cloudflaredStarted = $true
        break
    }
    catch {
        Write-Log "Cloudflared startup attempt $i failed: $($_.Exception.Message)"
        Stop-Cloudflared -ProcessRef $cloudflared
        Start-Sleep -Seconds 10
    }
}

if (-not $cloudflaredStarted) {
    Write-Log 'ERROR: Cloudflared failed to start after 3 attempts'
    throw 'Cloudflared failed to start after 3 attempts.'
}

Register-EngineEvent PowerShell.Exiting -Action { Stop-Cloudflared -ProcessRef $cloudflared } | Out-Null
Write-Log 'Cloudflared started successfully'

try {
    while ($true) {
        Start-Sleep -Seconds 300
    }
}
finally {
    Write-Log 'Cloudflared stopping'
    Stop-Cloudflared -ProcessRef $cloudflared
}
