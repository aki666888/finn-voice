@echo off
echo ========================================
echo  Finn Voice - Windows GPU Services
echo ========================================
echo.

REM Check if Python is available
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    exit /b 1
)

echo [1/3] Starting Chatterbox TTS on port 8004...
start "Finn-Chatterbox" cmd /c "cd /d D:\finn\finn-voice && python windows-startup\chatterbox-server.py"

echo Waiting 15 seconds for Chatterbox to load model...
timeout /t 15 /nobreak >nul

echo [2/3] Starting Audio Sidecar on port 8082...
start "Finn-Sidecar" cmd /c "cd /d D:\finn\finn-voice && python finn-audio-sidecar.py"

echo Waiting 20 seconds for Whisper model to load...
timeout /t 20 /nobreak >nul

echo [3/3] Checking status...
call windows-startup\check-status.bat

echo.
echo ========================================
echo  All services started
echo  Chatterbox TTS: http://0.0.0.0:8004
echo  Audio Sidecar:  http://0.0.0.0:8082
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
taskkill /FI "WINDOWTITLE eq Finn-Chatterbox" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Finn-Sidecar" /F >nul 2>nul
echo Done.
