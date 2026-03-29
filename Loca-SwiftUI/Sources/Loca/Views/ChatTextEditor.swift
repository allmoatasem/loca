import SwiftUI
import AppKit

// MARK: - Shared actions

/// Held by InputBar; handed to ChatTextEditor so format buttons can reach the NSTextView.
final class ChatInputActions: ObservableObject {
    weak var coordinator: ChatTextEditor.Coordinator?

    func wrap(_ marker: String) { coordinator?.wrapSelection(marker) }
    func codeBlock()             { coordinator?.insertCodeBlock() }
}

// MARK: - ChatTextEditor

/// Full-control NSTextView wrapper.
///  • Return → send,  Shift+Return → newline
///  • Native placeholder text drawn inside the view
///  • Exposes selection-wrapping through ChatInputActions
///  • Live syntax highlighting for markdown markers in the input field
struct ChatTextEditor: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String = "Ask anything…"
    var onSend: () -> Void
    var actions: ChatInputActions

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let tv = PlaceholderTextView()
        tv.placeholderString = placeholder
        tv.delegate          = context.coordinator
        tv.isRichText        = false
        tv.font              = .systemFont(ofSize: 14)
        tv.textContainerInset = NSSize(width: 2, height: 7)
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled  = false
        tv.allowsUndo        = true
        tv.drawsBackground   = false

        // ── Critical: NSTextView must be told to fill and wrap inside its scroll view ──
        tv.isVerticallyResizable   = true
        tv.isHorizontallyResizable = false
        tv.autoresizingMask        = .width
        tv.minSize = NSSize(width: 0, height: 0)
        tv.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        tv.textContainer?.widthTracksTextView = true
        tv.textContainer?.containerSize = NSSize(
            width: CGFloat.greatestFiniteMagnitude,
            height: CGFloat.greatestFiniteMagnitude
        )

        let sv = NSScrollView()
        sv.hasVerticalScroller = true
        sv.autohidesScrollers  = true
        sv.drawsBackground     = false
        sv.borderType          = .noBorder
        sv.documentView        = tv

        context.coordinator.textView = tv
        actions.coordinator = context.coordinator
        return sv
    }

    func updateNSView(_ sv: NSScrollView, context: Context) {
        guard let tv = sv.documentView as? PlaceholderTextView else { return }
        if tv.string != text {
            let sel = tv.selectedRange()
            tv.string = text
            let len = (text as NSString).length
            tv.setSelectedRange(NSRange(location: min(sel.location, len), length: 0))
            context.coordinator.applyHighlights(to: tv)
        }
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: ChatTextEditor
        weak var textView: PlaceholderTextView?
        private var isHighlighting = false

        init(_ parent: ChatTextEditor) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
            applyHighlights(to: tv)
        }

        func textView(_ tv: NSTextView, doCommandBy sel: Selector) -> Bool {
            if sel == #selector(NSResponder.insertNewline(_:)) {
                if NSEvent.modifierFlags.contains(.shift) { return false }
                parent.onSend()
                return true
            }
            return false
        }

        // MARK: Syntax highlighting

        func applyHighlights(to tv: NSTextView) {
            guard !isHighlighting, let ts = tv.textStorage else { return }
            isHighlighting = true
            defer { isHighlighting = false }

            let str = ts.string
            let full = NSRange(location: 0, length: (str as NSString).length)
            guard full.length > 0 else { return }

            let savedSel = tv.selectedRange()

            ts.beginEditing()

            // Reset to base styling
            let baseFont   = NSFont.systemFont(ofSize: 14)
            let labelColor = NSColor.labelColor
            ts.addAttribute(.font, value: baseFont, range: full)
            ts.addAttribute(.foregroundColor, value: labelColor, range: full)
            ts.removeAttribute(.backgroundColor, range: full)

            // Fenced code blocks  ```...```
            if let re = try? NSRegularExpression(pattern: "```[^`]*```", options: [.dotMatchesLineSeparators]) {
                re.enumerateMatches(in: str, range: full) { m, _, _ in
                    guard let r = m?.range, r.location != NSNotFound else { return }
                    ts.addAttribute(.font, value: NSFont.monospacedSystemFont(ofSize: 13, weight: .regular), range: r)
                    ts.addAttribute(.foregroundColor, value: NSColor.systemTeal, range: r)
                    ts.addAttribute(.backgroundColor, value: NSColor.tertiaryLabelColor.withAlphaComponent(0.08), range: r)
                }
            }

            // Inline code  `...`  (single backtick, same line)
            if let re = try? NSRegularExpression(pattern: "`[^`\n]+`") {
                re.enumerateMatches(in: str, range: full) { m, _, _ in
                    guard let r = m?.range, r.location != NSNotFound else { return }
                    ts.addAttribute(.font, value: NSFont.monospacedSystemFont(ofSize: 13, weight: .regular), range: r)
                    ts.addAttribute(.foregroundColor, value: NSColor.systemTeal, range: r)
                }
            }

            // Bold  **...**
            if let re = try? NSRegularExpression(pattern: "\\*\\*[^*\n]+\\*\\*") {
                re.enumerateMatches(in: str, range: full) { m, _, _ in
                    guard let r = m?.range, r.location != NSNotFound else { return }
                    ts.addAttribute(.font, value: NSFont.boldSystemFont(ofSize: 14), range: r)
                }
            }

            // Italic  _..._  or  *...*  (single, not adjacent to another * or _)
            if let re = try? NSRegularExpression(pattern: "(?<![*_])([*_])[^*_\n]+\\1(?![*_])") {
                re.enumerateMatches(in: str, range: full) { m, _, _ in
                    guard let r = m?.range, r.location != NSNotFound else { return }
                    let desc = NSFont.systemFont(ofSize: 14).fontDescriptor.withSymbolicTraits(.italic)
                    if let font = NSFont(descriptor: desc, size: 14) {
                        ts.addAttribute(.font, value: font, range: r)
                    }
                }
            }

            ts.endEditing()

            // Restore cursor (attribute editing can move selection in some versions)
            let safeLen = (tv.string as NSString).length
            let safeLoc = min(savedSel.location, safeLen)
            let safeLen2 = min(savedSel.length, safeLen - safeLoc)
            tv.setSelectedRange(NSRange(location: safeLoc, length: safeLen2))
        }

        // MARK: Format helpers

        func wrapSelection(_ marker: String) {
            guard let tv = textView else { return }
            let range = tv.selectedRange()
            if range.length > 0 {
                let selected = (tv.string as NSString).substring(with: range)
                tv.insertText("\(marker)\(selected)\(marker)", replacementRange: range)
            } else {
                tv.insertText("\(marker)\(marker)", replacementRange: range)
                tv.setSelectedRange(NSRange(location: range.location + marker.count, length: 0))
            }
        }

        func insertCodeBlock() {
            guard let tv = textView else { return }
            let range = tv.selectedRange()
            tv.insertText("```\n\n```", replacementRange: range)
            tv.setSelectedRange(NSRange(location: range.location + 4, length: 0))
        }
    }
}

// MARK: - PlaceholderTextView

final class PlaceholderTextView: NSTextView {
    var placeholderString: String = "" { didSet { needsDisplay = true } }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard string.isEmpty else { return }
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: NSColor.placeholderTextColor,
            .font: font ?? NSFont.systemFont(ofSize: NSFont.systemFontSize),
        ]
        let inset   = textContainerInset
        let padding = textContainer?.lineFragmentPadding ?? 5
        (placeholderString as NSString).draw(
            at: NSPoint(x: inset.width + padding, y: inset.height),
            withAttributes: attrs
        )
    }
}
