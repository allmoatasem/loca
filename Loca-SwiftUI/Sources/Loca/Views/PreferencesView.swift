import SwiftUI

struct PreferencesView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        TabView {
            GeneralPrefsTab()
                .tabItem { Label("General", systemImage: "gearshape") }
            InferencePrefsTab()
                .tabItem { Label("Inference", systemImage: "cpu") }
            SystemPromptPrefsTab()
                .tabItem { Label("System Prompt", systemImage: "text.bubble") }
        }
        .frame(width: 540)
        .fixedSize()
    }
}

// MARK: - General

private struct GeneralPrefsTab: View {
    @EnvironmentObject var state: AppState

    private let contextOptions = [4096, 8192, 16384, 32768, 65536, 131072, 262144]

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
                Picker("Default context window", selection: $state.contextWindow) {
                    ForEach(contextOptions, id: \.self) { n in
                        Text(ctxLabel(n)).tag(n)
                    }
                }
                .frame(width: 180)
                Text("The number of tokens the model keeps in memory per conversation. Higher values use more RAM.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(width: 540)
    }

    private func ctxLabel(_ n: Int) -> String { n >= 1024 ? "\(n / 1024)K tokens" : "\(n)" }
}

// MARK: - Inference

private struct InferencePrefsTab: View {
    @EnvironmentObject var state: AppState

    private var isCustom: Bool { state.selectedRecipe == "Custom" }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Recipe cards
            Text("Recipe")
                .font(.headline)
                .padding(.horizontal, 20)
                .padding(.top, 20)

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
                .padding(.horizontal, 20)
            }

            Divider()
                .padding(.horizontal, 20)

            // Parameter sliders
            Form {
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
            }
            .formStyle(.grouped)
            .padding(.horizontal, 20)
            .padding(.bottom, 20)
        }
        .frame(width: 540)
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
                        if recipe.maxTokens < 2048 {
                            Text("max \(recipe.maxTokens) tok")
                        }
                    }
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.secondary)
                }
            }
        }
        .padding(12)
        .frame(width: 110)
        .background(isSelected ? Color.accentColor.opacity(0.1) : Color(nsColor: .controlBackgroundColor))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.2), lineWidth: isSelected ? 1.5 : 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onTapGesture { onTap() }
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
        .frame(width: 540)
    }
}
