import Foundation
import SwiftUI

// MARK: - Chat

struct ChatRequest: Encodable {
    let mode: String
    let model_override: String?
    let messages: [ChatMessage]
    let stream: Bool
    let num_ctx: Int
    let research_mode: Bool
    // Inference preferences
    let temperature: Double?
    let top_p: Double?
    let top_k: Int?
    let repeat_penalty: Double?
    let max_tokens: Int?
    let system_prompt_override: String?
}

// MARK: - Preferences

enum ThemeMode: String, CaseIterable {
    case system, light, dark

    var label: String {
        switch self {
        case .system: return "System"
        case .light:  return "Light"
        case .dark:   return "Dark"
        }
    }

    var icon: String {
        switch self {
        case .system: return "circle.lefthalf.filled"
        case .light:  return "sun.max"
        case .dark:   return "moon"
        }
    }
}

struct InferenceRecipe: Identifiable, Equatable {
    let name: String
    let temperature: Double
    let topP: Double
    let topK: Int
    let repeatPenalty: Double
    let maxTokens: Int
    var id: String { name }

    static let all: [InferenceRecipe] = [
        InferenceRecipe(name: "Balanced",  temperature: 0.70, topP: 0.90, topK: 40, repeatPenalty: 1.10, maxTokens: 2048),
        InferenceRecipe(name: "Creative",  temperature: 1.00, topP: 0.95, topK: 50, repeatPenalty: 1.00, maxTokens: 2048),
        InferenceRecipe(name: "Precise",   temperature: 0.20, topP: 0.80, topK: 20, repeatPenalty: 1.15, maxTokens: 2048),
        InferenceRecipe(name: "Fast",      temperature: 0.70, topP: 0.90, topK: 40, repeatPenalty: 1.10, maxTokens: 512),
        InferenceRecipe(name: "Custom",    temperature: 0.70, topP: 0.90, topK: 40, repeatPenalty: 1.10, maxTokens: 2048),
    ]

    static func named(_ name: String) -> InferenceRecipe {
        all.first { $0.name == name } ?? all[0]
    }
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
    let search_triggered: Bool?
    let memory_injected: Bool?
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

// MARK: - Local models (managed by Loca's own inference backend)

struct LocalModel: Decodable, Identifiable {
    let name: String
    let path: String
    let format: String     // "gguf" or "mlx"
    let size_gb: Double
    let is_loaded: Bool
    let context_length: Int?
    let param_label: String?

    var id: String { name }

    var formatLabel: String { format.uppercased() }

    var sizeLabel: String {
        size_gb >= 1 ? String(format: "%.1f GB", size_gb)
                     : String(format: "%.0f MB", size_gb * 1024)
    }

    var contextLabel: String? {
        guard let ctx = context_length, ctx > 0 else { return nil }
        return ctx >= 1024 ? "\(ctx / 1024)K ctx" : "\(ctx) ctx"
    }
}

struct LocalModelsResponse: Decodable {
    let models: [LocalModel]
}

struct LoadModelRequest: Encodable {
    let name: String
    let ctx_size: Int?
    let n_gpu_layers: Int?
    let batch_size: Int?
    let num_threads: Int?
}

struct PerformanceSuggestion: Decodable {
    let n_gpu_layers: Int?
    let batch_size: Int?
    let num_threads: Int?
    /// "apple_silicon" | "nvidia" | "nvidia_no_vram" | "cpu_only"
    let source: String
}

struct ActiveModelResponse: Decodable {
    let name: String?
    let backend: String?
    let api_base: String?
    let running: Bool
}

struct DownloadProgress: Decodable {
    let percent: Double     // -1 = indeterminate
    let speed_mbps: Double
    let eta_s: Double
    let done: Bool
    let error: String?
    let total_bytes: Int64?
}

// MARK: - Hardware profiling & recommendations

struct HardwareProfile: Decodable {
    let platform: String
    let arch: String
    let cpu_name: String
    let total_ram_gb: Double
    let available_ram_gb: Double
    let has_apple_silicon: Bool
    let has_nvidia_gpu: Bool
    let supports_mlx: Bool
    let llmfit_available: Bool
}

struct ModelRecommendation: Decodable, Identifiable {
    let name: String
    let repo_id: String
    let filename: String?
    let format: String        // "gguf" or "mlx"
    let size_gb: Double
    let quant: String
    let context: Int
    let why: String
    let fit_level: String     // e.g. "Perfect Fit" | "Good Fit" | "Tight Fit"
    let use_case: String      // e.g. "code" | "reasoning" | "vision" | "general"
    let provider: String      // e.g. "Alibaba" | "Meta" | "Mistral" | "NVIDIA"
    let score: Double         // llmfit fit score 0–100
    let tps: Double           // estimated tokens/sec on this hardware

    // Synthesise missing keys for backward compat with old backend responses
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name      = try c.decode(String.self, forKey: .name)
        repo_id   = try c.decode(String.self, forKey: .repo_id)
        filename  = try? c.decode(String.self, forKey: .filename)
        format    = try c.decode(String.self, forKey: .format)
        size_gb   = try c.decode(Double.self, forKey: .size_gb)
        quant     = try c.decode(String.self, forKey: .quant)
        context   = try c.decode(Int.self, forKey: .context)
        why       = try c.decode(String.self, forKey: .why)
        fit_level = (try? c.decode(String.self, forKey: .fit_level)) ?? ""
        use_case  = (try? c.decode(String.self, forKey: .use_case)) ?? ""
        provider  = (try? c.decode(String.self, forKey: .provider)) ?? ""
        score     = (try? c.decode(Double.self, forKey: .score)) ?? 0
        tps       = (try? c.decode(Double.self, forKey: .tps)) ?? 0
    }
    private enum CodingKeys: String, CodingKey {
        case name, repo_id, filename, format, size_gb, quant, context, why, fit_level, use_case, provider, score, tps
    }

    var id: String { repo_id + (filename ?? "") }
    var formatLabel: String { format.uppercased() }
    var sizeLabel: String {
        size_gb >= 1 ? String(format: "%.1f GB", size_gb)
                     : String(format: "%.0f MB", size_gb * 1024)
    }

    /// Inferred category key for filter tabs.
    var category: String {
        let src = (use_case + " " + name + " " + repo_id).lowercased()
        if src.contains("code") || src.contains("coder")               { return "code" }
        if src.contains("vision") || src.contains("-vl") || src.contains("llava") { return "vision" }
        if src.contains("reason") || src.contains("think") || src.contains("-r1") { return "reasoning" }
        return "general"
    }

    /// Traffic-light color based on fit_level from llmfit.
    var fitColor: Color {
        let l = fit_level.lowercased()
        if l.contains("perfect") { return .green }
        if l.contains("good")    { return .yellow }
        if l.contains("tight")   { return .red }
        if l.isEmpty             { return .secondary }
        return .orange
    }

    var fitLabel: String {
        fit_level.isEmpty ? "" : fit_level
    }

    /// Extract parameter count from model name, e.g. "7B", "80B", "122B".
    /// For MoE names like "80B-A3B" returns only the total ("80B").
    var paramLabel: String? {
        let pattern = #"(\d+(?:\.\d+)?B)(?:-A\d+(?:\.\d+)?B)?"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive),
              let match = regex.firstMatch(in: name, range: NSRange(name.startIndex..., in: name)),
              let range = Range(match.range(at: 1), in: name)
        else { return nil }
        return String(name[range]).uppercased()
    }
}

struct RecommendedModelsResponse: Decodable {
    let total_ram_gb: Double
    let has_apple_silicon: Bool
    let llmfit_available: Bool
    let recommendations: [ModelRecommendation]
}

struct RepoFile: Decodable, Identifiable {
    var id: String { name }
    let name: String
    let size_gb: Double

    var sizeLabel: String {
        size_gb >= 1 ? String(format: "%.1f GB", size_gb)
                     : String(format: "%.0f MB", size_gb * 1024)
    }

    /// E.g. "Q4_K_M" extracted from "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    var quantLabel: String? {
        let upper = name.uppercased()
        for q in ["Q4_K_M","Q5_K_M","Q4_K_S","Q6_K","Q8_0","IQ4_XS","Q3_K_M","Q2_K","F16","BF16"] {
            if upper.contains(q) { return q }
        }
        return nil
    }
}

// MARK: - Models (legacy, kept for capability detection logic)

struct LMModel: Decodable, Identifiable {
    let id: String

    var capabilities: Set<ModelCapability> {
        let lower = id.lowercased()
        var caps: Set<ModelCapability> = []

        let visionTerms = [
            "llava", "moondream", "bakllava", "minicpm-v", "idefics", "cogvlm",
            "internvl", "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
            "blip", "pixtral", "phi-vision", "phi3-vision", "phi3.5-vision",
            "gemini-vision", "paligemma", "florence",
        ]
        let hasVLComponent = lower.hasPrefix("vl-") || lower.contains("-vl-")
            || lower.hasSuffix("-vl") || lower.hasSuffix(".vl")
        if visionTerms.contains(where: { lower.contains($0) }) || hasVLComponent {
            caps.insert(.vision)
        }

        let thinkTerms = [
            "qwq", "deepseek-r1", "r1-distill", "thinking",
            "skywork-o1", "marco-o1", "nemotron",
        ]
        let hasR1Component = lower.contains("-r1-") || lower.hasSuffix("-r1")
            || lower.contains(":r1")
        if thinkTerms.contains(where: { lower.contains($0) }) || hasR1Component {
            caps.insert(.thinking)
        }

        let codeTerms = [
            "coder", "codellama", "code-llama", "starcoder", "deepseek-coder",
            "codestral", "codegemma", "codeqwen", "wizardcoder", "phind-code",
            "magicoder", "qwen2.5-coder", "qwen3-coder",
        ]
        if codeTerms.contains(where: { lower.contains($0) }) {
            caps.insert(.code)
        }

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
    let updated: Double   // Unix timestamp (matches store column name)
    let starred: Bool
    let folder: String?

    var updatedDate: Date { Date(timeIntervalSince1970: updated) }

    private enum CodingKeys: String, CodingKey { case id, title, model, updated, starred, folder }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id      = try c.decode(String.self, forKey: .id)
        title   = try c.decode(String.self, forKey: .title)
        model   = try c.decode(String.self, forKey: .model)
        updated = try c.decode(Double.self, forKey: .updated)
        // SQLite returns INTEGER 0/1 for booleans
        starred = ((try? c.decode(Int.self, forKey: .starred)) ?? 0) != 0
        folder  = try? c.decode(String.self, forKey: .folder)
    }
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
    let created: Double   // Unix timestamp (matches store column name)
    let conv_id: String?
    let type: String?     // "user_fact" | "knowledge" | "correction"

    var createdDate: Date { Date(timeIntervalSince1970: created) }

    var typeLabel: String {
        switch type {
        case "knowledge":  return "Verified"
        case "correction": return "Correction"
        default:           return "Fact"
        }
    }

    var typeColor: String {
        switch type {
        case "knowledge":  return "blue"
        case "correction": return "orange"
        default:           return "secondary"
        }
    }
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
