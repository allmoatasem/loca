#!/bin/bash
# Compiles LocalAI.swift into the Loca.app binary.
# Run once after cloning, and again whenever LocalAI.swift changes.
#
# Requires: Xcode (not just Command Line Tools)
#   Install from the App Store, then run this script.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/LocalAI.swift"
OUT="$DIR/Loca.app/Contents/MacOS/LocalAI"

# Prefer full Xcode toolchain if available; fall back to xcode-select default
if [ -x "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc" ]; then
    SWIFTC="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
elif command -v swiftc &>/dev/null; then
    SWIFTC="swiftc"
else
    echo "Error: swiftc not found. Install Xcode from the App Store."
    exit 1
fi

SDK=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk

echo "Compiling Loca.app…"
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
SDKROOT="$SDK" \
"$SWIFTC" "$SRC" \
    -o "$OUT" \
    -framework Cocoa \
    -framework WebKit \
    -O

# Verify the output is actually a binary (not left as the old shell script)
if file "$OUT" | grep -q "shell script"; then
    echo "Error: compilation produced no binary. Check for Swift errors above."
    exit 1
fi

chmod +x "$OUT"

# Copy services script into the bundle's Resources/
RESOURCES="$DIR/Loca.app/Contents/Resources"
mkdir -p "$RESOURCES"
cp "$DIR/start_services.sh" "$RESOURCES/start_services.sh"
chmod +x "$RESOURCES/start_services.sh"

# Ad-hoc code-sign the binary — required on Apple Silicon
codesign --sign - --force "$OUT"

echo "Done — open Loca.app to launch."
