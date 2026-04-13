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
            CommandGroup(replacing: .appInfo) {
                Button("About Loca") {
                    AppState.shared.isAcknowledgementsOpen = true
                }
                Button("Glossary") {
                    AppState.shared.isGlossaryOpen = true
                }
                Button("Philosophy") {
                    AppState.shared.isPhilosophyOpen = true
                }
            }
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") {
                    AppState.shared.newConversation()
                }
                .keyboardShortcut("n")
            }
        }

        Settings {
            PreferencesView()
                .environmentObject(AppState.shared)
        }
    }
}
