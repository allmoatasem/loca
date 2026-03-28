import Cocoa
import WebKit

/// WKWebView subclass: intercepts mouseDown in the top drag-bar region so
/// the user can drag the window regardless of what CSS says.
class DraggableWebView: WKWebView {
    private let dragBarHeight: CGFloat = 38
    override func mouseDown(with event: NSEvent) {
        let loc = convert(event.locationInWindow, from: nil)
        // AppKit origin is bottom-left; top of view = bounds.height
        guard loc.y > bounds.height - dragBarHeight, let win = window else {
            super.mouseDown(with: event)
            return
        }
        // Manual window drag via nextEvent loop
        var prevLocation = event.locationInWindow
        while let e = win.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) {
            if e.type == .leftMouseUp { break }
            var frame = win.frame
            frame.origin.x += e.locationInWindow.x - prevLocation.x
            frame.origin.y += e.locationInWindow.y - prevLocation.y
            win.setFrameOrigin(frame.origin)
            prevLocation = e.locationInWindow
        }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backendProcess: Process?
    var pollTimer: Timer?
    var isReady = false

    // MARK: - Launch

    func log(_ msg: String) {
        let line = "\(Date()): \(msg)\n"
        if let data = line.data(using: .utf8) {
            let url = URL(fileURLWithPath: "/tmp/loca-swift.log")
            if let fh = try? FileHandle(forWritingTo: url) {
                fh.seekToEndOfFile(); fh.write(data); try? fh.close()
            } else {
                try? data.write(to: url)
            }
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("applicationDidFinishLaunching")
        buildMenu()
        buildWindow()
        log("window built")
        launchBackend()
        log("backend launched")
        showLoading()
        startPolling()
        log("polling started")
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
        // Use a non-persistent (in-memory) data store so there is no on-disk
        // cache to go stale between deployments.
        config.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        webView = DraggableWebView(frame: .zero, configuration: config)
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
        window.isMovableByWindowBackground = true
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("LocaMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Backend

    func launchBackend() {
        guard let scriptURL = Bundle.main.url(forResource: "start_services", withExtension: "sh") else {
            showError("start_services.sh not found in app bundle.")
            return
        }

        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            showError("start_services.sh not found at:\n\(scriptURL.path)")
            return
        }

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [scriptURL.path]
        // Working directory = repo root (parent of Loca.app)
        let repoRoot = Bundle.main.bundleURL.deletingLastPathComponent()
        p.currentDirectoryURL = repoRoot
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
        <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{
          background:#f5f5f0;color:#1a1a1a;
          font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display',sans-serif;
          display:flex;align-items:center;justify-content:center;height:100vh;
        }
        .card{
          text-align:center;
          background:#fff;
          border:1px solid #e5e5e0;
          border-radius:16px;
          padding:36px 44px;
          box-shadow:0 4px 24px rgba(0,0,0,.07);
          min-width:300px;
        }
        .logo{
          font-size:28px;font-weight:700;letter-spacing:-.5px;
          color:#1a1a1a;margin-bottom:24px;
        }
        .logo span{color:#5b5bd6}
        .ring{
          width:32px;height:32px;
          border:2.5px solid #e5e5e0;border-top-color:#5b5bd6;
          border-radius:50%;
          animation:spin .7s linear infinite;
          margin:0 auto 20px;
        }
        @keyframes spin{to{transform:rotate(360deg)}}
        .stage{
          font-size:14px;font-weight:500;color:#1a1a1a;
          margin-bottom:6px;min-height:20px;
        }
        .sub{font-size:12px;color:#8a8a8a;margin-bottom:20px}
        .bar-track{
          background:#efefeb;border-radius:4px;height:4px;
          overflow:hidden;margin-top:4px;
        }
        .bar-fill{
          height:100%;background:#5b5bd6;border-radius:4px;
          transition:width .5s ease;width:0%;
        }
        .log{
          font-size:11px;color:#b0b0a8;margin-top:16px;
          font-family:'SF Mono','Menlo',monospace;
          height:14px;overflow:hidden;
        }
        </style></head>
        <body><div class="card">
          <div class="logo">Lo<span>ca</span></div>
          <div class="ring"></div>
          <div class="stage" id="stage">Initialising…</div>
          <div class="sub">First launch may take a minute or two</div>
          <div class="bar-track"><div class="bar-fill" id="bar"></div></div>
          <div class="log" id="log"></div>
        </div>
        <script>
        function update(stage, progress, detail) {
          document.getElementById('stage').textContent = stage || 'Starting…';
          document.getElementById('bar').style.width  = (progress || 0) + '%';
          if (detail) document.getElementById('log').textContent = detail;
        }
        </script>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: - Polling

    func startPolling() {
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.checkReady()
            self?.checkStartupStatus()
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
                self?.log("health check passed — loading UI")
                var req = URLRequest(url: URL(string: "http://localhost:8000/")!)
                req.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
                self?.webView.load(req)
            }
        }.resume()
    }

    // MARK: - Startup status (reads /tmp/loca-startup-status.json written by start_services.sh)

    func checkStartupStatus() {
        guard !isReady else { return }
        let path = "/tmp/loca-startup-status.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stage = json["stage"] as? String else { return }
        let progress = json["progress"] as? Int ?? 0
        let detail   = json["detail"]   as? String ?? ""
        let escaped  = stage.replacingOccurrences(of: "'", with: "\\'")
        let detailEsc = detail.replacingOccurrences(of: "'", with: "\\'")
        DispatchQueue.main.async { [weak self] in
            self?.webView.evaluateJavaScript(
                "if(typeof update==='function')update('\(escaped)',\(progress),'\(detailEsc)')",
                completionHandler: nil
            )
        }
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
        log("applicationWillTerminate")
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
