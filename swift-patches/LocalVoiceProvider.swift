import Foundation
import os.log

// Local voice provider - connects to finn-audio-sidecar over LAN
// STT: WebSocket to ws://SIDECAR_IP:8082/listen (streams PCM16 in, gets transcription JSON out)
// TTS: HTTP POST to http://SIDECAR_IP:8082/tts/speak (text in, WAV audio out)
class LocalVoiceProvider: VoiceProvider {
    let providerType: VoiceProviderType = .local
    private(set) var isConnected: Bool = false

    private let sidecarUrl: String  // http://192.168.x.x:8082
    private let logger = Logger(subsystem: "com.finn.voice", category: "LocalVoiceProvider")

    // WebSocket for STT streaming
    private var wsTask: URLSessionWebSocketTask?
    private var urlSession: URLSession?
    private var shouldReconnect = false
    private var reconnectAttempts = 0
    private let maxReconnectAttempts = 5

    // Callbacks
    private var onTranscript: ((VoiceTranscript) -> Void)?
    private var onError: ((Error) -> Void)?
    private var onConnected: (() -> Void)?
    private var onDisconnected: (() -> Void)?

    // Stats for logging
    private var audioChunksSent: Int = 0
    private var totalBytesSent: Int = 0
    private var sessionStartTime: Date?

    init(sidecarUrl: String) {
        self.sidecarUrl = sidecarUrl
        logger.info("[LocalVoiceProvider] Initialized with sidecar: \(sidecarUrl)")
    }

    // MARK: - STT (WebSocket to /listen)

    func startListening(onTranscript: @escaping (VoiceTranscript) -> Void,
                        onError: @escaping (Error) -> Void,
                        onConnected: (() -> Void)? = nil,
                        onDisconnected: (() -> Void)? = nil) {
        self.onTranscript = onTranscript
        self.onError = onError
        self.onConnected = onConnected
        self.onDisconnected = onDisconnected
        self.shouldReconnect = true
        self.reconnectAttempts = 0
        self.audioChunksSent = 0
        self.totalBytesSent = 0
        self.sessionStartTime = Date()

        logger.info("[LocalVoiceProvider] startListening -> connecting to sidecar WS")
        connectWebSocket()
    }

    func stopListening() {
        logger.info("[LocalVoiceProvider] stopListening -> chunks_sent=\(self.audioChunksSent) bytes_sent=\(self.totalBytesSent)")
        if let start = sessionStartTime {
            let duration = Date().timeIntervalSince(start)
            logger.info("[LocalVoiceProvider] Session duration: \(String(format: "%.1f", duration))s")
        }

        shouldReconnect = false
        wsTask?.cancel(with: .goingAway, reason: nil)
        wsTask = nil
        isConnected = false
        onDisconnected?()
    }

    func sendAudioChunk(_ data: Data) {
        guard let ws = wsTask, isConnected else {
            if audioChunksSent == 0 {
                logger.warning("[LocalVoiceProvider] sendAudioChunk called but WS not connected")
            }
            return
        }

        audioChunksSent += 1
        totalBytesSent += data.count

        // Log every 50th chunk to avoid spam
        if audioChunksSent % 50 == 0 {
            logger.debug("[LocalVoiceProvider] Audio chunks sent: \(self.audioChunksSent) total_bytes: \(self.totalBytesSent)")
        }

        let message = URLSessionWebSocketTask.Message.data(data)
        ws.send(message) { [weak self] error in
            if let error = error {
                self?.logger.error("[LocalVoiceProvider] WS send error: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - TTS (HTTP POST to /tts/speak)

    func speak(text: String, voice: String? = nil, speed: Float = 1.0) async throws -> Data {
        let ttsUrl = "\(sidecarUrl)/tts/speak"
        logger.info("[LocalVoiceProvider] TTS request -> \(ttsUrl) text_len=\(text.count) voice=\(voice ?? "default")")

        let startTime = Date()

        var request = URLRequest(url: URL(string: ttsUrl)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 60

        let body: [String: Any] = [
            "text": text,
            "voice": voice ?? "Elena.wav",
            "speed": speed
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        let elapsed = Date().timeIntervalSince(startTime)

        guard let httpResponse = response as? HTTPURLResponse else {
            logger.error("[LocalVoiceProvider] TTS response is not HTTP")
            throw LocalVoiceError.invalidResponse
        }

        logger.info("[LocalVoiceProvider] TTS response -> status=\(httpResponse.statusCode) bytes=\(data.count) elapsed=\(String(format: "%.0f", elapsed * 1000))ms")

        guard httpResponse.statusCode == 200 else {
            let errorBody = String(data: data, encoding: .utf8) ?? "unknown"
            logger.error("[LocalVoiceProvider] TTS error: \(httpResponse.statusCode) body=\(errorBody)")
            throw LocalVoiceError.ttsError(httpResponse.statusCode, errorBody)
        }

        return data
    }

    // MARK: - WebSocket Connection

    private func connectWebSocket() {
        // Convert http:// to ws:// and add /listen path
        let wsUrl = sidecarUrl
            .replacingOccurrences(of: "http://", with: "ws://")
            .replacingOccurrences(of: "https://", with: "wss://")
        let fullUrl = "\(wsUrl)/listen"

        logger.info("[LocalVoiceProvider] Connecting WS to \(fullUrl)")

        guard let url = URL(string: fullUrl) else {
            logger.error("[LocalVoiceProvider] Invalid WS URL: \(fullUrl)")
            onError?(LocalVoiceError.invalidUrl(fullUrl))
            return
        }

        let session = URLSession(configuration: .default)
        self.urlSession = session
        let task = session.webSocketTask(with: url)
        self.wsTask = task
        task.resume()

        isConnected = true
        reconnectAttempts = 0
        logger.info("[LocalVoiceProvider] WS connected to sidecar")
        onConnected?()

        // Start receiving messages
        receiveMessage()
    }

    private func receiveMessage() {
        wsTask?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handleSidecarMessage(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.handleSidecarMessage(text)
                    }
                @unknown default:
                    self.logger.warning("[LocalVoiceProvider] Unknown WS message type")
                }
                // Continue receiving
                self.receiveMessage()

            case .failure(let error):
                self.logger.error("[LocalVoiceProvider] WS receive error: \(error.localizedDescription)")
                self.isConnected = false
                self.onDisconnected?()

                // Reconnect if should
                if self.shouldReconnect && self.reconnectAttempts < self.maxReconnectAttempts {
                    self.reconnectAttempts += 1
                    let delay = Double(self.reconnectAttempts) * 2.0
                    self.logger.info("[LocalVoiceProvider] Reconnecting in \(delay)s (attempt \(self.reconnectAttempts))")
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                        self.connectWebSocket()
                    }
                }
            }
        }
    }

    private func handleSidecarMessage(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            logger.warning("[LocalVoiceProvider] Failed to parse sidecar message: \(text.prefix(100))")
            return
        }

        logger.debug("[LocalVoiceProvider] Sidecar message: type=\(type)")

        switch type {
        case "speech_start":
            let itemId = json["item_id"] as? String ?? "unknown"
            logger.info("[LocalVoiceProvider] Speech started: \(itemId)")

        case "interim":
            let transcriptText = json["text"] as? String ?? ""
            let itemId = json["item_id"] as? String ?? "unknown"
            let language = json["language"] as? String ?? "en"
            logger.debug("[LocalVoiceProvider] Interim: '\(transcriptText.prefix(50))' item=\(itemId)")

            let transcript = VoiceTranscript(
                text: transcriptText,
                isFinal: false,
                itemId: itemId,
                language: language,
                confidence: 0.0
            )
            DispatchQueue.main.async { self.onTranscript?(transcript) }

        case "final":
            let transcriptText = json["text"] as? String ?? ""
            let itemId = json["item_id"] as? String ?? "unknown"
            let language = json["language"] as? String ?? "en"
            logger.info("[LocalVoiceProvider] Final transcript: '\(transcriptText.prefix(80))' item=\(itemId)")

            let transcript = VoiceTranscript(
                text: transcriptText,
                isFinal: true,
                itemId: itemId,
                language: language,
                confidence: 1.0
            )
            DispatchQueue.main.async { self.onTranscript?(transcript) }

        case "transcription":
            // Legacy format from some endpoints
            let transcriptText = json["text"] as? String ?? ""
            logger.info("[LocalVoiceProvider] Transcription: '\(transcriptText.prefix(80))'")
            let transcript = VoiceTranscript(
                text: transcriptText,
                isFinal: true,
                itemId: "finn_voice_legacy",
                language: "en",
                confidence: 1.0
            )
            DispatchQueue.main.async { self.onTranscript?(transcript) }

        default:
            logger.debug("[LocalVoiceProvider] Unhandled sidecar message type: \(type)")
        }
    }

    // MARK: - Health Check

    func checkHealth() async -> Bool {
        let healthUrl = "\(sidecarUrl)/health"
        logger.info("[LocalVoiceProvider] Health check -> \(healthUrl)")

        do {
            let (data, response) = try await URLSession.shared.data(from: URL(string: healthUrl)!)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                logger.warning("[LocalVoiceProvider] Health check failed: non-200 status")
                return false
            }
            if let body = String(data: data, encoding: .utf8) {
                logger.info("[LocalVoiceProvider] Health check OK: \(body.prefix(200))")
            }
            return true
        } catch {
            logger.error("[LocalVoiceProvider] Health check error: \(error.localizedDescription)")
            return false
        }
    }
}

// Error types
enum LocalVoiceError: LocalizedError {
    case invalidUrl(String)
    case invalidResponse
    case ttsError(Int, String)
    case sidecarUnavailable

    var errorDescription: String? {
        switch self {
        case .invalidUrl(let url): return "Invalid sidecar URL: \(url)"
        case .invalidResponse: return "Invalid response from sidecar"
        case .ttsError(let code, let body): return "TTS error \(code): \(body)"
        case .sidecarUnavailable: return "Audio sidecar not reachable"
        }
    }
}
