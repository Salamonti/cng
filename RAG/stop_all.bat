REM C:\RAG\stop_all.bat
@echo off
echo ========================================
echo Stopping All Servers
echo ========================================
echo.

echo [1/3] Stopping FastAPI Server...
wsl bash -c "pkill -f 'uvicorn server.app:app'"

echo [2/3] Stopping OCR Server...
wsl bash -c "pkill -f 'llama-server.*8090'"

echo [3/3] Stopping LLaMA Server...
wsl bash -c "pkill -f 'llama-server.*8081'"

echo.
echo ========================================
echo All servers stopped
echo ========================================
pause
