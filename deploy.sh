#!/bin/bash
# =============================================================================
# QForge — Full Deployment Script
# Builds the app, packages a DMG, creates a GitHub release, and updates
# the Homebrew tap formula so users can install with:
#   brew tap AdarshaGS/qforge && brew install --cask qforge
# =============================================================================

set -e

# ─── Configuration ────────────────────────────────────────────────────────────
GITHUB_USER="AdarshaGS"
GITHUB_REPO="QForge"
TAP_REPO="homebrew-qforge"                # must start with homebrew-
APP_NAME="QForge"
APP_NAME_LOWER="$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]')"
BUNDLE_ID="com.qforge.app"
DMG_NAME="QForge.dmg"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Tap repo: use the sibling directory of the main repo
TAP_DIR="$(dirname "$REPO_DIR")/${TAP_REPO}"

# Read version from first argument, or prompt
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    read -rp "Enter release version (e.g. 1.0.1): " VERSION
fi
TAG="v${VERSION}"

# ─── Colors ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

step()  { echo -e "\n${BOLD}${BLUE}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✔ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
die()   { echo -e "${RED}✘ $1${NC}"; exit 1; }

# =============================================================================
# STEP 1 — Preflight checks
# =============================================================================
step "[1/7] Preflight checks"

command -v python3  >/dev/null || die "python3 not found"
command -v hdiutil  >/dev/null || die "hdiutil not found (macOS only)"
command -v gh       >/dev/null || die "GitHub CLI (gh) not found — install with: brew install gh"
gh auth status      >/dev/null 2>&1 || die "Not logged in to GitHub CLI — run: gh auth login"

GH_USER=$(gh api user --jq .login 2>/dev/null)
echo "  Logged in as: ${GH_USER}"
if [ "$GH_USER" != "$GITHUB_USER" ]; then
    die "GitHub CLI is logged in as '${GH_USER}' but repo owner is '${GITHUB_USER}'. Run: gh auth login"
fi

# Configure git to use gh's token so all git pushes work automatically
gh auth setup-git

ok "All tools available"

# =============================================================================
# STEP 2 — Build the app with PyInstaller
# =============================================================================
step "[2/7] Building QForge.app with PyInstaller"

cd "$REPO_DIR"

# Ensure venv exists and is healthy
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install/upgrade build deps quietly
pip install --quiet --upgrade pip
pip install --quiet pyinstaller
pip install --quiet -r requirements.txt

# Clean previous artifacts
rm -rf build/ dist/ QForge.spec

# Generate PyInstaller spec
cat > QForge.spec << 'SPEC_EOF'
# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui/*.py',       'ui'),
        ('services/*.py', 'services'),
        ('utils/*.py',    'utils'),
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
        # Lazily-imported ui modules (inside methods — static analyser misses these)
        'ui.snippet_editor_dialog',
        'ui.snippet_manager',
        'ui.code_editor',
        'ui.connection_panel',
        'ui.db_switcher_dialog',
        'ui.query_history_dialog',
        'ui.quick_search_dialog',
        'ui.structure_editor',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='QForge',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='QForge',
)

app = BUNDLE(
    coll,
    name='QForge.app',
    icon=None,
    bundle_identifier='com.qforge.app',
    version='VERSION_PLACEHOLDER',
    info_plist={
        'NSPrincipalClass':        'NSApplication',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion':  '10.13.0',
        'CFBundleShortVersionString': 'VERSION_PLACEHOLDER',
        'CFBundleVersion':            'VERSION_PLACEHOLDER',
    },
)
SPEC_EOF

# Inject the actual version
sed -i '' "s/VERSION_PLACEHOLDER/${VERSION}/g" QForge.spec

# Run PyInstaller
pyinstaller QForge.spec --noconfirm --clean 2>&1 | tail -20

[ -d "dist/QForge.app" ] || die "PyInstaller did not produce QForge.app"
ok "QForge.app built → dist/QForge.app"

# =============================================================================
# STEP 3 — Package into a DMG
# =============================================================================
step "[3/7] Packaging DMG"

DMG_PATH="$REPO_DIR/$DMG_NAME"
TMP_DMG_DIR="$REPO_DIR/.dmg_tmp"

rm -f "$DMG_PATH"
rm -rf "$TMP_DMG_DIR"
mkdir -p "$TMP_DMG_DIR"

# Copy app into staging folder
cp -r "dist/QForge.app" "$TMP_DMG_DIR/"

# Create a symlink to /Applications so users can drag-and-drop
ln -s /Applications "$TMP_DMG_DIR/Applications"

# Build a read/write image first, then convert to compressed read-only
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov \
    -format UDRW \
    -fs HFS+ \
    "$REPO_DIR/.tmp_rw.dmg" >/dev/null

hdiutil convert \
    "$REPO_DIR/.tmp_rw.dmg" \
    -format UDZO \
    -o "$DMG_PATH" >/dev/null

rm -f "$REPO_DIR/.tmp_rw.dmg"
rm -rf "$TMP_DMG_DIR"

ok "DMG created → $DMG_PATH"

# =============================================================================
# STEP 4 — Compute SHA256 for Homebrew
# =============================================================================
step "[4/7] Computing SHA256 checksum"

SHA256=$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')
ok "SHA256: $SHA256"

# =============================================================================
# STEP 5 — Create GitHub release and upload DMG
# =============================================================================
step "[5/7] Creating GitHub release $TAG and uploading DMG"

RELEASE_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/download/${TAG}/${DMG_NAME}"

# Delete existing release/tag if it exists (re-deploy scenario)
if gh release view "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" >/dev/null 2>&1; then
    warn "Release $TAG already exists — deleting and recreating"
    gh release delete "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" --yes
    git tag -d "$TAG" 2>/dev/null || true
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
fi

gh release create "$TAG" \
    --repo "${GITHUB_USER}/${GITHUB_REPO}" \
    --title "QForge ${TAG} — Free Database Client" \
    --notes "## QForge ${TAG}

Professional database client — free alternative to TablePlus.

### Install via Homebrew
\`\`\`bash
brew tap ${GITHUB_USER}/${APP_NAME_LOWER}
brew install --cask qforge
\`\`\`

### Manual install
Download \`${DMG_NAME}\` below, open it, and drag QForge to Applications.

### What's new
- See FEATURE_ROADMAP.md for details

### Supported databases
MySQL · PostgreSQL · SQLite · SSH tunnels" \
    "$DMG_PATH"

ok "Release live → https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"

# =============================================================================
# STEP 6 — Update Homebrew tap formula
# =============================================================================
step "[6/7] Updating Homebrew tap formula"

# Clone tap repo if not present, otherwise pull latest
if [ ! -d "$TAP_DIR/.git" ]; then
    echo "  Cloning ${GITHUB_USER}/${TAP_REPO}..."
    git clone "https://github.com/${GITHUB_USER}/${TAP_REPO}.git" "$TAP_DIR"
fi

cd "$TAP_DIR"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git pull --rebase origin "$BRANCH"

# Create Casks/ folder first, then write the formula
mkdir -p "$TAP_DIR/Casks"

cat > "$TAP_DIR/Casks/qforge.rb" << FORMULA_EOF
cask "qforge" do
  version "${VERSION}"
  sha256 "${SHA256}"

  url "https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/download/v#{version}/${DMG_NAME}"
  name "${APP_NAME}"
  desc "Professional database client — free alternative to TablePlus"
  homepage "https://github.com/${GITHUB_USER}/${GITHUB_REPO}"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "${APP_NAME}.app"

  zap trash: [
    "~/Library/Application Support/${APP_NAME}",
    "~/Library/Preferences/${BUNDLE_ID}.plist",
    "~/Library/Saved Application State/${BUNDLE_ID}.savedState",
  ]

  caveats <<~EOS
    Launch QForge from Applications, or run:
      open -a ${APP_NAME}

    Docs & source: https://github.com/${GITHUB_USER}/${GITHUB_REPO}
  EOS
end
FORMULA_EOF

# Push via GitHub API — works regardless of local git credential config.
# gh is already verified as the repo owner in Step 1.
FORMULA_CONTENT=$(base64 < "$TAP_DIR/Casks/qforge.rb")
EXISTING_SHA=$(gh api "repos/${GITHUB_USER}/${TAP_REPO}/contents/Casks/qforge.rb" \
    --jq '.sha' 2>/dev/null || echo "")

if [ -n "$EXISTING_SHA" ]; then
    # File exists — update it
    gh api --method PUT "repos/${GITHUB_USER}/${TAP_REPO}/contents/Casks/qforge.rb" \
        -f message="qforge ${TAG}: update to ${VERSION} (sha256 ${SHA256:0:12}...)" \
        -f content="$FORMULA_CONTENT" \
        -f sha="$EXISTING_SHA" \
        --jq '.commit.sha' | xargs -I{} echo "  Committed: {}"
else
    # File doesn't exist yet — create it
    gh api --method PUT "repos/${GITHUB_USER}/${TAP_REPO}/contents/Casks/qforge.rb" \
        -f message="qforge ${TAG}: initial formula ${VERSION}" \
        -f content="$FORMULA_CONTENT" \
        --jq '.commit.sha' | xargs -I{} echo "  Committed: {}"
fi

ok "Tap formula pushed → https://github.com/${GITHUB_USER}/${TAP_REPO}/blob/master/Casks/qforge.rb"

# =============================================================================
# STEP 7 — Sync Homebrew tap and smoke-test
# =============================================================================
step "[7/7] Syncing Homebrew tap and smoke-testing"

# Clear any cached DMG so Homebrew re-downloads and re-verifies the fresh one
rm -f "$(brew --cache)/downloads/"*"--${DMG_NAME}" 2>/dev/null || true

# Force-pull the pushed formula into Homebrew's tap cache.
# 'brew update' is sometimes delayed — this guarantees Homebrew sees the
# formula that was just pushed to GitHub.
TAP_SLUG="$(echo "${GITHUB_USER}" | tr '[:upper:]' '[:lower:]')/${TAP_REPO}"
TAP_CACHE="$(brew --repository)/Library/Taps/${TAP_SLUG}"

if [ -d "$TAP_CACHE/.git" ]; then
    git -C "$TAP_CACHE" fetch origin
    CACHE_BRANCH=$(git -C "$TAP_CACHE" rev-parse --abbrev-ref HEAD)
    git -C "$TAP_CACHE" reset --hard "origin/${CACHE_BRANCH}"
    ok "Homebrew tap cache synced ($(grep sha256 "$TAP_CACHE/Casks/qforge.rb" | xargs))"
else
    brew tap "${TAP_SLUG}"
    ok "Homebrew tap installed"
fi

# Audit the formula — non-fatal, just a lint check
brew audit --cask qforge 2>&1 || warn "brew audit reported warnings (non-fatal)"

# =============================================================================
# Done
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  ✅  QForge ${TAG} deployed successfully!${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  GitHub release:   https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"
echo -e "  Homebrew tap:     https://github.com/${GITHUB_USER}/${TAP_REPO}"
echo ""
echo -e "${BOLD}Users can now install with:${NC}"
echo ""
echo -e "  brew tap ${GITHUB_USER}/${APP_NAME_LOWER}"
echo -e "  brew install --cask qforge"
echo ""
echo -e "  # or update an existing install:"
echo -e "  brew upgrade --cask qforge"
echo ""
