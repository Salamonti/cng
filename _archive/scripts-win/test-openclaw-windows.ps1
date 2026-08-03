$env:HOME = 'C:\Users\NoteGenerator'
$env:USERPROFILE = 'C:\Users\NoteGenerator'
$node = 'C:\Program Files\nodejs\node.exe'
$js = 'C:\Users\NoteGenerator\AppData\Roaming\npm\node_modules\openclaw\dist\index.js'

if (-not (Test-Path $js)) { throw "OpenClaw not found at $js" }

$existing = Get-NetTCPConnection -LocalPort 18789 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host 'Port 18789 already in use — skipping start.'
} else {
    Write-Host 'Starting OpenClaw gateway...'
    Start-Process -FilePath $node -ArgumentList "`"$js`" gateway --port 18789" -WorkingDirectory 'C:\Users\NoteGenerator' -WindowStyle Hidden
    Start-Sleep -Seconds 15
}

try {
    $code = (Invoke-WebRequest -Uri 'http://127.0.0.1:18789/health' -UseBasicParsing -TimeoutSec 15).StatusCode
    Write-Host "Health: HTTP $code"
} catch {
    Write-Host "Health failed: $_"
    if (Test-Path 'C:\Users\NoteGenerator\.openclaw\logs') {
        Get-ChildItem 'C:\Users\NoteGenerator\.openclaw\logs' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3 | ForEach-Object { $_.FullName }
    }
}
