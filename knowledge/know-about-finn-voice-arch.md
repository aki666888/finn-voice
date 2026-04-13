Finn Voice - Architecture and File Inventory


File Inventory

D:/finn/finn-voice/
  finn-audio-sidecar.py      Python FastAPI server. GPU STT/TTS/VAD/WakeWord. Runs on Windows PC.
                              Imports: torch, faster_whisper, openwakeword, fastapi, loguru, httpx
                              Calls: Chatterbox server at port 8004 (external)
                              Binds: 0.0.0.0:8082

  finn-voice-relay.js         Node.js WebSocket relay for Grok xAI Realtime voice.
                              Imports: ws, express, fs
                              Calls: wss://api.x.ai/v1/realtime, MCP executor, chat history API
                              Binds: 0.0.0.0:8081

  package.json                Node.js deps: ws, express

  config/
    finn-voice-config.json    Master config for sidecar + relay. Ports, keys, CUDA device, voices.
    voice-provider.md         Single word: deepgram, local, or disabled. Read by Finn Swift app.
    voice-sidecar-url.md      Single URL: http://IP:8082. Read by Finn Swift app.

  swift-patches/
    VoiceProvider.swift              Protocol + enum + VoiceProviderManager singleton
    LocalVoiceProvider.swift         WS client for STT (/listen), HTTP client for TTS (/tts/speak)
    PushToTalkManager+LocalVoice.swift   Extension: provider switch for STT, audio routing, TTS routing
    ChatToolExecutor+LocalTTS.swift  Extension: TTS branch for speak_response tool
    ConfigReader+Voice.swift         Extension: reads voice-provider.md and voice-sidecar-url.md

  knowledge/
    index.md                  Master index of knowledge files
    voice-modus-operandi.md   Architecture decisions, workflow, design rationale
    know-how-local-pipeline.md   Step-by-step local voice round trip
    know-about-sidecar-api.md    Complete sidecar API reference
    know-about-finn-voice-arch.md  This file
    know-about-swift-patches.md    Swift patch application guide

  logs/                       Runtime logs (loguru rotation, 7 day retention)
  temp/                       Temp audio files for STT processing


Dependency Map

  Finn.app (MacBook)
    -> VoiceProviderManager.loadConfig() reads config/voice-provider.md + config/voice-sidecar-url.md
    -> Creates LocalVoiceProvider(sidecarUrl)
    -> PushToTalkManager uses VoiceProvider protocol
      -> AudioCaptureService captures mic PCM16
      -> LocalVoiceProvider.sendAudioChunk() sends to sidecar WS
      -> LocalVoiceProvider.onTranscript callback updates UI
    -> ChatToolExecutor uses VoiceProvider for TTS
      -> LocalVoiceProvider.speak() POSTs to sidecar HTTP

  finn-audio-sidecar.py (Windows PC)
    -> Reads config/finn-voice-config.json at startup
    -> Loads faster-whisper, silero-vad, openwakeword at startup
    -> /listen WS: receives PCM16, runs VAD + whisper, sends transcription JSON
    -> /tts/speak HTTP: proxies to Chatterbox at port 8004
    -> Logs to logs/sidecar_YYYY-MM-DD.log

  finn-voice-relay.js (Windows PC, for Grok only)
    -> Reads config/finn-voice-config.json at startup
    -> WS relay: browser/swift <-> xAI realtime API
    -> Logs to logs/grok-relay.log


Startup Order

  1. Chatterbox TTS server (port 8004) - external, start first
  2. finn-audio-sidecar.py (port 8082) - needs Chatterbox running
  3. finn-voice-relay.js (port 8081) - only if using Grok voice
  4. Finn.app - connects to whichever is configured
