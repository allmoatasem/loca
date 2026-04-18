import SwiftUI

@main
struct LocaApp: App {

    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(AppState.shared)
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
        .commands {
            // App menu keeps the system default "About Loca" (version dialog)
            // so macOS users find it where they expect. Our curated
            // About / Glossary / Philosophy live under Help.
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") {
                    AppState.shared.newConversation()
                }
                .keyboardShortcut("n")
            }
            // Strip SwiftUI's noisy default View entries (tabs, toolbar,
            // sidebar toggles) — Loca has no sidebar/toolbar/tabs and the
            // View menu was otherwise empty filler.
            CommandGroup(replacing: .toolbar) { }
            CommandGroup(replacing: .sidebar) { }
            CommandGroup(replacing: .help) {
                Button("About Loca") {
                    AppState.shared.isAcknowledgementsOpen = true
                }
                Divider()
                Button("Glossary") {
                    AppState.shared.isGlossaryOpen = true
                }
                Button("Philosophy") {
                    AppState.shared.isPhilosophyOpen = true
                }
            }
        }

        Settings {
            PreferencesView()
                .environmentObject(AppState.shared)
        }
    }
}
