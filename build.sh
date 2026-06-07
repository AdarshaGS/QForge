#!/bin/bash

# QForge - Build Script
# Creates a standalone macOS application

set -e  # Exit on error

echo "🚀 Building QForge..."

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
rm -rf build/ dist/ *.spec

# Create PyInstaller spec file
echo "${BLUE}📝 Creating build configuration...${NC}"
cat > QForge.spec << 'EOF'
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui/*.py', 'ui'),
        ('services/*.py', 'services'),
        ('utils/*.py', 'utils'),
    ],
    hiddenimports=[
        'pymysql',
        'psycopg2',
        'pandas',
        'numpy',
        'sqlparse',
        'sshtunnel',
        'paramiko',
        'openpyxl',
        'pyarrow',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QForge',
)

app = BUNDLE(
    coll,
    name='QForge.app',
    icon=None,
    bundle_identifier='com.qforge.app',
    version='1.0.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '10.13.0',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
    },
)
EOF

# Build the application
echo "${BLUE}🔨 Building application...${NC}"
pyinstaller --clean QForge.spec

# Check if build succeeded
if [ -d "dist/QForge.app" ]; then
    echo "${GREEN}✅ Build successful!${NC}"
    echo ""
    echo "📦 Application created at: ${BLUE}dist/QForge.app${NC}"
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
