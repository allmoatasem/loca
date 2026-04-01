#!/bin/bash
# Builds the Loca SwiftUI app (Loca-SwiftUI/ SPM package) and installs a
# fully self-contained .app bundle to ~/Applications/Loca.app.
#
# The bundle includes all Python source so it runs without the repo present.
# User data (SQLite DB, venv) lives in ~/Library/Application Support/Loca/.
#
# Requires: Xcode (not just Command Line Tools)
#   Install from the App Store, then run this script.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PKG="$DIR/Loca-SwiftUI"
BUNDLE="$DIR/Loca.app"
MACOS="$BUNDLE/Contents/MacOS"
RESOURCES="$BUNDLE/Contents/Resources"

# ── 1. Build Swift binary ─────────────────────────────────────────────────────

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
printf 'APPL????' > "$BUNDLE/Contents/PkgInfo"

# Swift binary
rm -f "$MACOS/LocalAI"
cp "$BINARY" "$MACOS/Loca"
chmod +x "$MACOS/Loca"

# Startup script
cp "$DIR/start_services.sh" "$RESOURCES/start_services.sh"
chmod +x "$RESOURCES/start_services.sh"

# Python backend — copy everything needed to run without the repo
echo "Bundling Python backend…"
rm -rf "$RESOURCES/src" "$RESOURCES/prompts"
cp -R "$DIR/src"     "$RESOURCES/src"
cp -R "$DIR/prompts" "$RESOURCES/prompts"
cp    "$DIR/requirements.txt"       "$RESOURCES/requirements.txt"
cp    "$DIR/config.yaml"            "$RESOURCES/config.yaml"
# SearXNG settings (both filenames used by different versions)
for f in searxng-settings.yml searxng-settings.yaml; do
    [ -f "$DIR/$f" ] && cp "$DIR/$f" "$RESOURCES/$f"
done
# Remove __pycache__ dirs to keep the bundle clean
find "$RESOURCES/src" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Icon
if [ -f "$DIR/Loca-SwiftUI/Sources/Loca/Assets.xcassets/AppIcon.appiconset/loca.icns" ]; then
    cp "$DIR/Loca-SwiftUI/Sources/Loca/Assets.xcassets/AppIcon.appiconset/loca.icns" "$RESOURCES/loca.icns"
elif [ -f "$RESOURCES/loca.icns" ]; then
    : # already there
fi

# ── 3. Sign ───────────────────────────────────────────────────────────────────

# Strip iCloud/Finder extended attributes that block ad-hoc signing
xattr -cr "$BUNDLE" 2>/dev/null || true

codesign --sign - --force --deep "$BUNDLE"
echo "Built and signed: $BUNDLE"

# ── 4. Sync to ~/Applications ─────────────────────────────────────────────────

DEST="$HOME/Applications/Loca.app"
echo "Installing to $DEST…"

# Full rsync so Python source, prompts, etc. are all in sync
rsync -a --delete "$BUNDLE/" "$DEST/"

codesign --sign - --force --deep "$DEST"
echo "Updated ~/Applications/Loca.app"
echo "Done — open Loca.app to launch."
