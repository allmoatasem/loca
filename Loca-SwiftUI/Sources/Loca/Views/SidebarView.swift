import SwiftUI
import AppKit

struct SidebarView: View {
    @EnvironmentObject var state: AppState
    @State private var multiSelection: Set<String> = []

    var body: some View {
        VStack(spacing: 0) {
            controlsPanel
            Divider()
            conversationList
            Divider()
            SidebarFooter()
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        }
    }

    // MARK: - Controls

    private var controlsPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button(action: state.newConversation) {
                Label("New Conversation", systemImage: "square.and.pencil")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)

            if !state.availableCapabilities.isEmpty {
                capabilityPicker
            }

            localModelPicker

            HStack {
                Text("Context").font(.caption).foregroundColor(.secondary)
                Spacer()
                Picker("", selection: $state.contextWindow) {
                    ForEach([4096, 8192, 16384, 32768, 65536, 131072, 262144], id: \.self) { n in
                        Text(ctxLabel(n)).tag(n)
                    }
                }
                .labelsHidden()
                .frame(width: 80)
            }

            Toggle("Deep Research", isOn: $state.researchMode)
                .controlSize(.mini)
                .toggleStyle(.switch)
                .help("Deep Research uses a headless browser (Playwright) to fully render pages and extract content, instead of reading raw HTML. Much more accurate for dynamic sites. Slower.")

            HStack {
                Spacer()
                Button {
                    state.isSettingsOpen = true
                } label: {
                    Image(systemName: "gearshape")
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("Open settings — manage models, download new models, configure context window")
                .sheet(isPresented: $state.isSettingsOpen) {
                    SettingsView()
                        .environmentObject(state)
                }
            }
        }
        .padding(12)
    }

    private var localModelPicker: some View {
        Group {
            if state.localModels.isEmpty {
                Button {
                    state.isSettingsOpen = true
                } label: {
                    Label("Download a model…", systemImage: "arrow.down.circle")
                        .font(.system(size: 11))
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.plain)
            } else {
                Picker("Model", selection: $state.selectedModelId) {
                    ForEach(state.localModels) { m in
                        HStack(spacing: 4) {
                            Text(m.name).lineLimit(1)
                            Text(m.formatLabel)
                                .font(.system(size: 9))
                                .foregroundColor(.secondary)
                        }
                        .tag(m.name as String?)
                    }
                }
                .labelsHidden()
                .help(state.localModels.first(where: { $0.name == state.selectedModelId })
                    .map { "\($0.name) · \($0.formatLabel) · \($0.sizeLabel)" } ?? "Select a model")
                .onChange(of: state.selectedModelId) {
                    // Load the newly selected model into the inference backend
                    if let name = state.selectedModelId, name != state.activeModelName {
                        state.loadModel(name, ctxSize: state.contextWindow)
                    }
                }
            }
        }
    }

    private var capabilityPicker: some View {
        HStack(spacing: 0) {
            ForEach(state.availableCapabilities) { cap in
                Button { state.selectedCapability = cap } label: {
                    HStack(spacing: 4) {
                        Image(systemName: cap.systemIcon).font(.system(size: 10))
                        Text(cap.label).font(.system(size: 11, weight: .medium))
                    }
                    .padding(.horizontal, 6).padding(.vertical, 5)
                    .frame(maxWidth: .infinity)
                    .background(state.selectedCapability == cap ? Color.accentColor.opacity(0.15) : Color.clear)
                    .foregroundColor(state.selectedCapability == cap ? .accentColor : .secondary)
                }
                .buttonStyle(.plain)
                if cap != state.availableCapabilities.last { Divider().frame(height: 18) }
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.25)))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func ctxLabel(_ n: Int) -> String { n >= 1024 ? "\(n / 1024)K" : "\(n)" }
    private func shortModelName(_ id: String) -> String { id.split(separator: "/").last.map(String.init) ?? id }

    // MARK: - Conversation list

    private var conversationList: some View {
        VStack(spacing: 0) {
            convSearchBar
            Divider()
            convListBody
        }
    }

    private var convSearchBar: some View {
        ConvSearchBar(
            query: $state.conversationQuery,
            onSearch: { state.searchConversations() },
            onClear: { state.conversationQuery = ""; state.conversationResults = [] }
        )
    }

    private var convListBody: some View {
        let displayConvs = state.conversationQuery.isEmpty
            ? state.conversations
            : state.conversationResults
        return convList(displayConvs)
    }

    private func convList(_ convs: [ConversationMeta]) -> some View {
        let folders = Array(Set(convs.compactMap { ($0.folder?.isEmpty == false) ? $0.folder : nil })).sorted()
        let unfoldered = convs.filter { $0.folder == nil || $0.folder!.isEmpty }

        return List(selection: $multiSelection) {
            if !folders.isEmpty {
                ForEach(folders, id: \.self) { folder in
                    Section {
                        ForEach(convs.filter { $0.folder == folder }) { conv in
                            conversationRow(conv)
                        }
                    } header: {
                        Text(folder)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 2)
                            .dropDestination(for: String.self) { draggedIds, _ in
                                let toMove = resolveMove(draggedIds)
                                for id in toMove { state.setConversationFolder(id, folder: folder) }
                                return true
                            }
                    }
                }
                if !unfoldered.isEmpty {
                    Section {
                        ForEach(unfoldered) { conv in conversationRow(conv) }
                    } header: {
                        Text("Other")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 2)
                            .dropDestination(for: String.self) { draggedIds, _ in
                                let toMove = resolveMove(draggedIds)
                                for id in toMove { state.setConversationFolder(id, folder: nil) }
                                return true
                            }
                    }
                }
            } else {
                ForEach(convs) { conv in conversationRow(conv) }
            }
        }
        .listStyle(.sidebar)
        // Drop zone at the bottom: drag conversations here to create a new folder
        .safeAreaInset(edge: .bottom) {
            if !multiSelection.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "folder.badge.plus").font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Text("\(multiSelection.count) selected — drag to a folder header")
                        .font(.system(size: 11)).foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity).padding(8)
                .background(Color.accentColor.opacity(0.07))
            }
        }
        .onChange(of: multiSelection) {
            // Single selection → load conversation; multi-select stays silent
            if multiSelection.count == 1, let id = multiSelection.first {
                state.loadConversation(id)
            }
        }
        .overlay {
            if convs.isEmpty && state.isBackendReady {
                emptyListOverlay
            }
        }
    }

    /// If the dragged item is among the multi-selection, move all of them; else just the one.
    private func resolveMove(_ draggedIds: [String]) -> Set<String> {
        guard let first = draggedIds.first else { return [] }
        return multiSelection.contains(first) ? multiSelection : [first]
    }

    private var emptyListOverlay: some View {
        VStack(spacing: 6) {
            if !state.conversationQuery.isEmpty {
                Text("No results for \"\(state.conversationQuery)\"")
                    .font(.system(size: 12)).foregroundColor(.secondary)
            } else {
                Text("No conversations yet")
                    .font(.system(size: 12)).foregroundColor(.secondary)
                Button("Reload") { state.reloadConversations() }
                    .font(.system(size: 11)).buttonStyle(.link)
            }
        }
    }

    @ViewBuilder
    private func conversationRow(_ conv: ConversationMeta) -> some View {
        ConversationRow(conv: conv)
            .tag(conv.id)
            .draggable(conv.id)
            .contextMenu {
                if multiSelection.count > 1 && multiSelection.contains(conv.id) {
                    Text("\(multiSelection.count) conversations selected")
                        .font(.caption).foregroundColor(.secondary)
                    Button("Set Folder for All…") { promptFolderForSelection() }
                    Button("Delete All", role: .destructive) {
                        for id in multiSelection { state.deleteConversation(id) }
                        multiSelection = []
                    }
                } else {
                    Button(conv.starred ? "Unstar" : "Star") { state.toggleStar(conv.id) }
                    Button("Set Folder…") { promptFolder(for: conv) }
                    if conv.folder != nil {
                        Button("Remove from Folder") { state.setConversationFolder(conv.id, folder: nil) }
                    }
                    Divider()
                    Button("Delete", role: .destructive) { state.deleteConversation(conv.id) }
                }
            }
    }

    private func promptFolderForSelection() {
        let alert = NSAlert()
        alert.messageText = "Set Folder for \(multiSelection.count) Conversations"
        alert.informativeText = "Leave empty to remove from folder:"
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        tf.placeholderString = "Folder name…"
        alert.accessoryView = tf
        alert.addButton(withTitle: "Set")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            let folder = tf.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            for id in multiSelection {
                state.setConversationFolder(id, folder: folder.isEmpty ? nil : folder)
            }
            multiSelection = []
        }
    }

    private func promptFolder(for conv: ConversationMeta) {
        let alert = NSAlert()
        alert.messageText = "Set Folder"
        alert.informativeText = "Enter a folder name (leave empty to remove from folder):"
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        tf.stringValue = conv.folder ?? ""
        tf.placeholderString = "Folder name…"
        alert.accessoryView = tf
        alert.addButton(withTitle: "Set")
        alert.addButton(withTitle: "Cancel")
        tf.becomeFirstResponder()
        if alert.runModal() == .alertFirstButtonReturn {
            let folder = tf.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            state.setConversationFolder(conv.id, folder: folder.isEmpty ? nil : folder)
        }
    }
}

// MARK: - Conversation search bar (owns FocusState so TextField gets focus)

struct ConvSearchBar: View {
    @Binding var query: String
    let onSearch: () -> Void
    let onClear: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .foregroundColor(.secondary).font(.system(size: 11))
            TextField("Search conversations…", text: $query)
                .font(.system(size: 12))
                .textFieldStyle(.plain)
                .focused($focused)
                .onSubmit { onSearch() }
                .onChange(of: query) {
                    if query.isEmpty { onClear() } else { onSearch() }
                }
            if !query.isEmpty {
                Button(action: onClear) {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary).font(.system(size: 11))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color(nsColor: .controlBackgroundColor))
        .onTapGesture { focused = true }
    }
}

// MARK: - Conversation row

struct ConversationRow: View {
    @EnvironmentObject var state: AppState
    let conv: ConversationMeta
    @State private var isHovered = false

    var body: some View {
        HStack(spacing: 6) {
            VStack(alignment: .leading, spacing: 2) {
                Text(conv.title).font(.system(size: 13)).lineLimit(1)
                HStack(spacing: 4) {
                    Text(relativeDate(conv.updatedDate))
                        .font(.system(size: 11)).foregroundColor(.secondary)
                    if let folder = conv.folder, !folder.isEmpty {
                        Text(folder)
                            .font(.system(size: 9))
                            .padding(.horizontal, 5).padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.1))
                            .foregroundColor(.accentColor)
                            .clipShape(Capsule())
                    }
                }
            }
            Spacer()
            Button { state.toggleStar(conv.id) } label: {
                Image(systemName: conv.starred ? "star.fill" : "star")
                    .font(.system(size: 11))
                    .foregroundColor(conv.starred ? .yellow : Color.secondary.opacity(0.4))
            }
            .buttonStyle(.plain)
            .opacity(conv.starred || isHovered ? 1 : 0)
            .animation(.easeInOut(duration: 0.12), value: isHovered)
        }
        .contentShape(Rectangle())
        .onHover { isHovered = $0 }
        .padding(.vertical, 2)
    }

    private func relativeDate(_ date: Date) -> String {
        let rf = RelativeDateTimeFormatter()
        rf.unitsStyle = .short
        return rf.localizedString(for: date, relativeTo: Date())
    }
}

// MARK: - Sidebar footer

struct SidebarFooter: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(spacing: 8) {
            if let used = state.ramUsed, let total = state.ramTotal {
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(format: "%.1f / %.0f GB", used, total))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                    ProgressView(value: used, total: total)
                        .progressViewStyle(.linear)
                        .frame(height: 3)
                        .tint(used / total > 0.85 ? .orange : .accentColor)
                }
                .help("System RAM in use / total. The loaded model consumes most of this. Updated every 10 seconds.")
            }
            Spacer()
            Button {
                state.isDarkMode.toggle()
            } label: {
                Image(systemName: state.isDarkMode ? "sun.max" : "moon")
                    .font(.system(size: 16))
                    .foregroundColor(.secondary)
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .help("Toggle dark/light theme. Preference is saved locally.")

            Button { state.isMemoryPanelOpen = true } label: {
                Image(systemName: "brain")
                    .font(.system(size: 16))
                    .foregroundColor(.secondary)
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .help("Memories — facts extracted from your conversations and injected into every new chat. Click to view and manage them.")
        }
    }
}
