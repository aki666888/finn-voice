Finn Voice - Modus Operandi


Section 1 - Architectural Design Decisions and Nuances

- Why cross-machine (MacBook -> Windows PC -> MacBook)
  - Finn runs on MacBook Air (no discrete GPU, no CUDA)
  - STT (faster-whisper large-v3) and TTS (Chatterbox) need CUDA GPU
  - Windows PC has 2x RTX 3090 24GB + 1x 4060ti 16GB -> massive GPU headroom
  - Sidecar runs on Windows, accessible over LAN on 0.0.0.0:8082
  - MacBook sends audio over WiFi, receives text/audio back
  - Latency: ~50-100ms LAN round trip, acceptable for voice

- Why WebSocket for STT, HTTP for TTS
  - STT is streaming: audio chunks flow continuously, transcriptions come back in real-time
  - WebSocket is natural fit for bidirectional streaming
  - TTS is request/response: send text, get WAV back
  - HTTP POST is simpler, no connection management needed for one-shot requests

- Why Silero VAD on sidecar (not on MacBook)
  - VAD determines when speech starts/stops -> triggers transcription
  - Running VAD on sidecar means MacBook just streams raw audio, no processing
  - Sidecar handles VAD + STT + silence detection in one place
  - Simpler MacBook code: just capture mic, send bytes, receive text

- Why keep Deepgram as fallback
  - Deepgram works without Windows PC (cloud, internet only)
  - Useful when traveling, when Windows PC is off, or as quick fallback
  - Radio button in settings: Deepgram Cloud / Local GPU / Disabled
  - VoiceProvider protocol abstracts both behind same interface

- Why config in MD files (matching Finn pattern)
  - Finn already uses config/models/*.md for model slot config
  - config/voice-provider.md = one word: deepgram, local, or disabled
  - config/voice-sidecar-url.md = one URL: http://192.168.x.x:8082
  - ConfigReader.swift already knows how to read these
  - Portable: config/ folder travels with the app

- Why comprehensive logging
  - Cross-machine debugging is hard: network issues, firewall, latency spikes
  - Sidecar logs to D:/finn/finn-voice/logs/ with loguru (rotation, retention)
  - Grok relay logs to D:/finn/finn-voice/logs/grok-relay.log
  - Swift patches use os_log with com.finn.voice subsystem
  - Every audio chunk, VAD state, transcription, TTS request is logged with timing


Section 2 - Knowledge Files and Where They Apply

- voice-modus-operandi.md (this file)
  Architecture decisions, workflow, design rationale.
  Helpful when: making design decisions, understanding why things are the way they are.

- know-how-local-pipeline.md
  End-to-end step-by-step of the local voice round trip.
  Helpful when: implementing the pipeline, debugging audio flow, tracing data path.

- know-about-sidecar-api.md
  Complete API reference for finn-audio-sidecar.py.
  Helpful when: writing code that talks to the sidecar, debugging request/response formats.

- know-about-finn-voice-arch.md
  File inventory, dependency map, startup order.
  Helpful when: onboarding, finding where code lives, understanding file relationships.

- know-about-swift-patches.md
  Swift patch files and how to apply them to Finn Xcode project.
  Helpful when: integrating voice patches, understanding what changes in Finn app code.


Section 3 - Overall Workflow

- Configuration phase
  - Edit config/voice-provider.md -> set to "local"
  - Edit config/voice-sidecar-url.md -> set to Windows PC IP (http://192.168.x.x:8082)
  - Edit config/finn-voice-config.json -> set cuda_device, ports, voices
  - VoiceProviderManager.loadConfig() reads these at Finn app startup

- Startup order
  1. Chatterbox TTS server on Windows PC (port 8004) -> external, start separately
  2. finn-audio-sidecar.py on Windows PC (port 8082) -> loads whisper, VAD, wakeword
  3. Finn.app on MacBook -> reads config, initializes LocalVoiceProvider

- Voice round trip (user speaks)
  1. User holds Option key (PTT) on MacBook
  2. PushToTalkManager -> startAudioTranscriptionWithProvider() -> .local branch
  3. AudioCaptureService captures mic at 16kHz PCM16
  4. LocalVoiceProvider.sendAudioChunk() -> WebSocket to sidecar /listen
  5. Sidecar: Silero VAD detects speech start -> logs speech_start
  6. Sidecar: Accumulates audio, runs interim whisper transcriptions
  7. Sidecar: VAD detects silence -> runs final whisper transcription
  8. Sidecar: Sends {"type":"final","text":"...","item_id":"finn_voice_xxx"} via WS
  9. LocalVoiceProvider receives transcript -> calls onTranscript callback
  10. PushToTalkManager.handleLocalTranscript() -> updates input field
  11. User releases Option key -> text submitted to ChatProvider
  12. ChatProvider -> OpenRouterBridge -> OpenRouter/Claude -> response text

- Voice round trip (AI speaks)
  1. AI response includes speak_response tool call
  2. ChatToolExecutor.executeSpeakResponseWithProvider() -> .local branch
  3. LocalVoiceProvider.speak(text:) -> HTTP POST to sidecar /tts/speak
  4. Sidecar proxies to Chatterbox (port 8004) -> generates WAV
  5. WAV bytes returned via HTTP response
  6. AVAudioPlayer plays the audio on MacBook speakers

- Grok voice (separate pipeline, not cross-machine for TTS)
  1. finn-voice-relay.js runs on Windows PC (port 8081)
  2. Connects to wss://api.x.ai/v1/realtime (Grok handles STT+TTS+LLM all in one)
  3. MacBook connects via WebSocket to relay
  4. Audio streams bidirectionally through relay to xAI
  5. Tool calls batched and executed via MCP executor
  6. This is a fully cloud pipeline, sidecar GPUs not used for STT/TTS
