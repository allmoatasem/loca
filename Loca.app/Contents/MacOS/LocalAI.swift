import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backendProcess: Process?
    var pollTimer: Timer?
    var isReady = false

    // MARK: - Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        launchBackend()
        showLoading()
        startPolling()
    }

    // MARK: - Menu

    func buildMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        bar.addItem(appItem)

        let appMenu = NSMenu(title: "Loca")
        appItem.submenu = appMenu
        appMenu.addItem(NSMenuItem(
            title: "New Conversation",
            action: #selector(newConversation),
            keyEquivalent: "n"
        ))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Quit Loca",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        ))
        NSApp.mainMenu = bar
    }

    @objc func newConversation() {
        webView.evaluateJavaScript("newConversation()", completionHandler: nil)
    }

    // MARK: - Window

    func buildWindow() {
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1080, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Loca"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("LocaMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Backend

    func launchBackend() {
        guard let execURL = Bundle.main.executableURL else { return }
        let scriptURL = execURL
            .deletingLastPathComponent()
            .appendingPathComponent("start_services.sh")

        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            showError("start_services.sh not found at:\n\(scriptURL.path)")
            return
        }

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [scriptURL.path]
        p.currentDirectoryURL = scriptURL.deletingLastPathComponent()
        p.environment = env

        do {
            try p.run()
            backendProcess = p
        } catch {
            showError("Could not launch backend:\n\(error.localizedDescription)")
        }
    }

    // MARK: - Loading screen

    func showLoading() {
        let html = """
        <!DOCTYPE html><html><head><style>
        * { margin:0; padding:0; box-sizing:border-box }
        body {
          background:#f5f5f0; color:#1a1a1a;
          font-family:-apple-system,sans-serif;
          display:flex; align-items:center; justify-content:center; height:100vh;
        }
        .wrap { text-align:center }
        .ring {
          width:34px; height:34px;
          border:3px solid #e5e5e0; border-top-color:#5b5bd6;
          border-radius:50%;
          animation:spin .75s linear infinite;
          margin:0 auto 18px;
        }
        @keyframes spin { to { transform:rotate(360deg) } }
        h2 { font-size:16px; font-weight:600; margin-bottom:6px }
        p  { font-size:13px; color:#8a8a8a }
        </style></head>
        <body><div class="wrap">
          <div class="ring"></div>
          <h2>Starting Loca…</h2>
          <p>This may take a moment on first launch.</p>
        </div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: - Polling

    func startPolling() {
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.checkReady()
        }
        // Time out after 8 minutes (first-run setup can be slow)
        Timer.scheduledTimer(withTimeInterval: 480, repeats: false) { [weak self] _ in
            guard self?.isReady == false else { return }
            self?.pollTimer?.invalidate()
            self?.showError(
                "Backend didn't start within 8 minutes.\n\nCheck logs:\n" +
                "  /tmp/loca-proxy.log\n  /tmp/loca-searxng.log"
            )
        }
    }

    func checkReady() {
        guard let url = URL(string: "http://localhost:8000/health") else { return }
        URLSession.shared.dataTask(with: url) { [weak self] _, resp, _ in
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return }
            DispatchQueue.main.async {
                guard self?.isReady == false else { return }
                self?.isReady = true
                self?.pollTimer?.invalidate()
                self?.webView.load(URLRequest(url: URL(string: "http://localhost:8000/")!))
            }
        }.resume()
    }

    // MARK: - Navigation delegate

    func webView(
        _ webView: WKWebView,
        decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        // Open external links in the default browser, not in the app
        if let url = action.request.url,
           action.navigationType == .linkActivated,
           url.host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // MARK: - Error

    func showError(_ msg: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.messageText = "Loca failed to start"
            alert.informativeText = msg
            alert.alertStyle = .critical
            alert.addButton(withTitle: "Quit")
            alert.runModal()
            NSApp.terminate(nil)
        }
    }

    // MARK: - Lifecycle

    func applicationWillTerminate(_ notification: Notification) {
        pollTimer?.invalidate()
        backendProcess?.terminate()
        backendProcess?.waitUntilExit()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }
}

// MARK: - Entry point

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
