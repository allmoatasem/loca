import AppKit
import SwiftUI

/// Handles macOS lifecycle events that SwiftUI's Scene API doesn't reach:
/// - Launching the Python backend on startup
/// - Terminating the backend on quit
/// - Custom window chrome (traffic lights, transparent titlebar)
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var backendProcess: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureWindow()
        launchBackend()
    }

    func applicationWillTerminate(_ notification: Notification) {
        backendProcess?.terminate()
        backendProcess?.waitUntilExit()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: - Window chrome

    private func configureWindow() {
        guard let window = NSApp.windows.first else { return }
        window.titlebarAppearsTransparent = true
        window.titleVisibility            = .hidden
        window.styleMask.insert(.fullSizeContentView)
        window.isMovableByWindowBackground = false
        window.setFrameAutosaveName("LocaMain")
    }

    // MARK: - Backend

    @MainActor
    private func launchBackend() {
        guard let scriptURL = Bundle.main.url(forResource: "start_services", withExtension: "sh")
                           ?? locateStartScript() else {
            AppState.shared.startupError = "start_services.sh not found."
            return
        }

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments     = [scriptURL.path]
        p.environment   = env

        do {
            try p.run()
            backendProcess = p
        } catch {
            AppState.shared.startupError = "Could not start backend: \(error.localizedDescription)"
        }
    }

    /// Resolves start_services.sh from project_path.txt (installed app) or relative path (dev).
    private func locateStartScript() -> URL? {
        let resourcesDir = Bundle.main.resourceURL
        if let pathFile = resourcesDir?.appendingPathComponent("project_path.txt"),
           let projectPath = try? String(contentsOf: pathFile).trimmingCharacters(in: .whitespacesAndNewlines) {
            let script = URL(fileURLWithPath: projectPath).appendingPathComponent("start_services.sh")
            if FileManager.default.fileExists(atPath: script.path) { return script }
        }
        // Dev: script is two levels up from Package.swift
        let dev = URL(fileURLWithPath: #file)
            .deletingLastPathComponent()  // App/
            .deletingLastPathComponent()  // Loca/
            .deletingLastPathComponent()  // Sources/
            .deletingLastPathComponent()  // Loca-SwiftUI/
            .appendingPathComponent("start_services.sh")
        return FileManager.default.fileExists(atPath: dev.path) ? dev : nil
    }
}
