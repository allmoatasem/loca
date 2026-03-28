#!/bin/bash
# Builds the Loca SwiftUI app (Loca-SwiftUI/ SPM package) and installs it.
# Run once after cloning, and again whenever Swift source files change.
#
# Requires: Xcode (not just Command Line Tools)
#   Install from the App Store, then run this script.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PKG="$DIR/Loca-SwiftUI"
BUNDLE="$DIR/Loca.app"
MACOS="$BUNDLE/Contents/MacOS"
RESOURCES="$BUNDLE/Contents/Resources"

# ── 1. Build ──────────────────────────────────────────────────────────────────

if [ -x "/Applications/Xcode.app/Contents/Developer/usr/bin/swift" ]; then
    SWIFT="/Applications/Xcode.app/Contents/Developer/usr/bin/swift"
elif command -v swift &>/dev/null; then
    SWIFT="swift"
else
    echo "Error: swift not found. Install Xcode from the App Store."
    exit 1
fi

echo "Building Loca (release)…"
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
"$SWIFT" build -c release --package-path "$PKG"

BINARY="$PKG/.build/release/Loca"
if [ ! -f "$BINARY" ]; then
    echo "Error: build succeeded but binary not found at $BINARY"
    exit 1
fi

# ── 2. Assemble bundle ────────────────────────────────────────────────────────

mkdir -p "$MACOS" "$RESOURCES"

# Remove old AppKit binary if it's still there
rm -f "$MACOS/LocalAI"

cp "$BINARY" "$MACOS/Loca"
chmod +x "$MACOS/Loca"

cp "$DIR/start_services.sh" "$RESOURCES/start_services.sh"
chmod +x "$RESOURCES/start_services.sh"

# ── 3. Sign ───────────────────────────────────────────────────────────────────

codesign --sign - --force --deep "$BUNDLE"
echo "Built and signed: $BUNDLE"

# ── 4. Sync to ~/Applications ─────────────────────────────────────────────────

DEST="$HOME/Applications/Loca.app"
mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources"

cp "$MACOS/Loca"                       "$DEST/Contents/MacOS/Loca"
cp "$BUNDLE/Contents/Info.plist"       "$DEST/Contents/Info.plist"
cp "$RESOURCES/start_services.sh"      "$DEST/Contents/Resources/start_services.sh"
chmod +x "$DEST/Contents/MacOS/Loca" "$DEST/Contents/Resources/start_services.sh"

# Copy icon if present
if [ -f "$RESOURCES/loca.icns" ]; then
    cp "$RESOURCES/loca.icns" "$DEST/Contents/Resources/loca.icns"
fi

# Write project path so AppDelegate can find start_services.sh at runtime
echo "$DIR" > "$DEST/Contents/Resources/project_path.txt"

codesign --sign - --force --deep "$DEST"
echo "Updated ~/Applications/Loca.app"
echo "Done — open Loca.app to launch."
