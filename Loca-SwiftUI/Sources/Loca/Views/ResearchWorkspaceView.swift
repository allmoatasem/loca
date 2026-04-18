import SwiftUI

/// Research Partner — Swift-side twin of `ResearchWorkspaceView.svelte`.
/// Overview + Sources + Notes + Watches tabs match the browser UI. Notes
/// is a freeform markdown scratchpad with the same 700ms autosave debounce
/// as Svelte. Partner-mode toggles live in the chat composer.
struct ResearchWorkspaceView: View {
    @EnvironmentObject var state: AppState

    enum Tab: String, CaseIterable, Identifiable {
        case overview, sources, notes, watches
        var id: String { rawValue }
        var label: String {
            switch self {
            case .overview: return "Overview"
            case .sources:  return "Sources"
            case .notes:    return "Notes"
            case .watches:  return "Watches"
            }
        }
    }

    @State private var activeTab: Tab = .overview
    @State private var detail: ProjectDetail?
    @State private var items: [ProjectItem] = []
    @State private var isLoading = false
    // Source-kind filter — mirrors the Svelte dropdown so Mac users can
    // narrow Sources to a single kind. Empty string = all.
    @State private var filterKind: String = ""

    // Notes — freeform markdown scratchpad with 700ms debounced autosave.
    @State private var notesDraft: String = ""
    @State private var notesSaveTask: Task<Void, Never>?

    // New-project form
    @State private var newTitle = ""
    @State private var newScope = ""

    // Scope editor
    @State private var scopeDraft = ""

    // Dig deeper
    @State private var digDraft = ""
    @State private var digBusy = false
    @State private var digStatus: String?

    // Vault sync
    @State private var vaultPath = ""
    @State private var vaultBusy = false
    @State private var vaultStatus: String?

    // New watch
    @State private var watchScope = ""
    @State private var watchMinutes: Int = 1440

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            tabBar
            Divider()
            content
        }
        .frame(width: 620, height: 720)
        .onAppear {
            Task { await state.loadProjects() }
            if let id = state.activeProjectId { Task { await refreshDetail(id) } }
        }
        .onChange(of: state.activeProjectId) { _, id in
            scopeDraft = ""
            notesDraft = ""
            notesSaveTask?.cancel()
            notesSaveTask = nil
            detail = nil
            items = []
            if let id { Task { await refreshDetail(id) } }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Research")
                .font(.system(size: 14, weight: .semibold))
            Picker("", selection: Binding<String>(
                get: { state.activeProjectId ?? "" },
                set: { state.setActiveProject($0.isEmpty ? nil : $0) }
            )) {
                Text(state.projects.isEmpty
                     ? "Create your first project"
                     : "Choose a project…"
                ).tag("")
                if state.activeProjectId != nil {
                    Text("— Unset active project —").tag("")
                }
                ForEach(state.projects) { p in
                    Text(p.title).tag(p.id)
                }
            }
            .labelsHidden()
            .frame(maxWidth: 260)
            Spacer()
            Button {
                state.isResearchOpen = false
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.secondary)
                    .frame(width: 24, height: 24)
                    .background(Color.secondary.opacity(0.1))
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 20)
        .padding(.top, 14)
        .padding(.bottom, 10)
    }

    private var tabBar: some View {
        HStack(spacing: 4) {
            ForEach(Tab.allCases) { tab in
                Button { activeTab = tab } label: {
                    Text(tab.label)
                        .font(.system(size: 12))
                        .padding(.horizontal, 12).padding(.vertical, 5)
                        .background(
                            activeTab == tab
                              ? Color.accentColor.opacity(0.15)
                              : Color.clear
                        )
                        .foregroundColor(activeTab == tab ? .accentColor : .secondary)
                        .cornerRadius(6)
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(activeTab == tab ? Color.accentColor.opacity(0.4) : .clear)
                        )
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private var content: some View {
        if state.activeProjectId == nil {
            newProjectForm
        } else if let d = detail {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    switch activeTab {
                    case .overview: overviewTab(d)
                    case .sources:  sourcesTab(d)
                    case .notes:    notesTab(d)
                    case .watches:  watchesTab(d)
                    }
                }
                .padding(20)
            }
        } else {
            VStack { Text("Loading project…").foregroundColor(.secondary) }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var newProjectForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Start a research project")
                .font(.system(size: 13, weight: .semibold))
            Text("A project bundles a topic, its bookmarked sources, notes, and background watches. Pick an existing one above, or create a new one.")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 4) {
                Text("Title").font(.system(size: 11, weight: .medium))
                TextField("Transformers in audio", text: $newTitle)
                    .textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Scope").font(.system(size: 11, weight: .medium))
                TextEditor(text: $newScope)
                    .font(.system(size: 12))
                    .frame(minHeight: 80, maxHeight: 120)
                    .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.3)))
            }
            Button { Task { await createProject() } } label: {
                Text("Create project")
                    .font(.system(size: 12, weight: .medium))
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)
            .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(20)
        .frame(maxWidth: 460, alignment: .leading)
        .frame(maxHeight: .infinity, alignment: .top)
    }

    @ViewBuilder
    private func overviewTab(_ d: ProjectDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("SCOPE").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            TextEditor(text: $scopeDraft)
                .font(.system(size: 12))
                .frame(minHeight: 70)
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.3)))
            HStack {
                HStack(spacing: 14) {
                    statChip("\(d.items_count) sources")
                    statChip("\(d.conversations.count) convs")
                    statChip("\(d.watches.count) watches")
                }
                Spacer()
                Button("Save scope") { Task { await saveScope() } }
                    .buttonStyle(.borderless)
                Button("Delete") { Task { await deleteProject() } }
                    .buttonStyle(.borderless)
                    .foregroundColor(.red)
            }
        }

        VStack(alignment: .leading, spacing: 6) {
            Text("QUICK ACTIONS").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            HStack(spacing: 8) {
                Button("Attach current conversation") { Task { await attachCurrent() } }
                    .disabled(state.activeConversationId == nil)
                Button("Pin a quote") { Task { await pinQuote() } }
            }
        }

        VStack(alignment: .leading, spacing: 6) {
            Text("DIG DEEPER").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            Text("Bounded web research on a sub-scope. Top hits are imported into memory and bookmarked here.")
                .font(.system(size: 11)).foregroundColor(.secondary)
            HStack {
                TextField("e.g. WaveNet baselines", text: $digDraft)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await digDeeper() } }
                Button(digBusy ? "Working…" : "Dig") { Task { await digDeeper() } }
                    .disabled(digBusy || digDraft.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let s = digStatus { Text(s).font(.system(size: 11)).foregroundColor(.secondary) }
        }
    }

    @ViewBuilder
    private func sourcesTab(_ d: ProjectDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("SOURCES (\(items.count))")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.secondary)
                Spacer()
                Picker("", selection: $filterKind) {
                    Text("All").tag("")
                    Text("Conversations").tag("conv")
                    Text("Quotes").tag("quote")
                    Text("Web URLs").tag("web_url")
                    Text("Memories").tag("memory")
                    Text("Vault chunks").tag("vault_chunk")
                    Text("Vault syncs").tag("vault_sync")
                }
                .labelsHidden()
                .frame(maxWidth: 160)
                .onChange(of: filterKind) { _, _ in
                    if let id = state.activeProjectId {
                        Task { await reloadItems(id) }
                    }
                }
            }
            HStack {
                TextField("Obsidian vault path", text: $vaultPath)
                    .textFieldStyle(.roundedBorder)
                Button(vaultBusy ? "Syncing…" : "Sync vault") { Task { await syncVault() } }
                    .disabled(vaultBusy || vaultPath.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let s = vaultStatus { Text(s).font(.system(size: 11)).foregroundColor(.secondary) }
        }
        if items.isEmpty {
            Text("No sources pinned yet. Attach a conversation, pin a quote, dig deeper, or sync a vault.")
                .font(.system(size: 11)).foregroundColor(.secondary)
        } else {
            VStack(spacing: 6) {
                ForEach(items) { it in
                    itemRow(it)
                }
            }
        }
    }

    @ViewBuilder
    private func notesTab(_ d: ProjectDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("NOTES")
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(.secondary)
            Text("Freeform markdown scratchpad. Autosaves ~700ms after you stop typing.")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            TextEditor(text: $notesDraft)
                .font(.system(size: 12))
                .frame(minHeight: 380)
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color.secondary.opacity(0.3))
                )
                .onChange(of: notesDraft) { _, _ in scheduleNotesSave() }
            if notesDraft.isEmpty {
                Text("Questions, todos, open threads, quotes, hypotheses — anything.")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary.opacity(0.7))
            }
        }
    }

    @ViewBuilder
    private func watchesTab(_ d: ProjectDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("NEW WATCH").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            Text("Background search every N minutes; new URLs get appended to Sources. Minimum 60 min, max 2 weeks.")
                .font(.system(size: 11)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Sub-scope (e.g. new audio transformer papers on arXiv)", text: $watchScope)
                .textFieldStyle(.roundedBorder)
            Picker("Every", selection: $watchMinutes) {
                Text("1 hour").tag(60)
                Text("6 hours").tag(360)
                Text("Daily").tag(1440)
                Text("Weekly").tag(10080)
            }
            .pickerStyle(.menu)
            Button("Create watch") { Task { await createWatch() } }
                .buttonStyle(.borderless)
                .disabled(watchScope.trimmingCharacters(in: .whitespaces).isEmpty)
        }

        VStack(alignment: .leading, spacing: 6) {
            Text("ACTIVE WATCHES (\(d.watches.count))")
                .font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            if d.watches.isEmpty {
                Text("No watches yet.").font(.system(size: 11)).foregroundColor(.secondary)
            } else {
                ForEach(d.watches) { w in
                    HStack {
                        Image(systemName: "timer").foregroundColor(.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(w.sub_scope).font(.system(size: 12, weight: .medium))
                            Text(watchLabel(w)).font(.system(size: 10)).foregroundColor(.secondary)
                        }
                        Spacer()
                        Button { Task { await deleteWatch(w.id) } } label: {
                            Image(systemName: "xmark.circle")
                                .foregroundColor(.secondary)
                        }.buttonStyle(.plain)
                    }
                    .padding(8)
                    .background(Color.secondary.opacity(0.05))
                    .cornerRadius(5)
                }
            }
        }
    }

    private func watchLabel(_ w: ProjectWatch) -> String {
        let every = w.schedule_minutes < 1440
            ? "every \(w.schedule_minutes)min"
            : "every \(w.schedule_minutes / 1440)d"
        let last = w.last_run.map { "last run \(Self.fmt($0))" } ?? "never run"
        return "\(every) · \(last)"
    }

    private static func fmt(_ ts: Double) -> String {
        let d = Date(timeIntervalSince1970: ts)
        let f = DateFormatter()
        f.dateStyle = .short
        return f.string(from: d)
    }

    @ViewBuilder
    private func itemRow(_ it: ProjectItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(kindIcon(it.kind))
            VStack(alignment: .leading, spacing: 2) {
                if let url = it.url, !url.isEmpty,
                   let parsed = URL(string: url) {
                    Link(it.title.isEmpty ? url : it.title, destination: parsed)
                        .font(.system(size: 12, weight: .semibold))
                } else {
                    Text(it.title.isEmpty ? "(untitled)" : it.title)
                        .font(.system(size: 12, weight: .semibold))
                }
                if !it.body.isEmpty {
                    Text(String(it.body.prefix(240)))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("\(it.kind) · \(Self.fmt(it.created))")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            Spacer()
            Button { Task { await deleteItem(it.id) } } label: {
                Image(systemName: "xmark")
                    .foregroundColor(.secondary)
                    .font(.system(size: 10))
            }
            .buttonStyle(.plain)
        }
        .padding(8)
        .background(Color.secondary.opacity(0.05))
        .cornerRadius(5)
    }

    private func kindIcon(_ k: String) -> String {
        switch k {
        case "conv":        return "💬"
        case "memory":      return "🧠"
        case "vault_chunk": return "📓"
        case "web_url":     return "🌐"
        case "quote":       return "❝"
        case "vault_sync":  return "🗃️"
        default:            return "•"
        }
    }

    private func statChip(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundColor(.secondary)
    }

    // MARK: - Actions

    private func refreshDetail(_ id: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let d = try await BackendClient.shared.getProject(id)
            detail = d
            scopeDraft = d.scope
            // Only hydrate notesDraft when switching projects, not on a
            // passive refresh mid-edit — otherwise an in-flight keystroke
            // would get clobbered by the stale server copy.
            if notesDraft.isEmpty || notesSaveTask == nil {
                notesDraft = d.notes
            }
            await reloadItems(id)
        } catch {
            detail = nil
            items = []
        }
    }

    private func scheduleNotesSave() {
        notesSaveTask?.cancel()
        let snapshot = notesDraft
        notesSaveTask = Task {
            try? await Task.sleep(nanoseconds: 700_000_000)
            if Task.isCancelled { return }
            await saveNotes(snapshot)
        }
    }

    private func saveNotes(_ text: String) async {
        guard let id = state.activeProjectId else { return }
        do {
            try await BackendClient.shared.patchProject(id, notes: text)
        } catch {}
    }

    private func reloadItems(_ id: String) async {
        do {
            items = try await BackendClient.shared.listProjectItems(
                id, kind: filterKind.isEmpty ? nil : filterKind
            )
        } catch {
            items = []
        }
    }

    private func createProject() async {
        let title = newTitle.trimmingCharacters(in: .whitespaces)
        guard !title.isEmpty else { return }
        do {
            let p = try await BackendClient.shared.createProject(title: title, scope: newScope)
            newTitle = ""
            newScope = ""
            await state.loadProjects()
            state.setActiveProject(p.id)
        } catch {}
    }

    private func saveScope() async {
        guard let id = state.activeProjectId else { return }
        do {
            try await BackendClient.shared.patchProject(id, scope: scopeDraft)
            await refreshDetail(id)
        } catch {}
    }

    private func deleteProject() async {
        guard let id = state.activeProjectId, let d = detail else { return }
        // No SwiftUI dialog helper, just act — rely on Svelte for double-confirm.
        do {
            try await BackendClient.shared.deleteProject(id)
            _ = d
            state.setActiveProject(nil)
            await state.loadProjects()
        } catch {}
    }

    private func attachCurrent() async {
        guard let pid = state.activeProjectId,
              let cid = state.activeConversationId else { return }
        do {
            try await BackendClient.shared.attachConversationToProject(projectId: pid, convId: cid)
            await refreshDetail(pid)
        } catch {}
    }

    private func pinQuote() async {
        guard let pid = state.activeProjectId else { return }
        // Simple inline-prompt replacement — a modal would be nicer but
        // keeps scope tight. Reuses the New-project title field area
        // would be worse UX than just piping through a dialog on Svelte.
        // For Mac, we wire up a minimal input sheet in v1.1.
        digStatus = "Use the browser UI to paste a quote for now."
    }

    private func digDeeper() async {
        guard let pid = state.activeProjectId else { return }
        let sub = digDraft.trimmingCharacters(in: .whitespaces)
        guard !sub.isEmpty else { return }
        digBusy = true
        digStatus = "Searching…"
        do {
            let r = try await BackendClient.shared.digDeeper(projectId: pid, subScope: sub, maxResults: 5)
            let fresh = r.bookmarks.filter { !$0.duplicate }.count
            digStatus = "Bookmarked \(fresh) new source\(fresh == 1 ? "" : "s") of \(r.total)."
            digDraft = ""
            await reloadItems(pid)
        } catch {
            digStatus = "Failed."
        }
        digBusy = false
    }

    private func syncVault() async {
        guard let pid = state.activeProjectId else { return }
        let p = vaultPath.trimmingCharacters(in: .whitespaces)
        guard !p.isEmpty else { return }
        vaultBusy = true
        vaultStatus = "Ingesting vault…"
        do {
            let r = try await BackendClient.shared.syncVault(projectId: pid, path: p)
            vaultStatus = "Synced: \(r.stored) stored, \(r.skipped) skipped (of \(r.total))."
            await reloadItems(pid)
        } catch {
            vaultStatus = "Failed."
        }
        vaultBusy = false
    }

    private func createWatch() async {
        guard let pid = state.activeProjectId else { return }
        let s = watchScope.trimmingCharacters(in: .whitespaces)
        guard !s.isEmpty else { return }
        let minutes = max(60, min(watchMinutes, 60 * 24 * 14))
        do {
            try await BackendClient.shared.createWatch(projectId: pid, subScope: s, scheduleMinutes: minutes)
            watchScope = ""
            await refreshDetail(pid)
        } catch {}
    }

    private func deleteWatch(_ wid: String) async {
        guard let pid = state.activeProjectId else { return }
        do {
            try await BackendClient.shared.deleteWatch(projectId: pid, watchId: wid)
            await refreshDetail(pid)
        } catch {}
    }

    private func deleteItem(_ iid: String) async {
        guard let pid = state.activeProjectId else { return }
        do {
            try await BackendClient.shared.deleteProjectItem(projectId: pid, itemId: iid)
            await reloadItems(pid)
        } catch {}
    }
}
