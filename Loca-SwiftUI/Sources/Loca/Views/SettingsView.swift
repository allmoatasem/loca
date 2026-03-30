import SwiftUI

// MARK: - Main SettingsView

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    enum Tab: String, CaseIterable {
        case downloaded = "Downloaded"
        case discover   = "Discover"
        case settings   = "Settings"
        var icon: String {
            switch self {
            case .downloaded: return "internaldrive"
            case .discover:   return "cpu"
            case .settings:   return "gearshape"
            }
        }
    }
    @State private var tab: Tab = .downloaded

    var body: some View {
        VStack(spacing: 0) {
            // ── Header ───────────────────────────────────────────────────
            HStack(spacing: 12) {
                Text("Manage Models")
                    .font(.headline)
                Spacer()
                Picker("", selection: $tab) {
                    ForEach(Tab.allCases, id: \.self) { t in
                        Label(t.rawValue, systemImage: t.icon).tag(t)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 300)
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.return)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)

            Divider()

            // ── Content ──────────────────────────────────────────────────
            switch tab {
            case .downloaded: DownloadedModelsTab()
            case .discover:   DiscoverTab()
            case .settings:   ModelSettingsTab()
            }
        }
        .frame(width: 900, height: 700)
        .onAppear {
            state.reloadLocalModels()
            state.reloadRecommendations()
        }
    }
}

// MARK: - Downloaded Models Tab

private struct DownloadedModelsTab: View {
    @EnvironmentObject var state: AppState
    @State private var modelToDelete: LocalModel?
    @State private var showDeleteConfirm = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if state.localModels.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(state.localModels) { m in modelRow(m) }
                    }
                    .padding(20)
                }
            }
            if let err = state.modelLoadError {
                Divider()
                Text(err)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 8)
            }
        }
        .alert("Delete \"\(modelToDelete?.name ?? "")\"?",
               isPresented: $showDeleteConfirm, presenting: modelToDelete) { m in
            Button("Delete", role: .destructive) { state.deleteModel(m.name) }
            Button("Cancel", role: .cancel) {}
        } message: { m in
            Text("Permanently deletes the file (\(m.sizeLabel)). Cannot be undone.")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "internaldrive").font(.system(size: 36)).foregroundColor(.secondary)
            Text("No models downloaded yet")
                .font(.system(size: 14)).foregroundColor(.secondary)
            Text("Go to the Discover tab to find hardware-optimised models.")
                .font(.system(size: 12)).foregroundColor(Color.secondary.opacity(0.7))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func modelRow(_ m: LocalModel) -> some View {
        HStack(spacing: 10) {
            Circle()
                .fill(m.is_loaded ? Color.green : Color.secondary.opacity(0.3))
                .frame(width: 8, height: 8)
                .help(m.is_loaded ? "Loaded in inference backend" : "Not loaded")

            VStack(alignment: .leading, spacing: 2) {
                Text(m.name).font(.system(size: 13)).lineLimit(1)
                HStack(spacing: 6) {
                    modelBadge(m.formatLabel,
                          bg: m.format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1),
                          fg: m.format == "mlx" ? .purple : .blue)
                    Text(m.sizeLabel).font(.system(size: 11)).foregroundColor(.secondary)
                }
            }

            Spacer()

            if state.isLoadingModel && state.selectedModelId == m.name {
                ProgressView().scaleEffect(0.7)
            } else if !m.is_loaded {
                Button("Load") { state.selectedModelId = m.name }
                    .controlSize(.small)
                    .buttonStyle(.bordered)
                    .help("Load this model into the inference backend")
            } else {
                Button {
                    state.unloadModel()
                } label: {
                    Label("Eject", systemImage: "eject")
                        .font(.system(size: 11))
                }
                .controlSize(.small)
                .buttonStyle(.bordered)
                .help("Stop inference server and free RAM/GPU memory")
            }

            Button(role: .destructive) {
                modelToDelete = m
                showDeleteConfirm = true
            } label: {
                Image(systemName: "trash")
                    .font(.system(size: 12))
                    .foregroundColor(m.is_loaded ? Color.secondary.opacity(0.4) : .red)
            }
            .buttonStyle(.plain)
            .disabled(m.is_loaded)
            .help(m.is_loaded ? "Unload this model before deleting" : "Delete from disk")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Discover Tab

private struct DiscoverTab: View {
    @EnvironmentObject var state: AppState
    @State private var selectedCategory = "all"
    @State private var searchText = ""

    private let categories = ["all", "general", "code", "reasoning", "vision", "writing"]

    private var filtered: [ModelRecommendation] {
        var result = state.recommendedModels
        if selectedCategory != "all" {
            result = result.filter { $0.category == selectedCategory }
        }
        let q = searchText.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            result = result.filter {
                $0.name.lowercased().contains(q)
                || $0.provider.lowercased().contains(q)
                || $0.use_case.lowercased().contains(q)
                || $0.repo_id.lowercased().contains(q)
            }
        }
        return result
    }

    var body: some View {
        VStack(spacing: 0) {
            // ── Toolbar ──────────────────────────────────────────────────
            HStack(spacing: 10) {
                hardwareBadge
                Spacer()
                categoryFilter
            }
            .padding(.horizontal, 20)
            .padding(.top, 10)
            .padding(.bottom, 4)

            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.secondary).font(.system(size: 11))
                    TextField("Search models, providers…", text: $searchText)
                        .textFieldStyle(.plain).font(.system(size: 12))
                    if !searchText.isEmpty {
                        Button { searchText = "" } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundColor(.secondary).font(.system(size: 11))
                        }.buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
                refreshButton
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 6)

            Divider()

            // ── Legend ───────────────────────────────────────────────────
            HStack(spacing: 14) {
                legendDot(.green,     "Perfect fit")
                legendDot(.yellow,    "Good fit")
                legendDot(.orange,    "Tight fit")
                legendDot(.secondary, "Unknown")
                Spacer()
                if !state.recommendedModels.isEmpty {
                    Text("\(filtered.count) of \(state.recommendedModels.count)")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 6)

            Divider()

            // ── List ─────────────────────────────────────────────────────
            if state.isLoadingRecommendations {
                Spacer()
                ProgressView("Detecting hardware…").frame(maxWidth: .infinity)
                Spacer()
            } else if filtered.isEmpty {
                Spacer()
                VStack(spacing: 6) {
                    Image(systemName: "cpu").font(.system(size: 32)).foregroundColor(.secondary)
                    Text(state.recommendedModels.isEmpty
                         ? "No recommendations yet — tap ↺ to scan your hardware."
                         : "No results for the current filters.")
                        .font(.system(size: 13)).foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(filtered) { rec in
                            RecommendationRow(rec: rec)
                        }
                    }
                    .padding(16)
                }
            }

            // ── Download status bar ───────────────────────────────────────
            if let dl = state.activeDownload {
                Divider()
                DownloadStatusBar(dl: dl)
            }
        }
    }

    // MARK: Toolbar pieces

    private var hardwareBadge: some View {
        Group {
            if let hw = state.hardwareProfile {
                HStack(spacing: 6) {
                    Image(systemName: hw.has_apple_silicon ? "apple.logo" : (hw.has_nvidia_gpu ? "display" : "cpu"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Text("\(hw.cpu_name.split(separator: " ").prefix(4).joined(separator: " "))  ·  \(String(format: "%.0f GB", hw.total_ram_gb))")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    if hw.llmfit_available {
                        Text("llmfit ✓").font(.system(size: 10)).foregroundColor(.green)
                    } else {
                        Button {
                            state.installLlmfit()
                        } label: {
                            Text(state.isInstallingLlmfit ? "Installing…" : "Install llmfit")
                                .font(.system(size: 10))
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.mini)
                        .disabled(state.isInstallingLlmfit)
                        .help("Install llmfit for hardware-optimised recommendations")
                    }
                }
            }
        }
    }

    private var categoryFilter: some View {
        HStack(spacing: 4) {
            ForEach(categories, id: \.self) { cat in
                Button {
                    selectedCategory = cat
                } label: {
                    Text(cat.capitalized)
                        .font(.system(size: 11, weight: .medium))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(selectedCategory == cat
                            ? Color.accentColor.opacity(0.15)
                            : Color.secondary.opacity(0.08))
                        .foregroundColor(selectedCategory == cat ? .accentColor : .secondary)
                        .clipShape(RoundedRectangle(cornerRadius: 5))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var refreshButton: some View {
        Button { state.reloadRecommendations() } label: {
            Image(systemName: "arrow.clockwise").font(.system(size: 12))
        }
        .buttonStyle(.plain)
        .help("Re-scan hardware and refresh recommendations")
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).font(.system(size: 10)).foregroundColor(.secondary)
        }
    }
}

// MARK: - Recommendation Row

private struct RecommendationRow: View {
    @EnvironmentObject var state: AppState
    let rec: ModelRecommendation

    private var alreadyDownloaded: Bool {
        state.localModels.contains(where: { $0.name == (rec.filename ?? rec.repo_id.split(separator: "/").last.map(String.init) ?? rec.name) })
    }
    private var isThisDownloading: Bool {
        state.activeDownload?.repoId == rec.repo_id
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
    }
    private var isAnyDownloading: Bool {
        state.activeDownload != nil
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                // Fitness indicator
                Circle()
                    .fill(rec.fitColor)
                    .frame(width: 9, height: 9)
                    .help(rec.fitLabel.isEmpty ? "Fit unknown" : rec.fitLabel)

                // Model info
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(rec.name)
                            .font(.system(size: 12, weight: .medium))
                            .lineLimit(1)
                        modelBadge(rec.formatLabel,
                              bg: rec.format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1),
                              fg: rec.format == "mlx" ? .purple : .blue)
                        if !rec.provider.isEmpty {
                            Text(rec.provider)
                                .font(.system(size: 9))
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 4).padding(.vertical, 2)
                                .background(Color.secondary.opacity(0.08))
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                        Text(rec.sizeLabel)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                        Text(rec.quant)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                        Text("\(rec.context / 1024)K ctx")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                    Text(rec.why)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }

                Spacer()

                // Action
                if alreadyDownloaded {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.system(size: 16))
                        .help("Already downloaded")
                } else if isThisDownloading {
                    ProgressView().scaleEffect(0.65)
                } else {
                    Button {
                        state.startModelDownload(
                            repoId: rec.repo_id,
                            filename: rec.filename,
                            format: rec.format
                        )
                    } label: {
                        Label("Get", systemImage: "arrow.down.circle")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
                    .disabled(isAnyDownloading)
                    .help("Download \(rec.name)")
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 7))
    }
}

// MARK: - Download Status Bar

private struct DownloadStatusBar: View {
    let dl: AppState.ActiveDownload

    var body: some View {
        VStack(spacing: 5) {
            HStack(spacing: 8) {
                if dl.done {
                    Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
                    Text("Download complete: \(dl.repoId.split(separator: "/").last.map(String.init) ?? dl.repoId)")
                        .font(.system(size: 12))
                } else if let err = dl.error {
                    Image(systemName: "xmark.circle.fill").foregroundColor(.red)
                    Text(err).font(.system(size: 11)).foregroundColor(.red).lineLimit(1)
                } else {
                    ProgressView().scaleEffect(0.6)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Downloading \(dl.repoId.split(separator: "/").last.map(String.init) ?? dl.repoId)…")
                            .font(.system(size: 12))
                        HStack(spacing: 8) {
                            if dl.percent >= 0 {
                                Text(String(format: "%.1f%%", dl.percent))
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundColor(.secondary)
                            }
                            if dl.speedMbps > 0 {
                                Text(String(format: "%.1f MB/s", dl.speedMbps))
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundColor(.secondary)
                            }
                            if dl.etaSeconds > 0 {
                                Text("ETA \(etaLabel(dl.etaSeconds))")
                                    .font(.system(size: 11))
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }
                Spacer()
            }
            if !dl.done && dl.error == nil && dl.percent >= 0 {
                ProgressView(value: dl.percent, total: 100)
                    .progressViewStyle(.linear)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private func etaLabel(_ s: Double) -> String {
        let s = Int(s)
        if s < 60 { return "\(s)s" }
        return "\(s / 60)m \(s % 60)s"
    }
}

// MARK: - Model Settings Tab

private struct ModelSettingsTab: View {
    @EnvironmentObject var state: AppState

    @State private var downloadRepoId   = ""
    @State private var downloadFilename = ""
    @State private var downloadFormat   = "gguf"
    @State private var hfSuggestions: [HFSuggestion] = []
    @State private var hfSearchTask: Task<Void, Never>?
    @State private var showSuggestions  = false

    private struct HFSuggestion: Identifiable {
        let id = UUID()
        let repo_id: String
        let downloads: Int
    }
    private let formats = ["gguf", "mlx"]
    private let ctxOptions = [4096, 8192, 16384, 32768, 65536, 131072]

    private var isDownloading: Bool {
        state.activeDownload != nil
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
    }
    private var downloadError: String? { state.activeDownload?.error }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                downloadSection
                Divider()
                contextSection
            }
            .padding(20)
        }
    }

    // MARK: Download

    private var downloadSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Download from Hugging Face", systemImage: "arrow.down.circle")
                .font(.system(size: 13, weight: .semibold))

            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Format").font(.system(size: 11)).foregroundColor(.secondary)
                    Picker("", selection: $downloadFormat) {
                        ForEach(formats, id: \.self) { Text($0.uppercased()).tag($0) }
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                    .frame(width: 120)
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Repository ID").font(.system(size: 11)).foregroundColor(.secondary)
                    TextField(downloadFormat == "gguf"
                              ? "bartowski/Qwen2.5-7B-Instruct-GGUF"
                              : "mlx-community/Qwen2.5-7B-Instruct-4bit",
                              text: $downloadRepoId)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                        .onChange(of: downloadRepoId) { fetchHFSuggestions(downloadRepoId) }
                    if showSuggestions {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(hfSuggestions) { s in
                                Button {
                                    downloadRepoId = s.repo_id
                                    showSuggestions = false
                                    hfSuggestions = []
                                } label: {
                                    HStack {
                                        Text(s.repo_id).font(.system(size: 11)).lineLimit(1)
                                        Spacer()
                                        Text("\(s.downloads / 1000)K↓").font(.system(size: 10)).foregroundColor(.secondary)
                                    }
                                    .padding(.horizontal, 8).padding(.vertical, 5).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .background(Color(nsColor: .controlBackgroundColor))
                                Divider()
                            }
                        }
                        .background(Color(nsColor: .windowBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
                        .shadow(radius: 4)
                    }
                }
            }

            if downloadFormat == "gguf" {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Filename (required for GGUF)").font(.system(size: 11)).foregroundColor(.secondary)
                    TextField("Qwen2.5-7B-Instruct-Q4_K_M.gguf", text: $downloadFilename)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                }
            } else {
                Text("Leave filename blank — the entire model directory will be downloaded.")
                    .font(.system(size: 11)).foregroundColor(.secondary)
            }

            if let err = downloadError { Text(err).font(.system(size: 11)).foregroundColor(.red) }

            Button(action: startDownload) {
                Label(isDownloading ? "Downloading…" : "Download", systemImage: "arrow.down.circle")
            }
            .disabled(isDownloading
                || downloadRepoId.trimmingCharacters(in: .whitespaces).isEmpty
                || (downloadFormat == "gguf" && downloadFilename.trimmingCharacters(in: .whitespaces).isEmpty))
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
    }

    // MARK: Context window

    private var contextSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Context Window", systemImage: "doc.text.magnifyingglass")
                .font(.system(size: 13, weight: .semibold))

            HStack(spacing: 12) {
                Picker("", selection: $state.contextWindow) {
                    ForEach(ctxOptions, id: \.self) { n in
                        Text(ctxLabel(n)).tag(n)
                    }
                }
                .labelsHidden()
                .frame(width: 100)

                Text("Larger values allow longer conversations but use more RAM. Takes effect on next model load.")
                    .font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            }

            if let active = state.activeModelName {
                Button("Reload \(active) with \(ctxLabel(state.contextWindow)) context") {
                    state.loadModel(active, ctxSize: state.contextWindow)
                }
                .controlSize(.small)
                .buttonStyle(.bordered)
            }
        }
    }

    // MARK: Helpers

    private func fetchHFSuggestions(_ query: String) {
        hfSearchTask?.cancel()
        guard query.count >= 2 else { hfSuggestions = []; showSuggestions = false; return }
        hfSearchTask = Task {
            try? await Task.sleep(nanoseconds: 400_000_000)
            guard !Task.isCancelled else { return }
            guard let url = URL(string: "http://localhost:8000/api/hf-search?q=\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query)&format=\(downloadFormat)&limit=8") else { return }
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                struct Resp: Decodable { struct M: Decodable { let repo_id: String; let downloads: Int }; let models: [M] }
                let resp = try JSONDecoder().decode(Resp.self, from: data)
                await MainActor.run {
                    hfSuggestions = resp.models.map { HFSuggestion(repo_id: $0.repo_id, downloads: $0.downloads) }
                    showSuggestions = !hfSuggestions.isEmpty
                }
            } catch {}
        }
    }

    private func startDownload() {
        let repoId   = downloadRepoId.trimmingCharacters(in: .whitespaces)
        let filename = downloadFilename.trimmingCharacters(in: .whitespaces)
        guard !repoId.isEmpty else { return }
        state.startModelDownload(
            repoId: repoId,
            filename: filename.isEmpty ? nil : filename,
            format: downloadFormat
        )
    }

    private func ctxLabel(_ n: Int) -> String { n >= 1024 ? "\(n / 1024)K" : "\(n)" }
}

// MARK: - Shared badge helper

private func modelBadge(_ text: String, bg: Color, fg: Color) -> some View {
    Text(text)
        .font(.system(size: 9))
        .padding(.horizontal, 5).padding(.vertical, 2)
        .background(bg)
        .foregroundColor(fg)
        .clipShape(Capsule())
}
