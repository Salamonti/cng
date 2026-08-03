@echo off
setlocal

echo ========================================
echo Stopping Windows service stack
echo ========================================

echo [1/4] Stopping OfficeStack...
sc stop OfficeStack

echo [2/4] Stopping OpenClaw...
sc stop openclaw

echo [3/4] Stopping Cloudflare tunnel service...
sc stop cloudflare

echo [4/4] Leaving Docker Desktop Service running by default.
echo To stop Docker too, run: sc stop com.docker.service

echo.
echo Stack stop commands sent. Use stack-status.bat to confirm state.
endlocal
exit /b 0
