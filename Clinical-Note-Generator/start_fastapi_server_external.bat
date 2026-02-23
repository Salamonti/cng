REM C:\Clinical-Note-Generator\start_fastapi_server_external.bat
@echo off
echo Starting FastAPI Server (External Services)...
echo ===========================================

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

REM Required external service endpoints (edit as needed)
if "%NOTEGEN_URL_PRIMARY%"=="" set "NOTEGEN_URL_PRIMARY=http://127.0.0.1:8081"
if "%NOTEGEN_URL_FALLBACK%"=="" set "NOTEGEN_URL_FALLBACK=http://127.0.0.1:8036"
if "%OCR_URL_PRIMARY%"=="" set "OCR_URL_PRIMARY=http://127.0.0.1:8090"
if "%OCR_URL_FALLBACK%"=="" set "OCR_URL_FALLBACK=http://127.0.0.1:8091"
if "%RAG_URL%"=="" set "RAG_URL=http://127.0.0.1:8007"
if "%ASR_URL%"=="" set "ASR_URL=http://127.0.0.1:8095"
if "%ASR_URL_FALLBACK%"=="" set "ASR_URL_FALLBACK=http://127.0.0.1:8096"
if "%ASR_API_KEY%"=="" set "ASR_API_KEY=notegenadmin"

REM Legacy env var kept for compatibility; not used by whisper.cpp inference proxy
if "%ASR_ENABLE_DIARIZATION%"=="" set "ASR_ENABLE_DIARIZATION=0"

REM Check if required files exist
if not exist "server\app.py" (
    echo ERROR: server\app.py not found
    pause
    exit /b 1
)

echo Starting FastAPI server on port 7860...
echo NOTEGEN_URL_PRIMARY=%NOTEGEN_URL_PRIMARY%
echo NOTEGEN_URL_FALLBACK=%NOTEGEN_URL_FALLBACK%
echo OCR_URL_PRIMARY=%OCR_URL_PRIMARY%
echo OCR_URL_FALLBACK=%OCR_URL_FALLBACK%
echo RAG_URL=%RAG_URL%
echo ASR_URL=%ASR_URL%
echo ASR_URL_FALLBACK=%ASR_URL_FALLBACK%
echo ASR_API_KEY=%ASR_API_KEY%
echo ASR_ENABLE_DIARIZATION=%ASR_ENABLE_DIARIZATION%
echo.

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
