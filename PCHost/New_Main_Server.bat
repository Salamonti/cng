REM New_Main_Server.bat
@echo off
REM ============================================================================
REM  Clinical Notes - Node.js Reverse Proxy Server Launcher
REM  Main entry point for NSSM service and manual execution
REM ============================================================================

setlocal EnableDelayedExpansion

REM Change to script directory
cd /d "%~dp0"

REM Set production environment
set NODE_ENV=production

REM Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

REM ============================================================================
REM  STEP 1: Verify Node.js Installation
REM ============================================================================
echo [1/5] Checking Node.js installation...

where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not in PATH
    echo.
    echo Please install Node.js from https://nodejs.org/
    echo.
    exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js found but not working properly
    exit /b 1
)

echo    Node.js: OK
echo.

REM ============================================================================
REM  STEP 2: Verify Required Files
REM ============================================================================
echo [2/5] Verifying required files...

if not exist "server.js" (
    echo ERROR: server.js not found in %CD%
    exit /b 1
)
echo    server.js: OK

if not exist "package.json" (
    echo ERROR: package.json not found in %CD%
    exit /b 1
)
echo    package.json: OK

if not exist "config\server_config.json" (
    echo WARNING: config\server_config.json not found
    echo    Server will use default configuration
) else (
    echo    config\server_config.json: OK
)

if not exist "web" (
    echo WARNING: web directory not found
) else (
    echo    web directory: OK
)
echo.

REM ============================================================================
REM  STEP 3: Install/Update Dependencies
REM ============================================================================
echo [3/5] Checking dependencies...

if not exist "node_modules" (
    echo    Installing dependencies...
    echo.
    call npm install
    if errorlevel 1 (
        echo ERROR: npm install failed
        exit /b 1
    )
    echo    Dependencies installed
) else (
    echo    node_modules: OK
)
echo.

REM ============================================================================
REM  STEP 4: Check if Port is Already in Use
REM ============================================================================
echo [4/5] Checking port availability...

set PORT_IN_USE=0

netstat -ano | findstr ":3443" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: Port 3443 is already in use!
    set PORT_IN_USE=1
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3443"') do (
        echo    Process ID: %%a
        for /f "tokens=1" %%b in ('tasklist ^| findstr "%%a"') do (
            echo    Process: %%b
        )
    )
)

netstat -ano | findstr ":3000" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: Port 3000 is already in use!
    set PORT_IN_USE=1
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000"') do (
        echo    Process ID: %%a
        for /f "tokens=1" %%b in ('tasklist ^| findstr "%%a"') do (
            echo    Process: %%b
        )
    )
)

if %PORT_IN_USE%==1 (
    echo.
    echo SOLUTION: Run Kill_Old_Node_Processes.bat to clean up old processes
    echo           Or manually kill the processes listed above
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)

echo    Ports 3000 and 3443: Available
echo.

REM ============================================================================
REM  STEP 5: Start Node.js Server
REM ============================================================================
echo [5/5] Starting Node.js reverse proxy server...
echo.
echo Server Configuration:
echo    HTTP Port: 3000
echo    HTTPS Port: 3443
echo    Backend: http://127.0.0.1:7860
echo    Working Directory: %CD%
echo.
echo ============================================================================
echo Server is running...
echo Press Ctrl+C to stop
echo ============================================================================
echo.

REM Start the server
node server.js

REM Capture exit code
set EXIT_CODE=%ERRORLEVEL%

echo.
echo Server stopped with exit code: %EXIT_CODE%

endlocal
exit /b %EXIT_CODE%
