@echo off

REM === Request Admin privileges ===
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title Finn Voice - Control Panel
cd /d D:\finn\finn-voice

echo ========================================
echo  Finn Voice - Windows GPU Services
echo  Running as Administrator
echo ========================================
echo.

REM Create dirs if needed
if not exist "logs" mkdir logs
if not exist "temp" mkdir temp
if not exist "voices" mkdir voices

echo [1/4] Starting Chatterbox TTS (cuda:1) on port 8004...
start "Finn-Chatterbox" /min cmd /c "cd /d D:\finn\finn-voice && python windows-startup\chatterbox-server.py"

echo Waiting 15 seconds for model load...
ping -n 16 127.0.0.1 >nul

echo [2/4] Starting Audio Sidecar (cuda:0) on port 8082...
start "Finn-Sidecar" /min cmd /c "cd /d D:\finn\finn-voice && python finn-audio-sidecar.py"

echo Waiting 20 seconds for Whisper model load...
ping -n 21 127.0.0.1 >nul

echo [3/4] Starting mDNS Advertiser...
start "Finn-Advertise" /min cmd /c "cd /d D:\finn\finn-voice && python advertise.py"

ping -n 4 127.0.0.1 >nul

echo [4/4] Checking status...
echo.
echo --- Chatterbox TTS (8004) ---
curl -s http://localhost:8004/health 2>nul
if errorlevel 1 echo   NOT RUNNING YET
echo.
echo --- Audio Sidecar (8082) ---
curl -s http://localhost:8082/health 2>nul
if errorlevel 1 echo   NOT RUNNING YET
echo.
echo --- GPU Memory ---
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader
echo.

echo ========================================
echo  Services launched (check minimized windows)
echo  Press any key to STOP and FREE GPU memory
echo ========================================
pause >nul

echo.
echo ========================================
echo  STOPPING ALL SERVICES + FREEING GPU
echo ========================================
echo.

echo [1/4] Killing Chatterbox...
taskkill /FI "WINDOWTITLE eq Finn-Chatterbox" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Finn-Chatterbox*" /F >nul 2>nul

echo [2/4] Killing Sidecar...
taskkill /FI "WINDOWTITLE eq Finn-Sidecar" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Finn-Sidecar*" /F >nul 2>nul

echo [3/4] Killing Advertiser...
taskkill /FI "WINDOWTITLE eq Finn-Advertise" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Finn-Advertise*" /F >nul 2>nul

REM Kill ANY remaining python processes that might hold GPU memory
echo [3.5/4] Killing any orphan finn python processes...
for /f "tokens=2" %%i in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST 2^>nul ^| findstr "PID"') do (
    wmic process where "ProcessId=%%i" get CommandLine 2>nul | findstr /i "finn" >nul 2>nul
    if not errorlevel 1 (
        echo   Killing PID %%i (finn-related)
        taskkill /PID %%i /F >nul 2>nul
    )
)

echo [4/4] Forcing CUDA memory release...
python -c "import torch; [torch.cuda.set_device(i) or torch.cuda.empty_cache() or torch.cuda.reset_peak_memory_stats(i) for i in range(torch.cuda.device_count())]; print('CUDA cache cleared on all', torch.cuda.device_count(), 'devices')" 2>nul

REM Wait a moment for GPU memory to actually free
ping -n 3 127.0.0.1 >nul

echo.
echo --- GPU Memory After Cleanup ---
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader
echo.
echo ========================================
echo  All services stopped. GPU memory freed.
echo ========================================
pause
