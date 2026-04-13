import Foundation
import os.log

// Voice provider selection - stored in config/voice-provider.md
enum VoiceProviderType: String {
    case deepgram = "deepgram"   // Cloud Deepgram Nova-3 STT + Aura TTS (existing)
    case local = "local"         // Local GPU sidecar (Whisper STT + Chatterbox TTS)
    case disabled = "disabled"   // No voice, text only
}

// Transcript result from any provider
struct VoiceTranscript {
    let text: String
    let isFinal: Bool
    let itemId: String
    let language: String
    let confidence: Double
}

// Protocol that both Deepgram and Local providers implement
protocol VoiceProvider: AnyObject {
    var providerType: VoiceProviderType { get }
    var isConnected: Bool { get }

    // STT
    func startListening(onTranscript: @escaping (VoiceTranscript) -> Void,
                        onError: @escaping (Error) -> Void,
                        onConnected: (() -> Void)?,
                        onDisconnected: (() -> Void)?)
    func stopListening()
    func sendAudioChunk(_ data: Data)

    // TTS
    func speak(text: String, voice: String?, speed: Float) async throws -> Data  // Returns WAV audio
}

// Resolves which provider to use based on config
class VoiceProviderManager {
    static let shared = VoiceProviderManager()
    private let logger = Logger(subsystem: "com.finn.voice", category: "VoiceProviderManager")

    private(set) var currentProvider: VoiceProvider?
    private(set) var providerType: VoiceProviderType = .disabled

    // Read from config/voice-provider.md (one line: deepgram, local, or disabled)
    func loadConfig() {
        logger.info("[VoiceProviderManager] Loading voice provider config...")

        let configPath = VoiceProviderManager.resolveConfigPath("voice-provider.md")
        let sidecarUrlPath = VoiceProviderManager.resolveConfigPath("voice-sidecar-url.md")

        // Read provider type
        if let content = try? String(contentsOfFile: configPath, encoding: .utf8) {
            let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
            providerType = VoiceProviderType(rawValue: trimmed) ?? .disabled
            logger.info("[VoiceProviderManager] Provider type: \(trimmed)")
        } else {
            providerType = .deepgram // Default to existing behavior
            logger.warning("[VoiceProviderManager] No voice-provider.md found, defaulting to deepgram")
        }

        // Read sidecar URL (for local provider)
        var sidecarUrl = "http://localhost:8082"
        if let content = try? String(contentsOfFile: sidecarUrlPath, encoding: .utf8) {
            sidecarUrl = content.trimmingCharacters(in: .whitespacesAndNewlines)
            logger.info("[VoiceProviderManager] Sidecar URL: \(sidecarUrl)")
        }

        // Instantiate provider
        switch providerType {
        case .deepgram:
            logger.info("[VoiceProviderManager] Using Deepgram cloud provider")
            currentProvider = nil // Will use existing TranscriptionService path
        case .local:
            logger.info("[VoiceProviderManager] Using Local GPU sidecar at \(sidecarUrl)")
            currentProvider = LocalVoiceProvider(sidecarUrl: sidecarUrl)
        case .disabled:
            logger.info("[VoiceProviderManager] Voice disabled")
            currentProvider = nil
        }
    }

    // Find config path relative to app bundle or working directory
    private static func resolveConfigPath(_ filename: String) -> String {
        // Check relative to executable first (portable)
        let execDir = Bundle.main.bundlePath
        let candidates = [
            "\(execDir)/../config/\(filename)",
            "\(execDir)/../../config/\(filename)",
            "config/\(filename)"
        ]
        for path in candidates {
            if FileManager.default.fileExists(atPath: path) {
                return path
            }
        }
        return candidates.last! // Return last candidate even if missing
    }
}
