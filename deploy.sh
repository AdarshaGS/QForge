#!/bin/bash
# QForge — Upload DMG to GitHub Releases
# Assumes QForge.dmg is already built (run build.sh first)
#
# NOTE: pushing a vX.Y.Z tag now triggers
# .github/workflows/build-release.yml, which builds QForge from that tagged
# commit on a clean CI runner, publishes its own GitHub Release, AND (via
# the update-homebrew-tap job) pushes the version/sha256 bump to
# adarshags/homebrew-qforge automatically — the normal release path is just:
#   1. Bump APP_VERSION in utils/updater.py, commit.
#   2. git tag vX.Y.Z && git push origin vX.Y.Z
# This script is now only a manual fallback — for a hotfix where CI is down,
# or the HOMEBREW_TAP_TOKEN repo secret isn't set (the tap job warns and
# skips rather than failing if so). Don't run this script AND push that tag
# for the same version — whichever runs second will fail because the
# release already exists, and both would try to push to the same tap.

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────
GITHUB_USER="AdarshaGS"
GITHUB_REPO="QForge"
DMG_NAME="QForge.dmg"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DMG_PATH="$REPO_DIR/$DMG_NAME"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    read -rp "Enter release version (e.g. 1.1.0): " VERSION
fi
TAG="v${VERSION}"

# ─── Colors / helpers ───────────────────────────────────────────
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
step()    { echo -e "\n${BOLD}${BLUE}━━━ $1 ━━━${NC}"; }
substep() { echo -e "  ${CYAN}→ $1${NC}"; }
ok()      { echo -e "  ${GREEN}✔  $1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠  $1${NC}"; }
die()     { echo -e "\n${RED}✘  $1${NC}"; exit 1; }

echo -e "\n${BOLD}QForge Deploy — releasing ${TAG}${NC}"
echo -e "────────────────────────────────────────"

# ─── Step 1: Preflight ──────────────────────────────────────────
step "[1/4] Preflight checks"

substep "Checking GitHub CLI (gh)..."
command -v gh >/dev/null || die "GitHub CLI not found — run: brew install gh"
ok "gh found: $(gh --version | head -1)"

substep "Checking GitHub auth..."
gh auth status >/dev/null 2>&1 || die "Not logged in — run: gh auth login"
GH_USER=$(gh api user --jq .login 2>/dev/null)
[ "$GH_USER" = "$GITHUB_USER" ] || die "Logged in as '$GH_USER', expected '$GITHUB_USER'"
gh auth setup-git
ok "Authenticated as $GH_USER on github.com"

substep "Checking DMG file..."
[ -f "$DMG_PATH" ] || die "DMG not found: $DMG_PATH  →  run build.sh first"
DMG_SIZE_HR=$(du -sh "$DMG_PATH" | cut -f1)
DMG_SIZE_BYTES=$(stat -f%z "$DMG_PATH")
ok "Found: $DMG_NAME  ($DMG_SIZE_HR, $DMG_SIZE_BYTES bytes)"

# ─── Step 2: SHA256 ─────────────────────────────────────────────
step "[2/4] SHA256 checksum"

substep "Hashing $DMG_NAME..."
SHA256=$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')
ok "$SHA256"

# ─── Step 3: Create GitHub release (no attachment yet) ──────────
step "[3/4] Creating GitHub release"

# ─── Step 3: Create GitHub release (no attachment yet) ──────────
step "[3/4] Creating GitHub release"

if gh release view "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" >/dev/null 2>&1; then
    echo ""
    echo -e "  ${RED}✘  Release $TAG already exists on GitHub.${NC}"
    echo -e "  ${YELLOW}Re-releasing the same version confuses Homebrew — users already on${NC}"
    echo -e "  ${YELLOW}$TAG won't get the update because brew compares version strings only.${NC}"
    echo ""
    echo -e "  Options:"
    echo -e "    ${BOLD}y${NC} — delete and overwrite $TAG  (use only for hotfixes; run 'brew reinstall --cask qforge' manually after)"
    echo -e "    ${BOLD}n${NC} — abort so you can bump the version number first  ${GREEN}(recommended)${NC}"
    echo ""
    read -rp "  Overwrite $TAG? [y/n] " _OVERWRITE
    if [ "${_OVERWRITE,,}" != "y" ]; then
        echo ""
        echo -e "  ${YELLOW}Aborted. Bump APP_VERSION in utils/updater.py, re-run build.sh, then deploy.sh.${NC}"
        echo ""
        exit 0
    fi
    warn "Overwriting $TAG..."
    substep "Deleting existing release $TAG..."
    gh release delete "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" --yes
    ok "Release deleted"
    substep "Removing local git tag $TAG..."
    git tag -d "$TAG" 2>/dev/null || true
    substep "Removing remote git tag $TAG..."
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
    ok "Tags cleared"
    echo ""
    echo -e "  ${YELLOW}NOTE: existing brew users on $TAG must run:  brew reinstall --cask qforge${NC}"
    echo ""
fi

substep "Creating release $TAG on GitHub..."
gh release create "$TAG" \
    --repo "${GITHUB_USER}/${GITHUB_REPO}" \
    --title "QForge ${TAG}" \
    --notes "## QForge ${TAG}

Download \`${DMG_NAME}\` below, open it, and drag QForge to Applications.

**SHA256:** \`${SHA256}\`"
ok "Release created: https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"

# ─── Step 4: Upload DMG with progress ───────────────────────────
step "[4/4] Uploading $DMG_NAME ($DMG_SIZE_HR)"

substep "Fetching upload URL..."
UPLOAD_URL=$(gh api "repos/${GITHUB_USER}/${GITHUB_REPO}/releases/tags/${TAG}" \
    --jq '.upload_url' | sed 's/{?name,label}//')
ok "Upload URL ready"

substep "Getting auth token..."
GH_TOKEN=$(gh auth token)

echo ""
echo -e "  ${BOLD}Uploading to GitHub...${NC}"
echo -e "  (${DMG_SIZE_HR} — progress shown below)"
echo ""

# curl -# shows a live hash-mark progress bar:
#   ######################################## 100.0%
DOWNLOAD_URL=$(curl -# \
    -X POST \
    -H "Authorization: token $GH_TOKEN" \
    -H "Content-Type: application/octet-stream" \
    --data-binary "@$DMG_PATH" \
    "${UPLOAD_URL}?name=${DMG_NAME}" \
    2>&1 | tee /dev/stderr | tail -1 | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('browser_download_url',''))" \
    2>/dev/null || true)

# Simpler fallback: just run upload and let curl print its own progress
if [ -z "$DOWNLOAD_URL" ]; then
    curl --progress-bar \
        -X POST \
        -H "Authorization: token $GH_TOKEN" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@$DMG_PATH" \
        "${UPLOAD_URL}?name=${DMG_NAME}" \
        -o /tmp/qforge_upload_resp.json
    DOWNLOAD_URL=$(python3 -c \
        "import json; d=json.load(open('/tmp/qforge_upload_resp.json')); print(d.get('browser_download_url',''))" \
        2>/dev/null || true)
fi

echo ""
ok "Upload complete!"
[ -n "$DOWNLOAD_URL" ] && ok "Download URL: $DOWNLOAD_URL"

# ─── Step 5: Update Homebrew tap formula ────────────────────────
step "[5/5] Updating Homebrew tap (adarshags/homebrew-qforge)"

TAP_DIR="/opt/homebrew/Library/Taps/adarshags/homebrew-qforge"
TAP_FILE="$TAP_DIR/Casks/qforge.rb"

if [ ! -f "$TAP_FILE" ]; then
    warn "Tap not found at $TAP_FILE — skipping Homebrew update"
    warn "Run: brew tap adarshags/qforge  then re-run deploy.sh"
else
    substep "Patching $TAP_FILE..."
    # Replace version and sha256 lines
    sed -i '' "s/  version \".*\"/  version \"${VERSION}\"/" "$TAP_FILE"
    sed -i '' "s/  sha256 \".*\"/  sha256 \"${SHA256}\"/" "$TAP_FILE"
    ok "Formula updated to ${VERSION} with correct SHA256"

    substep "Committing and pushing tap..."
    cd "$TAP_DIR"
    git add Casks/qforge.rb
    git commit -m "qforge ${TAG}"
    git pull --rebase --quiet
    git push
    ok "Tap pushed — brew upgrade --cask qforge will now work"
    cd "$REPO_DIR"
fi

# Also patch the in-repo copy of qforge.rb
substep "Patching repo copy of qforge.rb..."
sed -i '' "s/  version \".*\"/  version \"${VERSION}\"/" "$REPO_DIR/qforge.rb"
sed -i '' "s/  sha256 \".*\"/  sha256 \"${SHA256}\"/" "$REPO_DIR/qforge.rb"
ok "Repo qforge.rb updated"

# Patch APP_VERSION in utils/updater.py
substep "Patching utils/updater.py APP_VERSION..."
sed -i '' "s/^APP_VERSION  = \".*\"/APP_VERSION  = \"${VERSION}\"/" "$REPO_DIR/utils/updater.py"
ok "APP_VERSION set to ${VERSION}"

# Commit the main repo changes
substep "Committing version bump to main repo..."
cd "$REPO_DIR"
git add qforge.rb utils/updater.py
git commit -m "v${VERSION}: bump APP_VERSION and cask formula" 2>/dev/null || ok "(nothing new to commit in main repo)"

# ─── Done ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   ✅  QForge ${TAG} released successfully!${NC}${BOLD}${GREEN}            ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo -e "  ${BOLD}Release:${NC} https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"
echo -e "  ${BOLD}Size:${NC}    ${DMG_SIZE_HR}"
echo -e "  ${BOLD}SHA256:${NC}  ${SHA256}"
echo ""
