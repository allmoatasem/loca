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

    var body: some View {
        VStack(spacing: 0) {
            MessagesScrollView()
            if let stats = state.lastStats, !state.isStreaming {
                GenerationStatsBar(stats: stats)
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
                onSend: sendIfReady
            )
        }
        .sheet(isPresented: $state.isMemoryPanelOpen) {
            MemoryPanel().environmentObject(state)
        }
    }

    private func sendIfReady() {
        let trimmed = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !state.isStreaming, !trimmed.isEmpty || !attachments.isEmpty else { return }
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

    var body: some View {
        HStack(spacing: 0) {
            Spacer()
            Group {
                label(stats.model.split(separator: "/").last.map(String.init) ?? stats.model)
                separator()
                if stats.ttftMs > 0 {
                    label(String(format: "TTFT %.0fms", stats.ttftMs))
                    separator()
                }
                if stats.tokensPerSec > 0 {
                    label(String(format: "%.0f tok/s", stats.tokensPerSec))
                    separator()
                }
                if stats.completionTokens > 0 {
                    label("\(stats.promptTokens + stats.completionTokens) tokens")
                }
            }
            Spacer()
        }
        .padding(.vertical, 4)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, design: .monospaced))
            .foregroundColor(.secondary)
    }

    private func separator() -> some View {
        Text("  ·  ")
            .font(.system(size: 10))
            .foregroundColor(Color.secondary.opacity(0.4))
    }
}

// MARK: - Messages scroll view

struct MessagesScrollView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if state.messages.isEmpty && !state.isStreaming {
                    emptyState
                } else {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(state.messages.enumerated()), id: \.element.id) { idx, msg in
                            let isLastStreaming = state.isStreaming && idx == state.messages.count - 1
                            MessageBubble(message: msg, showTypingIndicator: isLastStreaming)
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
        VStack(spacing: 12) {
            HStack(spacing: 0) {
                Text("Lo").font(.system(size: 28, weight: .bold))
                Text("ca").font(.system(size: 28, weight: .bold)).foregroundColor(.accentColor)
            }
            Text("How can I help?")
                .font(.system(size: 15))
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 120)
    }
}

// MARK: - Message bubble

struct MessageBubble: View {
    @EnvironmentObject var state: AppState
    let message: ChatMessage
    let showTypingIndicator: Bool

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if isUser {
                Spacer(minLength: 80)
                VStack(alignment: .trailing, spacing: 6) {
                    // File/image attachments shown above the text (like Claude)
                    attachmentPreviews
                    bubbleContent
                        .background(Color.accentColor.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            } else {
                modelAvatar
                bubbleContent
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                Spacer(minLength: 80)
            }
        }
    }

    // MARK: Avatar — initials derived from the model that generated this response

    private var modelAvatar: some View {
        ZStack {
            Circle().fill(Color.accentColor)
            Text(modelInitials(state.actualModel ?? state.selectedModelId))
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(.white)
        }
        .frame(width: 26, height: 26)
    }

    private func modelInitials(_ id: String?) -> String {
        guard let id, !id.isEmpty else { return "AI" }
        let name = String(id.split(separator: "/").last ?? Substring(id))
        let skip = Set(["instruct", "chat", "it", "gguf", "hf", "v1", "v2", "v3",
                        "v4", "q4", "q8", "fp16", "awq", "bnb", "gptq"])
        let parts = name.components(separatedBy: CharacterSet(charactersIn: "-_.:"))
            .filter { p in
                !p.isEmpty &&
                p.first?.isLetter == true &&
                !skip.contains(p.lowercased()) &&
                !p.allSatisfy({ $0.isNumber || $0 == "." })
            }
        switch parts.count {
        case 0: return "AI"
        case 1: return String(parts[0].prefix(2)).uppercased()
        default: return (String(parts[0].prefix(1)) + String(parts[1].prefix(1))).uppercased()
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
        let ns = text as NSString
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
            Text(plain)
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
            MarkdownView(text: plain)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parse(text).enumerated()), id: \.offset) { _, seg in
                switch seg {
                case .prose(let s):
                    if let attr = try? AttributedString(
                        markdown: s,
                        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                    ) {
                        Text(attr).font(.system(size: 14)).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text(s).font(.system(size: 14)).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
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
                Text(code)
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
                if state.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView().scaleEffect(0.55)
                        Text("Generating…").font(.system(size: 11)).foregroundColor(.secondary)
                    }
                    .padding(.trailing, 4)
                }
            }
            .padding(.horizontal, 12).padding(.top, 8)

            HStack(alignment: .bottom, spacing: 8) {
                ChatTextEditor(
                    text: $text,
                    placeholder: "Ask anything…  (Shift+Return for newline)",
                    onSend: onSend,
                    actions: inputActions
                )
                .frame(minHeight: 38, maxHeight: 120)

                VStack(spacing: 6) {
                    Button(action: pickFile) {
                        Image(systemName: "paperclip")
                            .font(.system(size: 15)).foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain).disabled(isUploading).help("Attach file")

                    Button(action: onSend) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 26))
                            .foregroundColor(canSend ? .accentColor : Color.secondary.opacity(0.3))
                    }
                    .buttonStyle(.plain).disabled(!canSend).help("Send (Return)")
                }
                .padding(.bottom, 4)
            }
            .padding(.horizontal, 12).padding(.bottom, 12)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var canSend: Bool {
        !state.isStreaming &&
        (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    private func pickFile() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories    = false
        guard panel.runModal() == .OK else { return }
        isUploading = true
        Task {
            for url in panel.urls {
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

    var body: some View {
        NavigationStack {
            List {
                if let err = state.memoryExtractionError {
                    Text("Extraction failed: \(err)")
                        .font(.caption).foregroundColor(.red)
                }
                ForEach(state.memories) { memory in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(memory.content).font(.system(size: 13)).textSelection(.enabled)
                        Text(memory.createdDate, style: .relative)
                            .font(.caption).foregroundColor(.secondary)
                    }
                    .padding(.vertical, 2)
                }
                .onDelete { offsets in
                    let ids = offsets.map { state.memories[$0].id }
                    state.memories.remove(atOffsets: offsets)
                    for id in ids { Task { try? await BackendClient.shared.deleteMemory(id) } }
                }
            }
            .navigationTitle("Memories (\(state.memories.count))")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        state.extractMemories()
                    } label: {
                        if state.isExtractingMemories {
                            ProgressView().scaleEffect(0.7)
                        } else {
                            Text("Extract from chat")
                        }
                    }
                    .disabled(state.messages.isEmpty || state.isExtractingMemories)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { state.isMemoryPanelOpen = false }
                }
            }
        }
        .frame(minWidth: 420, minHeight: 320)
        .onAppear { state.loadMemories() }
    }
}
