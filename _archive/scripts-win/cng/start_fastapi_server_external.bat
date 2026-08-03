REM start_fastapi_server_external.bat
@echo off
echo Starting FastAPI Server (External Services)...
echo ===========================================

REM Canonical operator launcher. Service URLs / ports: ..\service_endpoints.json (repo root). Optional overrides: docs\ENV_VARIABLES.md.
REM Change to the correct directory
cd /d "%~dp0"

REM Check if virtual environment exists
if exist ".venv\Scripts\python.exe" (
    echo Using virtual environment: .venv
    set PYTHON_CMD=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    echo Using virtual environment: venv
    set PYTHON_CMD=venv\Scripts\python.exe
) else if exist "cenv\Scripts\python.exe" (
    echo Using virtual environment: cenv
    set PYTHON_CMD=cenv\Scripts\python.exe
) else (
    echo Using system Python
    set PYTHON_CMD=python
)

REM Defaults for NOTEGEN / OCR / RAG / ASR come from ..\service_endpoints.json when Python loads server.app.
REM Set variables below only to override the JSON for this session.
if "%ASR_API_KEY%"=="" set "ASR_API_KEY=notegenadmin"

REM ---------------------------------------------------------------------------
REM Optional LLM overrides (port = number in URL, e.g. :9080). Full table: docs\ENV_VARIABLES.md
REM
REM TWO DIFFERENT QA MODES — do not confuse them:
REM   (1) QA text-only  — /api/qa/chat  — uses LLM_QA_TEXT_URL, or if EMPTY uses NOTEGEN_URL_*.
REM   (2) QA + image    — vision route  — uses LLM_QA_VISION_URL, else VISION_QA_URL, else OCR
REM       (see resolve chain in ENV_VARIABLES.md; not the same vars as text QA).
REM
REM To give QA text its own llama-server, set e.g.:
REM   set LLM_QA_TEXT_URL=http://127.0.0.1:9080
REM To give vision QA its own server, set e.g.:
REM   set LLM_QA_VISION_URL=http://127.0.0.1:8081
REM   (or set VISION_QA_URL=... — older name, still supported)
REM ---------------------------------------------------------------------------

REM Legacy env var kept for compatibility; not used by whisper.cpp inference proxy
if "%ASR_ENABLE_DIARIZATION%"=="" set "ASR_ENABLE_DIARIZATION=0"

REM Check if required files exist
if not exist "server\app.py" (
    echo ERROR: server\app.py not found
    pause
    exit /b 1
)

if "%FASTAPI_PORT%"=="" (
  for /f "delims=" %%a in ('"%PYTHON_CMD%" -c "import os,json; p=os.path.abspath(os.path.join(os.getcwd(),'..','service_endpoints.json')); d=json.load(open(p,encoding='utf-8')) if os.path.isfile(p) else {}; print(int(d.get('fastapi_port',7860)))"') do set FASTAPI_PORT=%%a
)
if "%FASTAPI_PORT%"=="" set "FASTAPI_PORT=7860"

echo Starting FastAPI server on port %FASTAPI_PORT%...
echo.
echo --- Default LLM for notes (and for QA text if you do not set LLM_QA_TEXT_URL below^) ---
echo NOTEGEN_URL_PRIMARY=%NOTEGEN_URL_PRIMARY%
echo NOTEGEN_URL_FALLBACK=%NOTEGEN_URL_FALLBACK%
echo.
echo --- Optional: note-gen only (empty = same as NOTEGEN above^) ---
echo LLM_NOTE_GEN_URL=%LLM_NOTE_GEN_URL%
echo LLM_NOTE_GEN_URL_FALLBACK=%LLM_NOTE_GEN_URL_FALLBACK%
echo.
echo --- QA text-only /api/qa/chat (empty LLM_QA_TEXT_URL = use NOTEGEN_URL_PRIMARY above^) ---
echo LLM_QA_TEXT_URL=%LLM_QA_TEXT_URL%
echo LLM_QA_TEXT_URL_FALLBACK=%LLM_QA_TEXT_URL_FALLBACK%
echo.
echo --- QA with image / vision (empty = chain: LLM_QA_VISION -^> VISION_QA -^> OCR -^> default^; see docs^) ---
echo LLM_QA_VISION_URL=%LLM_QA_VISION_URL%
echo LLM_QA_VISION_URL_FALLBACK=%LLM_QA_VISION_URL_FALLBACK%
echo VISION_QA_URL=%VISION_QA_URL%
echo VISION_QA_URL_FALLBACK=%VISION_QA_URL_FALLBACK%
echo.
echo --- OCR / RAG / ASR ---
echo OCR_URL_PRIMARY=%OCR_URL_PRIMARY%
echo OCR_URL_FALLBACK=%OCR_URL_FALLBACK%
echo RAG_URL=%RAG_URL%
echo ASR_URL=%ASR_URL%
echo ASR_URL_FALLBACK=%ASR_URL_FALLBACK%
echo ASR_API_KEY=%ASR_API_KEY%
echo ASR_ENABLE_DIARIZATION=%ASR_ENABLE_DIARIZATION%
echo.

REM Admin UI: process start/stop (office + AI). Production default OFF — set to 1 only on trusted operator/dev consoles.
REM See ..\docs\OPERATOR_RUNBOOK_WINDOWS.md section "Recommended defaults".
if "%ADMIN_PROCESS_CONTROL_ENABLED%"=="" set "ADMIN_PROCESS_CONTROL_ENABLED=0"

REM Start uvicorn with proper settings
"%PYTHON_CMD%" -m uvicorn server.app:app ^
    --host 0.0.0.0 ^
    --port %FASTAPI_PORT% ^
    --workers 1 ^
    --proxy-headers ^
    --forwarded-allow-ips 127.0.0.1,::1 ^
    --log-level info

echo.
echo FastAPI server stopped.
pause
