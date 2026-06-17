#!/usr/bin/env bash
# Build Anduin.app for macOS using PyInstaller, then package as .dmg and .tar.gz
#
# Usage:  ./scripts/build_macos.sh
# Output: dist/Anduin.app, dist/Anduin-VERSION-macos.dmg, dist/Anduin-VERSION-macos.tar.gz
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=$(python -c "from anduin import __version__; print(__version__)")
echo "Building Anduin v${VERSION} for macOS..."

# ── 1. Clean previous build ──────────────────────────────────────────────────
rm -rf build dist

# ── 2. PyInstaller ───────────────────────────────────────────────────────────
echo "Running PyInstaller..."
pyinstaller anduin.spec --noconfirm

if [ ! -d "dist/Anduin.app" ]; then
    echo "ERROR: dist/Anduin.app not found"
    exit 1
fi
echo "Built dist/Anduin.app"

# ── 3. Create .tar.gz for updates ────────────────────────────────────────────
echo "Creating update archive..."
TARBALL="dist/Anduin-${VERSION}-macos.tar.gz"
tar -czf "${TARBALL}" -C dist Anduin.app
echo "Created ${TARBALL}"

# ── 4. Create .dmg for initial install ───────────────────────────────────────
echo "Creating DMG..."
DMG="dist/Anduin-${VERSION}-macos.dmg"
DMG_RW="dist/_anduin_rw.dmg"

# Create a temporary directory for the DMG contents
DMG_DIR=$(mktemp -d)
cp -R dist/Anduin.app "${DMG_DIR}/"
ln -s /Applications "${DMG_DIR}/Applications"

# Create a read-write DMG first so we can set Finder view options
hdiutil create -volname "Anduin" \
    -srcfolder "${DMG_DIR}" \
    -ov -format UDRW \
    "${DMG_RW}"
rm -rf "${DMG_DIR}"

# Mount the read-write DMG and configure the Finder window
MOUNT_DIR=$(hdiutil attach "${DMG_RW}" -readwrite -noverify | grep "/Volumes/Anduin" | tail -1 | awk '{print $NF}')
# Wait for mount
sleep 1

osascript <<APPLESCRIPT
tell application "Finder"
    tell disk "Anduin"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set bounds of container window to {200, 120, 680, 400}
        set theViewOptions to icon view options of container window
        set arrangement of theViewOptions to not arranged
        set icon size of theViewOptions to 80
        set position of item "Anduin.app" of container window to {120, 140}
        set position of item "Applications" of container window to {360, 140}
        close
    end tell
end tell
APPLESCRIPT

# Ensure writes are flushed
sync
hdiutil detach "${MOUNT_DIR}" -quiet

# Convert to compressed read-only DMG
hdiutil convert "${DMG_RW}" -format UDZO -o "${DMG}"
rm -f "${DMG_RW}"
echo "Created ${DMG}"

# ── 5. Generate checksums ────────────────────────────────────────────────────
echo "Checksums:"
TARBALL_SHA=$(shasum -a 256 "${TARBALL}" | cut -d' ' -f1)
DMG_SHA=$(shasum -a 256 "${DMG}" | cut -d' ' -f1)
echo "  ${TARBALL}: ${TARBALL_SHA}"
echo "  ${DMG}: ${DMG_SHA}"

# ── 6. Generate latest.json ─────────────────────────────────────────────────
# This file gets uploaded as a release asset for the updater to fetch
cat > dist/latest.json <<EOF
{
    "version": "${VERSION}",
    "macos": {
        "url": "https://github.com/zacharias-bm/anduin/releases/download/v${VERSION}/Anduin-${VERSION}-macos.tar.gz",
        "sha256": "${TARBALL_SHA}"
    }
}
EOF
echo "Created dist/latest.json"
echo ""
echo "Done! Upload these to a GitHub Release:"
echo "  dist/Anduin-${VERSION}-macos.dmg"
echo "  dist/Anduin-${VERSION}-macos.tar.gz"
echo "  dist/latest.json"
