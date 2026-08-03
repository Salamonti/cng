@echo off
setlocal

echo ========================================
echo Windows service stack status
echo ========================================
sc query com.docker.service

echo.
sc query cloudflare

echo.
sc query openclaw

echo.
sc query OfficeStack

echo.
echo Docker containers:
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

endlocal
exit /b 0
