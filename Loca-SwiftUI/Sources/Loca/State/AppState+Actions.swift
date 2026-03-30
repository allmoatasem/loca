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
                    break
                }
                try? await Task.sleep(for: .seconds(1.5))
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
            try await BackendClient.shared.loadModel(name: name, ctxSize: ctxSize)
            await _loadLocalModels()
        } catch {
            modelLoadError = error.localizedDescription
        }
        isLoadingModel = false
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

    func _startDownload(repoId: String, filename: String?, format: String) {
        Task {
            do {
                let dlId = try await BackendClient.shared.startDownload(
                    repoId: repoId, filename: filename, format: format
                )
                activeDownload = ActiveDownload(
                    repoId: repoId, filename: filename, format: format,
                    downloadId: dlId, percent: -1
                )
                await _pollDownload(dlId)
            } catch {
                activeDownload?.error = error.localizedDescription
            }
        }
    }

    func _pollDownload(_ dlId: String) async {
        guard let url = URL(string: "http://localhost:8000/api/models/download/\(dlId)/progress") else { return }
        do {
            let (bytes, _) = try await URLSession.shared.bytes(from: url)
            var buffer = ""
            for try await byte in bytes {
                buffer += String(bytes: [byte], encoding: .utf8) ?? ""
                while let nl = buffer.firstIndex(of: "\n") {
                    let line = String(buffer[..<nl])
                    buffer = String(buffer[buffer.index(after: nl)...])
                    if line.hasPrefix("data: "),
                       let data = String(line.dropFirst(6)).data(using: .utf8),
                       let p = try? JSONDecoder().decode(DownloadProgress.self, from: data) {
                        activeDownload?.percent    = p.percent
                        activeDownload?.speedMbps  = p.speed_mbps
                        activeDownload?.etaSeconds = p.eta_s
                        if let tb = p.total_bytes, tb > 0 { activeDownload?.totalBytes = tb }
                        if let err = p.error { activeDownload?.error = err; return }
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
            activeDownload?.error = error.localizedDescription
        }
    }

    // MARK: - Hardware & recommendations

    func _loadRecommendations() async {
        isLoadingRecommendations = true
        do {
            let hw = try await BackendClient.shared.fetchHardwareProfile()
            hardwareProfile = hw
            llmfitAvailable = hw.llmfit_available
            let resp = try await BackendClient.shared.fetchRecommendedModels()
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

        // Guard: reject images for non-vision models
        let imageAttachments = attachments.filter { $0.type == "image" }
        if !imageAttachments.isEmpty {
            let modelId = (activeModelName ?? selectedModelId ?? "").lowercased()
            let isVision = LMModel(id: modelId).capabilities.contains(.vision)
            if !isVision {
                let errorMsg = ChatMessage(
                    role: "assistant",
                    content: .text("⚠️ The current model (**\(activeModelName ?? "unknown")**) does not support images. Please load a vision model (e.g. one containing \"llava\", \"-vl\", or \"minicpm-v\" in the name) and try again.")
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
            research_mode:  researchMode
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
