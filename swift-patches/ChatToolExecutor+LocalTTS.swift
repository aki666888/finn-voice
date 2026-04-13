import Foundation
import AVFoundation
import os.log

// Extension to add local TTS support to ChatToolExecutor
// Patches executeSpeakResponse to route through local sidecar when configured

extension ChatToolExecutor {

    private static let ttsLogger = Logger(subsystem: "com.finn.voice", category: "ChatTool+LocalTTS")

    // Replacement for executeSpeakResponse that checks voice provider
    static func executeSpeakResponseWithProvider(_ args: [String: Any]) async -> String {
        guard let text = args["text"] as? String, !text.isEmpty else {
            return "Error: missing 'text' parameter"
        }

        let providerType = VoiceProviderManager.shared.providerType
        ttsLogger.info("[TTS] executeSpeakResponse -> provider=\(providerType.rawValue) text_len=\(text.count)")

        switch providerType {
        case .local:
            return await executeLocalTTS(text: text)
        case .deepgram:
            // Call original Deepgram path
            ttsLogger.info("[TTS] Routing to Deepgram Aura (original path)")
            return await executeSpeakResponse(args)  // Original function
        case .disabled:
            ttsLogger.info("[TTS] Voice disabled, skipping TTS")
            return "Voice output disabled"
        }
    }

    // Local sidecar TTS
    // NOTE: ttsAudioPlayer is a static var on ChatToolExecutor (original).
    // If it is declared private/fileprivate, change to internal for extension access.
    private static func executeLocalTTS(text: String) async -> String {
        guard let provider = VoiceProviderManager.shared.currentProvider else {
            ttsLogger.error("[TTS] No local provider available")
            return "Error: Local voice provider not configured"
        }

        ttsLogger.info("[TTS] Local sidecar TTS for \(text.count) chars")
        let startTime = Date()

        do {
            // Get user speed preference (same as Deepgram path)
            let speed = UserDefaults.standard.double(forKey: "voiceResponseSpeed")
            let clampedSpeed = speed > 0 ? min(max(speed, 0.25), 2.0) : 1.0

            // Get audio from sidecar
            let audioData = try await provider.speak(text: text, voice: nil, speed: Float(clampedSpeed))

            let elapsed = Date().timeIntervalSince(startTime)
            ttsLogger.info("[TTS] Local TTS received \(audioData.count) bytes in \(String(format: "%.0f", elapsed * 1000))ms")

            // Validate audio data
            guard audioData.count > 100 else {
                ttsLogger.error("[TTS] Audio data too small: \(audioData.count) bytes")
                return "Error: TTS returned empty audio"
            }

            // Play audio (same as Deepgram path)
            let player = try AVAudioPlayer(data: audioData)
            player.enableRate = true
            player.rate = Float(clampedSpeed)
            ttsAudioPlayer = player
            player.play()

            ttsLogger.info("[TTS] Playing local TTS audio at speed \(clampedSpeed)")
            return "Speaking: \(text.prefix(100))..."

        } catch {
            let elapsed = Date().timeIntervalSince(startTime)
            ttsLogger.error("[TTS] Local TTS failed after \(String(format: "%.0f", elapsed * 1000))ms: \(error.localizedDescription)")
            return "Error: Local TTS failed - \(error.localizedDescription)"
        }
    }
}
