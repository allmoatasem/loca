import Foundation

// Action implementations split out to keep AppState.swift focused on shape.
extension AppState {

    // MARK: - Backend health polling

    func _startHealthPolling() {
        Task {
            while !isBackendReady {
                // Update startup status from the file written by start_services.sh
                if let s = await BackendClient.shared.startupStatus() {
                    startupStatus = s
                }
                if await BackendClient.shared.isHealthy() {
                    isBackendReady = true
                    await _loadLocalModels()
                    _loadModels()
                    await _loadConversationList()
                    await _loadMemories()
                    _scheduleStatsPoll()
                    if lmStudioMode {
                        await _checkServerStatus()
                        _scheduleServerStatusPoll()
                    }
                    break
                }
                try? await Task.sleep(for: .seconds(0.75))
            }
        }
    }

    // MARK: - External server status

    func _checkServerStatus() async {
        guard isBackendReady else { return }
        if let status = try? await BackendClient.shared.fetchServerStatus() {
            if status.mode != "native" {
                externalServerRunning = status.server_running ?? false
                externalModels = status.models ?? []
            }
        }
    }

    func _startExternalServer() async {
        isStartingExternalServer = true
        do {
            try await BackendClient.shared.startExternalServer()
            // Give the server a few seconds to start up, then recheck
            try? await Task.sleep(for: .seconds(4))
            await _checkServerStatus()
        } catch {
            // Non-fatal — user will see "not running" state still
        }
        isStartingExternalServer = false
    }

    private func _scheduleServerStatusPoll() {
        Task {
            while isBackendReady && lmStudioMode {
                try? await Task.sleep(for: .seconds(8))
                await _checkServerStatus()
            }
        }
    }

    // MARK: - Local models

    func _loadLocalModels() async {
        do {
            localModels = try await BackendClient.shared.fetchLocalModels()
            let active = try? await BackendClient.shared.activeModel()
            activeModelName = active?.name
            activeBackend   = active?.backend
        } catch {
            // Non-fatal
        }
    }

    func _loadModel(_ name: String, ctxSize: Int? = nil) async {
        isLoadingModel = true
        modelLoadError = nil
        do {
            try await BackendClient.shared.loadModel(
                name: name,
                ctxSize: ctxSize,
                nGpuLayers: nGpuLayers,
                batchSize: batchSize,
                numThreads: numThreads
            )
            await _loadLocalModels()
        } catch {
            modelLoadError = error.localizedDescription
        }
        isLoadingModel = false
    }

    func _suggestPerformanceParams() async {
        isSuggestingParams = true
        paramSuggestionError = nil
        do {
            let vram: Double? = nvidiaVramGb > 0 ? nvidiaVramGb : nil
            let suggestion = try await BackendClient.shared.suggestParams(nvidiaVramGb: vram)
            if suggestion.source == "nvidia_no_vram" {
                paramSuggestionError = "Enter your GPU VRAM size above, then try again."
            } else {
                if let ngl = suggestion.n_gpu_layers { nGpuLayers = ngl }
                if let bs  = suggestion.batch_size    { batchSize  = bs  }
                if let nt  = suggestion.num_threads   { numThreads = nt  }
            }
        } catch {
            paramSuggestionError = error.localizedDescription
        }
        isSuggestingParams = false
    }

    func _unloadModel() async {
        do {
            try await BackendClient.shared.unloadModel()
            await _loadLocalModels()
        } catch {
            modelLoadError = error.localizedDescription
        }
    }

    func _deleteModel(_ name: String) async {
        do {
            try await BackendClient.shared.deleteModel(name: name)
            await _loadLocalModels()
        } catch {
            modelLoadError = error.localizedDescription
        }
    }

    // MARK: - Downloads

    func _pauseDownload() async {
        guard let dl = activeDownload, !dl.done, dl.error == nil, !dl.downloadId.isEmpty else { return }
        try? await BackendClient.shared.pauseDownload(id: dl.downloadId)
        activeDownload?.paused = true
        activeDownload?.speedMbps = 0
        activeDownload?.etaSeconds = 0
    }

    func _cancelDownload() async {
        guard let dl = activeDownload else { return }
        try? await BackendClient.shared.cancelDownload(id: dl.downloadId)
        activeDownload = nil
    }

    func _startDownload(repoId: String, filename: String?, format: String) {
        // Show progress immediately so the UI isn't stuck with no feedback.
        // When resuming a paused download, preserve the last known percent and
        // total size so the bar doesn't jump back to 0% / indeterminate.
        if activeDownload == nil {
            activeDownload = ActiveDownload(
                repoId: repoId, filename: filename, format: format,
                downloadId: "", percent: -1
            )
        } else {
            // Resume path — clear transient state while keeping progress figures
            activeDownload?.paused = false
            activeDownload?.downloadId = ""
            activeDownload?.error = nil
            activeDownload?.speedMbps = 0
            activeDownload?.etaSeconds = 0
        }
        Task {
            do {
                let dlId = try await BackendClient.shared.startDownload(
                    repoId: repoId, filename: filename, format: format
                )
                activeDownload?.downloadId = dlId
                await _pollDownload(dlId)
            } catch {
                activeDownload?.error = error.localizedDescription
            }
        }
    }

    func _pollDownload(_ dlId: String) async {
        guard let url = URL(string: "http://localhost:8000/api/models/download/\(dlId)/progress") else { return }
        // Use a long-lived session so the SSE stream doesn't time out during large downloads
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest  = 3600    // wait up to 1h between bytes
        config.timeoutIntervalForResource = 86400   // allow up to 24h total
        let session = URLSession(configuration: config)
        do {
            let (bytes, _) = try await session.bytes(from: url)
            var buffer = ""
            for try await byte in bytes {
                buffer += String(bytes: [byte], encoding: .utf8) ?? ""
                while let nl = buffer.firstIndex(of: "\n") {
                    let line = String(buffer[..<nl])
                    buffer = String(buffer[buffer.index(after: nl)...])
                    if line.hasPrefix("data: "),
                       let data = String(line.dropFirst(6)).data(using: .utf8),
                       let p = try? JSONDecoder().decode(DownloadProgress.self, from: data) {
                        // Check for terminal events first — don't let a cancelled/error
                        // event overwrite the last known progress percent.
                        if let err = p.error {
                            if err != "cancelled" { activeDownload?.error = err }
                            return
                        }
                        activeDownload?.percent    = p.percent
                        activeDownload?.speedMbps  = p.speed_mbps
                        activeDownload?.etaSeconds = p.eta_s
                        if let tb = p.total_bytes, tb > 0 { activeDownload?.totalBytes = tb }
                        if p.done {
                            activeDownload?.done = true
                            activeDownload?.percent = 100
                            await _loadLocalModels()
                            return
                        }
                    }
                }
            }
        } catch {
            // The server closes the SSE connection after sending done=true, which can cause
            // URLSession to throw a network error. Only surface it if we didn't already succeed.
            if activeDownload?.done != true {
                activeDownload?.error = error.localizedDescription
            }
        }
    }

    // MARK: - Hardware & recommendations

    func _loadRecommendations(force: Bool = false) async {
        isLoadingRecommendations = true
        do {
            let hw = try await BackendClient.shared.fetchHardwareProfile()
            hardwareProfile = hw
            llmfitAvailable = hw.llmfit_available
            let resp = try await BackendClient.shared.fetchRecommendedModels(force: force)
            recommendedModels = resp.recommendations
        } catch {
            // Non-fatal
        }
        isLoadingRecommendations = false
    }

    func _installLlmfit() async {
        isInstallingLlmfit = true
        do {
            let ok = try await BackendClient.shared.installLlmfit()
            if ok {
                llmfitAvailable = true
                await _loadRecommendations()
            }
        } catch {
            // Non-fatal
        }
        isInstallingLlmfit = false
    }

    // MARK: - Models (capability routing, uses local model names)

    func _loadModels() {
        Task {
            do {
                availableModels = try await BackendClient.shared.fetchLMModels()
                if models(for: selectedCapability).isEmpty {
                    selectedCapability = .general
                }
                let capModels = models(for: selectedCapability)
                let ids = availableModels.map(\.id)
                if selectedModelId == nil || !ids.contains(selectedModelId!) {
                    selectedModelId = capModels.first?.id ?? availableModels.first?.id
                }
            } catch {
                // Non-fatal: model list stays empty
            }
        }
    }

    // MARK: - Conversations

    func _newConversation() {
        if !messages.isEmpty {
            Task { await _saveCurrentConversation() }
        }
        messages = []
        activeConversationId = nil
        streamingText = ""
    }

    func _loadConversationList() async {
        do {
            conversations = try await BackendClient.shared.listConversations()
        } catch {}
    }

    func _loadConversation(_ id: String) async {
        do {
            let detail = try await BackendClient.shared.getConversation(id)
            messages = detail.messages
            activeConversationId = id
        } catch {}
    }

    func _deleteConversation(_ id: String) async {
        do {
            try await BackendClient.shared.deleteConversation(id)
            conversations.removeAll { $0.id == id }
            if activeConversationId == id { _newConversation() }
        } catch {}
    }

    func _saveCurrentConversation() async {
        let userMessages = messages.filter { $0.role == "user" }
        guard let first = userMessages.first else { return }
        let title = String(first.content.plainText.prefix(60))
        let req = SaveConversationRequest(
            id: activeConversationId,
            title: title,
            messages: messages,
            model: selectedModelId ?? selectedCapability.modeHint
        )
        do {
            let id = try await BackendClient.shared.saveConversation(req)
            activeConversationId = id
            await _loadConversationList()
        } catch {}
    }

    // MARK: - Send message

    func _send(_ text: String, attachments: [UploadResult] = []) async {
        guard !isStreaming else { return }

        // Guard: no model available in native mode — show a helpful prompt instead
        // of a raw connection error.
        if isBackendReady && !lmStudioMode && !isRemoteServer {
            if localModels.isEmpty {
                messages.append(ChatMessage(role: "user", content: .text(text)))
                messages.append(ChatMessage(role: "assistant", content: .text(
                    "⚠️ No models downloaded yet. Open **Settings → Models** to download a model first."
                )))
                return
            }
            if !localModels.contains(where: { $0.is_loaded }) {
                messages.append(ChatMessage(role: "user", content: .text(text)))
                messages.append(ChatMessage(role: "assistant", content: .text(
                    "⚠️ No model is loaded. Open **Settings → Models** and tap **Load** next to a model."
                )))
                return
            }
        }

        // Guard: reject images for non-vision models
        let imageAttachments = attachments.filter { $0.type == "image" }
        if !imageAttachments.isEmpty {
            let loadedModel = localModels.first(where: { $0.is_loaded })
            let isVision = loadedModel?.supports_vision == true
            if !isVision {
                let errorMsg = ChatMessage(
                    role: "assistant",
                    content: .text("⚠️ The current model (**\(activeModelName ?? "unknown")**) does not support images. Please load a vision model and try again.")
                )
                messages.append(errorMsg)
                return
            }
        }

        // Build message content (text + images)
        let content: MessageContent
        let textAttachments  = attachments.filter { $0.type != "image" }
        let textPrefix = textAttachments
            .compactMap { a -> String? in
                switch a.type {
                case "text":  return "<attachment name=\"\(a.name)\">\n\(a.content ?? "")\n</attachment>"
                case "audio": return "[Audio file: \(a.name)]"
                case "video": return "[Video file: \(a.name)]"
                default:      return "[File: \(a.name)]"
                }
            }
            .joined(separator: "\n\n")

        let fullText = [textPrefix, text].filter { !$0.isEmpty }.joined(separator: "\n\n")

        if imageAttachments.isEmpty {
            content = .text(fullText)
        } else {
            var parts: [ContentPart] = [.text(.init(type: "text", text: fullText))]
            for img in imageAttachments {
                if let url = img.data {
                    parts.append(.image(.init(type: "image_url", image_url: .init(url: url))))
                }
            }
            content = .parts(parts)
        }

        let userMsg = ChatMessage(role: "user", content: content)
        messages.append(userMsg)

        let assistantPlaceholder = ChatMessage(role: "assistant", content: .text(""))
        messages.append(assistantPlaceholder)
        let assistantIdx = messages.count - 1

        isStreaming   = true
        streamingText = ""
        actualModel   = nil

        let request = ChatRequest(
            mode:           selectedCapability.modeHint,
            model_override: selectedModelId,
            messages:       messages.dropLast().map { $0 },   // don't send the empty placeholder
            stream:         true,
            num_ctx:        contextWindow,
            research_mode:  researchMode && !lockdownMode,
            temperature:    temperature,
            top_p:          topP,
            top_k:          topK,
            repeat_penalty: repeatPenalty,
            max_tokens:     maxTokens,
            system_prompt_override: systemPromptOverride.isEmpty ? nil : systemPromptOverride
        )

        var full = ""
        let tStart = Date()
        var firstTokenTime: Date?
        var lastUsage: UsageStats?

        do {
            for try await raw in BackendClient.shared.streamChat(request) {
                guard let data = raw.data(using: .utf8),
                      let chunk = try? JSONDecoder().decode(SSEChunk.self, from: data) else { continue }

                if let model = chunk.model, actualModel == nil { actualModel = model }
                if let u = chunk.usage { lastUsage = u }

                if let delta = chunk.choices?.first?.delta.content {
                    if firstTokenTime == nil { firstTokenTime = Date() }
                    full += delta
                    streamingText = full
                    messages[assistantIdx] = ChatMessage(role: "assistant", content: .text(full))
                }
            }
        } catch {
            full += "\n\n[Error: \(error.localizedDescription)]"
            messages[assistantIdx] = ChatMessage(role: "assistant", content: .text(full))
        }

        streamingText = ""
        isStreaming   = false

        let totalMs = Date().timeIntervalSince(tStart) * 1000
        let ttftMs  = firstTokenTime.map { $0.timeIntervalSince(tStart) * 1000 } ?? 0
        lastStats = GenerationStats(
            model:            actualModel ?? selectedCapability.modeHint,
            promptTokens:     lastUsage?.prompt_tokens     ?? 0,
            completionTokens: lastUsage?.completion_tokens ?? 0,
            ttftMs:           ttftMs,
            totalMs:          totalMs,
            searchTriggered:  lastUsage?.search_triggered  ?? false,
            memoryInjected:   lastUsage?.memory_injected   ?? false
        )

        // Save conversation after each turn
        await _saveCurrentConversation()
    }

    // MARK: - Memories

    func _loadMemories() async {
        do { memories = try await BackendClient.shared.listMemories() } catch {}
    }

    func _extractMemories() async {
        guard !messages.isEmpty else { return }
        isExtractingMemories = true
        memoryExtractionError = nil
        defer { isExtractingMemories = false }
        let msgs = messages.map { ["role": $0.role, "content": $0.content.plainText] }
        do {
            let extracted = try await BackendClient.shared.extractMemories(messages: msgs, convId: activeConversationId)
            // Deduplicate by id before appending
            let existing = Set(memories.map(\.id))
            memories.append(contentsOf: extracted.filter { !existing.contains($0.id) })
        } catch {
            memoryExtractionError = error.localizedDescription
        }
    }

    // MARK: - Conversation star / folder / search

    func _toggleStar(_ id: String) async {
        guard let idx = conversations.firstIndex(where: { $0.id == id }) else { return }
        let newVal = !conversations[idx].starred
        do {
            try await BackendClient.shared.patchConversation(id, starred: newVal)
            await _loadConversationList()
        } catch {}
    }

    func _setFolder(_ id: String, folder: String?) async {
        do {
            try await BackendClient.shared.patchConversation(id, folder: .some(folder))
            await _loadConversationList()
        } catch {}
    }

    func _searchConversations() async {
        let q = conversationQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { conversationResults = []; return }
        do {
            conversationResults = try await BackendClient.shared.searchConversations(q)
        } catch {
            conversationResults = []
        }
    }

    // MARK: - Vault

    func _detectVaults() async {
        do {
            detectedVaults = try await BackendClient.shared.detectVaults()
            if selectedVaultPath.isEmpty, let first = detectedVaults.first {
                selectedVaultPath = first.path
            }
        } catch {}
    }

    func _scanVault() async {
        guard !selectedVaultPath.isEmpty else { return }
        isVaultScanning = true
        vaultError = nil
        vaultScanResult = nil
        do {
            let result = try await BackendClient.shared.scanVault(path: selectedVaultPath)
            vaultScanResult = result
            if let err = result.error { vaultError = err }
            else { await _analyseVault() }
        } catch { vaultError = error.localizedDescription }
        isVaultScanning = false
    }

    func _analyseVault() async {
        guard !selectedVaultPath.isEmpty else { return }
        isVaultAnalysing = true
        vaultError = nil
        do { vaultAnalysis = try await BackendClient.shared.vaultAnalysis(path: selectedVaultPath) }
        catch { vaultError = error.localizedDescription }
        isVaultAnalysing = false
    }

    func _vaultSearch(_ query: String) async {
        guard !selectedVaultPath.isEmpty, !query.trimmingCharacters(in: .whitespaces).isEmpty else {
            vaultSearchResults = []
            return
        }
        isVaultSearching = true
        vaultSearchError = nil
        do { vaultSearchResults = try await BackendClient.shared.semanticSearch(path: selectedVaultPath, query: query) }
        catch { vaultSearchError = error.localizedDescription }
        isVaultSearching = false
    }

    // MARK: - System stats

    private func _scheduleStatsPoll() {
        Task {
            while isBackendReady {
                await _pollSystemStats()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    func _pollSystemStats() async {
        if let s = try? await BackendClient.shared.systemStats() {
            ramUsed  = s.ram_used_gb
            ramTotal = s.ram_total_gb
        }
    }
}
