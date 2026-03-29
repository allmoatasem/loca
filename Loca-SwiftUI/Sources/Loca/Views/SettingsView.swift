import SwiftUI

/// Settings sheet — model management, download, hardware-aware recommendations, context window.
struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var downloadRepoId     = ""
    @State private var downloadFilename   = ""
    @State private var downloadFormat     = "gguf"
    @State private var isDownloading      = false
    @State private var downloadProgress: Double = 0   // -1 = indeterminate, 0–100
    @State private var downloadError: String?
    @State private var downloadId: String?
    @State private var downloadDone       = false
    @State private var modelToDelete: LocalModel?
    @State private var showDeleteConfirm  = false

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
                Text("\(hw.cpu_name)  ·  \(String(format: "%.0f GB RAM", hw.total_ram_gb))\(hw.has_apple_silicon ? "  ·  Apple Silicon" : hw.has_nvidia_gpu ? "  ·  NVIDIA GPU" : "")")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }

            if state.recommendedModels.isEmpty && !state.isLoadingRecommendations {
                Text("No recommendations available — backend may not be running yet.")
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
        let alreadyDownloaded = state.localModels.contains(where: {
            $0.name == rec.filename ?? rec.repo_id.split(separator: "/").last.map(String.init) ?? rec.name
        })

        return HStack(spacing: 8) {
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
            } else {
                Button {
                    prefillDownload(rec)
                } label: {
                    Label("Get", systemImage: "arrow.down.circle")
                        .font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.mini)
                .help("Pre-fill the download form for this model")
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
        // Scroll is not programmatic here; user can see the pre-filled fields below
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

    // MARK: - Download action

    private func startDownload() {
        let repoId   = downloadRepoId.trimmingCharacters(in: .whitespaces)
        let filename = downloadFilename.trimmingCharacters(in: .whitespaces)
        guard !repoId.isEmpty else { return }

        isDownloading   = true
        downloadProgress = -1
        downloadError   = nil
        downloadDone    = false

        Task {
            do {
                let dlId = try await BackendClient.shared.startDownload(
                    repoId: repoId,
                    filename: filename.isEmpty ? nil : filename,
                    format: downloadFormat
                )
                downloadId = dlId
                await pollDownloadProgress(dlId)
            } catch {
                downloadError = error.localizedDescription
                isDownloading = false
            }
        }
    }

    private func pollDownloadProgress(_ dlId: String) async {
        // Poll /api/models/download/{id}/progress via SSE
        guard let url = URL(string: "http://localhost:8000/api/models/download/\(dlId)/progress") else { return }

        do {
            let (bytes, _) = try await URLSession.shared.bytes(from: url)
            var buffer = ""
            for try await byte in bytes {
                buffer += String(bytes: [byte], encoding: .utf8) ?? ""
                while let nl = buffer.firstIndex(of: "\n") {
                    let line = String(buffer[..<nl])
                    buffer = String(buffer[buffer.index(after: nl)...])
                    if line.hasPrefix("data: ") {
                        let payload = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
                        if let data = payload.data(using: .utf8),
                           let progress = try? JSONDecoder().decode(DownloadProgress.self, from: data) {
                            await MainActor.run {
                                downloadProgress = progress.percent
                                if let err = progress.error {
                                    downloadError = err
                                    isDownloading = false
                                }
                                if progress.done {
                                    downloadDone  = true
                                    isDownloading = false
                                    downloadProgress = 100
                                    state.reloadLocalModels()
                                }
                            }
                        }
                    }
                }
            }
        } catch {
            await MainActor.run {
                downloadError = error.localizedDescription
                isDownloading = false
            }
        }
    }

    private func ctxLabel(_ n: Int) -> String { n >= 1024 ? "\(n / 1024)K" : "\(n)" }
}
