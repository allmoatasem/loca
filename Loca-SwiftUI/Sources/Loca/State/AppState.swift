import Foundation
import Combine

/// Central observable state for the whole app.
/// Views read from here; mutations go through methods so logic stays testable.
@MainActor
final class AppState: ObservableObject {

    static let shared = AppState()

    // MARK: - Startup

    @Published var isBackendReady     = false
    @Published var startupStatus      = StartupStatus(stage: "Initialising…", progress: 0)
    @Published var startupError: String?

    // MARK: - Mode & Model

    @Published var selectedMode: ChatMode = .general
    @Published var availableModels: [LMModel] = []
    @Published var selectedModelId: String?    // nil = use config default for mode

    // MARK: - Conversations

    @Published var conversations: [ConversationMeta] = []
    @Published var activeConversationId: String?
    @Published var messages: [ChatMessage] = []

    // MARK: - Streaming

    @Published var isStreaming    = false
    @Published var streamingText  = ""   // partial assistant reply being built
    @Published var actualModel: String?  // model name reported by the backend

    // MARK: - Memories

    @Published var memories: [Memory] = []
    @Published var isMemoryPanelOpen  = false

    // MARK: - Research mode

    @Published var researchMode = false

    // MARK: - System stats

    @Published var ramUsed:  Double?
    @Published var ramTotal: Double?

    // MARK: - Settings

    @Published var contextWindow: Int = 32768
    @Published var isDarkMode: Bool   = false

    // MARK: - Actions (implementations provided in AppState+Actions.swift)

    func startHealthPolling() { _startHealthPolling() }
    func loadModels()          { _loadModels() }
    func newConversation()     { _newConversation() }
    func loadConversation(_ id: String) { Task { await _loadConversation(id) } }
    func deleteConversation(_ id: String) { Task { await _deleteConversation(id) } }
    func send(_ text: String, attachments: [UploadResult] = []) { Task { await _send(text, attachments: attachments) } }
    func loadMemories()        { Task { await _loadMemories() } }
    func extractMemories()     { Task { await _extractMemories() } }
    func pollSystemStats()     { Task { await _pollSystemStats() } }
}

// MARK: - ChatMode

enum ChatMode: String, CaseIterable, Identifiable {
    case general, code, reason, write
    var id: String { rawValue }

    var label: String {
        switch self {
        case .general: return "General"
        case .code:    return "Code"
        case .reason:  return "Reason"
        case .write:   return "Write"
        }
    }

    var description: String {
        switch self {
        case .general: return "Vision · chat · code · analysis"
        case .code:    return "Code generation · debugging · review"
        case .reason:  return "Planning · trade-offs · math · logic"
        case .write:   return "Drafting · editing · summarisation"
        }
    }
}
