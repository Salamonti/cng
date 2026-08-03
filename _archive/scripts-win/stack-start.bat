@echo off
setlocal

echo === Starting Workstation Stack (Windows) ===

echo [1/3] Starting Cloudflare tunnel...
net start cloudflare >nul 2>&1
timeout /t 5 /nobreak >nul

echo [2/3] Starting OpenClaw...
net start openclaw >nul 2>&1
timeout /t 5 /nobreak >nul

echo [3/3] Starting OfficeStack...
net start OfficeStack >nul 2>&1
timeout /t 5 /nobreak >nul

echo === Verifying ===
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:18789/health' -UseBasicParsing -TimeoutSec 5; Write-Host ('OpenClaw health: ' + $r.StatusCode) } catch { Write-Host ('OpenClaw health: FAIL - ' + $_.Exception.Message) }"
nssm status cloudflare
nssm status openclaw
nssm status OfficeStack

echo === Done ===
pause
endlocal
exit /b 0
