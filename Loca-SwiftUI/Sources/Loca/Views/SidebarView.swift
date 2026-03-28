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

            // Capability tabs — only shows capabilities present in loaded models
            if !state.availableCapabilities.isEmpty {
                capabilityPicker
            }

            // Model list filtered to selected capability
            let capModels = state.models(for: state.selectedCapability)
            if !capModels.isEmpty {
                Picker("Model", selection: $state.selectedModelId) {
                    ForEach(capModels) { m in
                        Text(shortModelName(m.id)).lineLimit(1).tag(m.id as String?)
                    }
                }
                .labelsHidden()
                .onChange(of: state.selectedCapability) {
                    if let first = state.models(for: state.selectedCapability).first {
                        state.selectedModelId = first.id
                    }
                }
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

            Toggle("Deep Research", isOn: $state.researchMode)
                .controlSize(.mini)
                .toggleStyle(.switch)
                .help("Fetches full page content via Playwright (slower, richer). Basic SearXNG search is always on for factual questions.")
        }
        .padding(12)
    }

    /// Icon+label tabs for each available capability.
    private var capabilityPicker: some View {
        HStack(spacing: 0) {
            ForEach(state.availableCapabilities) { cap in
                Button {
                    state.selectedCapability = cap
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: cap.systemIcon).font(.system(size: 10))
                        Text(cap.label).font(.system(size: 11, weight: .medium))
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 5)
                    .frame(maxWidth: .infinity)
                    .background(
                        state.selectedCapability == cap
                            ? Color.accentColor.opacity(0.15)
                            : Color.clear
                    )
                    .foregroundColor(
                        state.selectedCapability == cap ? .accentColor : .secondary
                    )
                }
                .buttonStyle(.plain)

                if cap != state.availableCapabilities.last {
                    Divider().frame(height: 18)
                }
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.25)))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func ctxLabel(_ n: Int) -> String {
        n >= 1024 ? "\(n / 1024)K" : "\(n)"
    }

    /// Shows only the last path component of a model ID (strips org prefix if any).
    private func shortModelName(_ id: String) -> String {
        id.split(separator: "/").last.map(String.init) ?? id
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
        .overlay {
            if state.conversations.isEmpty {
                Text("No conversations yet")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }
        }
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
