import Foundation

// MARK: - Chat

struct ChatRequest: Encodable {
    let mode: String
    let model_override: String?
    let messages: [ChatMessage]
    let stream: Bool
    let num_ctx: Int
    let research_mode: Bool
}

struct ChatMessage: Codable, Identifiable, Equatable {
    var id: UUID = UUID()
    let role: String          // "user" | "assistant" | "system"
    var content: MessageContent

    enum CodingKeys: String, CodingKey { case role, content }
}

/// Content can be plain text or a multipart array (text + image_url).
enum MessageContent: Codable, Equatable {
    case text(String)
    case parts([ContentPart])

    var plainText: String {
        switch self {
        case .text(let s): return s
        case .parts(let ps): return ps.compactMap { if case .text(let t) = $0 { return t.text } else { return nil } }.joined(separator: "\n")
        }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let s = try? c.decode(String.self) { self = .text(s); return }
        self = .parts(try c.decode([ContentPart].self))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .text(let s): try c.encode(s)
        case .parts(let ps): try c.encode(ps)
        }
    }
}

enum ContentPart: Codable, Equatable {
    case text(TextPart)
    case image(ImagePart)

    struct TextPart: Codable, Equatable {
        let type: String   // "text"
        let text: String
    }
    struct ImagePart: Codable, Equatable {
        let type: String   // "image_url"
        let image_url: ImageURL
        struct ImageURL: Codable, Equatable { let url: String }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let t = try? c.decode(TextPart.self) { self = .text(t); return }
        self = .image(try c.decode(ImagePart.self))
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self { case .text(let t): try c.encode(t); case .image(let i): try c.encode(i) }
    }
}

struct SSEChunk: Decodable {
    struct Choice: Decodable {
        struct Delta: Decodable {
            let content: String?
            let role: String?
        }
        let delta: Delta
        let finish_reason: String?
    }
    let choices: [Choice]?
    let usage: UsageStats?
    let model: String?
}

struct UsageStats: Decodable {
    let prompt_tokens: Int
    let completion_tokens: Int
    let total_tokens: Int
}

// MARK: - Model capability

enum ModelCapability: String, CaseIterable, Identifiable, Comparable {
    case general  = "general"
    case code     = "code"
    case thinking = "thinking"
    case vision   = "vision"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .general:  return "General"
        case .code:     return "Code"
        case .thinking: return "Thinking"
        case .vision:   return "Vision"
        }
    }

    var systemIcon: String {
        switch self {
        case .general:  return "bubble.left.and.bubble.right"
        case .code:     return "chevron.left.forwardslash.chevron.right"
        case .thinking: return "brain.head.profile"
        case .vision:   return "eye"
        }
    }

    /// Mode string sent to the backend (maps to a system prompt).
    var modeHint: String {
        switch self {
        case .general:  return "general"
        case .code:     return "code"
        case .thinking: return "thinking"
        case .vision:   return "vision"
        }
    }

    static func < (lhs: ModelCapability, rhs: ModelCapability) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

// MARK: - Models

struct LMModel: Decodable, Identifiable {
    let id: String

    /// Infers capabilities from the model's ID / name.
    var capabilities: Set<ModelCapability> {
        let lower = id.lowercased()
        var caps: Set<ModelCapability> = []

        // Vision models
        let visionTerms = ["llava", "moondream", "bakllava", "minicpm-v", "idefics",
                           "cogvlm", "internvl", "qwen-vl", "blip", "clip"]
        if visionTerms.contains(where: { lower.contains($0) })
            || lower.hasSuffix("-vl") || lower.contains("-vl-") || lower.hasSuffix("vl") {
            caps.insert(.vision)
        }

        // Thinking / reasoning models
        let thinkTerms = ["qwq", "deepseek-r1", "r1-distill", "thinking",
                          "skywork-o1", "marco-o1"]
        if thinkTerms.contains(where: { lower.contains($0) })
            || lower.contains("-r1-") || lower.hasSuffix("-r1") {
            caps.insert(.thinking)
        }

        // Code-specialist models
        let codeTerms = ["coder", "codellama", "code-llama", "starcoder", "deepseek-coder",
                         "codestral", "codegemma", "codeqwen", "wizardcoder", "phind-code",
                         "magicoder", "qwen2.5-coder"]
        if codeTerms.contains(where: { lower.contains($0) }) {
            caps.insert(.code)
        }

        // Fallback to general
        return caps.isEmpty ? [.general] : caps
    }
}

struct ModelListResponse: Decodable {
    let data: [LMModel]
}

// MARK: - Conversations

struct ConversationMeta: Decodable, Identifiable {
    let id: String
    let title: String
    let model: String
    let updated_at: String
}

struct ConversationDetail: Decodable {
    let id: String
    let title: String
    let messages: [ChatMessage]
}

struct ConversationListResponse: Decodable {
    let conversations: [ConversationMeta]
}

struct SaveConversationRequest: Encodable {
    let id: String?
    let title: String
    let messages: [ChatMessage]
    let model: String
}

struct SaveConversationResponse: Decodable {
    let id: String
}

// MARK: - Memories

struct Memory: Decodable, Identifiable {
    let id: String
    let content: String
    let created_at: String
}

struct MemoryListResponse: Decodable {
    let memories: [Memory]
}

struct AddMemoryResponse: Decodable {
    let id: String
}

struct ExtractMemoriesResponse: Decodable {
    let memories: [Memory]
}

// MARK: - File upload

struct UploadResult: Decodable {
    let type: String          // "image" | "text" | "audio" | "video" | "binary"
    let name: String
    let data: String?         // base64 data-URL for images
    let content: String?      // extracted text for PDFs / text files
}

// MARK: - System

struct SystemStats: Decodable {
    let ram_used_gb: Double?
    let ram_total_gb: Double?
}

struct StartupStatus: Decodable {
    let stage: String
    let progress: Int
    var detail: String?
}
