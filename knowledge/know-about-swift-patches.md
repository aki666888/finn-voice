Swift Patches - Application Guide


Overview

  All Swift files in swift-patches/ are designed to be dropped into the Finn Xcode project.
  They extend existing Finn classes via extensions (no modification to original files needed).
  Exception: some private members in PushToTalkManager and ChatToolExecutor need access level
  changed from private to internal for extensions to access them.


Files and What They Do

  VoiceProvider.swift
    - VoiceProviderType enum: deepgram, local, disabled
    - VoiceTranscript struct: text, isFinal, itemId, language, confidence
    - VoiceProvider protocol: startListening, stopListening, sendAudioChunk, speak
    - VoiceProviderManager singleton: reads config MDs, creates appropriate provider
    - Call VoiceProviderManager.shared.loadConfig() at app startup (AppDelegate or AppState)

  LocalVoiceProvider.swift
    - Implements VoiceProvider protocol for local sidecar
    - STT: URLSessionWebSocketTask to ws://sidecar:8082/listen
    - TTS: URLSession HTTP POST to http://sidecar:8082/tts/speak
    - Handles reconnection (5 attempts, exponential backoff)
    - Health check method: checkHealth() async -> Bool
    - Parses sidecar JSON: speech_start, interim, final, transcription

  PushToTalkManager+LocalVoice.swift
    - Extension on PushToTalkManager
    - startAudioTranscriptionWithProvider() -> branches on provider type
    - startLocalTranscription() -> connects LocalVoiceProvider, hooks callbacks
    - handleLocalTranscript() -> updates aiInputText (mirrors Deepgram handler)
    - routeAudioChunk() -> sends PCM16 to correct provider
    - stopLocalTranscriptionIfActive() -> clean shutdown
    - speakWithProvider() -> static async, routes TTS to local or Deepgram
    - REQUIRES: transcriptSegments, preVoiceInputText, effectiveBarState, lastInterimText,
      startMicCapture() changed from private to internal in original PushToTalkManager

  ChatToolExecutor+LocalTTS.swift
    - Extension on ChatToolExecutor
    - executeSpeakResponseWithProvider() -> branches on provider type
    - executeLocalTTS() -> calls provider.speak(), plays via AVAudioPlayer
    - REQUIRES: ttsAudioPlayer changed from private to internal in original ChatToolExecutor

  ConfigReader+Voice.swift
    - Extension on ConfigReader
    - readVoiceProvider() -> reads config/voice-provider.md
    - readVoiceSidecarUrl() -> reads config/voice-sidecar-url.md
    - Uses same path resolution as existing model slot reads


Integration Steps

  1. Copy all 5 Swift files from swift-patches/ into Finn Xcode project Sources/Voice/
  2. In PushToTalkManager.swift: change these from private to internal:
     - transcriptSegments
     - preVoiceInputText
     - effectiveBarState
     - lastInterimText
     - startMicCapture()
     - startDeepgramTranscription() (rename existing Deepgram code to this)
  3. In ChatToolExecutor.swift: change ttsAudioPlayer from private to internal
  4. In PushToTalkManager.startAudioTranscription(): replace body with call to
     startAudioTranscriptionWithProvider()
  5. In ChatToolExecutor where executeSpeakResponse is called: replace with
     executeSpeakResponseWithProvider()
  6. In AppDelegate or AppState init: add VoiceProviderManager.shared.loadConfig()
  7. Copy config/voice-provider.md and config/voice-sidecar-url.md to Finn's config/ folder
  8. Build and test
