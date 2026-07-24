#!/bin/bash

# QForge - Build Script
# Creates a standalone macOS application

set -e  # Exit on error

# ─── Read version from updater.py ───────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_VERSION=$(grep '^APP_VERSION' "$SCRIPT_DIR/utils/updater.py" | sed "s/.*= *\"//;s/\".*//"  )
if [ -z "$APP_VERSION" ]; then
    echo "❌ Could not read APP_VERSION from utils/updater.py"
    exit 1
fi
echo "🚀 Building QForge v${APP_VERSION}..."

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "${RED}❌ Virtual environment not found!${NC}"
    echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Check if PyInstaller is installed
if ! pip show pyinstaller > /dev/null 2>&1; then
    echo "${BLUE}📦 Installing PyInstaller...${NC}"
    pip install pyinstaller
fi

# Clean previous builds
echo "${BLUE}🧹 Cleaning previous builds...${NC}"
rm -rf build/ dist/

# Build the application
echo "${BLUE}🔨 Building application...${NC}"
pyinstaller --clean QForge.spec

# Check if build succeeded
if [ -d "dist/QForge.app" ]; then
    echo "${GREEN}✅ Build successful!${NC}"
    echo ""
    echo "📦 Application created at: ${BLUE}dist/QForge.app${NC}"
    echo ""

    # Prune unused Qt frameworks. QForge only imports PySide6.QtCore/QtGui/
    # QtWidgets, but PySide6's PyInstaller hook copies its entire Qt/lib tree
    # regardless of Analysis(excludes=...) — that only affects Python module
    # scanning, not this hook's own binary bundling. Verified via `otool -L`
    # across every binary in the built app that nothing links against any of
    # these frameworks, so removing them is safe.
    echo "${BLUE}✂️  Pruning unused Qt frameworks...${NC}"
    QT_LIB_DIR="dist/QForge.app/Contents/Frameworks/PySide6/Qt/lib"
    PRUNED_MB=0
    for fw in QtQml QtQuick QtQuick3D QtQmlModels QtQmlMeta QtQmlWorkerScript \
              QtPdf QtPdfWidgets QtVirtualKeyboard QtVirtualKeyboardQml QtOpenGL; do
        FW_PATH="$QT_LIB_DIR/${fw}.framework"
        if [ -d "$FW_PATH" ]; then
            FW_MB=$(du -sm "$FW_PATH" | cut -f1)
            PRUNED_MB=$((PRUNED_MB + FW_MB))
            rm -rf "$FW_PATH"
        fi
        # PyInstaller also drops top-level convenience symlinks to each
        # framework's binary directly under Contents/Frameworks and
        # Contents/Resources — remove those too or they're left dangling.
        rm -f "dist/QForge.app/Contents/Frameworks/${fw}" \
              "dist/QForge.app/Contents/Resources/${fw}"
    done
    echo "   removed ~${PRUNED_MB}MB of unused Qt frameworks"

    # Re-sign after modifying bundle contents (PyInstaller ad-hoc-signs the
    # bundle during BUNDLE(); deleting files after that invalidates it).
    codesign --force --deep -s - dist/QForge.app 2>&1 | tail -5
    echo ""

    # Get app size
    APP_SIZE=$(du -sh dist/QForge.app | cut -f1)
    echo "📏 Size: ${APP_SIZE}"
    echo ""
    
    # Create DMG (optional)
    echo "${BLUE}📀 Creating DMG installer...${NC}"
    
    # Clean old DMG
    rm -f QForge.dmg
    
    # Create DMG
    hdiutil create -volname "QForge" -srcfolder dist/QForge.app -ov -format UDZO QForge.dmg
    
    if [ -f "QForge.dmg" ]; then
        DMG_SIZE=$(du -sh QForge.dmg | cut -f1)
        echo "${GREEN}✅ DMG created!${NC}"
        echo "📀 Installer: ${BLUE}QForge.dmg${NC} (${DMG_SIZE})"
    fi
    
    echo ""
    echo "${GREEN}🎉 Build complete!${NC}"
    echo ""
    echo "To distribute:"
    echo "1. ${BLUE}Share QForge.dmg${NC} with users"
    echo "2. Users drag QForge.app to Applications folder"
    echo "3. On first launch, users need to:"
    echo "   - Right-click → Open (to bypass Gatekeeper)"
    echo "   - Or run: ${BLUE}xattr -cr /Applications/QForge.app${NC}"
    echo ""
    echo "For signed distribution (no Gatekeeper warning):"
    echo "1. Get Apple Developer account ($99/year)"
    echo "2. Sign with: ${BLUE}codesign --deep --force --sign \"Developer ID\" dist/QForge.app${NC}"
    echo "3. Notarize with Apple"
    echo ""
else
    echo "${RED}❌ Build failed!${NC}"
    exit 1
fi
