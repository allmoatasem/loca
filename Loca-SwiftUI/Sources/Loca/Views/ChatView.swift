import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - ChatView

struct ChatView: View {
    @EnvironmentObject var state: AppState
    @State private var inputText = ""
    @State private var attachments: [UploadResult] = []
    @State private var isUploading = false
    @StateObject private var inputActions = ChatInputActions()
    @State private var chatSearch = ""
    @State private var showChatSearch = false
    @StateObject private var voiceRecorder = AudioRecorder()
    @StateObject private var voicePlayer = AudioPlayer()
    @State private var wasStreaming = false
    @State private var isSpeakingResponse = false
    // Bumped whenever voice is stopped or a new response starts so any
    // in-flight chunk pipeline from a prior turn aborts cleanly.
    @State private var ttsSessionId = 0

    var body: some View {
        VStack(spacing: 0) {
            if showChatSearch {
                ChatSearchBar(query: $chatSearch) {
                    showChatSearch = false; chatSearch = ""
                }
            }
            MessagesScrollView(searchQuery: showChatSearch ? chatSearch : "")
            if let stats = state.lastStats, !state.isStreaming {
                GenerationStatsBar(stats: stats, contextWindow: state.contextWindow)
            }
            Divider()
            if !attachments.isEmpty || isUploading {
                AttachmentBar(attachments: $attachments, isUploading: isUploading)
                    .padding(.horizontal, 12)
                    .padding(.top, 8)
            }
            InputBar(
                text: $inputText,
                attachments: $attachments,
                isUploading: $isUploading,
                inputActions: inputActions,
                onSend: sendIfReady,
                voiceRecorder: voiceRecorder
            )
        }
        .overlay {
            if state.isMemoryPanelOpen {
                ZStack {
                    Color.black.opacity(0.3)
                        .ignoresSafeArea()
                        .onTapGesture { state.isMemoryPanelOpen = false }
                    MemoryPanel().environmentObject(state)
                        .frame(width: 460, height: 400)
                        .background(.ultraThickMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .shadow(radius: 20)
                }
                .transition(.opacity)
                .animation(.easeInOut(duration: 0.2), value: state.isMemoryPanelOpen)
            }
        }
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button {
                    showChatSearch.toggle()
                    if !showChatSearch { chatSearch = "" }
                } label: {
                    Image(systemName: showChatSearch ? "magnifyingglass.circle.fill" : "magnifyingglass")
                        .foregroundColor(showChatSearch ? .accentColor : .secondary)
                }
                .help("Search in conversation (⌘F)")
                .keyboardShortcut("f")
            }
        }
        .onChange(of: state.isStreaming) {
            if wasStreaming && !state.isStreaming && state.isVoiceMode {
                // LLM response done — speak it, then resume listening when
                // playback finishes. If synthesis fails or returns nothing,
                // `speakLastResponse` falls back to an immediate resume so
                // the loop never gets stuck.
                speakLastResponse()
            }
            wasStreaming = state.isStreaming
        }
        .onChange(of: state.isVoiceMode) {
            if state.isVoiceMode {
                voiceRecorder.start()
            } else {
                voiceRecorder.stop()
                voicePlayer.stop()
                ttsSessionId &+= 1
                isSpeakingResponse = false
            }
        }
        .onChange(of: voiceRecorder.completedAudio) {
            guard let wavData = voiceRecorder.completedAudio else { return }
            voiceRecorder.completedAudio = nil
            handleVoiceAudio(wavData)
        }
        .onDisappear {
            if state.isVoiceMode {
                voiceRecorder.stop()
            }
        }
    }

    // MARK: - Voice transcription + playback

    private func speakLastResponse() {
        guard state.isVoiceMode else { return }
        let text = state.messages.reversed()
            .first(where: { $0.role == "assistant" })?
            .content.plainText
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let chunks = Self.splitIntoSpeechChunks(text)
        guard !chunks.isEmpty else {
            voiceRecorder.resumeListening()
            return
        }
        isSpeakingResponse = true
        ttsSessionId &+= 1
        let session = ttsSessionId
        let voice = state.voiceConfig?.tts_voice
        let speed = state.voiceConfig?.tts_speed
        Task {
            // Pipeline: start synthesising chunk 0, and while it plays kick
            // off chunk 1's synth. Playback of chunk 1 can begin the moment
            // it's ready — no waiting for the whole response to be
            // processed upfront.
            var nextData: Task<Data?, Never> = synthTask(
                chunks[0], voice: voice, speed: speed, session: session
            )
            for (i, _) in chunks.enumerated() {
                let data = await nextData.value
                if session != ttsSessionId { return }
                nextData = i + 1 < chunks.count
                    ? synthTask(chunks[i + 1], voice: voice, speed: speed, session: session)
                    : Task { nil }
                guard let data else { continue }
                await playChunk(data, session: session)
                if session != ttsSessionId { return }
            }
            await MainActor.run {
                guard session == ttsSessionId else { return }
                isSpeakingResponse = false
                if state.isVoiceMode { voiceRecorder.resumeListening() }
            }
        }
    }

    private func synthTask(
        _ text: String, voice: String?, speed: Double?, session: Int
    ) -> Task<Data?, Never> {
        Task {
            do {
                let data = try await BackendClient.shared.synthesizeSpeech(
                    text: text, voice: voice, speed: speed
                )
                if session != ttsSessionId { return nil }
                return data
            } catch {
                await MainActor.run { state.voiceError = error.localizedDescription }
                return nil
            }
        }
    }

    @MainActor
    private func playChunk(_ data: Data, session: Int) async {
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            guard session == ttsSessionId, state.isVoiceMode else {
                cont.resume(); return
            }
            voicePlayer.onFinished = {
                cont.resume()
            }
            voicePlayer.play(data)
        }
    }

    /// Split into roughly-sentence-sized chunks (~240 chars) and merge
    /// tiny fragments so playback isn't choppy. Mirrors the splitter in
    /// ChatView.svelte so both clients chunk responses identically.
    static func splitIntoSpeechChunks(_ text: String) -> [String] {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return [] }
        let pattern = #"(?<=[.!?])\s+"#
        var pieces: [String]
        if let regex = try? NSRegularExpression(pattern: pattern) {
            let range = NSRange(raw.startIndex..., in: raw)
            let matches = regex.matches(in: raw, range: range)
            var last = raw.startIndex
            pieces = []
            for m in matches {
                guard let r = Range(m.range, in: raw) else { continue }
                pieces.append(String(raw[last..<r.lowerBound]))
                last = r.upperBound
            }
            pieces.append(String(raw[last...]))
        } else {
            pieces = [raw]
        }
        // Hard-wrap any chunk over 240 chars.
        let wrapped: [String] = pieces.flatMap { p -> [String] in
            guard p.count > 240 else { return [p] }
            var out: [String] = []
            var i = p.startIndex
            while i < p.endIndex {
                let j = p.index(i, offsetBy: 240, limitedBy: p.endIndex) ?? p.endIndex
                out.append(String(p[i..<j]))
                i = j
            }
            return out
        }
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }

        // Coalesce very short pieces into their neighbours.
        var merged: [String] = []
        for p in wrapped {
            if let last = merged.last, last.count < 40 || p.count < 20 {
                merged[merged.count - 1] = "\(last) \(p)"
            } else {
                merged.append(p)
            }
        }
        return merged
    }

    private func handleVoiceAudio(_ wavData: Data) {
        state.isTranscribing = true
        Task {
            do {
                let transcription = try await BackendClient.shared.transcribeAudio(wavData)
                await MainActor.run {
                    state.isTranscribing = false
                    if !transcription.isEmpty {
                        state.send(transcription)
                    }
                    // Resume listening after LLM + TTS cycle
                    // (if no TTS, resume immediately)
                    if !state.isVoiceMode { return }
                    if transcription.isEmpty {
                        voiceRecorder.resumeListening()
                    }
                    // If non-empty, the cycle is:
                    // send → isStreaming → onChange stops streaming → TTS plays → onFinished → resumeListening
                }
            } catch {
                await MainActor.run {
                    state.isTranscribing = false
                    state.voiceError = error.localizedDescription
                    voiceRecorder.resumeListening()
                }
            }
        }
    }

    private func sendIfReady() {
        let trimmed = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !state.isStreaming, !state.isLoadingModel, !trimmed.isEmpty || !attachments.isEmpty else { return }
        let t   = inputText
        let att = attachments
        inputText   = ""
        attachments = []
        state.send(t, attachments: att)
    }
}

// MARK: - Generation stats bar

struct GenerationStatsBar: View {
    let stats: AppState.GenerationStats
    let contextWindow: Int

    private var totalTok: Int { stats.promptTokens + stats.completionTokens }
    private var usagePct: Int {
        guard contextWindow > 0 else { return 0 }
        return Int(Double(totalTok) / Double(contextWindow) * 100)
    }
    private var truncated: Bool { usagePct >= 95 }

    private var plainText: String {
        let model = stats.model.split(separator: "/").last.map(String.init) ?? stats.model
        var parts = [model]
        if stats.ttftMs > 0 { parts.append(String(format: "TTFT %.1fs", stats.ttftMs / 1000)) }
        if stats.tokensPerSec > 0 { parts.append(String(format: "%.0f tok/s", stats.tokensPerSec)) }
        if stats.totalMs > 0 { parts.append(String(format: "%.1fs", stats.totalMs / 1000)) }
        if totalTok > 0 {
            parts.append("P:\(stats.promptTokens) + C:\(stats.completionTokens)")
            parts.append("\(totalTok) / \(contextWindow / 1024)K (\(usagePct)%)")
        }
        if stats.searchTriggered { parts.append("Search") }
        if stats.memoryInjected { parts.append("Memory") }
        if truncated { parts.append("⚠ truncated") }
        return parts.joined(separator: "  ·  ")
    }

    var body: some View {
        HStack(spacing: 0) {
            Spacer()
            HStack(spacing: 6) {
                let model = stats.model.split(separator: "/").last.map(String.init) ?? stats.model
                mono(model)
                if stats.ttftMs > 0 { dot(); mono(String(format: "TTFT %.1fs", stats.ttftMs / 1000)) }
                if stats.tokensPerSec > 0 { dot(); mono(String(format: "%.0f tok/s", stats.tokensPerSec)) }
                if stats.totalMs > 0 { dot(); mono(String(format: "%.1fs total", stats.totalMs / 1000)) }
                if totalTok > 0 {
                    dot()
                    mono("P:\(stats.promptTokens) + C:\(stats.completionTokens)")
                    dot()
                    mono("\(totalTok)/\(contextWindow/1024)K (\(usagePct)%)")
                        .foregroundColor(usagePct > 80 ? .orange : .secondary)
                }
                if stats.searchTriggered {
                    dot()
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.accentColor)
                        .help("Web search was triggered")
                }
                if stats.memoryInjected {
                    dot()
                    Image(systemName: "brain")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.purple)
                        .help("Memories were injected")
                }
                if truncated {
                    dot()
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 9))
                        .foregroundColor(.orange)
                        .help("Context window may be truncated (≥95% used)")
                }
            }
            Spacer()
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(plainText, forType: .string)
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.system(size: 10))
                    .foregroundColor(Color.secondary.opacity(0.5))
            }
            .buttonStyle(.plain).help("Copy stats").padding(.trailing, 8)
        }
        .padding(.vertical, 5)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private func mono(_ s: String) -> some View {
        Text(s).font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
    }
    private func dot() -> some View {
        Text("·").font(.system(size: 10)).foregroundColor(Color.secondary.opacity(0.35))
    }
}

// MARK: - Chat search bar

struct ChatSearchBar: View {
    @Binding var query: String
    let onClose: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary).font(.system(size: 11))
                TextField("Search in conversation…", text: $query)
                    .font(.system(size: 13)).textFieldStyle(.plain)
                    .focused($focused)
                if !query.isEmpty {
                    Button(action: onClose) {
                        Image(systemName: "xmark").font(.system(size: 10, weight: .medium))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(Color(nsColor: .controlBackgroundColor))
            Divider()
        }
        .onAppear { focused = true }
    }
}

// MARK: - Messages scroll view

struct MessagesScrollView: View {
    @EnvironmentObject var state: AppState
    let searchQuery: String

    private var displayed: [ChatMessage] {
        guard !searchQuery.isEmpty else { return state.messages }
        return state.messages.filter {
            $0.content.plainText.localizedCaseInsensitiveContains(searchQuery)
        }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if displayed.isEmpty && !state.isStreaming {
                    emptyState
                } else {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(displayed.enumerated()), id: \.element.id) { idx, msg in
                            let isLastStreaming = state.isStreaming && searchQuery.isEmpty && idx == displayed.count - 1
                            MessageBubble(message: msg, showTypingIndicator: isLastStreaming, highlight: searchQuery)
                                .id(msg.id)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(16)
                }
            }
            .onChange(of: state.messages.count) {
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom") }
            }
            .onChange(of: state.streamingText) {
                proxy.scrollTo("bottom")
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            HStack(spacing: 0) {
                Text("Lo").font(.system(size: 28, weight: .bold))
                Text("ca").font(.system(size: 28, weight: .bold)).foregroundColor(.accentColor)
            }
            modelStateView
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 80)
    }

    @ViewBuilder
    private var modelStateView: some View {
        if state.lmStudioMode {
            // External server (LM Studio / Ollama)
            if state.externalServerRunning == nil {
                // Still checking
                HStack(spacing: 8) {
                    ProgressView().scaleEffect(0.7)
                    Text("Connecting to \(externalServerName)…")
                        .font(.system(size: 13))
                        .foregroundColor(.secondary)
                }
            } else if state.externalServerRunning == false {
                externalServerOffView
            } else if state.externalModels.isEmpty {
                externalNoModelsView
            } else {
                Text("How can I help?")
                    .font(.system(size: 15))
                    .foregroundColor(.secondary)
            }
        } else if !state.isRemoteServer {
            // Native mode
            if state.localModels.isEmpty {
                noModelsDownloadedView
            } else if !state.localModels.contains(where: { $0.is_loaded }) {
                noModelLoadedView
            } else {
                Text("How can I help?")
                    .font(.system(size: 15))
                    .foregroundColor(.secondary)
            }
        } else {
            // Remote server — just show the prompt
            Text("How can I help?")
                .font(.system(size: 15))
                .foregroundColor(.secondary)
        }
    }

    private var externalServerName: String {
        if state.lmStudioUrl.contains("11434") { return "Ollama" }
        if state.lmStudioUrl.contains("1234")  { return "LM Studio" }
        return "Inference server"
    }

    private var noModelsDownloadedView: some View {
        modelStatusCard(
            icon: "arrow.down.circle",
            iconColor: .secondary,
            title: "No models downloaded",
            subtitle: "Download a model to get started.",
            actions: {
                AnyView(Button("Open Models") { state.isSettingsOpen = true }
                    .buttonStyle(.borderedProminent).controlSize(.regular))
            }
        )
    }

    private var noModelLoadedView: some View {
        modelStatusCard(
            icon: "cpu",
            iconColor: .secondary,
            title: "No model loaded",
            subtitle: "Load a model in Settings → Models to start chatting.",
            actions: {
                AnyView(Button("Open Models") { state.isSettingsOpen = true }
                    .buttonStyle(.borderedProminent).controlSize(.regular))
            }
        )
    }

    private var externalServerOffView: some View {
        modelStatusCard(
            icon: "network.slash",
            iconColor: .orange,
            title: "\(externalServerName) is not running",
            subtitle: "Start \(externalServerName) to connect.",
            actions: {
                AnyView(HStack(spacing: 10) {
                    Button {
                        state.startExternalServer()
                    } label: {
                        HStack(spacing: 6) {
                            if state.isStartingExternalServer {
                                ProgressView().scaleEffect(0.7)
                            } else {
                                Image(systemName: "play.circle")
                            }
                            Text("Open \(externalServerName)")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.isStartingExternalServer)

                    Button("Check Again") { state.checkServerStatus() }
                        .buttonStyle(.bordered)
                })
            }
        )
    }

    private var externalNoModelsView: some View {
        modelStatusCard(
            icon: "tray",
            iconColor: .secondary,
            title: "No models in \(externalServerName)",
            subtitle: "Load a model in \(externalServerName), then tap Refresh.",
            actions: {
                AnyView(Button("Refresh") { state.checkServerStatus() }
                    .buttonStyle(.bordered))
            }
        )
    }

    private func modelStatusCard(
        icon: String,
        iconColor: Color,
        title: String,
        subtitle: String,
        actions: () -> AnyView
    ) -> some View {
        VStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 30))
                .foregroundColor(iconColor)
            Text(title)
                .font(.system(size: 14, weight: .medium))
            Text(subtitle)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 280)
            actions()
                .padding(.top, 2)
        }
    }
}

// MARK: - Message bubble

struct MessageBubble: View {
    @EnvironmentObject var state: AppState
    let message: ChatMessage
    let showTypingIndicator: Bool
    var highlight: String = ""
    @State private var isHovered = false

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if isUser {
                Spacer(minLength: 80)
                VStack(alignment: .trailing, spacing: 6) {
                    attachmentPreviews
                    bubbleContent
                        .background(Color.accentColor.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .overlay(alignment: .topLeading) { copyButton(isUser: true) }
                }
            } else {
                // No avatar on the assistant side — matches the Svelte bubble
                // which reads as a single framed reply, not an avatar+prose pair.
                bubbleContent
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                    )
                    .overlay(alignment: .topTrailing) { copyButton(isUser: false) }
                Spacer(minLength: 80)
            }
        }
        .onHover { isHovered = $0 }
        // Intercept `loca-memory:N` URLs produced by the memory-citation
        // link pass in ProseView. The resource part of the URL is the
        // 1-based index into this turn's citation pool — resolve it to
        // the actual memory id via the per-turn map so the panel can
        // scroll to and flash that row.
        .environment(\.openURL, OpenURLAction { url in
            if url.scheme == "loca-memory" {
                let idxString = url.absoluteString
                    .replacingOccurrences(of: "loca-memory:", with: "")
                if let idx = Int(idxString),
                   let ids = state.citationIdsByMessageId[message.id],
                   idx >= 1, idx <= ids.count {
                    state.memoryHighlightId = ids[idx - 1]
                } else {
                    state.memoryHighlightId = nil
                }
                state.isMemoryPanelOpen = true
                return .handled
            }
            return .systemAction
        })
    }

    @ViewBuilder
    private func copyButton(isUser: Bool) -> some View {
        if isHovered {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(message.content.plainText, forType: .string)
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(.secondary)
                    .padding(5)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 5))
            }
            .buttonStyle(.plain)
            .padding(isUser ? .leading : .trailing, -28)
            .padding(.top, 4)
            .transition(.opacity.combined(with: .scale(scale: 0.8)))
        }
    }

    // MARK: Attachment previews (shown above user bubble)

    @ViewBuilder
    private var attachmentPreviews: some View {
        switch message.content {
        case .parts(let parts):
            let images = parts.compactMap { p -> String? in
                if case .image(let img) = p { return img.image_url.url }
                return nil
            }
            if !images.isEmpty {
                HStack(spacing: 6) {
                    ForEach(images, id: \.self) { url in
                        InlineImageView(dataURL: url)
                    }
                }
            }
        case .text(let t):
            // Surface non-image file attachment chips parsed from the prepended XML
            let chips = parseAttachmentChips(from: t)
            if !chips.isEmpty {
                HStack(spacing: 6) {
                    ForEach(chips, id: \.self) { name in
                        fileChip(name)
                    }
                }
            }
        }
    }

    private func parseAttachmentChips(from text: String) -> [String] {
        // Matches: <attachment name="filename.pdf">
        let pattern = try? NSRegularExpression(pattern: #"<attachment name="([^"]+)">"#)
        return pattern?.matches(in: text, range: NSRange(text.startIndex..., in: text))
            .compactMap { m -> String? in
                guard let r = Range(m.range(at: 1), in: text) else { return nil }
                return String(text[r])
            } ?? []
    }

    private func fileChip(_ name: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: fileIcon(for: name)).font(.system(size: 10))
            Text(name).font(.system(size: 11)).lineLimit(1).frame(maxWidth: 140)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.secondary.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func fileIcon(for name: String) -> String {
        let ext = (name as NSString).pathExtension.lowercased()
        switch ext {
        case "pdf": return "doc.richtext"
        case "txt", "md": return "doc.text"
        case "mp3", "m4a", "wav": return "waveform"
        case "mp4", "mov": return "film"
        default: return "paperclip"
        }
    }

    // MARK: Message content

    @ViewBuilder
    private var bubbleContent: some View {
        let plain = cleanText(message.content.plainText)
        if isUser {
            Text(highlighted(plain))
                .font(.system(size: 14))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        } else if plain.isEmpty {
            TypingIndicator()
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
        } else {
            let split = splitThinkBlocks(plain)
            VStack(alignment: .leading, spacing: 8) {
                if !split.thinking.isEmpty {
                    ThinkBlockView(text: split.thinking)
                }
                if !split.answer.isEmpty {
                    MarkdownView(text: split.answer, highlight: highlight)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }

    /// Returns an AttributedString with matching ranges highlighted.
    private func highlighted(_ raw: String) -> AttributedString {
        var attr = AttributedString(raw)
        attr.font = .system(size: 14)
        guard !highlight.isEmpty else { return attr }
        var searchStart = attr.startIndex
        while searchStart < attr.endIndex {
            guard let range = attr[searchStart...].range(of: highlight, options: .caseInsensitive) else { break }
            attr[range].backgroundColor = Color.yellow.opacity(0.55)
            attr[range].foregroundColor = Color.black
            searchStart = range.upperBound
        }
        return attr
    }

    /// Extracts `<think>…</think>` reasoning blocks (reasoning models) from the
    /// streamed text so the UI can render them collapsed and dimmed.
    func splitThinkBlocks(_ text: String) -> (thinking: String, answer: String) {
        guard text.contains("<think>") else { return ("", text) }
        let re = try! NSRegularExpression(pattern: "<think>([\\s\\S]*?)(</think>|$)", options: [])
        let ns = NSRange(text.startIndex..., in: text)
        let matches = re.matches(in: text, range: ns)
        if matches.isEmpty { return ("", text) }
        var thinkingParts: [String] = []
        var answer = ""
        var last = text.startIndex
        for m in matches {
            guard let full = Range(m.range, in: text),
                  let inner = Range(m.range(at: 1), in: text) else { continue }
            answer.append(contentsOf: text[last..<full.lowerBound])
            thinkingParts.append(String(text[inner]))
            last = full.upperBound
        }
        answer.append(contentsOf: text[last...])
        return (thinkingParts.joined(separator: "\n\n"), answer)
    }

    /// Strips the `<attachment>` XML prefix from user messages before display.
    private func cleanText(_ text: String) -> String {
        let stripped = text.replacingOccurrences(
            of: #"<attachment name="[^"]*">[\s\S]*?</attachment>\n?\n?"#,
            with: "",
            options: .regularExpression
        )
        return stripped.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Inline image thumbnail

struct InlineImageView: View {
    let dataURL: String

    var body: some View {
        if let img = loadImage() {
            Image(nsImage: img)
                .resizable()
                .scaledToFill()
                .frame(width: 80, height: 80)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func loadImage() -> NSImage? {
        if dataURL.hasPrefix("data:") {
            guard let comma = dataURL.firstIndex(of: ",") else { return nil }
            let b64 = String(dataURL[dataURL.index(after: comma)...])
            guard let data = Data(base64Encoded: b64) else { return nil }
            return NSImage(data: data)
        }
        guard let url = URL(string: dataURL), let data = try? Data(contentsOf: url) else { return nil }
        return NSImage(data: data)
    }
}

/// MARK: - Think block (reasoning traces)

struct ThinkBlockView: View {
    let text: String
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: { isExpanded.toggle() }) {
                HStack(spacing: 4) {
                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9))
                    Text("Thinking…")
                        .font(.system(size: 12, weight: .medium))
                }
                .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)

            if isExpanded {
                Text(text)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .opacity(0.85)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .padding(8)
        .background(Color.secondary.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .strokeBorder(Color.secondary.opacity(0.25), lineWidth: 1)
        )
    }
}

// MARK: - Typing indicator

struct TypingIndicator: View {
    @State private var phase = false

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(Color.secondary.opacity(0.6))
                    .frame(width: 7, height: 7)
                    .offset(y: phase ? -3 : 0)
                    .animation(
                        .easeInOut(duration: 0.4).repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.15),
                        value: phase
                    )
            }
        }
        .onAppear { phase = true }
    }
}

// MARK: - Markdown rendering

struct MarkdownView: View {
    let text: String
    var highlight: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parse(text).enumerated()), id: \.offset) { _, seg in
                switch seg {
                case .prose(let s):
                    ProseView(text: s, highlight: highlight)
                case .code(let lang, let code):
                    CodeBlock(language: lang, code: code)
                }
            }
        }
    }

    private enum Segment { case prose(String); case code(String?, String) }

    private static let fenceRE = try! NSRegularExpression(
        pattern: "```([^\\n]*)\\n([\\s\\S]*?)```", options: []
    )

    private func parse(_ input: String) -> [Segment] {
        var result: [Segment] = []
        var remaining = input
        while !remaining.isEmpty {
            let nsRange = NSRange(remaining.startIndex..., in: remaining)
            guard let m = Self.fenceRE.firstMatch(in: remaining, range: nsRange) else {
                if !remaining.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    result.append(.prose(remaining))
                }
                break
            }
            let beforeEnd = remaining.index(remaining.startIndex, offsetBy: m.range.location)
            let before = String(remaining[..<beforeEnd])
            if !before.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                result.append(.prose(before))
            }
            let lang: String? = Range(m.range(at: 1), in: remaining).map {
                let s = String(remaining[$0]).trimmingCharacters(in: .whitespaces)
                return s.isEmpty ? nil : s
            } ?? nil
            let code = Range(m.range(at: 2), in: remaining).map { String(remaining[$0]) } ?? ""
            result.append(.code(lang, code))
            let afterIdx = remaining.index(remaining.startIndex, offsetBy: m.range.location + m.range.length)
            remaining = String(remaining[afterIdx...])
        }
        return result.isEmpty ? [.prose(input)] : result
    }
}

// MARK: - Prose renderer (headers, bullets, numbered lists, tables, paragraphs)

struct ProseView: View {
    let text: String
    var highlight: String = ""

    /// Wrap `[memory: N]` citations in a markdown link with a custom
    /// `loca-memory:` URL scheme. The bubble intercepts those URLs via
    /// `OpenURLAction` and opens the Memory panel on tap. Mirrors the
    /// Svelte `linkMemoryCitations` pass.
    static func linkMemoryCitations(_ raw: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"\[memory:\s*(\d+)\]"#) else {
            return raw
        }
        let ns = raw as NSString
        let matches = re.matches(in: raw, range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return raw }
        var result = ""
        var cursor = 0
        for m in matches {
            let full = m.range
            let idxRange = m.range(at: 1)
            result += ns.substring(with: NSRange(location: cursor, length: full.location - cursor))
            let label = ns.substring(with: full)
            let idx = ns.substring(with: idxRange)
            result += "[\(label)](loca-memory:\(idx))"
            cursor = full.location + full.length
        }
        if cursor < ns.length {
            result += ns.substring(with: NSRange(location: cursor, length: ns.length - cursor))
        }
        return result
    }

    private enum LineGroup {
        case header(Int, String)
        case bullet(String)
        case numbered(Int, String)
        case paragraph(String)
        case hrule
        case table([[String]])   // rows → cells
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(Array(lineGroups(text).enumerated()), id: \.offset) { _, group in
                renderGroup(group)
            }
        }
    }

    private func lineGroups(_ input: String) -> [LineGroup] {
        var groups: [LineGroup] = []
        var paragraphLines: [String] = []
        var tableLines: [String] = []
        let numberedRE = try? NSRegularExpression(pattern: #"^(\d+)\.\s+(.+)"#)

        func flushParagraph() {
            let joined = paragraphLines.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { groups.append(.paragraph(joined)) }
            paragraphLines = []
        }

        func flushTable() {
            guard !tableLines.isEmpty else { return }
            var rows: [[String]] = []
            for tl in tableLines {
                let cells = tl.components(separatedBy: "|").dropFirst().dropLast()
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                let isSep = cells.allSatisfy { c in c.isEmpty || c.allSatisfy { $0 == "-" || $0 == ":" } }
                if !isSep && !cells.isEmpty { rows.append(Array(cells)) }
            }
            if !rows.isEmpty { groups.append(.table(rows)) }
            tableLines = []
        }

        for line in input.components(separatedBy: "\n") {
            if line.hasPrefix("|") {
                flushParagraph()
                tableLines.append(line)
            } else {
                flushTable()
                if line.hasPrefix("### ") {
                    flushParagraph(); groups.append(.header(3, String(line.dropFirst(4))))
                } else if line.hasPrefix("## ") {
                    flushParagraph(); groups.append(.header(2, String(line.dropFirst(3))))
                } else if line.hasPrefix("# ") {
                    flushParagraph(); groups.append(.header(1, String(line.dropFirst(2))))
                } else if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("+ ") {
                    flushParagraph(); groups.append(.bullet(String(line.dropFirst(2))))
                } else if let m = numberedRE?.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)),
                          let nr = Range(m.range(at: 1), in: line),
                          let cr = Range(m.range(at: 2), in: line),
                          let n = Int(line[nr]) {
                    flushParagraph(); groups.append(.numbered(n, String(line[cr])))
                } else if line == "---" || line == "***" || line == "___" {
                    flushParagraph(); groups.append(.hrule)
                } else if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    flushParagraph()
                } else {
                    paragraphLines.append(line)
                }
            }
        }
        flushParagraph()
        flushTable()
        return groups.isEmpty ? [.paragraph(input)] : groups
    }

    @ViewBuilder
    private func renderGroup(_ group: LineGroup) -> some View {
        switch group {
        case .header(let level, let s):
            let sz: CGFloat = level == 1 ? 20 : level == 2 ? 17 : 15
            Text(inline(s, baseSize: sz))
                .font(level == 1 ? .system(size: 20, weight: .bold)
                    : level == 2 ? .system(size: 17, weight: .semibold)
                    : .system(size: 15, weight: .semibold))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(.top, level < 3 ? 4 : 2)
        case .bullet(let s):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•").font(.system(size: 14)).foregroundColor(.secondary)
                    .padding(.leading, 4)
                Text(inline(s)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        case .numbered(let n, let s):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("\(n).").font(.system(size: 14)).foregroundColor(.secondary)
                    .padding(.leading, 4)
                Text(inline(s)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        case .paragraph(let s):
            Text(inline(s)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
        case .hrule:
            Divider().padding(.vertical, 4)
        case .table(let rows):
            tableView(rows)
        }
    }

    private func tableView(_ rows: [[String]]) -> some View {
        let colCount = rows.map(\.count).max() ?? 1
        return VStack(alignment: .leading, spacing: 0) {
            ForEach(rows.indices, id: \.self) { ri in
                HStack(spacing: 0) {
                    ForEach(0..<colCount, id: \.self) { ci in
                        let cell = ci < rows[ri].count ? rows[ri][ci] : ""
                        Group {
                            if ri == 0 {
                                Text(inline(cell, baseSize: 13))
                                    .font(.system(size: 13, weight: .semibold))
                                    .padding(.horizontal, 10).padding(.vertical, 6)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(Color.secondary.opacity(0.1))
                            } else {
                                Text(inline(cell, baseSize: 13))
                                    .font(.system(size: 13))
                                    .padding(.horizontal, 10).padding(.vertical, 6)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(ri % 2 == 0 ? Color.secondary.opacity(0.03) : Color.clear)
                            }
                        }
                        .textSelection(.enabled)
                        if ci < colCount - 1 {
                            Divider()
                        }
                    }
                }
                if ri < rows.count - 1 { Divider() }
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.25)))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .padding(.vertical, 4)
    }

    /// Parses inline markdown and applies explicit font attributes so SwiftUI Text
    /// renders bold, italic and inline code correctly regardless of the view's
    /// .font() modifier. Optionally highlights search matches.
    private func inline(_ raw: String, baseSize: CGFloat = 14) -> AttributedString {
        // Pre-process citation markers into markdown links so the parser
        // gives us proper `.link` runs to style + intercept. Matches the
        // Svelte `linkMemoryCitations` step. URL scheme is `loca-memory:`
        // — the bubble-level OpenURLAction catches it and opens the panel.
        let withCitations = Self.linkMemoryCitations(raw)
        guard var attr = try? AttributedString(
            markdown: withCitations,
            options: .init(interpretedSyntax: .inlineOnly)
        ) else { return AttributedString(raw) }

        for run in attr.runs {
            // Citation runs — styled as a pill that reads "clickable"
            // without mimicking a normal hyperlink.
            if let url = run.link, url.scheme == "loca-memory" {
                attr[run.range].font = .system(size: max(baseSize - 1, 11), design: .monospaced)
                attr[run.range].foregroundColor = Color.accentColor
                attr[run.range].backgroundColor = Color.accentColor.opacity(0.12)
                attr[run.range].underlineStyle = nil
                continue
            }
            guard let intent = run.inlinePresentationIntent else { continue }
            if intent.contains(.code) {
                attr[run.range].font = .system(size: max(baseSize - 1, 11), design: .monospaced)
                attr[run.range].foregroundColor = Color.accentColor.opacity(0.9)
            } else if intent.contains(.stronglyEmphasized) && intent.contains(.emphasized) {
                attr[run.range].font = .system(size: baseSize, weight: .bold).italic()
            } else if intent.contains(.stronglyEmphasized) {
                attr[run.range].font = .system(size: baseSize, weight: .bold)
            } else if intent.contains(.emphasized) {
                attr[run.range].font = .system(size: baseSize).italic()
            } else {
                attr[run.range].font = .system(size: baseSize)
            }
        }

        // Search highlights
        if !highlight.isEmpty {
            var pos = attr.startIndex
            while pos < attr.endIndex,
                  let range = attr[pos...].range(of: highlight, options: .caseInsensitive) {
                attr[range].backgroundColor = Color.yellow.opacity(0.55)
                attr[range].foregroundColor  = Color.black
                pos = range.upperBound
            }
        }
        return attr
    }
}

// MARK: - Code block

struct CodeBlock: View {
    let language: String?
    let code: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let lang = language {
                HStack {
                    Text(lang)
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 4)
                    Spacer()
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(code, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc").font(.system(size: 11)).foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain).help("Copy").padding(.trailing, 8)
                }
                .background(Color.secondary.opacity(0.06))
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Text(SyntaxHighlighter.highlight(code, language: language))
                    .font(.system(size: 12, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Input bar

struct InputBar: View {
    @EnvironmentObject var state: AppState
    @Binding var text: String
    @Binding var attachments: [UploadResult]
    @Binding var isUploading: Bool
    let inputActions: ChatInputActions
    let onSend: () -> Void
    let voiceRecorder: AudioRecorder

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 2) {
                FormatButton(icon: "bold",   help: "Bold (**text**)")  { inputActions.wrap("**") }
                FormatButton(icon: "italic", help: "Italic (_text_)")  { inputActions.wrap("_") }
                FormatButton(icon: "chevron.left.forwardslash.chevron.right",
                             help: "Inline code (`text`)")             { inputActions.wrap("`") }
                FormatButton(icon: "text.alignleft",
                             help: "Code block (```)")                 { inputActions.codeBlock() }
                Spacer()
                if state.isLoadingModel {
                    HStack(spacing: 4) {
                        ProgressView().scaleEffect(0.55)
                        Text("Loading model…").font(.system(size: 11)).foregroundColor(.secondary)
                    }
                    .padding(.trailing, 4)
                } else if state.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView().scaleEffect(0.55)
                        Text("Generating…").font(.system(size: 11)).foregroundColor(.secondary)
                    }
                    .padding(.trailing, 4)
                }
            }
            .padding(.horizontal, 12).padding(.top, 8)

            ChatTextEditor(
                text: $text,
                placeholder: "Ask anything…  (Shift+Return for newline)",
                onSend: onSend,
                actions: inputActions
            )
            .frame(minHeight: 38, maxHeight: 120)
            .padding(.horizontal, 12)

            HStack(spacing: 8) {
                InputToolButton(
                    icon: "drop", label: "Deep Dive",
                    isActive: state.researchMode, isDisabled: state.lockdownMode
                ) { if !state.lockdownMode { state.researchMode.toggle() } }
                .help("Deep Dive — multi-step research: plan sub-queries, fetch full pages, synthesise with citations, verify")

                InputToolButton(
                    icon: "lock", label: "Lockdown",
                    isActive: state.lockdownMode, isDisabled: false
                ) {
                    state.lockdownMode.toggle()
                    if state.lockdownMode { state.researchMode = false }
                }
                .help("Lockdown — disable all network tools")

                if let activeProject = state.activeProject {
                    Divider().frame(height: 14)
                    // Clickable pill — tapping it exits research mode
                    // for this chat session. Replaces the Research-modal
                    // "Unset active project" dropdown row.
                    Button {
                        state.setActiveProject(nil)
                    } label: {
                        HStack(spacing: 3) {
                            Text("📚 \(activeProject.title)")
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Text("×").opacity(0.6)
                        }
                        .font(.system(size: 10))
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .overlay(
                            RoundedRectangle(cornerRadius: 999)
                                .stroke(Color.secondary.opacity(0.35))
                        )
                    }
                    .buttonStyle(.plain)
                    .help("\(activeProject.title) — click to exit research mode")
                    ForEach(PartnerMode.allCases) { mode in
                        InputToolButton(
                            icon: mode.icon, label: mode.label,
                            isActive: state.partnerMode == mode, isDisabled: false
                        ) { state.partnerMode = mode }
                        .help(partnerHelp(mode))
                    }
                }

                Button(action: pickFile) {
                    HStack(spacing: 4) {
                        Image(systemName: "paperclip").font(.system(size: 10))
                        Text("Upload").font(.system(size: 10, weight: .medium))
                    }
                    .padding(.horizontal, 7).padding(.vertical, 4)
                    .background(Color.secondary.opacity(0.08))
                    .foregroundColor(.secondary)
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.2)))
                }
                .buttonStyle(.plain).disabled(isUploading).help("Attach file")

                // Voice mode toggle — enables mic input + auto-TTS
                InputToolButton(
                    icon: state.isVoiceMode ? "waveform" : "mic",
                    label: "Voice",
                    isActive: state.isVoiceMode,
                    isDisabled: false
                ) {
                    if state.isVoiceMode {
                        state.isVoiceMode = false
                    } else {
                        // Check if voice models are downloaded
                        state.fetchVoiceConfig()
                        let allReady = state.voiceConfig?.models.allSatisfy(\.downloaded) ?? false
                        if allReady {
                            state.isVoiceMode = true
                        } else {
                            state.showVoiceSetup = true
                        }
                    }
                }
                .help(state.isVoiceMode ? "Voice mode ON — mic listens, responses are spoken" : "Enable voice mode")
                .sheet(isPresented: $state.showVoiceSetup) {
                    VoiceSetupSheet()
                        .environmentObject(state)
                }

                Spacer()

                if state.isVoiceMode {
                    if let err = state.voiceError {
                        Text(err)
                            .font(.system(size: 10))
                            .foregroundColor(.red)
                            .lineLimit(1)
                            .frame(maxWidth: 200)
                            .onTapGesture { state.voiceError = nil }
                            .help("Click to dismiss")
                    }
                    VoiceStatusIndicator(
                        recorderState: voiceRecorder.state,
                        audioLevel: voiceRecorder.audioLevel,
                        isTranscribing: state.isTranscribing,
                        isStreaming: state.isStreaming
                    )
                }

                Button(action: onSend) {
                    HStack(spacing: 5) {
                        Image(systemName: "arrow.up").font(.system(size: 11, weight: .semibold))
                        Text("Send").font(.system(size: 12, weight: .semibold))
                    }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(canSend ? Color.accentColor : Color.secondary.opacity(0.15))
                    .foregroundColor(canSend ? .white : Color.secondary.opacity(0.4))
                    .clipShape(RoundedRectangle(cornerRadius: 7))
                }
                .buttonStyle(.plain).disabled(!canSend).help("Send (Return)")
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        // Drag-and-drop attachments from Finder. Mirrors the Svelte UI's
        // drop-to-attach; the existing "+" button still works as before.
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            let group = DispatchGroup()
            var urls: [URL] = []
            for provider in providers {
                if provider.canLoadObject(ofClass: URL.self) {
                    group.enter()
                    _ = provider.loadObject(ofClass: URL.self) { item, _ in
                        if let url = item { urls.append(url) }
                        group.leave()
                    }
                }
            }
            group.notify(queue: .main) {
                self.uploadURLs(urls)
            }
            return true
        }
    }

    private var canSend: Bool {
        !state.isStreaming &&
        !state.isLoadingModel &&
        (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    private func partnerHelp(_ mode: PartnerMode) -> String {
        switch mode {
        case .default_: return "Default partner — biased to project sources, normal chat"
        case .critique: return "Critique — plays devil's advocate; surfaces weak claims + counter-arguments"
        case .teach:    return "Teach — step-by-step pedagogy; intuition first, formalism second"
        }
    }

    private func pickFile() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories    = false
        guard panel.runModal() == .OK else { return }
        uploadURLs(panel.urls)
    }

    /// Shared upload path for both NSOpenPanel picks and drag-dropped
    /// file URLs. Each URL is read off-main, uploaded to /api/upload,
    /// and the resulting chip appended on the main actor.
    fileprivate func uploadURLs(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        isUploading = true
        Task {
            for url in urls {
                guard let data = try? Data(contentsOf: url) else { continue }
                let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                          ?? "application/octet-stream"
                if let result = try? await BackendClient.shared.uploadFile(
                    data, filename: url.lastPathComponent, mimeType: mime
                ) {
                    await MainActor.run { attachments.append(result) }
                }
            }
            await MainActor.run { isUploading = false }
        }
    }
}

// MARK: - Format button

struct FormatButton: View {
    let icon: String
    let help: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 12)).foregroundColor(.secondary)
                .frame(width: 26, height: 22)
        }
        .buttonStyle(.plain).help(help)
    }
}

// MARK: - Attachment bar

struct AttachmentBar: View {
    @Binding var attachments: [UploadResult]
    let isUploading: Bool
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Array(attachments.enumerated()), id: \.offset) { idx, att in
                    AttachmentChip(attachment: att) { attachments.remove(at: idx) }
                }
                if isUploading {
                    HStack(spacing: 5) {
                        ProgressView().scaleEffect(0.6)
                        Text("Uploading…").font(.system(size: 12)).foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Color.secondary.opacity(0.1)).clipShape(Capsule())
                }
            }
        }
    }
}

struct AttachmentChip: View {
    let attachment: UploadResult
    let onRemove: () -> Void
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon).font(.system(size: 10)).foregroundColor(.secondary)
            Text(attachment.name).font(.system(size: 12)).lineLimit(1).frame(maxWidth: 120)
            Button(action: onRemove) {
                Image(systemName: "xmark").font(.system(size: 9, weight: .bold)).foregroundColor(.secondary)
            }.buttonStyle(.plain)
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        .background(Color.secondary.opacity(0.1)).clipShape(Capsule())
    }
    private var icon: String {
        switch attachment.type {
        case "image": return "photo"
        case "audio": return "waveform"
        case "video": return "film"
        case "text":  return "doc.text"
        default:      return "paperclip"
        }
    }
}

// MARK: - Memory panel

struct MemoryPanel: View {
    @EnvironmentObject var state: AppState
    @State private var searchText = ""
    @State private var recallResults: [Memory] = []
    @State private var isRecalling = false
    @State private var recallError: String?

    private var displayedMemories: [Memory] {
        if !searchText.isEmpty && !recallResults.isEmpty { return recallResults }
        if searchText.isEmpty { return state.memories }
        // Local filter while recall is in-flight
        return state.memories.filter {
            $0.content.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                // Show the total — `memories.count` is just the current
                // page (50), which made users think they'd lost facts
                // after opening a large store.
                Text("Memories (\(state.memoriesTotal))")
                    .font(.headline)
                Spacer()
                Button {
                    state.extractMemories()
                } label: {
                    if state.isExtractingMemories {
                        ProgressView().scaleEffect(0.7)
                    } else {
                        Text("Save from chat")
                    }
                }
                .controlSize(.small)
                .buttonStyle(.bordered)
                .help("Store recent messages verbatim for future recall")
                .disabled(state.messages.isEmpty || state.isExtractingMemories)

                Button("Done") { state.isMemoryPanelOpen = false }
                    .controlSize(.small)
                    .keyboardShortcut(.escape, modifiers: [])
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)

            // Search / recall bar
            HStack(spacing: 6) {
                Image(systemName: isRecalling ? "waveform" : "magnifyingglass")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                TextField("Search memories…", text: $searchText)
                    .font(.system(size: 12))
                    .textFieldStyle(.plain)
                    .onChange(of: searchText) {
                        _triggerRecall()
                    }
                if !searchText.isEmpty {
                    Button { searchText = ""; recallResults = [] } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 16)
            .padding(.bottom, 8)

            Divider()

            if let err = state.memoryExtractionError {
                Text("Save failed: \(err)")
                    .font(.caption).foregroundColor(.red)
                    .padding(.horizontal, 16).padding(.top, 8)
            }
            if let err = recallError {
                Text("Recall failed: \(err)")
                    .font(.caption).foregroundColor(.red)
                    .padding(.horizontal, 16).padding(.top, 8)
            }

            if displayedMemories.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: searchText.isEmpty ? "brain" : "magnifyingglass")
                        .font(.system(size: 28)).foregroundColor(.secondary)
                    Text(searchText.isEmpty ? "No memories yet" : "No matches found")
                        .font(.system(size: 13)).foregroundColor(.secondary)
                    if searchText.isEmpty {
                        Text("Click \"Save from chat\" after a conversation\nto store messages for future recall.")
                            .font(.system(size: 11)).foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding()
            } else {
                ScrollViewReader { proxy in
                List {
                    if !searchText.isEmpty && !recallResults.isEmpty {
                        Section(header: Text("Semantic matches for \"\(searchText)\"")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(.secondary)) {
                            ForEach(displayedMemories) { memory in
                                MemoryRow(memory: memory).id(memory.id)
                            }
                        }
                    } else {
                        ForEach(displayedMemories) { memory in
                            MemoryRow(memory: memory).id(memory.id)
                        }
                        .onDelete { offsets in
                            let ids = offsets.map { displayedMemories[$0].id }
                            state.memories.removeAll { ids.contains($0.id) }
                            state.memoriesTotal = max(0, state.memoriesTotal - ids.count)
                            for id in ids { Task { try? await BackendClient.shared.deleteMemory(id) } }
                        }
                        if searchText.isEmpty && state.memories.count < state.memoriesTotal {
                            Button {
                                Task { await state.loadMoreMemories() }
                            } label: {
                                HStack {
                                    Spacer()
                                    if state.isLoadingMoreMemories {
                                        ProgressView().controlSize(.small)
                                    } else {
                                        Text("Load more (\(state.memories.count) of \(state.memoriesTotal))")
                                            .font(.system(size: 12))
                                            .foregroundColor(.accentColor)
                                    }
                                    Spacer()
                                }
                                .padding(.vertical, 4)
                            }
                            .buttonStyle(.plain)
                            .disabled(state.isLoadingMoreMemories)
                        }
                    }
                }
                .onChange(of: state.memoryHighlightId) { _, new in
                    guard let id = new else { return }
                    Task {
                        // Best-effort page-in until the row appears; cap
                        // to avoid runaway loops when citations point
                        // at an id no longer in the store.
                        var attempts = 0
                        while !state.memories.contains(where: { $0.id == id }),
                              state.memories.count < state.memoriesTotal,
                              attempts < 20 {
                            await state.loadMoreMemories()
                            attempts += 1
                        }
                        try? await Task.sleep(nanoseconds: 50_000_000)
                        withAnimation(.easeInOut(duration: 0.35)) {
                            proxy.scrollTo(id, anchor: .center)
                        }
                        // Clear after the flash so re-opening the panel
                        // doesn't re-trigger the same highlight.
                        try? await Task.sleep(nanoseconds: 1_800_000_000)
                        if state.memoryHighlightId == id { state.memoryHighlightId = nil }
                    }
                }
                .onAppear {
                    // Run the same effect on first appear in case the
                    // panel was opened with a pending highlight.
                    if let id = state.memoryHighlightId {
                        Task {
                            try? await Task.sleep(nanoseconds: 120_000_000)
                            withAnimation(.easeInOut(duration: 0.35)) {
                                proxy.scrollTo(id, anchor: .center)
                            }
                        }
                    }
                }
                }
            }
        }
        .onAppear { state.loadMemories() }
    }

    private func _triggerRecall() {
        guard !searchText.trimmingCharacters(in: .whitespaces).isEmpty else {
            recallResults = []
            recallError = nil
            return
        }
        let q = searchText
        Task {
            isRecalling = true
            recallError = nil
            do {
                let results = try await BackendClient.shared.recallMemories(query: q, limit: 8)
                if searchText == q {   // discard stale results
                    recallResults = results
                }
            } catch {
                if searchText == q { recallError = error.localizedDescription }
            }
            isRecalling = false
        }
    }
}

private struct MemoryRow: View {
    @EnvironmentObject var state: AppState
    let memory: Memory

    private var isFlashed: Bool { state.memoryHighlightId == memory.id }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(typeColor)
                    .frame(width: 6, height: 6)
                Text(memory.typeLabel)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.secondary)
                    .textCase(.uppercase)
                    .tracking(0.5)
                Spacer()
                Text(memory.createdDate, style: .relative)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            Text(memory.content)
                .font(.system(size: 12))
                .lineLimit(4)
                .textSelection(.enabled)
        }
        .padding(.vertical, 3)
        .padding(.horizontal, 6)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(isFlashed ? Color.accentColor.opacity(0.12) : Color.clear)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(isFlashed ? Color.accentColor : .clear, lineWidth: 1.5)
        )
        .animation(.easeInOut(duration: 0.3), value: isFlashed)
    }

    private var typeColor: Color {
        switch memory.type {
        case "knowledge":  return .blue
        case "correction": return .orange
        default:           return Color.secondary.opacity(0.5)
        }
    }
}

// MARK: - Input tool button (Research / Lockdown)

struct InputToolButton: View {
    let icon: String
    let label: String
    let isActive: Bool
    let isDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                Image(systemName: isActive && icon == "lock" ? "lock.fill" : icon)
                    .font(.system(size: 10))
                Text(label)
                    .font(.system(size: 10, weight: .medium))
            }
            .padding(.horizontal, 7).padding(.vertical, 4)
            .background(isActive ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.08))
            .foregroundColor(isActive ? .accentColor : .secondary)
            .clipShape(RoundedRectangle(cornerRadius: 5))
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(isActive ? Color.accentColor.opacity(0.4) : Color.secondary.opacity(0.2)))
        }
        .buttonStyle(.plain)
        .disabled(isDisabled)
        .opacity(isDisabled ? 0.35 : 1)
    }
}

// MARK: - Voice status indicator

struct VoiceStatusIndicator: View {
    let recorderState: AudioRecorder.State
    let audioLevel: Float
    let isTranscribing: Bool
    let isStreaming: Bool

    private var label: String {
        if isTranscribing { return "Transcribing…" }
        if isStreaming { return "Thinking…" }
        switch recorderState {
        case .recording: return "Listening…"
        case .listening: return "Waiting…"
        case .processing: return "Processing…"
        case .idle: return "Voice"
        }
    }

    private var icon: String {
        if isTranscribing || isStreaming { return "ellipsis" }
        switch recorderState {
        case .recording: return "mic.fill"
        case .listening: return "mic"
        case .processing: return "waveform"
        case .idle: return "mic.slash"
        }
    }

    private var color: Color {
        if isTranscribing { return .orange }
        if isStreaming { return .purple }
        switch recorderState {
        case .recording: return .red
        case .listening: return .green
        default: return .secondary
        }
    }

    var body: some View {
        HStack(spacing: 3) {
            if isTranscribing || isStreaming {
                ProgressView().scaleEffect(0.4).frame(width: 10, height: 10)
            } else if recorderState == .recording {
                HStack(spacing: 1) {
                    ForEach(0..<3, id: \.self) { i in
                        RoundedRectangle(cornerRadius: 1)
                            .fill(Color.red)
                            .frame(width: 2, height: barHeight(i))
                    }
                }
                .frame(width: 10, height: 10)
            } else {
                Image(systemName: icon).font(.system(size: 10))
            }
            Text(label).font(.system(size: 10, weight: .medium))
        }
        .padding(.horizontal, 7).padding(.vertical, 4)
        .background(color.opacity(0.12))
        .foregroundColor(color)
        .clipShape(RoundedRectangle(cornerRadius: 5))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(color.opacity(0.3)))
        .help(label)
    }

    private func barHeight(_ index: Int) -> CGFloat {
        let base: CGFloat = 4
        let level = CGFloat(audioLevel)
        let offsets: [CGFloat] = [0.6, 1.0, 0.8]
        return base + (10 * level * offsets[index])
    }
}
