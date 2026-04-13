import Foundation
import os.log

// Extension to add voice configuration reads to ConfigReader
// ConfigReader already reads config/models/*.md and config/openrouter-key.md
// This adds config/voice-provider.md and config/voice-sidecar-url.md

extension ConfigReader {

    private static let voiceConfigLogger = Logger(subsystem: "com.finn.voice", category: "ConfigReader+Voice")

    // Read voice provider type from config/voice-provider.md
    // Returns: "deepgram", "local", or "disabled"
    static func readVoiceProvider() -> String {
        let filename = "voice-provider.md"
        voiceConfigLogger.info("[ConfigReader] Reading voice provider from \(filename)")

        if let content = readConfigFile(filename) {
            let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
            voiceConfigLogger.info("[ConfigReader] Voice provider: '\(trimmed)'")
            return trimmed
        }

        voiceConfigLogger.warning("[ConfigReader] voice-provider.md not found, defaulting to 'deepgram'")
        return "deepgram"
    }

    // Read sidecar URL from config/voice-sidecar-url.md
    // Returns: URL string like "http://192.168.1.100:8082"
    static func readVoiceSidecarUrl() -> String {
        let filename = "voice-sidecar-url.md"
        voiceConfigLogger.info("[ConfigReader] Reading sidecar URL from \(filename)")

        if let content = readConfigFile(filename) {
            let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
            voiceConfigLogger.info("[ConfigReader] Sidecar URL: '\(trimmed)'")
            return trimmed
        }

        voiceConfigLogger.warning("[ConfigReader] voice-sidecar-url.md not found, defaulting to localhost:8082")
        return "http://localhost:8082"
    }

    // Helper: reads a config file from the config/ directory
    // Reuses the same path resolution logic as the existing ConfigReader
    private static func readConfigFile(_ filename: String) -> String? {
        // Same path resolution as existing ConfigReader for model slot files
        let execPath = Bundle.main.bundlePath
        let candidates = [
            URL(fileURLWithPath: execPath).deletingLastPathComponent().appendingPathComponent("config/\(filename)").path,
            URL(fileURLWithPath: execPath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("config/\(filename)").path,
            "config/\(filename)"
        ]

        for path in candidates {
            if FileManager.default.fileExists(atPath: path) {
                do {
                    let content = try String(contentsOfFile: path, encoding: .utf8)
                    voiceConfigLogger.debug("[ConfigReader] Found \(filename) at \(path)")
                    return content
                } catch {
                    voiceConfigLogger.error("[ConfigReader] Failed to read \(path): \(error.localizedDescription)")
                }
            }
        }

        voiceConfigLogger.warning("[ConfigReader] \(filename) not found in any candidate path")
        return nil
    }
}
