import Foundation

/// Async/await client for the local FastAPI proxy at localhost:8000.
/// All methods throw `BackendError` on failure.
actor BackendClient {

    static let shared = BackendClient()

    private let base = URL(string: "http://localhost:8000")!
    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest  = 300
        cfg.timeoutIntervalForResource = 600
        return URLSession(configuration: cfg)
    }()

    // MARK: - Health

    func isHealthy() async -> Bool {
        do {
            let (_, resp) = try await session.data(from: base.appendingPathComponent("health"))
            return (resp as? HTTPURLResponse)?.statusCode == 200
        } catch { return false }
    }

    // MARK: - Models

    func fetchLMModels() async throws -> [LMModel] {
        let (data, _) = try await get("/api/lm-models")
        let obj = try JSONDecoder().decode(ModelListResponse.self, from: data)
        return obj.data
    }

    // MARK: - Chat (streaming)

    /// Yields raw SSE `data:` line payloads as `String` chunks.
    nonisolated func streamChat(_ request: ChatRequest) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    var urlReq = URLRequest(url: base.appendingPathComponent("v1/chat/completions"))
                    urlReq.httpMethod = "POST"
                    urlReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    urlReq.httpBody  = try JSONEncoder().encode(request)

                    let (bytes, _) = try await session.bytes(for: urlReq)
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
