import Foundation
import os.log

// Extension to add local voice provider support to PushToTalkManager
// USAGE: Call VoiceProviderManager.shared.loadConfig() at app startup
// Then PushToTalkManager checks providerType before starting transcription
//
// NOTE: The following properties/methods from PushToTalkManager must be
// `internal` (not `private`) for this extension to access them:
//   - transcriptSegments: [String]
//   - preVoiceInputText: String
//   - effectiveBarState (whatever type holds .aiInputText)
//   - lastInterimText: String
//   - startMicCapture()
// If any of these are `private`, change them to `internal` in PushToTalkManager.swift.

extension PushToTalkManager {

    private static let voiceLogger = Logger(subsystem: "com.finn.voice", category: "PTT+LocalVoice")

    // Replace startAudioTranscription() with this
    // Call this from startAudioTranscription() after mic permission check
    func startAudioTranscriptionWithProvider() {
        let providerType = VoiceProviderManager.shared.providerType
        Self.voiceLogger.info("[PTT] startAudioTranscription -> provider=\(providerType.rawValue)")

        switch providerType {
        case .deepgram:
            // Original Deepgram path - no changes needed
            // Keep calling the existing startAudioTranscription() Deepgram code
            Self.voiceLogger.info("[PTT] Using Deepgram cloud STT")
            startDeepgramTranscription()

        case .local:
            // Local sidecar path
            Self.voiceLogger.info("[PTT] Using Local GPU sidecar STT")
            startLocalTranscription()

        case .disabled:
            Self.voiceLogger.info("[PTT] Voice disabled, not starting transcription")
        }
    }

    // Local sidecar transcription
    private func startLocalTranscription() {
        guard let provider = VoiceProviderManager.shared.currentProvider as? LocalVoiceProvider else {
            Self.voiceLogger.error("[PTT] LocalVoiceProvider not initialized")
            return
        }

        // Start mic capture (existing AudioCaptureService - already outputs PCM16 @ 16kHz)
        startMicCapture()

        // Connect to sidecar and start receiving transcriptions
        provider.startListening(
            onTranscript: { [weak self] transcript in
                Task { @MainActor in
                    self?.handleLocalTranscript(transcript)
                }
            },
            onError: { [weak self] error in
                Task { @MainActor in
                    Self.voiceLogger.error("[PTT] Local STT error: \(error.localizedDescription)")
                    self?.stopListening()
                }
            },
            onConnected: {
                Self.voiceLogger.info("[PTT] Connected to local sidecar STT")
            },
            onDisconnected: {
                Self.voiceLogger.info("[PTT] Disconnected from local sidecar STT")
            }
        )

        Self.voiceLogger.info("[PTT] Local transcription started - mic -> sidecar WS -> whisper -> text")
    }

    // Handle transcript from local sidecar (same shape as Deepgram handler)
    private func handleLocalTranscript(_ transcript: VoiceTranscript) {
        Self.voiceLogger.debug("[PTT] Local transcript: final=\(transcript.isFinal) text='\(transcript.text.prefix(50))'")

        if transcript.isFinal {
            // Final transcription - same as what happens when Deepgram sends speechFinal
            // Append to transcriptSegments and update UI
            transcriptSegments.append(transcript.text)
            let fullText = transcriptSegments.joined(separator: " ")

            // Update the input field (same as existing Deepgram handler)
            let prefix = preVoiceInputText.isEmpty ? "" : preVoiceInputText + " "
            effectiveBarState?.aiInputText = prefix + fullText

            Self.voiceLogger.info("[PTT] Final local transcript appended: '\(transcript.text.prefix(80))'")
        } else {
            // Interim - update display but don't commit
            let prefix = preVoiceInputText.isEmpty ? "" : preVoiceInputText + " "
            let committed = transcriptSegments.joined(separator: " ")
            let interim = committed.isEmpty ? transcript.text : committed + " " + transcript.text
            effectiveBarState?.aiInputText = prefix + interim
            lastInterimText = transcript.text
        }
    }

    // Stop local provider when PTT ends
    func stopLocalTranscriptionIfActive() {
        if VoiceProviderManager.shared.providerType == .local {
            Self.voiceLogger.info("[PTT] Stopping local sidecar STT")
            VoiceProviderManager.shared.currentProvider?.stopListening()
        }
    }

    // Route audio chunks to the right place
    // Called from AudioCaptureService's onAudioChunk callback
    func routeAudioChunk(_ data: Data) {
        switch VoiceProviderManager.shared.providerType {
        case .local:
            // Send PCM16 audio to sidecar via WebSocket
            VoiceProviderManager.shared.currentProvider?.sendAudioChunk(data)
        case .deepgram:
            // Send to Deepgram TranscriptionService (existing path)
            // transcriptionService already handles this via its own audio buffer
            break
        case .disabled:
            break
        }
    }
}

// Extension for TTS routing
extension PushToTalkManager {

    // Call this instead of directly calling Deepgram Aura TTS
    // Returns WAV audio data
    static func speakWithProvider(text: String, voice: String? = nil, speed: Float = 1.0) async throws -> Data? {
        let providerType = VoiceProviderManager.shared.providerType
        voiceLogger.info("[PTT] speakWithProvider -> provider=\(providerType.rawValue) text_len=\(text.count)")

        switch providerType {
        case .local:
            guard let provider = VoiceProviderManager.shared.currentProvider else {
                voiceLogger.error("[PTT] No local provider for TTS")
                return nil
            }
            let audioData = try await provider.speak(text: text, voice: voice, speed: speed)
            voiceLogger.info("[PTT] Local TTS returned \(audioData.count) bytes")
            return audioData

        case .deepgram:
            // Return nil to signal caller should use existing Deepgram Aura path
            voiceLogger.info("[PTT] Using Deepgram Aura TTS (existing path)")
            return nil

        case .disabled:
            voiceLogger.info("[PTT] Voice disabled, no TTS")
            return nil
        }
    }
}
