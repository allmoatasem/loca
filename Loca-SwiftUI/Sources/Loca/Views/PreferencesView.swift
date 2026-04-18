import SwiftUI

struct PreferencesView: View {
    @EnvironmentObject var state: AppState
    @State private var selectedTab = 0

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $selectedTab) {
                Text("General").tag(0)
                Text("Inference").tag(1)
                Text("Performance").tag(2)
                Text("System Prompt").tag(3)
                Text("Knowledge").tag(4)
                Text("Server").tag(5)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            switch selectedTab {
            case 1:  InferencePrefsTab()
            case 2:  PerformancePrefsTab()
            case 3:  SystemPromptPrefsTab()
            case 4:  KnowledgePrefsTab()
            case 5:  ServerPrefsTab()
            default: GeneralPrefsTab()
            }
        }
        // Wider than the original 540 so the 6 segmented-control labels
        // (General / Inference / Performance / System Prompt / Knowledge /
        // Server) size uniformly instead of the shorter tabs being
        // noticeably narrower. Fixed height so the window doesn't jump
        // between tabs; tabs with overflowing content scroll internally.
        .frame(width: 640, height: 640)
    }
}

// MARK: - General

private struct GeneralPrefsTab: View {
    @EnvironmentObject var state: AppState

    private let contextOptions = [4096, 8192, 16384, 32768, 65536, 131072, 262144]

    @State private var modelsDir: String = ""
    @State private var modelsDirStatus: String?

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $state.themeMode) {
                    ForEach(ThemeMode.allCases, id: \.self) { mode in
                        Label(mode.label, systemImage: mode.icon).tag(mode)
                    }
                }
                .pickerStyle(.radioGroup)
            }

            Section("Context") {
                HStack {
                    Text("Default context window")
                        .lineLimit(1)
                    Spacer()
                    Picker("", selection: $state.contextWindow) {
                        ForEach(contextOptions, id: \.self) { n in
                            Text(ctxLabel(n)).tag(n)
                        }
                    }
                    .labelsHidden()
                    .fixedSize()
                }
                Text("The number of tokens the model keeps in memory per conversation. Higher values use more RAM.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Section("Models directory") {
                HStack(spacing: 8) {
                    TextField("", text: $modelsDir, prompt: Text("~/loca_models"))
                        .textFieldStyle(.roundedBorder)
                        .labelsHidden()
                    Button("Choose…") { pickFolder() }
                    Button("Save") { Task { await saveModelsDir() } }
                        .disabled(modelsDir.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if let status = modelsDirStatus {
                    Text(status)
                        .font(.caption)
                        .foregroundColor(status.hasPrefix("Saved") ? .green : .red)
                }
                Text("Where Loca downloads and scans for GGUF / MLX models. Change this to store models on an external SSD. Restart recommended after changing.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .task { await loadModelsDir() }
    }

    private func loadModelsDir() async {
        if let v = try? await BackendClient.shared.fetchModelsDir() {
            modelsDir = v
        }
    }

    private func saveModelsDir() async {
        let path = modelsDir.trimmingCharacters(in: .whitespaces)
        guard !path.isEmpty else { return }
        do {
            let resolved = try await BackendClient.shared.updateModelsDir(path)
            modelsDir = resolved
            modelsDirStatus = "Saved — restart Loca for all services to use the new path."
        } catch {
            modelsDirStatus = "Failed to save: \(error.localizedDescription)"
        }
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        if panel.runModal() == .OK, let url = panel.url {
            modelsDir = url.path
        }
    }

    private func ctxLabel(_ n: Int) -> String { n >= 1024 ? "\(n / 1024)K tokens" : "\(n)" }
}

// MARK: - Inference

private struct InferencePrefsTab: View {
    @EnvironmentObject var state: AppState

    private var isCustom: Bool { state.selectedRecipe == "Custom" }

    var body: some View {
        // One Form containing everything — Form scrolls internally on
        // macOS, so Recipe + Parameters + Advanced all scroll together.
        // Nesting Form inside an outer ScrollView breaks scrolling because
        // the Form's internal scroll swallows the outer wheel events.
        Form {
            Section("Recipe") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(InferenceRecipe.all) { recipe in
                            RecipeCard(recipe: recipe, isSelected: state.selectedRecipe == recipe.name) {
                                state.selectedRecipe = recipe.name
                                if recipe.name != "Custom" {
                                    state.temperature   = recipe.temperature
                                    state.topP          = recipe.topP
                                    state.topK          = recipe.topK
                                    state.repeatPenalty = recipe.repeatPenalty
                                    state.maxTokens     = recipe.maxTokens
                                }
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }
            }

            Section("Parameters" + (isCustom ? "" : " (select Custom to edit)")) {
                    SliderRow(
                        label: "Temperature",
                        value: $state.temperature,
                        range: 0...2,
                        format: "%.2f",
                        hint: "Higher = more creative, lower = more deterministic",
                        enabled: isCustom
                    ) { state.selectedRecipe = "Custom" }

                    SliderRow(
                        label: "Top-P",
                        value: $state.topP,
                        range: 0...1,
                        format: "%.2f",
                        hint: "Nucleus sampling threshold",
                        enabled: isCustom
                    ) { state.selectedRecipe = "Custom" }

                    IntSliderRow(
                        label: "Top-K",
                        value: $state.topK,
                        range: 1...100,
                        hint: "Limits token candidates at each step",
                        enabled: isCustom
                    ) { state.selectedRecipe = "Custom" }

                    SliderRow(
                        label: "Repeat Penalty",
                        value: $state.repeatPenalty,
                        range: 1...2,
                        format: "%.2f",
                        hint: "Discourages repeating the same phrases",
                        enabled: isCustom
                    ) { state.selectedRecipe = "Custom" }

                    IntSliderRow(
                    label: "Max Tokens",
                    value: $state.maxTokens,
                    range: 128...8192,
                    hint: "Maximum tokens generated per response",
                    enabled: isCustom
                ) { state.selectedRecipe = "Custom" }
            }

            AdvancedBackendArgsSection()
        }
        .formStyle(.grouped)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

// MARK: - Advanced backend args (chat_template_kwargs + extra_body)

/// Exposes the raw JSON fields that Loca forwards to the backend:
///   - chat_template_kwargs → Jinja template vars (Qwen3 enable_thinking,
///     Qwen3.6 preserve_thinking, …)
///   - extra_body           → arbitrary sampling extras (min_p,
///     mirostat_tau, xtc_probability, dry_multiplier, grammar, …)
/// Also surfaces a three-state thinking-mode segmented control that keeps
/// the template-kwargs JSON in sync without the user editing it by hand.
private struct AdvancedBackendArgsSection: View {
    @EnvironmentObject var state: AppState

    private enum ThinkingMode: String, CaseIterable, Identifiable {
        case auto     = "Auto"
        case off      = "Off"
        case preserve = "Preserve"
        var id: String { rawValue }
    }

    @State private var ctkError: String?
    @State private var ebError:  String?

    private var thinkingMode: ThinkingMode {
        guard let data = state.chatTemplateKwargsJSON.data(using: .utf8),
              let obj  = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return .auto
        }
        if obj["preserve_thinking"] as? Bool == true { return .preserve }
        if obj["enable_thinking"]   as? Bool == false { return .off }
        return .auto
    }

    private func setThinkingMode(_ m: ThinkingMode) {
        var obj: [String: Any] = [:]
        if let data = state.chatTemplateKwargsJSON.data(using: .utf8),
           let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            obj = existing
        }
        obj.removeValue(forKey: "enable_thinking")
        obj.removeValue(forKey: "preserve_thinking")
        switch m {
        case .auto:     break
        case .off:      obj["enable_thinking"] = false
        case .preserve: obj["preserve_thinking"] = true
        }
        if obj.isEmpty {
            state.chatTemplateKwargsJSON = ""
        } else if let data = try? JSONSerialization.data(
            withJSONObject: obj, options: [.prettyPrinted, .sortedKeys]
        ), let str = String(data: data, encoding: .utf8) {
            state.chatTemplateKwargsJSON = str
        }
        ctkError = nil
    }

    private func validate(_ raw: String) -> String? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        guard let data = trimmed.data(using: .utf8) else { return "Invalid UTF-8" }
        do {
            _ = try JSONSerialization.jsonObject(with: data)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    var body: some View {
        Section("Advanced") {
            Text("Forwarded verbatim to the backend. Use **chat_template_kwargs** for template vars (Qwen3 `enable_thinking`, Qwen3.6 `preserve_thinking`). Use **extra_body** for sampling extras (`min_p`, `mirostat_tau`, `xtc_probability`, `dry_multiplier`, `grammar`, …).")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack {
                Text("Thinking mode")
                    .font(.system(size: 12))
                Spacer()
                Picker("", selection: Binding(
                    get: { thinkingMode },
                    set: { setThinkingMode($0) }
                )) {
                    ForEach(ThinkingMode.allCases) { m in Text(m.rawValue).tag(m) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 220)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("chat_template_kwargs (JSON)").font(.caption).foregroundColor(.secondary)
                TextEditor(text: $state.chatTemplateKwargsJSON)
                    .font(.system(size: 11, design: .monospaced))
                    .frame(minHeight: 60, maxHeight: 120)
                    .onChange(of: state.chatTemplateKwargsJSON) { _, new in ctkError = validate(new) }
                if let e = ctkError {
                    Text(e).font(.caption).foregroundColor(.red)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("extra_body (JSON)").font(.caption).foregroundColor(.secondary)
                TextEditor(text: $state.extraBodyJSON)
                    .font(.system(size: 11, design: .monospaced))
                    .frame(minHeight: 60, maxHeight: 120)
                    .onChange(of: state.extraBodyJSON) { _, new in ebError = validate(new) }
                if let e = ebError {
                    Text(e).font(.caption).foregroundColor(.red)
                }
            }
        }
    }
}

// MARK: - External server section

private struct ExternalServerSection: View {
    @EnvironmentObject var state: AppState

    private enum Preset: String, CaseIterable, Identifiable {
        case native   = "Native"
        case lmStudio = "LM Studio"
        case ollama   = "Ollama"

        var id: String { rawValue }
        var defaultUrl: String? {
            switch self {
            case .native:   return nil
            case .lmStudio: return "http://localhost:1234"
            case .ollama:   return "http://localhost:11434/v1"
            }
        }
    }

    @State private var preset: Preset = .native

    var body: some View {
        Form {
            Section {
                Picker("", selection: $preset) {
                    ForEach(Preset.allCases) { p in
                        Text(p.rawValue).tag(p)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .onChange(of: preset) { _, newPreset in
                    switch newPreset {
                    case .native:
                        state.lmStudioMode = false
                    case .lmStudio:
                        state.lmStudioUrl = "http://localhost:1234"
                        state.lmStudioMode = true
                    case .ollama:
                        state.lmStudioUrl = "http://localhost:11434/v1"
                        state.lmStudioMode = true
                    }
                }

                if preset != .native {
                    HStack {
                        Text("URL")
                            .frame(width: 40, alignment: .leading)
                        TextField(preset.defaultUrl ?? "http://localhost:1234", text: $state.lmStudioUrl)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit {
                                Task {
                                    try? await BackendClient.shared.setBackendMode(
                                        lmStudio: true,
                                        lmStudioUrl: state.lmStudioUrl
                                    )
                                }
                            }
                    }
                }

                Text(preset == .native
                     ? "Loca runs its own inference engine (mlx_lm on Apple Silicon, llama-server elsewhere). Models are managed in the Models tab."
                     : "Loca forwards LLM requests to this server. Its own features (memory, vault, research) remain active; only inference is delegated.")
                    .font(.caption)
                    .foregroundColor(.secondary)

            } header: {
                Text("Inference Engine")
            }
        }
        .formStyle(.grouped)
        .padding(.horizontal, 20)
        .padding(.top, 20)
        .onAppear {
            if !state.lmStudioMode {
                preset = .native
            } else if state.lmStudioUrl.contains("11434") {
                preset = .ollama
            } else {
                preset = .lmStudio
            }
        }
    }
}

private struct RecipeCard: View {
    let recipe: InferenceRecipe
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(recipe.name)
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.accentColor)
                        .font(.system(size: 12))
                }
            }
            Group {
                if recipe.name == "Custom" {
                    Text("Your own\nparameters")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                } else {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("temp \(String(format: "%.1f", recipe.temperature))")
                        Text("top-p \(String(format: "%.2f", recipe.topP))")
                        // Always render third line to keep equal height
                        Text(recipe.maxTokens < 2048 ? "max \(recipe.maxTokens) tok" : " ")
                    }
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(width: 110, height: 90)
        .background(isSelected ? Color.accentColor.opacity(0.1) : Color(nsColor: .controlBackgroundColor))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.2), lineWidth: isSelected ? 1.5 : 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onTapGesture { onTap() }
    }
}

// MARK: - Performance

private struct PerformancePrefsTab: View {
    @EnvironmentObject var state: AppState
    @State private var vramInput: String = ""

    private var hw: HardwareProfile? { state.hardwareProfile }

    private var isNvidia: Bool { hw?.has_nvidia_gpu == true && hw?.has_apple_silicon != true }
    private var isApple: Bool  { hw?.has_apple_silicon == true }

    private var hardwareBadge: String {
        guard let hw else { return "Detecting hardware…" }
        if hw.has_apple_silicon { return "\(hw.cpu_name)  ·  \(Int(hw.total_ram_gb)) GB unified memory" }
        if hw.has_nvidia_gpu    { return "NVIDIA GPU  ·  \(Int(hw.total_ram_gb)) GB system RAM" }
        return "CPU only  ·  \(Int(hw.total_ram_gb)) GB RAM"
    }

    var body: some View {
        Form {
            Section {
                HStack(spacing: 6) {
                    Image(systemName: isApple ? "memorychip" : (isNvidia ? "display" : "cpu"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Text(hardwareBadge)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                .padding(.bottom, 2)

                if isNvidia {
                    HStack {
                        Text("GPU VRAM (GB)")
                            .frame(width: 120, alignment: .leading)
                        TextField("e.g. 8", text: $vramInput)
                            .frame(width: 80)
                            .onAppear {
                                if state.nvidiaVramGb > 0 {
                                    vramInput = String(Int(state.nvidiaVramGb))
                                }
                            }
                            .onChange(of: vramInput) { _, val in
                                if let d = Double(val), d > 0 { state.nvidiaVramGb = d }
                            }
                        Spacer()
                    }
                    Text("Enter your GPU VRAM to enable hardware-aware suggestions.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                IntSliderRow(
                    label: "GPU Layers",
                    value: $state.nGpuLayers,
                    range: 0...99,
                    hint: "Layers offloaded to GPU — 99 = all (llama.cpp only, ignored by MLX)",
                    enabled: true
                ) {}

                IntSliderRow(
                    label: "Batch Size",
                    value: $state.batchSize,
                    range: 64...2048,
                    hint: "Prompt processing batch size — larger = faster but more memory",
                    enabled: true
                ) {}

                IntSliderRow(
                    label: "CPU Threads",
                    value: $state.numThreads,
                    range: 1...32,
                    hint: "CPU threads for non-matrix operations (llama.cpp only)",
                    enabled: true
                ) {}

                HStack {
                    Button(action: { state.suggestPerformanceParams() }) {
                        HStack(spacing: 6) {
                            if state.isSuggestingParams {
                                ProgressView().scaleEffect(0.7)
                            } else {
                                Image(systemName: "wand.and.stars")
                            }
                            Text("Suggest for my hardware")
                        }
                    }
                    .disabled(state.isSuggestingParams || hw == nil)
                    Spacer()
                }
                .padding(.top, 4)

                if let err = state.paramSuggestionError {
                    Text(err).font(.caption).foregroundColor(.red)
                }

                Text("Applied at model load time — reload your model after changing.")
                    .font(.caption)
                    .foregroundColor(.secondary)

            } header: {
                Text("Backend Parameters")
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onAppear { state.loadRecommendationsIfNeeded() }
    }
}

private struct SliderRow: View {
    let label: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let format: String
    let hint: String
    let enabled: Bool
    let onChange: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label).frame(width: 120, alignment: .leading)
                Slider(value: $value, in: range) { _ in onChange() }
                    .disabled(!enabled)
                Text(String(format: format, value))
                    .font(.system(size: 11, design: .monospaced))
                    .frame(width: 40, alignment: .trailing)
                    .foregroundColor(enabled ? .primary : .secondary)
            }
            Text(hint)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .opacity(enabled ? 1 : 0.55)
    }
}

private struct IntSliderRow: View {
    let label: String
    @Binding var value: Int
    let range: ClosedRange<Int>
    let hint: String
    let enabled: Bool
    let onChange: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label).frame(width: 120, alignment: .leading)
                Slider(value: Binding(
                    get: { Double(value) },
                    set: { value = Int($0); onChange() }
                ), in: Double(range.lowerBound)...Double(range.upperBound))
                .disabled(!enabled)
                Text("\(value)")
                    .font(.system(size: 11, design: .monospaced))
                    .frame(width: 40, alignment: .trailing)
                    .foregroundColor(enabled ? .primary : .secondary)
            }
            Text(hint)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .opacity(enabled ? 1 : 0.55)
    }
}

// MARK: - Server

private struct ServerPrefsTab: View {
    @EnvironmentObject var state: AppState
    @State private var hostInput: String = ""
    @State private var connectionStatus: ConnectionStatus = .unknown

    private enum ConnectionStatus: Equatable {
        case unknown, checking, connected, failed
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // ── Backend / inference source ───────────────────────────
                ExternalServerSection()

                Divider().padding(.horizontal, 20)

                // ── Remote Loca backend (Tailscale) ─────────────────────
                Form {
                    Section {
                        HStack {
                            Text("Backend host")
                                .frame(width: 120, alignment: .leading)
                            TextField("localhost", text: $hostInput)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { applyHost() }
                            Button("Connect") { applyHost() }
                                .disabled(hostInput.trimmingCharacters(in: .whitespacesAndNewlines) == state.serverHost)
                        }

                        HStack(spacing: 6) {
                            Circle()
                                .fill(statusColor)
                                .frame(width: 8, height: 8)
                            Text(statusLabel)
                                .font(.system(size: 11))
                                .foregroundColor(.secondary)
                        }
                        .padding(.top, 2)

                        Text("The host where Loca's backend proxy runs. Normally this is localhost. Change it to a Tailscale IP (e.g. 100.x.x.x) to offload the entire Loca stack — inference, memory, vault — to a more powerful machine.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } header: {
                        Text("Remote Loca Backend")
                    }

                    Section {
                        Button("Reset to localhost") {
                            hostInput = "localhost"
                            applyHost()
                        }
                        .disabled(state.serverHost == "localhost")
                    }
                }
                .formStyle(.grouped)
                .padding(.horizontal, 20)
                .padding(.bottom, 20)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onAppear {
            hostInput = state.serverHost
            checkConnection()
        }
    }

    private var statusColor: Color {
        switch connectionStatus {
        case .unknown:   return .gray
        case .checking:  return .yellow
        case .connected: return .green
        case .failed:    return .red
        }
    }

    private var statusLabel: String {
        switch connectionStatus {
        case .unknown:   return "Not checked"
        case .checking:  return "Connecting to \(state.serverHost)…"
        case .connected: return "Connected to \(state.serverHost)"
        case .failed:    return "Cannot reach \(state.serverHost):8000"
        }
    }

    private func applyHost() {
        let h = hostInput.trimmingCharacters(in: .whitespacesAndNewlines)
        state.serverHost = h.isEmpty ? "localhost" : h
        checkConnection()
    }

    private func checkConnection() {
        connectionStatus = .checking
        Task {
            let healthy = await BackendClient.shared.isHealthy()
            connectionStatus = healthy ? .connected : .failed
        }
    }
}

// MARK: - System Prompt

private struct SystemPromptPrefsTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section {
                TextEditor(text: $state.systemPromptOverride)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(minHeight: 180)
                    .overlay(
                        Group {
                            if state.systemPromptOverride.isEmpty {
                                Text("Leave empty to use the built-in prompts (recommended).\nIf set, this replaces the system prompt for all conversations.")
                                    .font(.system(size: 12))
                                    .foregroundColor(.secondary)
                                    .padding(6)
                                    .allowsHitTesting(false)
                                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                            }
                        }
                    )
            } header: {
                Text("System Prompt Override")
            } footer: {
                Text("Loca's built-in prompts are mode-aware (General, Code, Thinking, Vision) and include hardware context. An override applies to all modes.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
            }

            if !state.systemPromptOverride.isEmpty {
                Section {
                    Button("Clear Override") {
                        state.systemPromptOverride = ""
                    }
                    .foregroundColor(.red)
                }
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

// MARK: - Knowledge

private struct KnowledgePrefsTab: View {
    @State private var importPath = ""
    @State private var isImporting = false
    @State private var progressPct: Double = 0
    @State private var statusText = ""
    @State private var history: [ImportHistoryItem] = []

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("Path to file, folder, or URL…", text: $importPath)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))

                    HStack(spacing: 8) {
                        Button("Choose File…") {
                            let panel = NSOpenPanel()
                            panel.canChooseFiles = true
                            panel.canChooseDirectories = false
                            panel.allowsMultipleSelection = false
                            if panel.runModal() == .OK {
                                importPath = panel.url?.path ?? ""
                            }
                        }
                        .controlSize(.small)

                        Button("Choose Folder…") {
                            let panel = NSOpenPanel()
                            panel.canChooseFiles = false
                            panel.canChooseDirectories = true
                            panel.allowsMultipleSelection = false
                            if panel.runModal() == .OK {
                                importPath = panel.url?.path ?? ""
                            }
                        }
                        .controlSize(.small)

                        Spacer()

                        Button(isImporting ? "Importing…" : "Import") {
                            Task { await runImport() }
                        }
                        .controlSize(.small)
                        .buttonStyle(.borderedProminent)
                        .disabled(importPath.isEmpty || isImporting)
                    }

                    if isImporting || !statusText.isEmpty {
                        ProgressView(value: progressPct)
                            .progressViewStyle(.linear)
                        Text(statusText)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                }
            } header: {
                Text("Import Knowledge")
            } footer: {
                Text("Imports AI chat exports (Claude, ChatGPT), markdown, PDFs, EPUBs, DOCX, spreadsheets, images, and folders. Duplicate content is skipped automatically.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
            }

            if !history.isEmpty {
                Section {
                    ForEach(history) { item in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(item.source)
                                .font(.system(size: 12, weight: .medium))
                            Text("\(item.stored) chunks")
                                .font(.system(size: 12))
                                .foregroundColor(.secondary)
                            Spacer()
                            Text(item.importedDate)
                                .font(.system(size: 11))
                                .foregroundColor(.secondary)
                        }
                    }
                } header: {
                    Text("Past Imports")
                }
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .task { await loadHistory() }
    }

    private func loadHistory() async {
        history = (try? await BackendClient.shared.fetchImportHistory()) ?? []
    }

    private func runImport() async {
        guard !importPath.isEmpty else { return }
        isImporting = true
        progressPct = 0
        statusText = "Starting…"

        guard let url = URL(string: "http://localhost:8000/api/import") else {
            isImporting = false
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["path": importPath])

        do {
            let (bytes, _) = try await URLSession.shared.bytes(for: req)
            var buffer = ""
            for try await byte in bytes {
                let char = String(bytes: [byte], encoding: .utf8) ?? ""
                buffer += char
                if buffer.hasSuffix("\n\n") {
                    let lines = buffer.components(separatedBy: "\n")
                    for line in lines where line.hasPrefix("data: ") {
                        let payload = String(line.dropFirst(6))
                        if payload == "[DONE]" { break }
                        if let data = payload.data(using: .utf8),
                           let evt = try? JSONDecoder().decode(ImportProgressEvent.self, from: data) {
                            await MainActor.run { handleEvent(evt) }
                        }
                    }
                    buffer = ""
                }
            }
        } catch {
            statusText = "✗ \(error.localizedDescription)"
        }

        isImporting = false
        await loadHistory()
    }

    @MainActor
    private func handleEvent(_ evt: ImportProgressEvent) {
        switch evt.status {
        case "extracting":
            statusText = "Detected: \(evt.adapter ?? "") (\(evt.total ?? 0) chunks)"
        case "progress":
            let current = Double(evt.current ?? 0)
            let total = Double(evt.total ?? 1)
            progressPct = total > 0 ? current / total : 0
            statusText = "\(evt.current ?? 0)/\(evt.total ?? 0) — \(evt.skipped ?? 0) duplicates skipped"
        case "done":
            progressPct = 1.0
            statusText = "✓ \(evt.stored ?? 0) stored, \(evt.skipped ?? 0) skipped"
        case "error":
            statusText = "✗ \(evt.message ?? "Unknown error")"
        default:
            break
        }
    }
}
