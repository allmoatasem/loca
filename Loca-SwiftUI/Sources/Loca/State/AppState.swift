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

    // MARK: - Capability & Model

    @Published var selectedCapability: ModelCapability = .general
    @Published var availableModels: [LMModel] = []
    @Published var selectedModelId: String?

    /// Capabilities that have at least one loaded model.
    var availableCapabilities: [ModelCapability] {
        let found = Set(availableModels.flatMap { $0.capabilities })
        return ModelCapability.allCases.filter { found.contains($0) }
    }

    func models(for capability: ModelCapability) -> [LMModel] {
        availableModels.filter { $0.capabilities.contains(capability) }
    }

    // MARK: - Conversations

    @Published var conversations: [ConversationMeta] = []
    @Published var activeConversationId: String?
    @Published var messages: [ChatMessage] = []

    // MARK: - Streaming

    @Published var isStreaming    = false
    @Published var streamingText  = ""
    @Published var actualModel: String?

    // MARK: - Generation stats

    struct GenerationStats {
        let model: String
        let promptTokens: Int
        let completionTokens: Int
        let ttftMs: Double
        let totalMs: Double
        let searchTriggered: Bool
        let memoryInjected: Bool
        var tokensPerSec: Double {
            guard completionTokens > 0, totalMs > 1 else { return 0 }
            return Double(completionTokens) / (totalMs / 1000)
        }
    }
    @Published var lastStats: GenerationStats?

    // MARK: - Memories

    @Published var memories: [Memory] = []
    @Published var isMemoryPanelOpen       = false
    @Published var isExtractingMemories    = false
    @Published var memoryExtractionError: String?

    // MARK: - Research mode

    @Published var researchMode = false

    // MARK: - System stats

    @Published var ramUsed:  Double?
    @Published var ramTotal: Double?

    // MARK: - Local models

    @Published var localModels: [LocalModel] = []
    @Published var activeModelName: String?
    @Published var activeBackend: String?
    @Published var isLoadingModel     = false
    @Published var modelLoadError: String?
    @Published var isSettingsOpen     = false

    // MARK: - Active download (persists across sheet open/close)

    struct ActiveDownload {
        var repoId: String
        var filename: String?
        var format: String
        var downloadId: String
        var percent: Double       // -1 = indeterminate
        var speedMbps: Double = 0
        var etaSeconds: Double = 0
        var totalBytes: Int64 = 0
        var done: Bool = false
        var paused: Bool = false
        var error: String?
    }
    @Published var activeDownload: ActiveDownload?

    // MARK: - Hardware & recommendations

    @Published var hardwareProfile: HardwareProfile?
    @Published var recommendedModels: [ModelRecommendation] = []
    @Published var isLoadingRecommendations = false
    @Published var isInstallingLlmfit = false
    @Published var llmfitAvailable = false

    // MARK: - Settings

    @Published var contextWindow: Int  = 32768
    @Published var isDarkMode: Bool    = UserDefaults.standard.bool(forKey: "isDarkMode")

    // MARK: - Conversation search

    @Published var conversationQuery   = ""
    @Published var conversationResults: [ConversationMeta] = []

    // MARK: - Actions (implementations provided in AppState+Actions.swift)

    func startHealthPolling() { _startHealthPolling() }
    func loadModels()          { _loadModels() }
    func newConversation()     { _newConversation() }
    func loadConversation(_ id: String) { Task { await _loadConversation(id) } }
    func deleteConversation(_ id: String) { Task { await _deleteConversation(id) } }
    func send(_ text: String, attachments: [UploadResult] = []) { Task { await _send(text, attachments: attachments) } }
    func loadMemories()         { Task { await _loadMemories() } }
    func extractMemories()      { Task { await _extractMemories() } }
    func pollSystemStats()      { Task { await _pollSystemStats() } }
    func reloadConversations()  { Task { await _loadConversationList() } }
    func toggleStar(_ id: String)                          { Task { await _toggleStar(id) } }
    func setConversationFolder(_ id: String, folder: String?) { Task { await _setFolder(id, folder: folder) } }
    func searchConversations()                             { Task { await _searchConversations() } }
    func reloadLocalModels()    { Task { await _loadLocalModels() } }
    func loadModel(_ name: String, ctxSize: Int? = nil) { Task { await _loadModel(name, ctxSize: ctxSize) } }
    func deleteModel(_ name: String) { Task { await _deleteModel(name) } }
    func unloadModel()               { Task { await _unloadModel() } }
    func reloadRecommendations() { Task { await _loadRecommendations(force: true) } }
    func loadRecommendationsIfNeeded() { guard recommendedModels.isEmpty else { return }; Task { await _loadRecommendations(force: false) } }
    func installLlmfit()         { Task { await _installLlmfit() } }
    func startModelDownload(repoId: String, filename: String?, format: String) { _startDownload(repoId: repoId, filename: filename, format: format) }
    func pauseDownload()  { Task { await _pauseDownload() } }
    func resumeDownload() {
        guard let dl = activeDownload, dl.paused else { return }
        activeDownload?.paused = false
        _startDownload(repoId: dl.repoId, filename: dl.filename, format: dl.format)
    }
    func cancelDownload() { Task { await _cancelDownload() } }
}

