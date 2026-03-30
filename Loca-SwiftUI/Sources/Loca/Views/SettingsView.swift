import SwiftUI

// MARK: - Main SettingsView

struct SettingsView: View {
    @EnvironmentObject var state: AppState

    enum Tab: String, CaseIterable {
        case downloaded = "Downloaded"
        case discover   = "Discover"
        var icon: String {
            switch self {
            case .downloaded: return "internaldrive"
            case .discover:   return "cpu"
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
                .frame(width: 220)
                Spacer()
                Button("Done") { state.isSettingsOpen = false }
                    .keyboardShortcut(.return)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)

            Divider()

            // ── Content ──────────────────────────────────────────────────
            switch tab {
            case .downloaded: DownloadedModelsTab()
            case .discover:   DiscoverTab()
            }

            // ── Download status bar (persistent across all tabs) ──────────
            if let dl = state.activeDownload {
                Divider()
                DownloadStatusBar(dl: dl)
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
                    if let p = m.param_label {
                        Text(p).font(.system(size: 11, weight: .medium)).foregroundColor(.secondary)
                    }
                    Text(m.sizeLabel).font(.system(size: 11)).foregroundColor(.secondary)
                    if let ctx = m.contextLabel {
                        Text(ctx).font(.system(size: 10)).foregroundColor(.secondary)
                    }
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
    @State private var discoverMode: DiscoverMode = .forYou
    @State private var hfFormat = "gguf"
    @State private var hfQuery = ""
    @State private var hfHits: [HFHit] = []
    @State private var hfSearchTask: Task<Void, Never>?
    @State private var isSearchingHF = false

    enum DiscoverMode { case forYou, search }

    struct HFHit: Identifiable {
        let id = UUID()
        let repo_id: String
        let downloads: Int
    }

    private let categories = ["all", "general", "code", "reasoning", "vision"]

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
                Picker("", selection: $discoverMode) {
                    Text("For You").tag(DiscoverMode.forYou)
                    Text("Search HF").tag(DiscoverMode.search)
                }
                .pickerStyle(.segmented)
                .frame(width: 180)
                .onChange(of: discoverMode) { if $0 == .forYou { hfHits = []; hfQuery = "" } }

                Spacer()

                if discoverMode == .forYou {
                    hardwareBadge
                    categoryFilter
                } else {
                    Picker("", selection: $hfFormat) {
                        Text("GGUF").tag("gguf")
                        Text("MLX").tag("mlx")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 100)
                    .onChange(of: hfFormat) { _ in scheduleHFSearch() }
                }

                if discoverMode == .forYou { refreshButton }
            }
            .padding(.horizontal, 20)
            .padding(.top, 10)
            .padding(.bottom, 4)

            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.secondary).font(.system(size: 11))
                    if discoverMode == .forYou {
                        TextField("Search models, providers…", text: $searchText)
                            .textFieldStyle(.plain).font(.system(size: 12))
                        if !searchText.isEmpty {
                            Button { searchText = "" } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundColor(.secondary).font(.system(size: 11))
                            }.buttonStyle(.plain)
                        }
                    } else {
                        TextField("Search Hugging Face (e.g. Qwen2.5-7B-Instruct)…", text: $hfQuery)
                            .textFieldStyle(.plain).font(.system(size: 12))
                            .onChange(of: hfQuery) { _ in scheduleHFSearch() }
                        if !hfQuery.isEmpty {
                            Button { hfQuery = ""; hfHits = [] } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundColor(.secondary).font(.system(size: 11))
                            }.buttonStyle(.plain)
                        }
                        if isSearchingHF { ProgressView().scaleEffect(0.5) }
                    }
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 6)

            Divider()

            if discoverMode == .forYou {
                HStack(spacing: 14) {
                    legendDot(.green, "Perfect fit")
                    legendDot(.yellow, "Good fit")
                    legendDot(.orange, "Tight fit")
                    legendDot(.secondary, "Unknown")
                    Spacer()
                    if !state.recommendedModels.isEmpty {
                        Text("\(filtered.count) of \(state.recommendedModels.count)")
                            .font(.system(size: 11)).foregroundColor(.secondary)
                    }
                }
                .padding(.horizontal, 20).padding(.vertical, 6)
                Divider()
            }

            // ── Content ──────────────────────────────────────────────────
            if discoverMode == .forYou {
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
            } else {
                if hfHits.isEmpty && !isSearchingHF {
                    Spacer()
                    VStack(spacing: 6) {
                        Image(systemName: "magnifyingglass").font(.system(size: 32)).foregroundColor(.secondary)
                        Text(hfQuery.isEmpty ? "Search Hugging Face for models to download."
                                             : "No results for \"\(hfQuery)\".")
                            .font(.system(size: 13)).foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    Spacer()
                } else {
                    ScrollView {
                        LazyVStack(spacing: 6) {
                            ForEach(hfHits) { hit in
                                HFSearchRow(hit: hit, format: hfFormat)
                            }
                        }
                        .padding(16)
                    }
                }
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

    private func scheduleHFSearch() {
        hfSearchTask?.cancel()
        guard hfQuery.count >= 2 else { hfHits = []; return }
        hfSearchTask = Task {
            try? await Task.sleep(nanoseconds: 400_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run { isSearchingHF = true }
            guard let url = URL(string: "http://localhost:8000/api/hf-search?q=\(hfQuery.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? hfQuery)&format=\(hfFormat)&limit=20") else { return }
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                struct Resp: Decodable { struct M: Decodable { let repo_id: String; let downloads: Int }; let models: [M] }
                let resp = try JSONDecoder().decode(Resp.self, from: data)
                await MainActor.run {
                    hfHits = resp.models.map { HFHit(repo_id: $0.repo_id, downloads: $0.downloads) }
                    isSearchingHF = false
                }
            } catch {
                await MainActor.run { isSearchingHF = false }
            }
        }
    }
}

// MARK: - HF Search Row

private struct HFSearchRow: View {
    @EnvironmentObject var state: AppState
    let hit: DiscoverTab.HFHit
    let format: String
    @State private var showGGUFPicker = false

    private var isThisDownloading: Bool {
        state.activeDownload?.repoId == hit.repo_id
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
    }
    private var isAnyDownloading: Bool {
        state.activeDownload != nil
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
    }
    private var alreadyDownloaded: Bool {
        let modelName = hit.repo_id.split(separator: "/").last.map(String.init) ?? ""
        return state.localModels.contains { $0.name == modelName }
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(hit.repo_id.split(separator: "/").last.map(String.init) ?? hit.repo_id)
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1)
                    modelBadge(format.uppercased(),
                          bg: format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1),
                          fg: format == "mlx" ? .purple : .blue)
                }
                Text(hit.repo_id)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                Text("\(hit.downloads / 1000)K downloads")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }

            Spacer()

            if alreadyDownloaded {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green).font(.system(size: 16))
            } else if isThisDownloading {
                ProgressView().scaleEffect(0.65)
            } else {
                Button {
                    if format == "gguf" {
                        showGGUFPicker = true
                    } else {
                        state.startModelDownload(repoId: hit.repo_id, filename: nil, format: format)
                    }
                } label: {
                    Label("Get", systemImage: "arrow.down.circle").font(.system(size: 11))
                }
                .buttonStyle(.bordered).controlSize(.mini)
                .disabled(isAnyDownloading)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .sheet(isPresented: $showGGUFPicker) {
            GGUFFilePicker(repoId: hit.repo_id)
                .environmentObject(state)
        }
    }
}

// MARK: - Recommendation Row

private struct RecommendationRow: View {
    @EnvironmentObject var state: AppState
    let rec: ModelRecommendation
    @State private var showGGUFPicker = false

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

    private static let providerColor:    Color = .indigo
    private static let tpsColor:         Color = .teal
    private static let capabilityColor:  Color = .orange

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(rec.fitColor)
                .frame(width: 9, height: 9)
                .help(rec.fitLabel.isEmpty ? "Fit unknown" : rec.fitLabel)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(rec.name)
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1)
                    if let p = rec.paramLabel {
                        Text(p)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.secondary)
                    }
                    modelBadge(rec.formatLabel,
                          bg: rec.format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1),
                          fg: rec.format == "mlx" ? .purple : .blue)
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

                HStack(spacing: 5) {
                    if rec.score > 0 {
                        metaPill("\(Int(rec.score))% fit",
                                 bg: rec.fitColor.opacity(0.12),
                                 fg: rec.fitColor)
                    }
                    if !rec.provider.isEmpty {
                        metaPill(rec.provider,
                                 bg: Self.providerColor.opacity(0.1),
                                 fg: Self.providerColor)
                    }
                    if rec.tps > 0 {
                        metaPill("~\(Int(rec.tps)) tok/s",
                                 bg: Self.tpsColor.opacity(0.1),
                                 fg: Self.tpsColor)
                    }
                    if !rec.use_case.isEmpty {
                        metaPill(rec.use_case.capitalized,
                                 bg: Self.capabilityColor.opacity(0.1),
                                 fg: Self.capabilityColor)
                    }
                    if !rec.why.isEmpty {
                        Text(rec.why)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }
            }

            Spacer()

            if alreadyDownloaded {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                    .font(.system(size: 16))
                    .help("Already downloaded")
            } else if isThisDownloading {
                ProgressView().scaleEffect(0.65)
            } else {
                Button {
                    if rec.format == "gguf" && rec.filename == nil {
                        showGGUFPicker = true
                    } else {
                        state.startModelDownload(
                            repoId: rec.repo_id,
                            filename: rec.filename,
                            format: rec.format
                        )
                    }
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
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .sheet(isPresented: $showGGUFPicker) {
            GGUFFilePicker(repoId: rec.repo_id)
                .environmentObject(state)
        }
    }

    private func metaPill(_ text: String, bg: Color, fg: Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(fg)
            .padding(.horizontal, 5).padding(.vertical, 2)
            .background(bg)
            .clipShape(Capsule())
    }
}

// MARK: - GGUF File Picker

private struct GGUFFilePicker: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) var dismiss
    let repoId: String

    @State private var files: [RepoFile] = []
    @State private var isLoading = true
    @State private var loadError: String?

    private var modelName: String {
        repoId.split(separator: "/").last.map(String.init) ?? repoId
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Select file to download").font(.headline)
                    Text(repoId).font(.system(size: 11)).foregroundColor(.secondary)
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.escape)
            }
            .padding(20)
            Divider()

            if isLoading {
                Spacer()
                ProgressView("Loading file list…").frame(maxWidth: .infinity)
                Spacer()
            } else if let err = loadError {
                Spacer()
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 28)).foregroundColor(.orange)
                    Text(err).font(.system(size: 12)).foregroundColor(.secondary).multilineTextAlignment(.center)
                }
                .padding(20)
                Spacer()
            } else if files.isEmpty {
                Spacer()
                Text("No GGUF files found in this repository.")
                    .font(.system(size: 13)).foregroundColor(.secondary)
                Spacer()
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(files) { file in
                            HStack(spacing: 10) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(file.name)
                                        .font(.system(size: 12, weight: .medium))
                                        .lineLimit(1)
                                    HStack(spacing: 6) {
                                        if let q = file.quantLabel {
                                            Text(q)
                                                .font(.system(size: 9, weight: .semibold))
                                                .foregroundColor(.blue)
                                                .padding(.horizontal, 5).padding(.vertical, 2)
                                                .background(Color.blue.opacity(0.1))
                                                .clipShape(Capsule())
                                        }
                                        Text(file.sizeLabel)
                                            .font(.system(size: 11))
                                            .foregroundColor(.secondary)
                                    }
                                }
                                Spacer()
                                Button {
                                    state.startModelDownload(repoId: repoId, filename: file.name, format: "gguf")
                                    dismiss()
                                } label: {
                                    Label("Get", systemImage: "arrow.down.circle").font(.system(size: 11))
                                }
                                .buttonStyle(.borderedProminent).controlSize(.mini)
                                .disabled(state.activeDownload != nil && state.activeDownload?.done == false && state.activeDownload?.error == nil)
                            }
                            .padding(.horizontal, 14).padding(.vertical, 10)
                            .background(Color(nsColor: .controlBackgroundColor))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                    .padding(16)
                }
            }
        }
        .frame(width: 600, height: 420)
        .task {
            do {
                files = try await BackendClient.shared.fetchRepoFiles(repoId: repoId, format: "gguf")
                isLoading = false
            } catch {
                loadError = error.localizedDescription
                isLoading = false
            }
        }
    }
}

// MARK: - Download Status Bar

private struct DownloadStatusBar: View {
    @EnvironmentObject var state: AppState
    let dl: AppState.ActiveDownload

    private var modelName: String {
        dl.repoId.split(separator: "/").last.map(String.init) ?? dl.repoId
    }

    var body: some View {
        VStack(spacing: 5) {
            HStack(spacing: 8) {
                if dl.done {
                    Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
                    Text("Downloaded \(modelName)").font(.system(size: 12))
                } else if let err = dl.error {
                    Image(systemName: "xmark.circle.fill").foregroundColor(.red)
                    Text(err).font(.system(size: 11)).foregroundColor(.red).lineLimit(1)
                } else if dl.paused {
                    Image(systemName: "pause.circle.fill").foregroundColor(.orange)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Paused — \(modelName)").font(.system(size: 12))
                        if dl.percent >= 0 {
                            Text(String(format: "%.1f%% complete", dl.percent))
                                .font(.system(size: 11)).foregroundColor(.secondary)
                        }
                    }
                } else {
                    ProgressView().scaleEffect(0.6)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Downloading \(modelName)…").font(.system(size: 12))
                        HStack(spacing: 8) {
                            if dl.percent >= 0 {
                                Text(String(format: "%.1f%%", dl.percent))
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundColor(.secondary)
                            }
                            if dl.totalBytes > 0 && dl.percent >= 0 {
                                let downloaded = Double(dl.totalBytes) * dl.percent / 100
                                let total = Double(dl.totalBytes)
                                Text(String(format: "%.1f / %.1f GB", downloaded / 1e9, total / 1e9))
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

                if !dl.done, dl.error == nil {
                    if dl.paused {
                        Button { state.resumeDownload() } label: {
                            Label("Resume", systemImage: "play.fill")
                                .font(.system(size: 11))
                        }
                        .buttonStyle(.bordered).controlSize(.mini)
                        .tint(.green)
                    } else {
                        Button { state.pauseDownload() } label: {
                            Label("Pause", systemImage: "pause.fill")
                                .font(.system(size: 11))
                        }
                        .buttonStyle(.bordered).controlSize(.mini)
                    }
                    Button { state.cancelDownload() } label: {
                        Label("Cancel", systemImage: "xmark")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered).controlSize(.mini)
                    .tint(.red)
                }
            }

            if !dl.done, dl.error == nil, dl.percent >= 0 {
                ProgressView(value: dl.percent, total: 100)
                    .progressViewStyle(.linear)
                    .tint(dl.paused ? .orange : .accentColor)
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

// MARK: - Shared badge helper

private func modelBadge(_ text: String, bg: Color, fg: Color) -> some View {
    Text(text)
        .font(.system(size: 9))
        .padding(.horizontal, 5).padding(.vertical, 2)
        .background(bg)
        .foregroundColor(fg)
        .clipShape(Capsule())
}
