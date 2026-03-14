REM weekly_run.cmd
@echo off
setlocal
set SCRIPT_DIR=%~dp0
set REPO_DIR=%SCRIPT_DIR%..

set PS1=%REPO_DIR%\scripts\weekly_run.ps1

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

endlocal
exit /b %ERRORLEVEL%

