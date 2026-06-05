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

# Create a temporary directory for the DMG contents
DMG_DIR=$(mktemp -d)
cp -R dist/Anduin.app "${DMG_DIR}/"
ln -s /Applications "${DMG_DIR}/Applications"

hdiutil create -volname "Anduin" \
    -srcfolder "${DMG_DIR}" \
    -ov -format UDZO \
    "${DMG}"

rm -rf "${DMG_DIR}"
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
