REM C:\RAG\start_all.bat
@echo off
echo ========================================
echo Starting All Servers
echo ========================================
echo.

echo [1/3] Starting LLaMA Server...
start "LLaMA Server" cmd /k "%~dp0start_llama.bat"
timeout /t 3 /nobreak >nul

echo [2/3] Starting OCR Server...
start "OCR Server" cmd /k "%~dp0start_ocr.bat"
timeout /t 3 /nobreak >nul

echo [3/3] Starting FastAPI Server...
start "FastAPI Server" cmd /k "%~dp0start_fastapi.bat"

echo.
echo ========================================
echo All servers started in separate windows
echo ========================================
pause
