finn-voice - Folder Index

finn-audio-sidecar.py
  GPU audio sidecar: STT (faster-whisper), TTS (Chatterbox proxy), VAD (Silero), WakeWord (OpenWakeWord)
  Imports: torch, faster_whisper, openwakeword, fastapi, uvicorn, loguru, httpx, numpy, soundfile
  Reads: config/finn-voice-config.json
  Calls: Chatterbox server at configurable port (default 8004)
  Logs to: logs/sidecar_YYYY-MM-DD.log
  Binds: 0.0.0.0 on config port (default 8082)

finn-voice-relay.js
  Grok xAI Realtime voice WebSocket relay
  Imports: ws, express, fs, path
  Reads: config/finn-voice-config.json
  Calls: wss://api.x.ai/v1/realtime, MCP executor URL, chat history URL
  Logs to: logs/grok-relay.log
  Binds: 0.0.0.0 on config port (default 8081)

package.json
  Node.js dependencies for finn-voice-relay.js
  Dependencies: ws ^8.20.0, express ^4.21.0

config/
  Configuration files. JSON for sidecar/relay, MD for Finn Swift app ConfigReader.

swift-patches/
  Swift source files to patch into Finn.app Xcode project.
  VoiceProvider protocol, LocalVoiceProvider, PTT extension, TTS extension, ConfigReader extension.

knowledge/
  Architecture docs, API reference, how-tos, modus operandi.

logs/
  Runtime logs. Loguru rotation (daily), 7 day retention.

temp/
  Temporary audio files for STT processing. Auto-created, safe to delete.

windows-startup/
  Windows GPU service launchers. start-all.bat master launcher, individual scripts,
  firewall setup, Chatterbox TTS server, pip requirements, status checker.

voices/
  Voice WAV files for Chatterbox TTS. Drop speaker WAVs here for voice cloning.
