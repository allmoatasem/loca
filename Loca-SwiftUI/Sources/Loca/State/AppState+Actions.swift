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
                    await _loadModels()
                    await _loadConversationList()
                    await _loadMemories()
                    _scheduleStatsPoll()
                    break
                }
                try? await Task.sleep(for: .seconds(1.5))
            }
        }
    }

    // MARK: - Models

    func _loadModels() {
        Task {
            do {
                availableModels = try await BackendClient.shared.fetchLMModels()
                if selectedModelId == nil, let first = availableModels.first {
                    selectedModelId = first.id
                }
            } catch {
                // Non-fatal: model list just stays empty
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
            model: selectedModelId ?? selectedMode.rawValue
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

        // Build message content (text + images)
        let content: MessageContent
        let imageAttachments = attachments.filter { $0.type == "image" }
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
            mode:           selectedMode.rawValue,
            model_override: selectedModelId,
            messages:       messages.dropLast().map { $0 },   // don't send the empty placeholder
            stream:         true,
            num_ctx:        contextWindow,
            research_mode:  researchMode
        )

        var full = ""
        var tStart = Date()

        do {
            for try await raw in BackendClient.shared.streamChat(request) {
                guard let data = raw.data(using: .utf8),
                      let chunk = try? JSONDecoder().decode(SSEChunk.self, from: data) else { continue }

                if let model = chunk.model, actualModel == nil { actualModel = model }

                if let delta = chunk.choices?.first?.delta.content {
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

        // Save conversation after each turn
        await _saveCurrentConversation()
    }

    // MARK: - Memories

    func _loadMemories() async {
        do { memories = try await BackendClient.shared.listMemories() } catch {}
    }

    func _extractMemories() async {
        let msgs = messages.map { ["role": $0.role, "content": $0.content.plainText] }
        do {
            let extracted = try await BackendClient.shared.extractMemories(messages: msgs, convId: activeConversationId)
            memories.append(contentsOf: extracted)
        } catch {}
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
