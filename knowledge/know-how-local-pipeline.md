Local Voice Pipeline - End to End


Machines Involved

- MacBook Air (Finn app, macOS, no GPU)
  - Runs: Finn.app (Swift), PushToTalkManager, AudioCaptureService, LocalVoiceProvider
  - Captures mic audio at 16kHz PCM16
  - Sends audio over WiFi to Windows PC
  - Receives transcription text and TTS audio back

- Windows PC (GPU server, 2x3090 + 4060ti)
  - Runs: finn-audio-sidecar.py (FastAPI on port 8082)
  - Runs: Chatterbox TTS server (port 8004)
  - Loads: faster-whisper large-v3 on CUDA, Silero VAD on CPU, OpenWakeWord on CPU
  - Binds 0.0.0.0 -> accessible over LAN


STT Path (Speech to Text)

Step 1 - User presses PTT key on MacBook
  - PushToTalkManager.startAudioTranscriptionWithProvider()
  - VoiceProviderManager.providerType == .local
  - Calls startLocalTranscription()

Step 2 - Mic capture starts
  - AudioCaptureService.startCapture() -> CoreAudio IOProc
  - Captures at device native rate, resamples to 16kHz
  - Outputs PCM16 (int16) chunks via onAudioChunk callback

Step 3 - Audio sent to sidecar
  - PushToTalkManager.routeAudioChunk(data)
  - LocalVoiceProvider.sendAudioChunk(data)
  - URLSessionWebSocketTask sends binary data to ws://WINDOWS_IP:8082/listen

Step 4 - Sidecar receives audio
  - /listen WebSocket endpoint accepts binary PCM16 frames
  - Converts int16 -> float32 (divide by 32768)
  - Appends to audio_buffer and vad_buffer

Step 5 - VAD processes audio
  - Silero VAD runs on 512-sample chunks (32ms at 16kHz)
  - Detects speech start -> sends {"type":"speech_start","item_id":"finn_voice_xxxx"}
  - Counts silence samples when not speaking
  - Silence threshold = vad_silence_ms * 16 samples (default 1500ms = 24000 samples)

Step 6 - Interim transcription (during speech)
  - Every 300ms during speech, if audio_buffer > 8000 samples (0.5s)
  - Runs faster-whisper on accumulated audio buffer
  - Sends {"type":"interim","text":"partial text...","item_id":"finn_voice_xxxx"}

Step 7 - Final transcription (on silence)
  - When silence_samples >= silence_threshold and speech was detected
  - Runs faster-whisper on full audio buffer
  - Sends {"type":"final","text":"complete text","item_id":"finn_voice_xxxx","language":"en"}
  - Resets all buffers, VAD state, counters

Step 8 - MacBook receives transcript
  - LocalVoiceProvider.handleSidecarMessage() parses JSON
  - Creates VoiceTranscript struct (text, isFinal, itemId, language)
  - Calls onTranscript callback on main thread

Step 9 - UI updates
  - PushToTalkManager.handleLocalTranscript()
  - Interim: updates aiInputText with partial text (user sees live transcription)
  - Final: appends to transcriptSegments, updates aiInputText with full text

Step 10 - User releases PTT
  - stopListening() -> LocalVoiceProvider.stopListening()
  - WebSocket closed, stats logged
  - Text in aiInputText submitted to ChatProvider


TTS Path (Text to Speech)

Step 1 - AI generates response with speak_response tool
  - ChatProvider receives tool_use block with name "speak_response"
  - ChatToolExecutor.executeSpeakResponseWithProvider(args)
  - VoiceProviderManager.providerType == .local

Step 2 - TTS request to sidecar
  - LocalVoiceProvider.speak(text: "response text", voice: "Elena.wav", speed: 1.0)
  - HTTP POST to http://WINDOWS_IP:8082/tts/speak
  - Body: {"text": "...", "voice": "Elena.wav", "speed": 1.0}

Step 3 - Sidecar proxies to Chatterbox
  - /tts/speak endpoint receives request
  - Forwards to Chatterbox at http://localhost:8004/tts
  - Chatterbox body: voice_mode=predefined, predefined_voice_id, exaggeration=0.7, temperature=0.7, output_format=wav

Step 4 - Chatterbox generates audio
  - Runs TTS model on GPU
  - Returns WAV audio bytes

Step 5 - Audio returns to MacBook
  - Sidecar returns WAV as StreamingResponse
  - LocalVoiceProvider receives Data from HTTP response
  - Returns to ChatToolExecutor

Step 6 - MacBook plays audio
  - AVAudioPlayer(data: audioData)
  - player.enableRate = true, player.rate = speed
  - Plays through MacBook speakers/headphones


Troubleshooting

- No connection to sidecar
  -> Check Windows firewall allows inbound on 8082
  -> Check both machines on same network
  -> curl http://WINDOWS_IP:8082/health from MacBook terminal
  -> Check sidecar logs at D:/finn/finn-voice/logs/

- Transcription empty or wrong
  -> Check sidecar logs for STT timing and results
  -> Verify faster-whisper loaded on correct CUDA device
  -> Check audio format: must be PCM16 int16 at 16kHz

- TTS no audio
  -> Check Chatterbox running on port 8004
  -> curl -X POST http://WINDOWS_IP:8082/tts/speak -H "Content-Type: application/json" -d '{"text":"test","voice":"Elena.wav","speed":1.0}'
  -> Check sidecar logs for Chatterbox proxy errors

- High latency
  -> Check WiFi signal strength
  -> Check sidecar GPU utilization (nvidia-smi)
  -> Check if whisper is loading model every call (should be preloaded at startup)
