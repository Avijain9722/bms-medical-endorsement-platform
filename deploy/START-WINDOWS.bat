@echo off
REM ===================================================================
REM  BMS Medical Endorsement Platform
REM
REM  Double-click this file to start the platform.
REM
REM  The first run installs everything (a few minutes, no internet
REM  needed). Every run after that starts in a few seconds.
REM ===================================================================
setlocal
cd /d "%~dp0.."

echo.
echo  BMS Medical Endorsement Platform
echo  ================================
echo.

REM --- Python must already be installed; it cannot be bundled ---------
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python is not installed, or was not added to PATH.
    echo.
    echo  Install Python 3.11 or newer from https://python.org
    echo  IMPORTANT: tick "Add Python to PATH" during setup.
    echo.
    echo  Then double-click this file again.
    echo.
    pause
    exit /b 1
)

REM --- First run installs; later runs skip straight to starting -------
if not exist "app\.venv\Scripts\python.exe" (
    echo  First run - setting up. This takes a few minutes.
    echo  No internet connection is needed.
    echo.
    powershell -ExecutionPolicy Bypass -File "deploy\install.ps1"
    if errorlevel 1 (
        echo.
        echo  Setup failed. See the messages above.
        pause
        exit /b 1
    )
    echo.
    echo  Setup complete.
    echo.
)

echo  Starting... your browser will open at http://127.0.0.1:8000
echo.
echo  Leave this window open while you work.
echo  Close it, or press Ctrl+C, to stop the platform.
echo.

REM Give the server a moment to bind before the browser asks for it.
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"

cd app
.venv\Scripts\python.exe -m uvicorn bms.web.app:app --host 127.0.0.1 --port 8000

echo.
echo  The platform has stopped.
pause
