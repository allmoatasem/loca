import SwiftUI

/// Settings sheet — model management, download, hardware-aware recommendations, context window.
struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var downloadRepoId     = ""
    @State private var downloadFilename   = ""
    @State private var downloadFormat     = "gguf"
    @State private var modelToDelete: LocalModel?
    @State private var showDeleteConfirm  = false
    @State private var hfSuggestions: [HFSuggestion] = []
    @State private var hfSearchTask: Task<Void, Never>?
    @State private var showSuggestions    = false

    struct HFSuggestion: Identifiable {
        let id = UUID()
        let repo_id: String
        let downloads: Int
    }

    private var isDownloading: Bool { state.activeDownload != nil && state.activeDownload?.done == false && state.activeDownload?.error == nil }
    private var downloadProgress: Double { state.activeDownload?.percent ?? 0 }
    private var downloadDone: Bool { state.activeDownload?.done == true }
    private var downloadError: String? { state.activeDownload?.error }

    private let formats = ["gguf", "mlx"]
    private let ctxOptions = [4096, 8192, 16384, 32768, 65536, 131072]

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Settings")
                    .font(.headline)
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.return)
            }
            .padding(16)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    modelsSection
                    Divider()
                    recommendationsSection
                    Divider()
                    downloadSection
                    Divider()
                    contextSection
                }
                .padding(16)
            }
        }
        .frame(width: 520, height: 640)
        .onAppear {
            state.reloadLocalModels()
            state.reloadRecommendations()
        }
        .alert("Delete \"\(modelToDelete?.name ?? "")\"?",
               isPresented: $showDeleteConfirm,
               presenting: modelToDelete) { m in
            Button("Delete", role: .destructive) { state.deleteModel(m.name) }
            Button("Cancel", role: .cancel) {}
        } message: { m in
            Text("This will permanently delete the model file (\(m.sizeLabel)). This cannot be undone.")
        }
    }

    // MARK: - Models section

    private var modelsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Downloaded Models", systemImage: "internaldrive")
                .font(.system(size: 13, weight: .semibold))

            if state.localModels.isEmpty {
                Text("No models downloaded yet. Use the Download section below.")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            } else {
                VStack(spacing: 6) {
                    ForEach(state.localModels) { model in
                        modelRow(model)
                    }
                }
            }

            if let err = state.modelLoadError {
                Text(err)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
            }
        }
    }

    private func modelRow(_ model: LocalModel) -> some View {
        HStack(spacing: 8) {
            // Active indicator
            Circle()
                .fill(model.is_loaded ? Color.green : Color.secondary.opacity(0.3))
                .frame(width: 7, height: 7)
                .help(model.is_loaded ? "Currently loaded in inference backend" : "Not loaded")

            VStack(alignment: .leading, spacing: 1) {
                Text(model.name)
                    .font(.system(size: 12))
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(model.formatLabel)
                        .font(.system(size: 10))
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(model.format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1))
                        .foregroundColor(model.format == "mlx" ? .purple : .blue)
                        .clipShape(Capsule())
                    Text(model.sizeLabel)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            if state.isLoadingModel && state.selectedModelId == model.name {
                ProgressView().scaleEffect(0.6)
            } else if !model.is_loaded {
                Button("Load") {
                    state.selectedModelId = model.name
                    state.loadModel(model.name, ctxSize: state.contextWindow)
                }
                .controlSize(.mini)
                .buttonStyle(.bordered)
                .help("Load this model into the inference backend")
            }

            Button(role: .destructive) {
                modelToDelete = model
                showDeleteConfirm = true
            } label: {
                Image(systemName: "trash")
                    .font(.system(size: 11))
                    .foregroundColor(model.is_loaded ? .secondary : .red)
            }
            .buttonStyle(.plain)
            .disabled(model.is_loaded)
            .help(model.is_loaded ? "Unload this model before deleting" : "Delete this model from disk")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    // MARK: - Recommendations section

    private var recommendationsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Recommended for Your Hardware", systemImage: "cpu")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                if state.isLoadingRecommendations {
                    ProgressView().scaleEffect(0.6)
                } else {
                    Button {
                        state.reloadRecommendations()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.plain)
                    .help("Refresh hardware detection and recommendations")
                }
            }

            if let hw = state.hardwareProfile {
                HStack(spacing: 6) {
                    Text("\(hw.cpu_name)  ·  \(String(format: "%.0f GB RAM", hw.total_ram_gb))\(hw.has_apple_silicon ? "  ·  Apple Silicon" : hw.has_nvidia_gpu ? "  ·  NVIDIA GPU" : "")")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Spacer()
                    if hw.llmfit_available {
                        Label("llmfit", systemImage: "checkmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(.green)
                            .help("llmfit is installed — recommendations are hardware-optimised")
                    } else {
                        Button {
                            state.installLlmfit()
                        } label: {
                            if state.isInstallingLlmfit {
                                Label("Installing…", systemImage: "arrow.down.circle")
                                    .font(.system(size: 10))
                            } else {
                                Label("Install llmfit", systemImage: "arrow.down.circle")
                                    .font(.system(size: 10))
                            }
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.mini)
                        .disabled(state.isInstallingLlmfit)
                        .help("Install llmfit for smarter, hardware-aware model recommendations")
                    }
                }
            }

            if state.recommendedModels.isEmpty && !state.isLoadingRecommendations {
                Text("No recommendations yet — tap ↺ to detect your hardware.")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            } else {
                VStack(spacing: 6) {
                    ForEach(state.recommendedModels) { rec in
                        recommendationRow(rec)
                    }
                }
            }
        }
    }

    private func recommendationRow(_ rec: ModelRecommendation) -> some View {
        let alreadyDownloaded = state.localModels.contains(where: { $0.name == (rec.filename ?? rec.name) })
        let isThisDownloading = state.activeDownload?.repoId == rec.repo_id
            && state.activeDownload?.done == false
            && state.activeDownload?.error == nil
        let thisPercent = isThisDownloading ? (state.activeDownload?.percent ?? -1) : 0

        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(rec.name)
                            .font(.system(size: 12))
                            .lineLimit(1)
                        Text(rec.formatLabel)
                            .font(.system(size: 10))
                            .padding(.horizontal, 5).padding(.vertical, 2)
                            .background(rec.format == "mlx" ? Color.purple.opacity(0.12) : Color.blue.opacity(0.1))
                            .foregroundColor(rec.format == "mlx" ? .purple : .blue)
                            .clipShape(Capsule())
                        Text(rec.sizeLabel)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                    Text(rec.why)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }
                Spacer()
                if alreadyDownloaded {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.system(size: 14))
                        .help("Already downloaded")
                } else if isThisDownloading {
                    ProgressView().scaleEffect(0.6)
                } else {
                    Button {
                        prefillDownload(rec)
                    } label: {
                        Label("Get", systemImage: "arrow.down.circle")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
                    .disabled(isDownloading)
                    .help("Download this model")
                }
            }
            if isThisDownloading {
                if thisPercent < 0 {
                    ProgressView()
                        .progressViewStyle(.linear)
                        .frame(height: 3)
                } else {
                    ProgressView(value: thisPercent, total: 100)
                        .progressViewStyle(.linear)
                        .frame(height: 3)
                        .overlay(
                            Text(String(format: "%.0f%%", thisPercent))
                                .font(.system(size: 9))
                                .foregroundColor(.secondary)
                                .offset(x: 0, y: 8),
                            alignment: .trailing
                        )
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func prefillDownload(_ rec: ModelRecommendation) {
        downloadRepoId   = rec.repo_id
        downloadFilename = rec.filename ?? ""
        downloadFormat   = rec.format
        // Also start the download immediately
        state.startModelDownload(repoId: rec.repo_id, filename: rec.filename, format: rec.format)
    }

    // MARK: - Download section

    private var downloadSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Download Model from Hugging Face", systemImage: "arrow.down.circle")
                .font(.system(size: 13, weight: .semibold))

            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
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
                                            Text(s.repo_id)
                                                .font(.system(size: 11))
                                                .lineLimit(1)
                                            Spacer()
                                            Text("\(s.downloads / 1000)K↓")
                                                .font(.system(size: 10))
                                                .foregroundColor(.secondary)
                                        }
                                        .padding(.horizontal, 8)
                                        .padding(.vertical, 5)
                                        .contentShape(Rectangle())
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
                }

                if downloadFormat == "mlx" {
                    Text("Leave filename blank — the entire model directory will be downloaded.")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }

                // Progress
                if isDownloading {
                    VStack(alignment: .leading, spacing: 4) {
                        if downloadProgress < 0 {
                            ProgressView("Downloading…")
                                .progressViewStyle(.linear)
                        } else {
                            ProgressView(value: downloadProgress, total: 100) {
                                Text(String(format: "%.0f%%", downloadProgress))
                                    .font(.system(size: 11))
                            }
                            .progressViewStyle(.linear)
                        }
                        if downloadDone {
                            Label("Download complete", systemImage: "checkmark.circle.fill")
                                .foregroundColor(.green)
                                .font(.system(size: 11))
                        }
                    }
                }

                if let err = downloadError {
                    Text(err).font(.system(size: 11)).foregroundColor(.red)
                }

                Button(action: startDownload) {
                    Label(isDownloading ? "Downloading…" : "Download", systemImage: "arrow.down.circle")
                }
                .disabled(isDownloading || downloadRepoId.trimmingCharacters(in: .whitespaces).isEmpty
                    || (downloadFormat == "gguf" && downloadFilename.trimmingCharacters(in: .whitespaces).isEmpty))
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
        }
    }

    // MARK: - Context window section

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

                Text("Larger values allow longer conversations but use more RAM. Changes take effect when you load a model.")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
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

    // MARK: - HF Autocomplete

    private func fetchHFSuggestions(_ query: String) {
        hfSearchTask?.cancel()
        guard query.count >= 2 else { hfSuggestions = []; showSuggestions = false; return }
        hfSearchTask = Task {
            try? await Task.sleep(nanoseconds: 400_000_000) // 400ms debounce
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

    // MARK: - Download action

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
