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
    @Published var memoriesTotal: Int = 0
    @Published var isLoadingMoreMemories: Bool = false
    @Published var isMemoryPanelOpen          = false
    @Published var isAcknowledgementsOpen     = false
    @Published var isGlossaryOpen             = false
    @Published var isPhilosophyOpen           = false
    @Published var isExtractingMemories    = false
    @Published var memoryExtractionError: String?

    // MARK: - Research mode

    // Deep Dive = autonomous multi-role loop + Playwright full-page
    // content (consolidated from the old separate Research/Agent
    // toggles in omnibus #92).
    @Published var researchMode = false
    @Published var lockdownMode = false

    // MARK: - Research Partner (projects)

    @Published var projects: [Project] = []
    @Published var activeProjectId: String?
    @Published var partnerMode: PartnerMode = .default_
    @Published var isResearchOpen = false

    var activeProject: Project? {
        guard let id = activeProjectId else { return nil }
        return projects.first(where: { $0.id == id })
    }

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

    // LoRA adapter layered on the active model. `nil` = base-only.
    // `activateBusy` gates the Settings picker during the ~2–3 s
    // mlx_lm.server restart so the user can't double-click through.
    @Published var activeAdapter: String?
    @Published var adapters: [Adapter] = []
    @Published var activateBusy: Bool = false

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

    @Published var themeMode: ThemeMode = {
        let raw = UserDefaults.standard.string(forKey: "themeMode") ?? "system"
        return ThemeMode(rawValue: raw) ?? .system
    }() {
        didSet { UserDefaults.standard.set(themeMode.rawValue, forKey: "themeMode") }
    }

    @Published var contextWindow: Int = {
        let v = UserDefaults.standard.integer(forKey: "contextWindow")
        return v > 0 ? v : 32768
    }() {
        didSet { UserDefaults.standard.set(contextWindow, forKey: "contextWindow") }
    }

    @Published var temperature: Double = {
        let v = UserDefaults.standard.double(forKey: "temperature")
        return v > 0 ? v : 0.7
    }() {
        didSet { UserDefaults.standard.set(temperature, forKey: "temperature") }
    }

    @Published var topP: Double = {
        let v = UserDefaults.standard.double(forKey: "topP")
        return v > 0 ? v : 0.9
    }() {
        didSet { UserDefaults.standard.set(topP, forKey: "topP") }
    }

    @Published var topK: Int = {
        let v = UserDefaults.standard.integer(forKey: "topK")
        return v > 0 ? v : 40
    }() {
        didSet { UserDefaults.standard.set(topK, forKey: "topK") }
    }

    @Published var repeatPenalty: Double = {
        let v = UserDefaults.standard.double(forKey: "repeatPenalty")
        return v > 0 ? v : 1.1
    }() {
        didSet { UserDefaults.standard.set(repeatPenalty, forKey: "repeatPenalty") }
    }

    @Published var maxTokens: Int = {
        let v = UserDefaults.standard.integer(forKey: "maxTokens")
        return v > 0 ? v : 2048
    }() {
        didSet { UserDefaults.standard.set(maxTokens, forKey: "maxTokens") }
    }

    @Published var selectedRecipe: String = UserDefaults.standard.string(forKey: "selectedRecipe") ?? "Balanced" {
        didSet { UserDefaults.standard.set(selectedRecipe, forKey: "selectedRecipe") }
    }

    @Published var systemPromptOverride: String = UserDefaults.standard.string(forKey: "systemPromptOverride") ?? "" {
        didSet { UserDefaults.standard.set(systemPromptOverride, forKey: "systemPromptOverride") }
    }

    // Raw JSON strings forwarded verbatim to the backend. See
    // BackendClient.streamChat for the wire format. Kept as strings so
    // the user can paste any JSON; validation is client-side in
    // PreferencesView's Inference tab.
    @Published var chatTemplateKwargsJSON: String = UserDefaults.standard.string(forKey: "chatTemplateKwargsJSON") ?? "" {
        didSet { UserDefaults.standard.set(chatTemplateKwargsJSON, forKey: "chatTemplateKwargsJSON") }
    }
    @Published var extraBodyJSON: String = UserDefaults.standard.string(forKey: "extraBodyJSON") ?? "" {
        didSet { UserDefaults.standard.set(extraBodyJSON, forKey: "extraBodyJSON") }
    }

    // MARK: - Server connection

    @Published var serverHost: String = UserDefaults.standard.string(forKey: "serverHost") ?? "localhost" {
        didSet {
            UserDefaults.standard.set(serverHost, forKey: "serverHost")
            Task { await BackendClient.shared.updateBaseURL(host: serverHost) }
        }
    }

    var isRemoteServer: Bool { serverHost != "localhost" && serverHost != "127.0.0.1" }

    // MARK: - Performance params (backend tuning, applied at model load time)

    @Published var nGpuLayers: Int = {
        let v = UserDefaults.standard.integer(forKey: "nGpuLayers")
        return v > 0 ? v : 99
    }() {
        didSet { UserDefaults.standard.set(nGpuLayers, forKey: "nGpuLayers") }
    }

    @Published var batchSize: Int = {
        let v = UserDefaults.standard.integer(forKey: "batchSize")
        return v > 0 ? v : 512
    }() {
        didSet { UserDefaults.standard.set(batchSize, forKey: "batchSize") }
    }

    @Published var numThreads: Int = {
        let v = UserDefaults.standard.integer(forKey: "numThreads")
        return v > 0 ? v : 4
    }() {
        didSet { UserDefaults.standard.set(numThreads, forKey: "numThreads") }
    }

    /// Nvidia VRAM size entered by the user (GB). Zero means not set.
    @Published var nvidiaVramGb: Double = {
        UserDefaults.standard.double(forKey: "nvidiaVramGb")
    }() {
        didSet { UserDefaults.standard.set(nvidiaVramGb, forKey: "nvidiaVramGb") }
    }

    @Published var isSuggestingParams = false
    @Published var paramSuggestionError: String?

    // MARK: - Vault

    @Published var isVaultOpen          = false
    @Published var detectedVaults: [DetectedVault] = []
    @Published var selectedVaultPath: String = UserDefaults.standard.string(forKey: "vaultPath") ?? "" {
        didSet { UserDefaults.standard.set(selectedVaultPath, forKey: "vaultPath") }
    }
    @Published var vaultAnalysis: VaultAnalysis?
    @Published var isVaultScanning      = false
    @Published var isVaultAnalysing     = false
    @Published var vaultScanResult: VaultScanResult?
    @Published var vaultError: String?
    @Published var vaultSearchResults: [VaultSearchResult] = []
    @Published var isVaultSearching     = false
    @Published var vaultSearchError: String?

    // Obsidian Watcher — app-level background vault index. Mirror of
    // the `/api/obsidian/*` state. The watcher loop keeps these in
    // sync on its own tick; the UI polls periodically.
    @Published var watchedVaults: [WatchedVault] = []
    @Published var isRegisteringVault   = false
    @Published var watcherError: String?

    // MARK: - Voice mode

    @Published var isVoiceMode       = false
    @Published var isTranscribing    = false
    @Published var voiceError: String?
    @Published var voiceConfig: VoiceConfigResponse?
    @Published var showVoiceSetup    = false

    // MARK: - External server status (LM Studio / Ollama)

    /// nil = not yet checked; true/false = last known reachability
    @Published var externalServerRunning: Bool? = nil
    @Published var externalModels: [String] = []
    @Published var isStartingExternalServer = false

    // MARK: - Backend mode (native vs LM Studio)

    @Published var lmStudioMode: Bool = UserDefaults.standard.bool(forKey: "lmStudioMode") {
        didSet {
            UserDefaults.standard.set(lmStudioMode, forKey: "lmStudioMode")
            Task { try? await BackendClient.shared.setBackendMode(lmStudio: lmStudioMode, lmStudioUrl: lmStudioUrl) }
            if lmStudioMode {
                // Reset status so the UI re-checks immediately
                externalServerRunning = nil
                externalModels = []
                if isBackendReady { checkServerStatus() }
            }
        }
    }
    @Published var lmStudioUrl: String = UserDefaults.standard.string(forKey: "lmStudioUrl") ?? "http://localhost:1234" {
        didSet {
            UserDefaults.standard.set(lmStudioUrl, forKey: "lmStudioUrl")
        }
    }

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
    func loadMoreMemories() async { await _loadMoreMemories() }
    func extractMemories()      { Task { await _extractMemories() } }
    func pollSystemStats()      { Task { await _pollSystemStats() } }
    func reloadConversations()  { Task { await _loadConversationList() } }
    func toggleStar(_ id: String)                          { Task { await _toggleStar(id) } }
    func setConversationFolder(_ id: String, folder: String?) { Task { await _setFolder(id, folder: folder) } }
    func searchConversations()                             { Task { await _searchConversations() } }
    func reloadLocalModels()    { Task { await _loadLocalModels() } }
    func loadModel(_ name: String, ctxSize: Int? = nil) { Task { await _loadModel(name, ctxSize: ctxSize) } }
    func reloadAdapters(for model: String) { Task { await _loadAdapters(model) } }
    func activateAdapter(model: String, adapter: String?) { Task { await _activateAdapter(model: model, adapter: adapter) } }
    func suggestPerformanceParams() { Task { await _suggestPerformanceParams() } }
    func deleteModel(_ name: String) { Task { await _deleteModel(name) } }
    func unloadModel()               { Task { await _unloadModel() } }
    func reloadRecommendations() { Task { await _loadRecommendations(force: true) } }
    func loadRecommendationsIfNeeded() { guard recommendedModels.isEmpty else { return }; Task { await _loadRecommendations(force: false) } }
    func installLlmfit()         { Task { await _installLlmfit() } }
    func startModelDownload(repoId: String, filename: String?, format: String) { _startDownload(repoId: repoId, filename: filename, format: format) }
    func pauseDownload()  { Task { await _pauseDownload() } }
    func resumeDownload() {
        guard let dl = activeDownload, dl.paused else { return }
        _startDownload(repoId: dl.repoId, filename: dl.filename, format: dl.format)
    }
    func cancelDownload() { Task { await _cancelDownload() } }
    func detectVaults()   { Task { await _detectVaults() } }
    func scanVault()      { Task { await _scanVault() } }
    func analyseVault()   { Task { await _analyseVault() } }
    func selectVaultPath(_ path: String) { selectedVaultPath = path; analyseVault() }
    func vaultSearch(_ query: String) { Task { await _vaultSearch(query) } }
    func refreshWatchedVaults() { Task { await _refreshWatchedVaults() } }
    func registerWatchedVault(path: String) { Task { await _registerWatchedVault(path) } }
    func unregisterWatchedVault(path: String) { Task { await _unregisterWatchedVault(path) } }
    func scanWatchedVaultNow(path: String) { Task { await _scanWatchedVaultNow(path) } }
    func fetchVoiceConfig() { Task { do { voiceConfig = try await BackendClient.shared.fetchVoiceConfig() } catch {} } }
    func checkServerStatus()   { Task { await _checkServerStatus() } }
    func startExternalServer() { Task { await _startExternalServer() } }
}

