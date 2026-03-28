import SwiftUI

/// Top-level view. Shows the loading screen until the backend is ready,
/// then switches to the full chat layout.
struct RootView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Group {
            if let err = state.startupError {
                StartupErrorView(message: err)
            } else if state.isBackendReady {
                MainLayout()
            } else {
                StartupView()
            }
        }
        .frame(minWidth: 900, minHeight: 620)
        .onAppear { state.startHealthPolling() }
    }
}

// MARK: - Startup (loading) screen

struct StartupView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(spacing: 20) {
                // Logo
                HStack(spacing: 0) {
                    Text("Lo").font(.system(size: 32, weight: .bold))
                    Text("ca").font(.system(size: 32, weight: .bold)).foregroundColor(.accentColor)
                }

                // Spinner
                ProgressView()
                    .scaleEffect(0.8)
                    .padding(.bottom, 4)

                // Stage label
                Text(state.startupStatus.stage)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(.primary)
                    .animation(.easeInOut, value: state.startupStatus.stage)

                // Progress bar
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.secondary.opacity(0.15))
                            .frame(height: 4)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.accentColor)
                            .frame(width: geo.size.width * CGFloat(state.startupStatus.progress) / 100,
                                   height: 4)
                    }
                }
                .frame(height: 4)
                .frame(maxWidth: 240)
                .animation(.easeInOut(duration: 0.5), value: state.startupStatus.progress)
            }
            .padding(40)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(.background)
                    .shadow(color: .black.opacity(0.08), radius: 20, y: 4)
            )
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

// MARK: - Error screen

struct StartupErrorView: View {
    let message: String

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40))
                .foregroundColor(.red)
            Text("Loca failed to start")
                .font(.headline)
            Text(message)
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
            Button("Quit") { NSApp.terminate(nil) }
                .buttonStyle(.borderedProminent)
        }
        .padding(40)
    }
}

// MARK: - Main layout (placeholder — to be implemented)

struct MainLayout: View {
    @EnvironmentObject var state: AppState
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 320)
        } detail: {
            ChatView()
        }
        .navigationSplitViewStyle(.balanced)
    }
}

