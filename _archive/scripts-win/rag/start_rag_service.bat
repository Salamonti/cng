REM start_rag_service.bat
@echo off
echo ========================================
echo Starting RAG Service
echo ========================================
echo.
echo Activating virtual environment...
if exist "%~dp0venv\Scripts\activate.bat" (
  call "%~dp0venv\Scripts\activate.bat"
) else if exist "%~dp0ragvenv\Scripts\activate.bat" (
  call "%~dp0ragvenv\Scripts\activate.bat"
) else (
  echo ERROR: No venv. Create: cd /d "%~dp0" ^& py -3 -m venv venv ^& venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

echo Starting RAG query API on port 8007...
echo.
echo Press Ctrl+C to stop the server
echo.

cd /d "%~dp0"
python -m uvicorn query_api:app --host 0.0.0.0 --port 8007 --workers 2 --loop asyncio --http h11 --timeout-keep-alive 30
