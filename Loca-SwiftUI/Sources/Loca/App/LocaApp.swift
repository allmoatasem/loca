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
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") {
                    AppState.shared.newConversation()
                }
                .keyboardShortcut("n")
            }
        }
    }
}
