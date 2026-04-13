swift-patches - Folder Index

VoiceProvider.swift
  Protocol + types + manager for voice provider abstraction.
  Defines: VoiceProviderType enum, VoiceTranscript struct, VoiceProvider protocol, VoiceProviderManager singleton.
  Imports: Foundation, os.log
  Called by: PushToTalkManager+LocalVoice.swift, ChatToolExecutor+LocalTTS.swift

LocalVoiceProvider.swift
  Implements VoiceProvider for local GPU sidecar over LAN.
  STT: URLSessionWebSocketTask to ws://sidecar/listen
  TTS: URLSession HTTP POST to http://sidecar/tts/speak
  Imports: Foundation, os.log
  Calls: finn-audio-sidecar.py endpoints over network

PushToTalkManager+LocalVoice.swift
  Extension on PushToTalkManager adding local voice provider support.
  Adds: startAudioTranscriptionWithProvider, startLocalTranscription, handleLocalTranscript,
        routeAudioChunk, stopLocalTranscriptionIfActive, speakWithProvider
  Imports: Foundation, os.log
  Calls: VoiceProviderManager, LocalVoiceProvider, AudioCaptureService (existing)

ChatToolExecutor+LocalTTS.swift
  Extension on ChatToolExecutor adding local TTS support.
  Adds: executeSpeakResponseWithProvider, executeLocalTTS
  Imports: Foundation, AVFoundation, os.log
  Calls: VoiceProviderManager, LocalVoiceProvider.speak()

ConfigReader+Voice.swift
  Extension on ConfigReader adding voice config reads.
  Adds: readVoiceProvider(), readVoiceSidecarUrl()
  Imports: Foundation, os.log
  Reads: config/voice-provider.md, config/voice-sidecar-url.md
