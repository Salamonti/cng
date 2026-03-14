REM start_fastapi_server.bat
@echo off
echo Starting FastAPI Server...
echo ================================

REM Change to the correct directory
cd /d "%~dp0"

REM Check if virtual environment exists
if exist ".venv\Scripts\python.exe" (
    echo Using virtual environment: .venv
    set PYTHON_CMD=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    echo Using virtual environment: venv
    set PYTHON_CMD=venv\Scripts\python.exe
) else (
    echo Using system Python
    set PYTHON_CMD=python
)

REM Check if required files exist
if not exist "server\app.py" (
    echo ERROR: server\app.py not found
    pause
    exit /b 1
)

if not exist "config\config.json" (
    echo ERROR: config\config.json not found
    pause
    exit /b 1
)

echo Starting FastAPI server on port 7860...
echo Server directory: %cd%\server
echo Config: %cd%\config\config.json
echo Web files: %cd%\web
echo.

REM Pin WhisperX/FastAPI to GPU 1 unless already set
if "%CUDA_VISIBLE_DEVICES%"=="" (
    set "CUDA_VISIBLE_DEVICES=1"
    echo CUDA_VISIBLE_DEVICES not set; defaulting to GPU 1 for ASR/OCR services
)

REM Start uvicorn with proper settings
"%PYTHON_CMD%" -m uvicorn server.app:app ^
    --host 0.0.0.0 ^
    --port 7860 ^
    --workers 1 ^
    --proxy-headers ^
    --forwarded-allow-ips 127.0.0.1,::1 ^
    --log-level info

echo.
echo FastAPI server stopped.
pause
