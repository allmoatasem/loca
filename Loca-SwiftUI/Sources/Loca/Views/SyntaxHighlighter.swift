import SwiftUI
import AppKit

/// Lightweight, regex-driven syntax highlighter for chat code blocks.
/// Covers the languages the bundled Svelte UI already highlights so the two
/// surfaces stay visually in sync. Unknown languages fall through to plain
/// monospaced text.
enum SyntaxHighlighter {

    enum TokenKind {
        case keyword, string, number, comment, type

        var color: NSColor {
            switch self {
            case .keyword: return .systemPurple
            case .string:  return .systemGreen
            case .number:  return .systemOrange
            case .comment: return .secondaryLabelColor
            case .type:    return .systemTeal
            }
        }
    }

    struct Rule {
        let pattern: String
        let kind: TokenKind
    }

    static func highlight(_ code: String, language: String?) -> AttributedString {
        let base = AttributedString(code)
        guard let rules = rules(for: language?.lowercased()) else { return base }

        let ns = NSMutableAttributedString(string: code)
        let fullRange = NSRange(location: 0, length: (code as NSString).length)
        ns.addAttribute(.foregroundColor, value: NSColor.labelColor, range: fullRange)

        var claimed: [NSRange] = []
        for rule in rules {
            guard let regex = try? NSRegularExpression(pattern: rule.pattern, options: []) else { continue }
            regex.enumerateMatches(in: code, options: [], range: fullRange) { match, _, _ in
                guard let m = match else { return }
                let r = m.range
                for c in claimed where NSIntersectionRange(c, r).length > 0 { return }
                ns.addAttribute(.foregroundColor, value: rule.kind.color, range: r)
                claimed.append(r)
            }
        }

        return (try? AttributedString(ns, including: \.appKit)) ?? base
    }

    // MARK: - Language rules

    private static func rules(for language: String?) -> [Rule]? {
        switch language {
        case "swift": return swift
        case "python", "py": return python
        case "javascript", "js", "jsx", "typescript", "ts", "tsx": return javascript
        case "bash", "sh", "shell", "zsh": return bash
        case "json": return json
        case "yaml", "yml": return yaml
        case "rust", "rs": return rust
        case "go": return go
        case "java", "kotlin", "kt": return java
        case "c", "cpp", "c++", "objc", "objective-c": return cFamily
        case "css", "scss": return css
        case "sql": return sql
        case "html", "xml", "markup": return markup
        default: return nil
        }
    }

    private static func base(lineComment: String?,
                             blockComment: (String, String)? = nil,
                             extraString: [String] = [],
                             keywords: [String],
                             typePattern: String = "\\b[A-Z][A-Za-z0-9_]*\\b") -> [Rule] {
        var rules: [Rule] = []
        if let open = blockComment {
            let o = NSRegularExpression.escapedPattern(for: open.0)
            let c = NSRegularExpression.escapedPattern(for: open.1)
            rules.append(Rule(pattern: "\(o)[\\s\\S]*?\(c)", kind: .comment))
        }
        if let lc = lineComment {
            let esc = NSRegularExpression.escapedPattern(for: lc)
            rules.append(Rule(pattern: "\(esc)[^\\n]*", kind: .comment))
        }
        for extra in extraString {
            rules.append(Rule(pattern: extra, kind: .string))
        }
        // Standard double- and single-quoted strings with escape support.
        rules.append(Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"", kind: .string))
        rules.append(Rule(pattern: "'(?:[^'\\\\\\n]|\\\\.)*'", kind: .string))
        rules.append(Rule(pattern: "\\b\\d+(?:\\.\\d+)?\\b", kind: .number))
        let kw = keywords.map { NSRegularExpression.escapedPattern(for: $0) }.joined(separator: "|")
        rules.append(Rule(pattern: "\\b(?:\(kw))\\b", kind: .keyword))
        rules.append(Rule(pattern: typePattern, kind: .type))
        return rules
    }

    // swiftlint:disable line_length
    private static let swift: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        extraString: ["\"\"\"[\\s\\S]*?\"\"\""],
        keywords: [
            "associatedtype", "class", "deinit", "enum", "extension", "fileprivate", "func",
            "import", "init", "inout", "internal", "let", "open", "operator", "private",
            "protocol", "public", "rethrows", "static", "struct", "subscript", "typealias",
            "var", "break", "case", "continue", "default", "defer", "do", "else", "fallthrough",
            "for", "guard", "if", "in", "repeat", "return", "switch", "where", "while",
            "as", "Any", "catch", "false", "is", "nil", "self", "Self", "super", "throw",
            "throws", "true", "try", "async", "await", "actor", "some", "any",
        ]
    )

    private static let python: [Rule] = base(
        lineComment: "#",
        extraString: [
            "\"\"\"[\\s\\S]*?\"\"\"",
            "'''[\\s\\S]*?'''",
        ],
        keywords: [
            "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
            "del", "elif", "else", "except", "finally", "for", "from", "global", "if",
            "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
            "return", "try", "while", "with", "yield", "False", "None", "True", "match", "case",
        ]
    )

    private static let javascript: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        extraString: ["`(?:[^`\\\\]|\\\\.)*`"],
        keywords: [
            "break", "case", "catch", "class", "const", "continue", "debugger", "default",
            "delete", "do", "else", "export", "extends", "finally", "for", "function", "if",
            "import", "in", "instanceof", "let", "new", "of", "return", "super", "switch",
            "this", "throw", "try", "typeof", "var", "void", "while", "with", "yield",
            "async", "await", "static", "from", "as",
            "true", "false", "null", "undefined",
            "interface", "type", "enum", "implements", "namespace", "declare", "readonly",
            "abstract", "public", "private", "protected", "any", "number", "string", "boolean",
            "never", "unknown", "object", "keyof",
        ]
    )

    private static let bash: [Rule] = base(
        lineComment: "#",
        keywords: [
            "if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while",
            "until", "case", "esac", "function", "return", "break", "continue", "exit",
            "export", "local", "readonly", "source", "alias", "unset", "echo", "printf",
            "read", "test", "true", "false",
        ]
    )

    private static let json: [Rule] = [
        Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"\\s*:", kind: .keyword),
        Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"", kind: .string),
        Rule(pattern: "\\b-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b", kind: .number),
        Rule(pattern: "\\b(?:true|false|null)\\b", kind: .keyword),
    ]

    private static let yaml: [Rule] = [
        Rule(pattern: "#[^\\n]*", kind: .comment),
        Rule(pattern: "^[\\s-]*[A-Za-z_][A-Za-z0-9_-]*(?=\\s*:)",
             kind: .keyword),
        Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"", kind: .string),
        Rule(pattern: "'(?:[^'\\\\\\n]|\\\\.)*'", kind: .string),
        Rule(pattern: "\\b\\d+(?:\\.\\d+)?\\b", kind: .number),
        Rule(pattern: "\\b(?:true|false|null|yes|no|on|off)\\b", kind: .keyword),
    ]

    private static let rust: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        keywords: [
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else",
            "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop",
            "match", "mod", "move", "mut", "pub", "ref", "return", "Self", "self", "static",
            "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while",
        ]
    )

    private static let go: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        extraString: ["`[^`]*`"],
        keywords: [
            "break", "case", "chan", "const", "continue", "default", "defer", "else",
            "fallthrough", "for", "func", "go", "goto", "if", "import", "interface", "map",
            "package", "range", "return", "select", "struct", "switch", "type", "var",
            "true", "false", "nil",
        ]
    )

    private static let java: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        keywords: [
            "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
            "class", "const", "continue", "default", "do", "double", "else", "enum",
            "extends", "final", "finally", "float", "for", "goto", "if", "implements",
            "import", "instanceof", "int", "interface", "long", "native", "new", "package",
            "private", "protected", "public", "return", "short", "static", "strictfp",
            "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try",
            "void", "volatile", "while", "true", "false", "null",
            "fun", "val", "var", "when", "object", "data", "sealed", "suspend",
        ]
    )

    private static let cFamily: [Rule] = base(
        lineComment: "//",
        blockComment: ("/*", "*/"),
        keywords: [
            "auto", "break", "case", "char", "const", "continue", "default", "do", "double",
            "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long",
            "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct",
            "switch", "typedef", "union", "unsigned", "void", "volatile", "while",
            "bool", "class", "delete", "explicit", "false", "friend", "namespace", "new",
            "nullptr", "operator", "private", "protected", "public", "template", "this",
            "throw", "true", "try", "typename", "using", "virtual", "catch", "override",
            "final", "constexpr", "decltype", "noexcept", "mutable",
        ]
    )

    private static let css: [Rule] = [
        Rule(pattern: "/\\*[\\s\\S]*?\\*/", kind: .comment),
        Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"", kind: .string),
        Rule(pattern: "'(?:[^'\\\\\\n]|\\\\.)*'", kind: .string),
        Rule(pattern: "#[0-9a-fA-F]{3,8}\\b", kind: .number),
        Rule(pattern: "\\b\\d+(?:\\.\\d+)?(?:%|px|em|rem|vh|vw|s|ms|deg)?\\b", kind: .number),
        Rule(pattern: "[.#][A-Za-z_][A-Za-z0-9_-]*", kind: .type),
        Rule(pattern: "[A-Za-z-]+(?=\\s*:)", kind: .keyword),
    ]

    private static let sql: [Rule] = base(
        lineComment: "--",
        blockComment: ("/*", "*/"),
        keywords: [
            "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
            "DELETE", "CREATE", "TABLE", "INDEX", "VIEW", "DROP", "ALTER", "ADD", "COLUMN",
            "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "ON", "USING", "GROUP", "BY",
            "ORDER", "ASC", "DESC", "LIMIT", "OFFSET", "HAVING", "UNION", "ALL", "DISTINCT",
            "AS", "AND", "OR", "NOT", "NULL", "IS", "IN", "LIKE", "BETWEEN", "CASE", "WHEN",
            "THEN", "ELSE", "END", "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "UNIQUE",
            "DEFAULT", "CHECK", "CONSTRAINT", "AUTOINCREMENT", "IF", "EXISTS", "WITH",
            "select", "from", "where", "insert", "into", "values", "update", "set",
            "delete", "create", "table", "index", "view", "drop", "alter", "add", "column",
            "join", "left", "right", "inner", "outer", "full", "on", "using", "group", "by",
            "order", "asc", "desc", "limit", "offset", "having", "union", "all", "distinct",
            "as", "and", "or", "not", "null", "is", "in", "like", "between", "case", "when",
            "then", "else", "end", "primary", "key", "foreign", "references", "unique",
            "default", "check", "constraint", "if", "exists", "with",
        ]
    )

    private static let markup: [Rule] = [
        Rule(pattern: "<!--[\\s\\S]*?-->", kind: .comment),
        Rule(pattern: "\"(?:[^\"\\\\\\n]|\\\\.)*\"", kind: .string),
        Rule(pattern: "'(?:[^'\\\\\\n]|\\\\.)*'", kind: .string),
        Rule(pattern: "</?[A-Za-z][A-Za-z0-9-]*", kind: .keyword),
        Rule(pattern: "[A-Za-z-]+(?==)", kind: .type),
    ]
    // swiftlint:enable line_length
}
