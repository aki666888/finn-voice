@echo off
echo Setting up Windows Firewall rules for Finn Voice...
echo (Requires Administrator privileges)
echo.

netsh advfirewall firewall add rule name="Finn-Chatterbox-TTS" dir=in action=allow protocol=TCP localport=8004
netsh advfirewall firewall add rule name="Finn-Audio-Sidecar" dir=in action=allow protocol=TCP localport=8082
netsh advfirewall firewall add rule name="Finn-Grok-Relay" dir=in action=allow protocol=TCP localport=8081

echo.
echo Firewall rules added:
echo   Port 8004 - Chatterbox TTS (inbound TCP)
echo   Port 8082 - Audio Sidecar (inbound TCP)
echo   Port 8081 - Grok Voice Relay (inbound TCP)
echo.
echo Verify from MacBook: curl http://YOUR_WINDOWS_IP:8082/health
