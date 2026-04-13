@echo off
echo Checking Finn Voice services...
echo.

echo [Chatterbox TTS - port 8004]
curl -s http://localhost:8004/health >nul 2>nul
if errorlevel 1 (
    echo   STATUS: NOT RUNNING
) else (
    echo   STATUS: OK
    curl -s http://localhost:8004/health
    echo.
)
echo.

echo [Audio Sidecar - port 8082]
curl -s http://localhost:8082/health >nul 2>nul
if errorlevel 1 (
    echo   STATUS: NOT RUNNING
) else (
    echo   STATUS: OK
    curl -s http://localhost:8082/health
    echo.
)
echo.

echo [GPU Status]
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader
echo.
