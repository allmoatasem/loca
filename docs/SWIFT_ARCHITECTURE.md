# Loca — Swift App Architecture

The macOS app lives in `Loca-SwiftUI/`. It is a native SwiftUI application that communicates exclusively with the Python proxy on `localhost:8000` — it contains no inference logic of its own.

---

## Directory layout

```
Loca-SwiftUI/
  Sources/Loca/
    App/
      LocaApp.swift          ← @main entry point, WindowGroup, global .environmentObject
      AppDelegate.swift      ← NSApplicationDelegate (menu, appearance, lifecycle hooks)
    State/
      AppState.swift         ← @ObservableObject: all published state + public action stubs
      AppState+Actions.swift ← Private implementations of every action method
    Backend/
      BackendClient.swift    ← Thin HTTP client: URLSession-based, async/await, typed methods
      Models.swift           ← Codable data models shared between backend and views
    Views/
      RootView.swift         ← Top-level NavigationSplitView: sidebar + content pane
      SidebarView.swift      ← Conversation list, search, folder/star grouping
      ChatView.swift         ← Message list, streaming bubble, attachment strip, footer toolbar
      ChatTextEditor.swift   ← Custom NSTextView wrapper for multi-line input + paste handling
      SettingsView.swift     ← TabView: Manage Models (Installed / Discover / Search HF),
                               Memories, Settings
      VaultView.swift        ← Vault Analyser modal: vault picker, scan, 4-tab analysis
                               (Overview, Orphans, Broken Links, Suggestions)
      AcknowledgementsView.swift ← Credits and acknowledgments panel
      PreferencesView.swift  ← User preferences (theme, inference recipe, system prompt)
```

---

## Pattern: MVVM with a single AppState

```
                 ┌──────────────────────────────────────────┐
                 │              AppState (@MainActor)        │
                 │                                          │
                 │  @Published var messages: [ChatMessage]  │
                 │  @Published var isStreaming: Bool         │
                 │  @Published var localModels: [LocalModel]│
                 │  @Published var recommendedModels: [...]  │
                 │  @Published var activeDownload: ...      │
                 │  @Published var hardwareProfile: ...      │
                 │  ...                                     │
                 │                                          │
                 │  func send(_ text: String)               │
                 │  func loadModel(_ name: String)          │
                 │  func startDownload(...)                 │
                 │  func reloadRecommendations()            │
                 │  ...                                     │
                 └──────────────────────┬───────────────────┘
                                        │ @EnvironmentObject
                              ┌─────────┼──────────┐
                              │         │          │
                         RootView  ChatView  SettingsView  VaultView
                              │               │
                         SidebarView    DiscoverTab / InstalledTab / ...
```

**AppState** is a single `@MainActor` `ObservableObject` injected as `.environmentObject` at the root. Every view reads from it and calls methods on it — no view-level model objects. This keeps business logic testable and avoids prop-drilling.

**AppState+Actions.swift** contains the private `_`-prefixed implementations (e.g. `_send`, `_loadModel`, `_startDownload`). The public stubs in `AppState.swift` delegate to these via `Task { await ... }`, keeping the public API synchronous for view call sites.

---

## BackendClient

`BackendClient` is a stateless struct with one method per API endpoint. It is not injected as an environment object — `AppState+Actions.swift` creates it locally in each action. Every method is `async throws`.

Key methods:

| Method | Endpoint |
|---|---|
| `fetchHealth()` | `GET /health` |
| `fetchLocalModels()` | `GET /api/local-models` |
| `fetchActiveModel()` | `GET /api/models/active` |
| `loadModel(name:ctxSize:)` | `POST /api/models/load` |
| `deleteModel(name:)` | `DELETE /api/models/{name}` |
| `startDownload(repoId:filename:format:)` | `POST /api/models/download` |
| `cancelDownload(downloadId:)` | `POST /api/models/download/{id}/cancel` |
| `pauseDownload(downloadId:)` | `POST /api/models/download/{id}/pause` |
| `fetchRecommendedModels(force:)` | `GET /api/recommended-models` |
| `fetchHardwareProfile()` | `GET /api/hardware` |
| `installLlmfit()` | `POST /api/hardware/install-llmfit` |
| `fetchConversations()` | `GET /api/conversations` |
| `saveConversation(...)` | `POST /api/conversations` |
| `sendMessage(messages:...)` | `POST /v1/chat/completions` (streaming SSE) |
| `detectVaults()` | `GET /api/vault/detect` |
| `scanVault(path:)` | `POST /api/vault/scan` |
| `vaultAnalysis(path:)` | `GET /api/vault/analysis` |

SSE streaming (chat completions and download progress) uses `URLSession.bytes(for:)` — an `AsyncBytes` sequence — iterated line by line.

---

## Data models (Models.swift)

All types conform to `Codable` and are value types (`struct`). Key types:

| Type | Purpose |
|---|---|
| `ChatMessage` | Role + content + optional attachments |
| `ConversationMeta` | id, title, updatedAt, starred, folder |
| `LocalModel` | name, format, sizeGb, loaded |
| `ActiveDownload` | repoId, filename, format, downloadId, percent, speedMbps, etaSeconds |
| `HardwareProfile` | platform, arch, cpuName, totalRamGb, hasAppleSilicon, llmfitAvailable |
| `ModelRecommendation` | name, repoId, filename, format, sizeGb, fitLevel, useCase, provider, score, tps, why |
| `Memory` | id, content, type, createdAt |
| `DetectedVault` | name, path |
| `VaultAnalysis` | stats, orphans, dead_ends, broken_links, tag_orphans, link_suggestions |
| `VaultStats` | note_count, link_count, total_words, tag_count, top_tags, folder_count |
| `VaultScanResult` | ok, total, added, updated, removed, skipped, errors |
| `StartupStatus` | stage, progress |

---

## Key flows

### Startup

1. `LocaApp.swift` calls `AppState.shared.startHealthPolling()` on appear
2. `_startHealthPolling()` polls `GET /health` every 0.75 s until the backend is ready
3. While polling, startup progress is read from `/tmp/loca-startup-status.json` (written by `start_services.sh`)
4. Once ready, `_loadModels()`, `_loadConversationList()`, `_loadLocalModels()`, and `pollSystemStats()` are called
5. `loadRecommendationsIfNeeded()` is called when the Discover tab first appears — skips the fetch if `recommendedModels` is already populated

### Sending a message

1. View calls `state.send(text, attachments:)`
2. `_send` builds the message list, uploads any attachments first via `POST /api/upload`
3. Streams `POST /v1/chat/completions` — parses SSE chunks into `streamingText`
4. On completion, appends the assistant message and saves the conversation via `POST /api/conversations`
5. Triggers async memory extraction via `POST /api/extract-memories`

### Model download

1. User clicks Download → view calls `state.startDownload(repoId:filename:format:)`
2. `_startDownload` POSTs to `/api/models/download` → stores `download_id` in `activeDownload`
3. Streams `GET /api/models/download/{id}/progress` — updates `activeDownload.percent` and speed/ETA
4. Pause: calls `/pause` endpoint (task cancelled server-side, partial files kept) → sets `activeDownload.paused = true`
5. Resume: calls `startDownload` again with the same parameters — server resumes via HTTP Range headers
6. Cancel: calls `/cancel` endpoint → partial files deleted → `activeDownload = nil`

### Hardware recommendations

1. On Discover tab appear, `loadRecommendationsIfNeeded()` checks if cache is populated
2. If empty, calls `GET /api/recommended-models` → server runs llmfit (or returns in-memory cache)
3. Results populate `recommendedModels` — filtered/paginated in `SettingsView` locally
4. Refresh button calls `reloadRecommendations()` → `GET /api/recommended-models?force=true`

### Vault analysis

1. User clicks books icon in sidebar → `state.isVaultOpen = true` → VaultView overlay appears
2. `onAppear` calls `state.detectVaults()` → `GET /api/vault/detect` → populates vault picker
3. If one vault found, auto-selects and calls `state.analyseVault()` → `GET /api/vault/analysis`
4. If vault not indexed (note_count == 0), shows "Scan Now" prompt
5. Scan button calls `state.scanVault()` → `POST /api/vault/scan` → auto-refreshes analysis
6. Analysis displayed in 4 tabs: Overview (stats, tags, health), Orphans, Broken Links, Suggestions

---

## Threading

All AppState mutations happen on `@MainActor`. Network calls in `AppState+Actions.swift` are `async` and run on the cooperative thread pool; they update `@Published` properties via `await MainActor.run { }` or directly (since the whole class is `@MainActor`).

`BackendClient` is not actor-isolated — it is called from `async` contexts inside `AppState+Actions.swift`.
