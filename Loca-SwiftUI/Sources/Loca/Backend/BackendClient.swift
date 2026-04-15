import Foundation

/// Async/await client for the Loca FastAPI proxy.
/// Supports both local (localhost:8000) and remote (Tailscale IP) backends.
/// All methods throw `BackendError` on failure.
actor BackendClient {

    static let shared = BackendClient()

    private var base: URL
    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest  = 300
        cfg.timeoutIntervalForResource = 600
        return URLSession(configuration: cfg)
    }()

    init() {
        let host = UserDefaults.standard.string(forKey: "serverHost") ?? "localhost"
        self.base = URL(string: "http://\(host):8000")!
    }

    func updateBaseURL(host: String) {
        let sanitized = host.trimmingCharacters(in: .whitespacesAndNewlines)
        let h = sanitized.isEmpty ? "localhost" : sanitized
        self.base = URL(string: "http://\(h):8000")!
    }

    // MARK: - Health

    func isHealthy() async -> Bool {
        do {
            let (_, resp) = try await session.data(from: base.appendingPathComponent("health"))
            return (resp as? HTTPURLResponse)?.statusCode == 200
        } catch { return false }
    }

    // MARK: - Local models

    func fetchLocalModels() async throws -> [LocalModel] {
        let (data, _) = try await get("/api/local-models")
        return try JSONDecoder().decode(LocalModelsResponse.self, from: data).models
    }

    func loadModel(
        name: String,
        ctxSize: Int? = nil,
        nGpuLayers: Int? = nil,
        batchSize: Int? = nil,
        numThreads: Int? = nil
    ) async throws {
        let body = LoadModelRequest(
            name: name,
            ctx_size: ctxSize,
            n_gpu_layers: nGpuLayers,
            batch_size: batchSize,
            num_threads: numThreads
        )
        let (_, resp) = try await post("/api/models/load", body: body)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            throw BackendError.http(http.statusCode)
        }
    }

    func suggestParams(nvidiaVramGb: Double? = nil) async throws -> PerformanceSuggestion {
        var path = "/api/suggest-params"
        if let vram = nvidiaVramGb {
            path += "?nvidia_vram_gb=\(vram)"
        }
        let (data, _) = try await get(path)
        return try JSONDecoder().decode(PerformanceSuggestion.self, from: data)
    }

    func unloadModel() async throws {
        struct NoBody: Encodable {}
        let (_, resp) = try await post("/api/models/unload", body: NoBody())
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            throw BackendError.http(http.statusCode)
        }
    }

    func deleteModel(name: String) async throws {
        let encoded = name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name
        _ = try await delete("/api/models/\(encoded)")
    }

    func activeModel() async throws -> ActiveModelResponse {
        let (data, _) = try await get("/api/models/active")
        return try JSONDecoder().decode(ActiveModelResponse.self, from: data)
    }

    func startDownload(repoId: String, filename: String?, format: String) async throws -> String {
        var body: [String: Any] = ["repo_id": repoId, "format": format]
        if let f = filename { body["filename"] = f }
        let (data, _) = try await postRaw("/api/models/download", body: body)
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let downloadId = obj["download_id"] as? String else {
            throw BackendError.decode(NSError(domain: "Loca", code: 0, userInfo: [NSLocalizedDescriptionKey: "Missing download_id"]))
        }
        return downloadId
    }

    func pauseDownload(id: String) async throws {
        _ = try await postRaw("/api/models/download/\(id)/pause", body: [:])
    }

    func cancelDownload(id: String) async throws {
        _ = try await postRaw("/api/models/download/\(id)/cancel", body: [:])
    }

    func fetchRepoFiles(repoId: String, format: String = "gguf") async throws -> [RepoFile] {
        let encoded = repoId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? repoId
        guard let url = URL(string: "\(base.absoluteString)/api/repo-files?repo_id=\(encoded)&format=\(format)") else {
            throw URLError(.badURL)
        }
        let (data, _) = try await session.data(from: url)
        struct Resp: Decodable { let files: [RepoFile] }
        return try JSONDecoder().decode(Resp.self, from: data).files
    }

    // MARK: - Hardware & recommendations

    func fetchHardwareProfile() async throws -> HardwareProfile {
        let (data, _) = try await get("/api/hardware")
        return try JSONDecoder().decode(HardwareProfile.self, from: data)
    }

    func fetchRecommendedModels(force: Bool = false) async throws -> RecommendedModelsResponse {
        let path = force ? "/api/recommended-models?force=true" : "/api/recommended-models"
        let (data, _) = try await get(path)
        return try JSONDecoder().decode(RecommendedModelsResponse.self, from: data)
    }

    func installLlmfit() async throws -> Bool {
        struct Resp: Decodable { let ok: Bool }
        struct Empty: Encodable {}
        let (data, resp) = try await post("/api/hardware/install-llmfit", body: Empty())
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 { return false }
        return (try? JSONDecoder().decode(Resp.self, from: data))?.ok ?? false
    }

    // MARK: - Models (legacy)

    func fetchLMModels() async throws -> [LMModel] {
        let (data, _) = try await get("/api/local-models")
        // Map LocalModel list to LMModel for backward compat with capability picker
        let locals = try JSONDecoder().decode(LocalModelsResponse.self, from: data).models
        return locals.map { LMModel(id: $0.name) }
    }

    // MARK: - Chat (streaming)

    /// Yields raw SSE `data:` line payloads as `String` chunks.
    nonisolated func streamChat(_ request: ChatRequest) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let (chatURL, sess) = await self.streamConfig()
                    var urlReq = URLRequest(url: chatURL)
                    urlReq.httpMethod = "POST"
                    urlReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    urlReq.httpBody  = try JSONEncoder().encode(request)

                    let (bytes, _) = try await sess.bytes(for: urlReq)
                    var buffer = ""
                    for try await byte in bytes {
                        let ch = String(bytes: [byte], encoding: .utf8) ?? ""
                        buffer += ch
                        while let nl = buffer.firstIndex(of: "\n") {
                            let line = String(buffer[..<nl])
                            buffer = String(buffer[buffer.index(after: nl)...])
                            if line.hasPrefix("data: ") {
                                let payload = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
                                if payload != "[DONE]" {
                                    continuation.yield(payload)
                                }
                            }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    /// Returns the chat completions URL and session for use from nonisolated contexts.
    private func streamConfig() -> (URL, URLSession) {
        (base.appendingPathComponent("v1/chat/completions"), session)
    }

    // MARK: - Conversations

    func listConversations() async throws -> [ConversationMeta] {
        let (data, _) = try await get("/api/conversations")
        return try JSONDecoder().decode(ConversationListResponse.self, from: data).conversations
    }

    func getConversation(_ id: String) async throws -> ConversationDetail {
        let (data, _) = try await get("/api/conversations/\(id)")
        return try JSONDecoder().decode(ConversationDetail.self, from: data)
    }

    func saveConversation(_ conv: SaveConversationRequest) async throws -> String {
        let (data, _) = try await post("/api/conversations", body: conv)
        return try JSONDecoder().decode(SaveConversationResponse.self, from: data).id
    }

    func deleteConversation(_ id: String) async throws {
        _ = try await delete("/api/conversations/\(id)")
    }

    func patchConversation(_ id: String, starred: Bool? = nil, folder: String?? = nil) async throws {
        var body: [String: Any] = [:]
        if let s = starred { body["starred"] = s }
        if let f = folder {
            if let name = f { body["folder"] = name } else { body["folder"] = NSNull() }
        }
        guard !body.isEmpty else { return }
        _ = try await patchRaw("/api/conversations/\(id)", body: body)
    }

    func searchConversations(_ query: String) async throws -> [ConversationMeta] {
        var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        comps.path = "/api/search/conversations"
        comps.queryItems = [URLQueryItem(name: "q", value: query)]
        guard let url = comps.url else { return [] }
        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode(ConversationListResponse.self, from: data).conversations
    }

    // MARK: - Memories

    func listMemories() async throws -> [Memory] {
        let (data, _) = try await get("/api/memories")
        return try JSONDecoder().decode(MemoryListResponse.self, from: data).memories
    }

    func addMemory(content: String) async throws -> String {
        let (data, _) = try await post("/api/memories", body: ["content": content])
        return try JSONDecoder().decode(AddMemoryResponse.self, from: data).id
    }

    func deleteMemory(_ id: String) async throws {
        _ = try await delete("/api/memories/\(id)")
    }

    func extractMemories(messages: [[String: String]], convId: String?) async throws -> [Memory] {
        var body: [String: Any] = ["messages": messages]
        if let convId { body["conv_id"] = convId }
        let (data, _) = try await postRaw("/api/extract-memories", body: body)
        return try JSONDecoder().decode(ExtractMemoriesResponse.self, from: data).memories
    }

    // MARK: - Voice

    func transcribeAudio(_ audioData: Data, mimeType: String = "audio/wav") async throws -> String {
        let boundary = UUID().uuidString
        var urlReq = URLRequest(url: base.appendingPathComponent("v1/audio/transcriptions"))
        urlReq.httpMethod = "POST"
        urlReq.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"recording.wav\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        urlReq.httpBody = body

        let (respData, _) = try await session.data(for: urlReq)
        let result = try JSONDecoder().decode(TranscriptionResponse.self, from: respData)
        return result.text
    }

    func synthesizeSpeech(text: String, voice: String? = nil, speed: Double? = nil) async throws -> Data {
        var payload: [String: Any] = ["input": text]
        if let v = voice { payload["voice"] = v }
        if let s = speed { payload["speed"] = s }
        payload["response_format"] = "wav"

        var req = URLRequest(url: base.appendingPathComponent("v1/audio/speech"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, resp) = try await session.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            // Surface the actual error message from the backend
            if let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let errMsg = body["error"] as? String {
                throw BackendError.decode(NSError(domain: "Loca", code: http.statusCode,
                    userInfo: [NSLocalizedDescriptionKey: "TTS failed: \(errMsg)"]))
            }
            throw BackendError.http(http.statusCode)
        }
        return data
    }

    func voiceChat(audioData: Data, messages: [[String: String]]) async throws -> VoiceChatResponse {
        let boundary = UUID().uuidString
        var urlReq = URLRequest(url: base.appendingPathComponent("api/voice/chat"))
        urlReq.httpMethod = "POST"
        urlReq.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        // Audio file part
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"recording.wav\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n".data(using: .utf8)!)

        // Messages JSON part
        if let msgData = try? JSONSerialization.data(withJSONObject: messages),
           let msgStr = String(data: msgData, encoding: .utf8) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"messages\"\r\n\r\n".data(using: .utf8)!)
            body.append(msgStr.data(using: .utf8)!)
            body.append("\r\n".data(using: .utf8)!)
        }

        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        urlReq.httpBody = body

        let (respData, _) = try await session.data(for: urlReq)
        return try JSONDecoder().decode(VoiceChatResponse.self, from: respData)
    }

    func fetchVoiceConfig() async throws -> VoiceConfigResponse {
        let (data, _) = try await get("/api/voice/config")
        return try JSONDecoder().decode(VoiceConfigResponse.self, from: data)
    }

    // MARK: - File upload

    func uploadFile(_ data: Data, filename: String, mimeType: String) async throws -> UploadResult {
        let boundary = UUID().uuidString
        var urlReq = URLRequest(url: base.appendingPathComponent("api/upload"))
        urlReq.httpMethod = "POST"
        urlReq.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        urlReq.httpBody = body

        let (respData, _) = try await session.data(for: urlReq)
        return try JSONDecoder().decode(UploadResult.self, from: respData)
    }

    // MARK: - Vault

    func detectVaults() async throws -> [DetectedVault] {
        let (data, _) = try await get("/api/vault/detect")
        return try JSONDecoder().decode(DetectedVaultsResponse.self, from: data).vaults
    }

    func scanVault(path: String) async throws -> VaultScanResult {
        let (data, _) = try await post("/api/vault/scan", body: ["path": path])
        return try JSONDecoder().decode(VaultScanResult.self, from: data)
    }

    func vaultAnalysis(path: String) async throws -> VaultAnalysis {
        var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        comps.path = "/api/vault/analysis"
        comps.queryItems = [URLQueryItem(name: "path", value: path)]
        let (data, _) = try await session.data(from: comps.url!)
        return try JSONDecoder().decode(VaultAnalysis.self, from: data)
    }

    func semanticSearch(path: String, query: String, limit: Int = 20) async throws -> [VaultSearchResult] {
        var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        comps.path = "/api/vault/semantic-search"
        comps.queryItems = [
            URLQueryItem(name: "path", value: path),
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: "\(limit)"),
        ]
        let (data, _) = try await session.data(from: comps.url!)
        return try JSONDecoder().decode(VaultSearchResponse.self, from: data).results
    }

    // MARK: - System stats

    func systemStats() async throws -> SystemStats {
        let (data, _) = try await get("/system-stats")
        return try JSONDecoder().decode(SystemStats.self, from: data)
    }

    // MARK: - Startup status

    /// Reads /tmp/loca-startup-status.json written by start_services.sh.
    func startupStatus() -> StartupStatus? {
        let url = URL(fileURLWithPath: "/tmp/loca-startup-status.json")
        guard let data = try? Data(contentsOf: url),
              let s = try? JSONDecoder().decode(StartupStatus.self, from: data) else { return nil }
        return s
    }

    // MARK: - Helpers

    private func get(_ path: String) async throws -> (Data, URLResponse) {
        try await session.data(from: base.appendingPathComponent(String(path.dropFirst())))
    }

    private func post<T: Encodable>(_ path: String, body: T) async throws -> (Data, URLResponse) {
        var req = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody   = try JSONEncoder().encode(body)
        return try await session.data(for: req)
    }

    private func postRaw(_ path: String, body: [String: Any]) async throws -> (Data, URLResponse) {
        var req = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody   = try JSONSerialization.data(withJSONObject: body)
        return try await session.data(for: req)
    }

    private func delete(_ path: String) async throws -> (Data, URLResponse) {
        var req = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())))
        req.httpMethod = "DELETE"
        return try await session.data(for: req)
    }

    private func patchRaw(_ path: String, body: [String: Any]) async throws -> (Data, URLResponse) {
        var req = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())))
        req.httpMethod = "PATCH"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await session.data(for: req)
    }
}

enum BackendError: LocalizedError {
    case http(Int)
    case decode(Error)
    case network(Error)

    var errorDescription: String? {
        switch self {
        case .http(let code): return "Server returned HTTP \(code)"
        case .decode(let e): return "Parse error: \(e.localizedDescription)"
        case .network(let e): return e.localizedDescription
        }
    }
}
