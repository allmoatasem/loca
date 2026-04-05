import SwiftUI

/// Shown when the user enables voice mode but speech models aren't downloaded yet.
struct VoiceSetupSheet: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    private var sttReady: Bool {
        state.voiceConfig?.models.first(where: { $0.model_type == "stt" })?.downloaded ?? false
    }
    private var ttsReady: Bool {
        state.voiceConfig?.models.first(where: { $0.model_type == "tts" })?.downloaded ?? false
    }

    var body: some View {
        VStack(spacing: 20) {
            // Header
            VStack(spacing: 8) {
                Image(systemName: "mic.badge.plus")
                    .font(.system(size: 36))
                    .foregroundColor(.accentColor)
                Text("Voice Mode Setup")
                    .font(.title2.bold())
                Text("Voice mode requires two speech models to be downloaded. These run locally on your Mac — no data leaves your device.")
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
            }

            // Model status
            VStack(spacing: 12) {
                modelRow(
                    name: "Whisper Large v3 Turbo",
                    description: "Speech-to-text — transcribes your voice",
                    type: "stt",
                    downloaded: sttReady
                )
                modelRow(
                    name: "Kokoro 82M",
                    description: "Text-to-speech — speaks responses aloud",
                    type: "tts",
                    downloaded: ttsReady
                )
            }
            .padding(.horizontal, 20)

            // Buttons
            VStack(spacing: 10) {
                if sttReady && ttsReady {
                    Button {
                        state.isVoiceMode = true
                        dismiss()
                    } label: {
                        Text("Enable Voice Mode")
                            .font(.system(size: 14, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button {
                        // Models download on first use when voice mode is enabled.
                        // Just enable it — the backend will download automatically.
                        state.isVoiceMode = true
                        dismiss()
                    } label: {
                        Text("Download Recommended Models")
                            .font(.system(size: 14, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        // Open the settings panel so user can download models manually
                        dismiss()
                        state.isSettingsOpen = true
                    } label: {
                        Text("I will download models myself")
                            .font(.system(size: 13))
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                }

                Button("Cancel") { dismiss() }
                    .buttonStyle(.plain)
                    .foregroundColor(.secondary)
                    .font(.system(size: 13))
            }
            .padding(.horizontal, 40)
        }
        .padding(30)
        .frame(width: 460)
        .onAppear { state.fetchVoiceConfig() }
    }

    private func modelRow(name: String, description: String, type: String, downloaded: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: type == "stt" ? "ear" : "speaker.wave.2")
                .font(.system(size: 20))
                .foregroundColor(downloaded ? .green : .secondary)
                .frame(width: 30)

            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.system(size: 13, weight: .medium))
                Text(description)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }

            Spacer()

            if downloaded {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            } else {
                Text("Not downloaded")
                    .font(.system(size: 11))
                    .foregroundColor(.orange)
            }
        }
        .padding(12)
        .background(Color.secondary.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
