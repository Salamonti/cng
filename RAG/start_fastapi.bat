REM C:\RAG\start_fastapi.bat
@echo off
echo Starting FastAPI Server...
wsl bash -c "cd /home/islameissa/projects/Clinical-Note-Generator && python -m uvicorn server.app:app --host 0.0.0.0 --port 7860"
