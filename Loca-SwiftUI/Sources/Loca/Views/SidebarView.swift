import SwiftUI

struct SidebarView: View {
    @EnvironmentObject var state: AppState

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

            Picker("Mode", selection: $state.selectedMode) {
                ForEach(ChatMode.allCases) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            if !state.availableModels.isEmpty {
                Picker("Model", selection: $state.selectedModelId) {
                    Text("Auto").tag(nil as String?)
                    ForEach(state.availableModels) { m in
                        Text(m.id).lineLimit(1).tag(m.id as String?)
                    }
                }
                .labelsHidden()
            }

            HStack {
                Text("Context")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Picker("", selection: $state.contextWindow) {
                    ForEach([4096, 8192, 16384, 32768, 65536, 131072], id: \.self) { n in
                        Text(ctxLabel(n)).tag(n)
                    }
                }
                .labelsHidden()
                .frame(width: 80)
            }

            Toggle("Research", isOn: $state.researchMode)
                .controlSize(.mini)
                .toggleStyle(.switch)
        }
        .padding(12)
    }

    private func ctxLabel(_ n: Int) -> String {
        n >= 1024 ? "\(n / 1024)K" : "\(n)"
    }

    // MARK: - Conversation list

    private var conversationList: some View {
        List(
            selection: Binding(
                get: { state.activeConversationId },
                set: { if let id = $0 { state.loadConversation(id) } }
            )
        ) {
            ForEach(state.conversations) { conv in
                ConversationRow(conv: conv)
                    .tag(conv.id)
                    .contextMenu {
                        Button("Delete", role: .destructive) {
                            state.deleteConversation(conv.id)
                        }
                    }
            }
        }
        .listStyle(.sidebar)
    }
}

// MARK: - Conversation row

struct ConversationRow: View {
    let conv: ConversationMeta

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(conv.title)
                .font(.system(size: 13))
                .lineLimit(1)
            Text(relativeDate(conv.updated_at))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 2)
    }

    private func relativeDate(_ str: String) -> String {
        let withFractional: ISO8601DateFormatter = {
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return f
        }()
        let plain: ISO8601DateFormatter = {
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime]
            return f
        }()
        let date = withFractional.date(from: str) ?? plain.date(from: str)
        guard let date else { return str }
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
            }
            Spacer()
            Button {
                state.isMemoryPanelOpen = true
            } label: {
                Image(systemName: "brain")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .help("Memories")
        }
    }
}
