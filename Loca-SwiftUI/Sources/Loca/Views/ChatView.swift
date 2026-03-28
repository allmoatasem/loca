import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - ChatView

struct ChatView: View {
    @EnvironmentObject var state: AppState
    @State private var inputText = ""
    @State private var attachments: [UploadResult] = []
    @State private var isUploading = false

    var body: some View {
        VStack(spacing: 0) {
            MessagesScrollView()
            Divider()
            if !attachments.isEmpty {
                AttachmentBar(attachments: $attachments)
                    .padding(.horizontal, 12)
                    .padding(.top, 8)
            }
            InputBar(text: $inputText, attachments: $attachments, isUploading: $isUploading)
        }
        .sheet(isPresented: $state.isMemoryPanelOpen) {
            MemoryPanel()
                .environmentObject(state)
        }
    }
}

// MARK: - Messages

struct MessagesScrollView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if state.messages.isEmpty {
                    emptyState
                } else {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(state.messages) { msg in
                            MessageBubble(message: msg)
                                .id(msg.id)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(16)
                }
            }
            .onChange(of: state.messages.count) {
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo("bottom")
                }
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
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if isUser {
                Spacer(minLength: 80)
                bubbleContent
                    .background(Color.accentColor.opacity(0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            } else {
                avatar
                bubbleContent
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                Spacer(minLength: 80)
            }
        }
    }

    private var avatar: some View {
        ZStack {
            Circle().fill(Color.accentColor)
            Text("L")
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.white)
        }
        .frame(width: 26, height: 26)
    }

    @ViewBuilder
    private var bubbleContent: some View {
        if isUser {
            Text(message.content.plainText)
                .font(.system(size: 14))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        } else {
            MarkdownView(text: message.content.plainText)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        }
    }
}

// MARK: - Markdown rendering

struct MarkdownView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parse(text).enumerated()), id: \.offset) { _, segment in
                switch segment {
                case .prose(let s):
                    if let attr = try? AttributedString(
                        markdown: s,
                        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                    ) {
                        Text(attr)
                            .font(.system(size: 14))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text(s)
                            .font(.system(size: 14))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                case .code(let lang, let code):
                    CodeBlock(language: lang, code: code)
                }
            }
        }
    }

    // Split text into prose and fenced code block segments.
    private enum Segment { case prose(String); case code(String?, String) }

    // NSRegularExpression used instead of a regex literal to avoid backtick parsing issues.
    private static let fenceRE = try! NSRegularExpression(
        pattern: "```([^\\n]*)\\n([\\s\\S]*?)```",
        options: []
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
            // Text before the fence
            let beforeEnd = remaining.index(remaining.startIndex, offsetBy: m.range.location)
            let before = String(remaining[..<beforeEnd])
            if !before.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                result.append(.prose(before))
            }
            // Language tag and code body
            let lang: String? = Range(m.range(at: 1), in: remaining).map {
                let s = String(remaining[$0]).trimmingCharacters(in: .whitespaces)
                return s.isEmpty ? nil : s
            } ?? nil
            let code = Range(m.range(at: 2), in: remaining).map { String(remaining[$0]) } ?? ""
            result.append(.code(lang, code))
            // Advance past the match
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
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                    Spacer()
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(code, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help("Copy")
                    .padding(.trailing, 8)
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
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.secondary.opacity(0.2))
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Input bar

struct InputBar: View {
    @EnvironmentObject var state: AppState
    @Binding var text: String
    @Binding var attachments: [UploadResult]
    @Binding var isUploading: Bool
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            // Formatting toolbar
            HStack(spacing: 2) {
                FormatButton(icon: "bold", help: "Bold") { wrap("**") }
                FormatButton(icon: "italic", help: "Italic") { wrap("_") }
                FormatButton(icon: "chevron.left.forwardslash.chevron.right", help: "Inline code") { wrap("`") }
                FormatButton(icon: "text.alignleft", help: "Code block") {
                    text += "\n```\n\n```"
                }
                Spacer()
                if state.isStreaming {
                    ProgressView()
                        .scaleEffect(0.6)
                        .padding(.trailing, 4)
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 8)

            // TextEditor + send controls
            HStack(alignment: .bottom, spacing: 8) {
                ZStack(alignment: .topLeading) {
                    if text.isEmpty {
                        Text("Ask anything…")
                            .font(.system(size: 14))
                            .foregroundColor(Color(nsColor: .placeholderTextColor))
                            .padding(.top, 8)
                            .padding(.leading, 5)
                            .allowsHitTesting(false)
                    }
                    TextEditor(text: $text)
                        .font(.system(size: 14))
                        .frame(minHeight: 36, maxHeight: 120)
                        .scrollContentBackground(.hidden)
                        .focused($focused)
                }

                VStack(spacing: 6) {
                    Button(action: pickFile) {
                        Image(systemName: isUploading ? "arrow.triangle.2.circlepath" : "paperclip")
                            .font(.system(size: 15))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .disabled(isUploading)
                    .help("Attach file")

                    Button(action: send) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 26))
                            .foregroundColor(canSend ? .accentColor : Color.secondary.opacity(0.4))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSend)
                    .keyboardShortcut(.return, modifiers: .command)
                    .help("Send (⌘↩)")
                }
                .padding(.bottom, 4)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 12)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { focused = true }
    }

    private var canSend: Bool {
        !state.isStreaming &&
        (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    private func send() {
        guard canSend else { return }
        let t = text
        let att = attachments
        text = ""
        attachments = []
        state.send(t, attachments: att)
    }

    /// Wraps selected text in the focused NSTextView with a markdown marker pair,
    /// or appends the empty pair at the cursor position.
    private func wrap(_ marker: String) {
        guard let tv = NSApp.keyWindow?.firstResponder as? NSTextView else {
            text += "\(marker)\(marker)"
            return
        }
        let sel = tv.selectedRange()
        if sel.length > 0 {
            let selected = (tv.string as NSString).substring(with: sel)
            tv.insertText("\(marker)\(selected)\(marker)", replacementRange: sel)
        } else {
            tv.insertText("\(marker)\(marker)", replacementRange: sel)
            tv.setSelectedRange(NSRange(location: sel.location + marker.count, length: 0))
        }
    }

    private func pickFile() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        guard panel.runModal() == .OK else { return }

        isUploading = true
        Task {
            for url in panel.urls {
                guard let data = try? Data(contentsOf: url) else { continue }
                let mime = mimeType(for: url)
                if let result = try? await BackendClient.shared.uploadFile(
                    data, filename: url.lastPathComponent, mimeType: mime
                ) {
                    await MainActor.run { attachments.append(result) }
                }
            }
            await MainActor.run { isUploading = false }
        }
    }

    private func mimeType(for url: URL) -> String {
        UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
    }
}

// MARK: - Format button

struct FormatButton: View {
    let icon: String
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .frame(width: 26, height: 22)
        }
        .buttonStyle(.plain)
        .help(help)
    }
}

// MARK: - Attachment bar

struct AttachmentBar: View {
    @Binding var attachments: [UploadResult]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Array(attachments.enumerated()), id: \.offset) { idx, att in
                    AttachmentChip(attachment: att) {
                        attachments.remove(at: idx)
                    }
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
            Image(systemName: icon)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text(attachment.name)
                .font(.system(size: 12))
                .lineLimit(1)
                .frame(maxWidth: 120)
            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.secondary.opacity(0.1))
        .clipShape(Capsule())
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
                ForEach(state.memories) { memory in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(memory.content)
                            .font(.system(size: 13))
                            .textSelection(.enabled)
                        Text(memory.created_at)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 2)
                }
                .onDelete { offsets in
                    let ids = offsets.map { state.memories[$0].id }
                    state.memories.remove(atOffsets: offsets)
                    for id in ids {
                        Task { try? await BackendClient.shared.deleteMemory(id) }
                    }
                }
            }
            .navigationTitle("Memories")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button("Extract from chat") { state.extractMemories() }
                        .disabled(state.messages.isEmpty)
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
