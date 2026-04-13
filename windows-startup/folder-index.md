windows-startup - Folder Index

start-all.bat
  Master launcher. Starts Chatterbox, waits 15s, starts sidecar, waits 20s, checks status.
  Keeps window open. Press any key to stop all services.

start-chatterbox.bat
  Starts Chatterbox TTS server only. Port 8004.

start-sidecar.bat
  Starts Audio Sidecar only. Port 8082.

check-status.bat
  Checks health of both services + shows GPU status via nvidia-smi.
  Run anytime to verify services are up.

setup-firewall.bat
  Adds Windows Firewall inbound rules for ports 8004, 8082, 8081.
  Run once as Administrator.

chatterbox-server.py
  FastAPI Chatterbox TTS wrapper. Loads model on CUDA device.
  Imports: chatterbox.tts, torch, fastapi, uvicorn, loguru, soundfile
  Endpoints: /health, /tts, /tts/speak, /tts/voices, /api/ui/initial-data
  Logs to: D:/finn/finn-voice/logs/chatterbox_YYYY-MM-DD.log

requirements-windows.txt
  Python pip dependencies for Windows GPU environment.
  Install with: pip install -r requirements-windows.txt
