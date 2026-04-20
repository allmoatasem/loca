import SwiftUI

struct VaultView: View {
    @EnvironmentObject var state: AppState
    @State private var selectedTab = "overview"
    @State private var searchQuery = ""
    @State private var pendingRegisterPath = ""

    // Timer used to refresh the watched-vault list while the view is
    // open so last-scan and busy flags stay fresh without a reload.
    @State private var refreshTimer: Timer?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            watchedList
            if state.selectedVaultPath.isEmpty {
                emptyState
            } else if let analysis = state.vaultAnalysis {
                tabBar
                Divider()
                tabContent(analysis)
            } else if state.isVaultAnalysing || state.isVaultScanning {
                Spacer()
                ProgressView("Analysing vault...")
                Spacer()
            } else {
                scanPrompt
            }
        }
        .frame(width: 720, height: 600)
        .onAppear {
            state.detectVaults()
            state.refreshWatchedVaults()
            refreshTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in
                Task { @MainActor in state.refreshWatchedVaults() }
            }
        }
        .onDisappear {
            refreshTimer?.invalidate()
            refreshTimer = nil
        }
    }

    // MARK: - Watched list

    private var watchedList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Watched vaults").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
                Spacer()
            }
            if state.watchedVaults.isEmpty {
                HStack(spacing: 8) {
                    if !state.detectedVaults.isEmpty {
                        Picker("", selection: $pendingRegisterPath) {
                            Text("Pick a vault…").tag("")
                            ForEach(state.detectedVaults) { v in
                                Text(v.name).tag(v.path)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: 220)
                    } else {
                        TextField("/path/to/Obsidian/vault", text: $pendingRegisterPath)
                            .textFieldStyle(.roundedBorder)
                    }
                    Button(state.isRegisteringVault ? "Registering…" : "Watch this vault") {
                        state.registerWatchedVault(path: pendingRegisterPath)
                        pendingRegisterPath = ""
                    }
                    .disabled(state.isRegisteringVault || pendingRegisterPath.trimmingCharacters(in: .whitespaces).isEmpty)
                    .controlSize(.small)
                }
            } else {
                ForEach(state.watchedVaults) { v in
                    watchedRow(v)
                }
                HStack(spacing: 8) {
                    let undetected = state.detectedVaults.filter { dv in
                        !state.watchedVaults.contains(where: { $0.path == dv.path })
                    }
                    if !undetected.isEmpty {
                        Picker("", selection: $pendingRegisterPath) {
                            Text("Add another…").tag("")
                            ForEach(undetected) { v in
                                Text(v.name).tag(v.path)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: 220)
                    } else {
                        TextField("/path/to/another/vault", text: $pendingRegisterPath)
                            .textFieldStyle(.roundedBorder)
                    }
                    Button(state.isRegisteringVault ? "Adding…" : "Watch") {
                        state.registerWatchedVault(path: pendingRegisterPath)
                        pendingRegisterPath = ""
                    }
                    .disabled(state.isRegisteringVault || pendingRegisterPath.trimmingCharacters(in: .whitespaces).isEmpty)
                    .controlSize(.small)
                }
            }
            if let err = state.watcherError {
                Text(err).font(.system(size: 11)).foregroundColor(.red)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.secondary.opacity(0.04))
    }

    private func watchedRow(_ v: WatchedVault) -> some View {
        HStack(spacing: 8) {
            Button {
                state.selectVaultPath(v.path)
            } label: {
                VStack(alignment: .leading, spacing: 1) {
                    Text(v.name).font(.system(size: 12, weight: .medium))
                    Text(relativeScanTime(v))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            Button("Scan now") { state.scanWatchedVaultNow(path: v.path) }
                .disabled(v.busy)
                .controlSize(.mini)
            Button("Remove") { state.unregisterWatchedVault(path: v.path) }
                .controlSize(.mini)
                .tint(.red)
        }
        .padding(6)
        .background(v.path == state.selectedVaultPath ? Color.accentColor.opacity(0.12) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func relativeScanTime(_ v: WatchedVault) -> String {
        if v.busy { return "syncing…" }
        guard let ts = v.last_scan_at else { return "never scanned" }
        let secs = max(1, Int(Date().timeIntervalSince1970 - ts))
        let base: String
        if secs < 60 { base = "\(secs)s ago" }
        else if secs < 3600 { base = "\(secs / 60)m ago" }
        else if secs < 86_400 { base = "\(secs / 3600)h ago" }
        else { base = "\(secs / 86_400)d ago" }
        if let total = v.last_stats.total { return "\(base) · \(total) notes" }
        return base
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "books.vertical")
                .font(.system(size: 20))
                .foregroundColor(.accentColor)
            Text("Obsidian Watcher")
                .font(.system(size: 16, weight: .semibold))
            Spacer()
            vaultPicker
            Button(action: state.scanVault) {
                Label(state.isVaultScanning ? "Scanning..." : "Scan", systemImage: "arrow.clockwise")
            }
            .disabled(state.selectedVaultPath.isEmpty || state.isVaultScanning)
            .controlSize(.small)
            Button { state.isVaultOpen = false } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 16))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(16)
    }

    private var vaultPicker: some View {
        Group {
            if state.detectedVaults.isEmpty {
                Button("Browse...") { browseForVault() }
                    .controlSize(.small)
            } else {
                Picker("", selection: $state.selectedVaultPath) {
                    ForEach(state.detectedVaults) { v in
                        Text(v.name).tag(v.path)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 180)
                .onChange(of: state.selectedVaultPath) {
                    if !state.selectedVaultPath.isEmpty { state.analyseVault() }
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "questionmark.folder").font(.system(size: 36)).foregroundColor(.secondary)
            Text("No vault selected").font(.headline)
            Text("Select an Obsidian vault to analyse, or browse for one.").font(.caption).foregroundColor(.secondary)
            Button("Browse for Vault...") { browseForVault() }
            Spacer()
        }
    }

    private var scanPrompt: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "doc.text.magnifyingglass").font(.system(size: 36)).foregroundColor(.secondary)
            Text("Vault not indexed yet").font(.headline)
            Text("Scan your vault to build the index. This is read-only and does not modify your files.")
                .font(.caption).foregroundColor(.secondary).multilineTextAlignment(.center).frame(maxWidth: 360)
            Button("Scan Now") { state.scanVault() }.buttonStyle(.borderedProminent)
            if let err = state.vaultError {
                Text(err).font(.caption).foregroundColor(.red).padding(.top, 4)
            }
            Spacer()
        }
    }

    // MARK: - Tabs

    private var tabBar: some View {
        HStack(spacing: 0) {
            tabButton("Overview", id: "overview", icon: "chart.bar")
            tabButton("Orphans", id: "orphans", icon: "link.badge.plus")
            tabButton("Broken Links", id: "broken", icon: "exclamationmark.triangle")
            tabButton("Suggestions", id: "suggestions", icon: "lightbulb")
            tabButton("Search", id: "search", icon: "magnifyingglass")
        }
        .padding(.horizontal, 16).padding(.vertical, 6)
    }

    private func tabButton(_ label: String, id: String, icon: String) -> some View {
        Button {
            selectedTab = id
        } label: {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 10))
                Text(label).font(.system(size: 11, weight: .medium))
            }
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(selectedTab == id ? Color.accentColor.opacity(0.15) : Color.clear)
            .foregroundColor(selectedTab == id ? .accentColor : .secondary)
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func tabContent(_ analysis: VaultAnalysis) -> some View {
        if selectedTab == "search" {
            searchTab
        } else {
            ScrollView {
                switch selectedTab {
                case "overview":    overviewTab(analysis)
                case "orphans":     orphansTab(analysis)
                case "broken":      brokenTab(analysis)
                case "suggestions": suggestionsTab(analysis)
                default: EmptyView()
                }
            }
            .padding(16)
        }
    }

    // MARK: - Overview

    private func overviewTab(_ a: VaultAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 12) {
                statCard("Notes", value: "\(a.stats.note_count)", icon: "doc.text")
                statCard("Links", value: "\(a.stats.link_count)", icon: "link")
                statCard("Words", value: fmtNum(a.stats.total_words), icon: "textformat")
                statCard("Tags", value: "\(a.stats.tag_count)", icon: "tag")
                statCard("Folders", value: "\(a.stats.folder_count)", icon: "folder")
                statCard("Orphans", value: "\(a.orphans.count)", icon: "link.badge.plus",
                         tint: a.orphans.isEmpty ? .green : .orange)
                if let daily = a.stats.daily_note_count {
                    statCard("Daily Notes", value: "\(daily)", icon: "calendar")
                }
                if let open = a.stats.open_tasks {
                    statCard("Open Tasks", value: "\(open)", icon: "circle",
                             tint: open == 0 ? .green : .orange)
                }
                if let done = a.stats.done_tasks {
                    statCard("Done Tasks", value: "\(done)", icon: "checkmark.circle", tint: .green)
                }
            }
            if !a.stats.top_tags.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Top Tags").font(.system(size: 13, weight: .semibold))
                    FlowLayout(spacing: 6) {
                        ForEach(a.stats.top_tags) { tc in
                            HStack(spacing: 3) {
                                Text("#\(tc.tag)").font(.system(size: 11))
                                Text("\(tc.count)").font(.system(size: 10, weight: .medium)).foregroundColor(.secondary)
                            }
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(Color.accentColor.opacity(0.08)).clipShape(Capsule())
                        }
                    }
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Health").font(.system(size: 13, weight: .semibold))
                healthRow("Broken links", count: a.broken_links.count, good: a.broken_links.isEmpty)
                healthRow("Dead-end notes", count: a.dead_ends.count, good: a.dead_ends.count < 5)
                healthRow("Single-use tags", count: a.tag_orphans.count, good: a.tag_orphans.count < 5)
                healthRow("Link suggestions", count: a.link_suggestions.count, good: true)
            }
        }
    }

    // MARK: - Orphans

    private func orphansTab(_ a: VaultAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Orphan Notes — no incoming links").font(.system(size: 12)).foregroundColor(.secondary)
            if a.orphans.isEmpty {
                emptySection("No orphan notes found.")
            } else {
                ForEach(a.orphans) { o in
                    noteRow(title: o.title, path: o.rel_path,
                            detail: "\(o.word_count) words\(o.has_outgoing_links ? "" : " · isolated")",
                            icon: o.has_outgoing_links ? "link.badge.plus" : "exclamationmark.circle",
                            tint: o.has_outgoing_links ? .orange : .red)
                }
            }
            if !a.dead_ends.isEmpty {
                Divider().padding(.vertical, 4)
                Text("Dead-End Notes — no outgoing links").font(.system(size: 12)).foregroundColor(.secondary)
                ForEach(a.dead_ends) { d in
                    noteRow(title: d.title, path: d.rel_path, detail: "\(d.word_count) words",
                            icon: "arrow.right.circle", tint: .yellow)
                }
            }
        }
    }

    // MARK: - Broken links

    private func brokenTab(_ a: VaultAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Broken Links — point to notes that don't exist").font(.system(size: 12)).foregroundColor(.secondary)
            if a.broken_links.isEmpty {
                emptySection("No broken links found.")
            } else {
                ForEach(a.broken_links) { bl in
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 11)).foregroundColor(.red)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(bl.from_note).font(.system(size: 12)).lineLimit(1)
                            HStack(spacing: 4) {
                                Image(systemName: "arrow.right").font(.system(size: 9)).foregroundColor(.secondary)
                                Text(bl.to_note).font(.system(size: 11)).foregroundColor(.red).lineLimit(1)
                            }
                        }
                        Spacer()
                    }
                    .padding(8).background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }

    // MARK: - Suggestions

    private func suggestionsTab(_ a: VaultAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Link Suggestions — notes sharing tags but not linked").font(.system(size: 12)).foregroundColor(.secondary)
            if a.link_suggestions.isEmpty {
                emptySection("No suggestions. All related notes are connected.")
            } else {
                ForEach(a.link_suggestions) { s in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Image(systemName: "lightbulb.fill").font(.system(size: 11)).foregroundColor(.yellow)
                            Text(s.note_a.title).font(.system(size: 12, weight: .medium)).lineLimit(1)
                            Image(systemName: "arrow.left.arrow.right").font(.system(size: 9)).foregroundColor(.secondary)
                            Text(s.note_b.title).font(.system(size: 12, weight: .medium)).lineLimit(1)
                        }
                        Text(s.reason).font(.system(size: 11)).foregroundColor(.secondary)
                    }
                    .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }

    // MARK: - Search

    private var searchTab: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary).font(.system(size: 13))
                TextField("Search notes by meaning…", text: $searchQuery)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .onSubmit { state.vaultSearch(searchQuery) }
                if state.isVaultSearching {
                    ProgressView().controlSize(.small)
                } else if !searchQuery.isEmpty {
                    Button {
                        searchQuery = ""
                        state.vaultSearchResults = []
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                Button("Search") { state.vaultSearch(searchQuery) }
                    .controlSize(.small)
                    .disabled(searchQuery.trimmingCharacters(in: .whitespaces).isEmpty || state.isVaultSearching)
            }
            .padding(10)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .padding(16)

            Divider()

            if let err = state.vaultSearchError {
                Text(err).font(.caption).foregroundColor(.red).padding(16)
            } else if state.vaultSearchResults.isEmpty && !searchQuery.isEmpty && !state.isVaultSearching {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "doc.text.magnifyingglass").font(.system(size: 28)).foregroundColor(.secondary)
                    Text("No results for \"\(searchQuery)\"").font(.caption).foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(state.vaultSearchResults) { r in
                            searchResultRow(r)
                        }
                    }
                    .padding(16)
                }
            }
        }
    }

    private func searchResultRow(_ r: VaultSearchResult) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                if r.is_daily_note == true {
                    Image(systemName: "calendar").font(.system(size: 10)).foregroundColor(.accentColor)
                }
                Text(r.title).font(.system(size: 12, weight: .medium)).lineLimit(1)
                Spacer()
                if let score = r.score {
                    Text("\(Int(score * 100))%")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(score > 0.5 ? .green : score > 0.25 ? .orange : .secondary)
                }
            }
            if let snippet = r.snippet, !snippet.isEmpty {
                Text(snippet)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            HStack(spacing: 6) {
                Text(r.rel_path).font(.system(size: 10)).foregroundColor(.secondary).lineLimit(1)
                if let tc = r.tasks_count, tc > 0 {
                    Label("\(tc)", systemImage: "checkmark.circle")
                        .font(.system(size: 10)).foregroundColor(.secondary)
                }
                ForEach(r.tags.prefix(3), id: \.self) { tag in
                    Text("#\(tag)").font(.system(size: 10)).foregroundColor(.accentColor)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Helpers

    private func statCard(_ label: String, value: String, icon: String, tint: Color = .accentColor) -> some View {
        VStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 16)).foregroundColor(tint)
            Text(value).font(.system(size: 18, weight: .bold, design: .rounded))
            Text(label).font(.system(size: 11)).foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity).padding(12)
        .background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func healthRow(_ label: String, count: Int, good: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: good ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 11)).foregroundColor(good ? .green : .orange)
            Text(label).font(.system(size: 12))
            Spacer()
            Text("\(count)").font(.system(size: 12, weight: .medium, design: .monospaced)).foregroundColor(.secondary)
                .padding(.trailing, 8)
        }
    }

    @ViewBuilder
    private func scanSummary(_ scan: VaultScanResult) -> some View {
        let hasContent = [scan.added, scan.updated, scan.removed, scan.errors].contains(where: { ($0 ?? 0) > 0 })
        if hasContent {
            HStack(spacing: 16) {
                if let v = scan.added, v > 0 { Label("\(v) added", systemImage: "plus.circle").font(.system(size: 11)).foregroundColor(.green) }
                if let v = scan.updated, v > 0 { Label("\(v) updated", systemImage: "arrow.triangle.2.circlepath").font(.system(size: 11)).foregroundColor(.blue) }
                if let v = scan.removed, v > 0 { Label("\(v) removed", systemImage: "minus.circle").font(.system(size: 11)).foregroundColor(.red) }
                if let v = scan.errors, v > 0 { Label("\(v) errors", systemImage: "exclamationmark.triangle").font(.system(size: 11)).foregroundColor(.orange) }
            }
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func noteRow(title: String, path: String, detail: String, icon: String, tint: Color) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon).font(.system(size: 11)).foregroundColor(tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 12, weight: .medium)).lineLimit(1)
                Text(path).font(.system(size: 10)).foregroundColor(.secondary).lineLimit(1)
            }
            Spacer()
            Text(detail).font(.system(size: 10)).foregroundColor(.secondary)
        }
        .padding(8).background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func emptySection(_ message: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
            Text(message).font(.system(size: 12)).foregroundColor(.secondary)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func fmtNum(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.1fK", Double(n) / 1_000) }
        return "\(n)"
    }

    private func browseForVault() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.message = "Select your Obsidian vault folder"
        if panel.runModal() == .OK, let url = panel.url {
            state.selectVaultPath(url.path)
        }
    }
}

// MARK: - Flow layout for tags

struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0; var y: CGFloat = 0; var rowHeight: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 { y += rowHeight + spacing; x = 0; rowHeight = 0 }
            x += size.width + spacing; rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX; var y = bounds.minY; var rowHeight: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX { y += rowHeight + spacing; x = bounds.minX; rowHeight = 0 }
            sub.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing; rowHeight = max(rowHeight, size.height)
        }
    }
}
