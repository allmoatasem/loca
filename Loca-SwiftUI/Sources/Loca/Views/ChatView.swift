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
    let message: ChatMessage
    let showTypingIndicator: Bool

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if isUser {
                Spacer(minLength: 80)
                content
                    .background(Color.accentColor.opacity(0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            } else {
                avatar
                content
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                Spacer(minLength: 80)
            }
        }
    }

    private var avatar: some View {
        ZStack {
            Circle().fill(Color.accentColor)
            Text("L").font(.system(size: 11, weight: .bold)).foregroundColor(.white)
        }
        .frame(width: 26, height: 26)
    }

    @ViewBuilder
    private var content: some View {
        let plain = message.content.plainText
        if isUser {
            Text(plain)
                .font(.system(size: 14))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        } else if plain.isEmpty {
            // Streaming has started but no tokens yet — show typing indicator
            TypingIndicator()
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
        } else {
            MarkdownView(text: plain)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        }
    }
}

// MARK: - Typing indicator (three bouncing dots)

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
                        .easeInOut(duration: 0.4)
                            .repeatForever(autoreverses: true)
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

    private enum Segment { case prose(String); case code(String?, String) }

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
            // Formatting toolbar
            HStack(spacing: 2) {
                FormatButton(icon: "bold",   help: "Bold (**text**)") { inputActions.wrap("**") }
                FormatButton(icon: "italic", help: "Italic (_text_)")  { inputActions.wrap("_") }
                FormatButton(icon: "chevron.left.forwardslash.chevron.right",
                             help: "Inline code (`text`)")             { inputActions.wrap("`") }
                FormatButton(icon: "text.alignleft",
                             help: "Code block (```)")                 { inputActions.codeBlock() }
                Spacer()
                if state.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView().scaleEffect(0.55)
                        Text("Generating…")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                    .padding(.trailing, 4)
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 8)

            // Text editor + send controls
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
                            .font(.system(size: 15))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .disabled(isUploading)
                    .help("Attach file")

                    Button(action: onSend) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 26))
                            .foregroundColor(canSend ? .accentColor : Color.secondary.opacity(0.3))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSend)
                    .help("Send (Return)")
                }
                .padding(.bottom, 4)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 12)
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
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color.secondary.opacity(0.1))
                    .clipShape(Capsule())
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
                        Text(memory.content).font(.system(size: 13)).textSelection(.enabled)
                        Text(memory.created_at).font(.caption).foregroundColor(.secondary)
                    }
                    .padding(.vertical, 2)
                }
                .onDelete { offsets in
                    let ids = offsets.map { state.memories[$0].id }
                    state.memories.remove(atOffsets: offsets)
                    for id in ids { Task { try? await BackendClient.shared.deleteMemory(id) } }
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
