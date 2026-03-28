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

/// Full-control NSTextView wrapper:
///  • Return → send,  Shift+Return → newline
///  • Native placeholder text drawn inside the view (no positioning hacks)
///  • Exposes selection-wrapping through ChatInputActions
struct ChatTextEditor: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String = "Ask anything…"
    var onSend: () -> Void
    var actions: ChatInputActions

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let tv = PlaceholderTextView()
        tv.placeholderString           = placeholder
        tv.delegate                    = context.coordinator
        tv.isRichText                  = false
        tv.font                        = .systemFont(ofSize: 14)
        tv.textContainerInset          = NSSize(width: 2, height: 7)
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled  = false
        tv.allowsUndo                  = true
        tv.drawsBackground             = false

        let sv = NSScrollView()
        sv.documentView       = tv
        sv.hasVerticalScroller = true
        sv.autohidesScrollers  = true
        sv.drawsBackground     = false
        sv.borderType          = .noBorder

        context.coordinator.textView = tv
        actions.coordinator = context.coordinator
        return sv
    }

    func updateNSView(_ sv: NSScrollView, context: Context) {
        guard let tv = sv.documentView as? PlaceholderTextView else { return }
        // Only write back when the binding changed externally (e.g. cleared after send)
        if tv.string != text {
            let sel = tv.selectedRange()
            tv.string = text
            let len = (text as NSString).length
            tv.setSelectedRange(NSRange(location: min(sel.location, len), length: 0))
        }
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: ChatTextEditor
        weak var textView: PlaceholderTextView?

        init(_ parent: ChatTextEditor) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
        }

        func textView(_ textView: NSTextView, doCommandBy sel: Selector) -> Bool {
            if sel == #selector(NSResponder.insertNewline(_:)) {
                // Shift+Return inserts a newline; plain Return sends.
                if NSEvent.modifierFlags.contains(.shift) { return false }
                parent.onSend()
                return true
            }
            return false
        }

        // MARK: Format helpers (called by InputBar toolbar buttons)

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
            // Park cursor on the blank line inside the fence
            tv.setSelectedRange(NSRange(location: range.location + 4, length: 0))
        }
    }
}

// MARK: - PlaceholderTextView

/// NSTextView that draws its own placeholder string when empty.
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
