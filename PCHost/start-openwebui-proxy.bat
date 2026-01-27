@echo off
REM Startup script for Open WebUI HTTPS Proxy
REM This creates an HTTPS reverse proxy for Open WebUI on port 8443

echo ========================================
echo   Open WebUI HTTPS Proxy
echo ========================================
echo.
echo Starting HTTPS proxy for Open WebUI...
echo   HTTPS Port: 8443
echo   HTTP Port: 8013 (redirects to HTTPS)
echo   Backend: http://127.0.0.1:8035
echo   Access: https://ieissa.com:8443/
echo.

cd /d "%~dp0"
set NODE_ENV=production

REM Check if port 8443 is already in use
netstat -ano | findstr ":8443" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo WARNING: Port 8443 is already in use!
    echo Please stop the existing service first.
    exit /b 1
)

REM Check if port 8013 is already in use
netstat -ano | findstr ":8013" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo WARNING: Port 8013 is already in use!
    echo Please stop the existing service first.
    exit /b 1
)

REM Check if backend is reachable
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8035/ >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo WARNING: Open WebUI backend at http://127.0.0.1:8035 is not responding!
    echo Make sure the Open WebUI Docker container is running.
    echo.
)

echo Starting proxy server...
echo.
node openwebui-proxy.js

exit /b %ERRORLEVEL%
