REM C:\RAG\start_rag_service.bat
@echo off
echo ========================================
echo Starting RAG Service
echo ========================================
echo.
echo Activating virtual environment...
call "%~dp0ragvenv\Scripts\activate.bat"

echo Starting RAG query API on port 8007...
echo.
echo Press Ctrl+C to stop the server
echo.

cd /d "%~dp0"
python -m uvicorn query_api:app --host 0.0.0.0 --port 8007 --workers 1 --loop asyncio --http h11 --timeout-keep-alive 30
