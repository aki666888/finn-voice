Finn Audio Sidecar - API Reference
Port: 8082 (configurable via finn-voice-config.json)
Binding: 0.0.0.0 (LAN accessible)


REST Endpoints

GET /health
  Returns JSON with loaded models, CUDA device, VAD config.
  Response: {"status":"ok","app":"finn-audio-sidecar","device":"cuda:0","gpu_models":{...},"cpu_models":[...],"vad_sensitivity":0.5,"vad_silence_ms":1500}

POST /transcribe
  One-shot transcription. Send base64 audio, get text back.
  Request: {"audio_base64": "<base64 PCM16 16kHz>"}
  Response: {"success":true,"text":"transcribed text","language":"en","model":"faster-whisper"}

POST /tts/speak
  Text to speech via Chatterbox proxy. Returns WAV audio.
  Request: {"text":"hello world","voice":"Elena.wav","speed":1.0}
  Response: audio/wav binary (StreamingResponse)
  Chatterbox params: voice_mode=predefined, exaggeration=0.7, temperature=0.7, output_format=wav

GET /tts/voices
  List available Chatterbox voices.
  Proxies to Chatterbox /api/ui/initial-data
  Response: {"voices":[...],...}

POST /tts/preview
  Preview a TTS voice. Same as /tts/speak but intended for settings UI previews.
  Request: {"text":"preview text","voice":"Elena.wav","speed":1.0}
  Response: audio/wav binary

POST /kokoro/speak
  Kokoro TTS (currently disabled).
  Returns error: "Kokoro disabled - use Chatterbox via /tts/speak"

GET /kokoro/voices
  Kokoro voice list (returns empty when disabled).
  Response: {"voices":[],"status":"kokoro not loaded"}


WebSocket Endpoints

WS /listen
  Real-time STT with VAD. Primary endpoint for voice input.

  Client sends: binary PCM16 int16 frames at 16kHz

  Server sends JSON messages:
  - {"type":"speech_start","item_id":"finn_voice_abc12345"}
    Sent when VAD detects speech beginning. item_id is unique per utterance.

  - {"type":"interim","text":"partial transcri...","item_id":"finn_voice_abc12345","language":"en"}
    Sent every ~300ms during speech. Accumulated text so far.

  - {"type":"final","text":"complete transcription","item_id":"finn_voice_abc12345","language":"en"}
    Sent when VAD detects silence after speech (silence_threshold reached).
    This is the definitive transcription to use.

  VAD parameters:
  - 512-sample chunks at 16kHz (32ms per chunk)
  - Speech sensitivity from config (default 0.5)
  - Silence threshold from config (default 1500ms = 24000 samples)
  - Interim transcription interval: 300ms minimum gap

WS /dictate
  Continuous dictation mode. No auto-send on silence.
  User decides when to submit via GUI.

  Client sends: binary PCM16 int16 frames at 16kHz

  Server sends JSON messages:
  - {"type":"dictate","text":"accumulated text","language":"en"}
    Sent on speech end (VAD detects silence). Buffer cleared after.

  - {"type":"dictate_interim","text":"partial during speech","language":"en"}
    Sent every 300ms during long speech segments.

WS /wakeword
  Wake word detection + dictation mode.
  Always-on listening for wake word, then transcribes until stopped.

  Client sends first: JSON config message (optional)
    {"threshold": 0.5}
  Client sends ongoing: binary PCM16 int16 frames at 16kHz
  Client sends commands: JSON text messages
    {"command":"force_dictate"} -> starts dictation without wake word
    {"command":"stop_dictate"} -> stops dictation

  Server sends JSON messages:
  - {"type":"wake_start","wake_word":"hey_jarvis","score":0.85}
    Wake word detected. Dictation mode activated.

  - {"type":"wake_halt","wake_word":"alexa","score":0.9}
    Halt wake word detected. Emergency stop. Dictation deactivated.

  - {"type":"dictate","text":"transcribed text"}
    Transcription during dictation (on speech end).

  - {"type":"dictate_interim","text":"partial text"}
    Interim transcription during long speech.

  - {"type":"dictate_started","message":"Dictation mode activated"}
    Response to force_dictate command.

  - {"type":"dictate_stopped","message":"Dictation mode deactivated"}
    Response to stop_dictate command.

  Wake word models:
  - Start: configurable (default hey_jarvis)
  - Halt: configurable (default empty = disabled)
  - Threshold: 0.5 default, configurable via initial config message


Audio Format Requirements

- Sample rate: 16000 Hz (16kHz)
- Encoding: PCM16 signed int16 little-endian
- Channels: 1 (mono)
- Chunk size: any (sidecar buffers internally)
- Typical chunk: 3200 bytes = 100ms of audio (16000 * 2 bytes * 0.1s)
